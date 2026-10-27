import json, math, os, subprocess, sys, tempfile, time, ctypes
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ["LOCALAPPDATA"]) / "BambuFilamentCompanion"
DATA = ROOT / "data"
BRIDGE = ROOT / "BambuNetworkBridge.exe"
STUDIO_REFRESH = ROOT / "Refresh-StudioFilament.ps1"
LEDGER = DATA / "ledger.json"
STATUS = DATA / "status.json"
LOG = ROOT / "companion.log"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GUARD_SECONDS = 600
TASK_PAGE_SIZE = 100
MAX_TASK_PAGES = 20
FAILED_STARTUP_GRACE_SECONDS = 300
FAILED_MIN_EFFECTIVE_SECONDS = 60
FAILED_MIN_RATIO = 0.03
FAILED_MAX_RAW_RATIO = 1.10

def ensure():
    DATA.mkdir(parents=True, exist_ok=True)

def log(msg, level="INFO"):
    ensure()
    try:
        if LOG.exists() and LOG.stat().st_size > 5 * 1024 * 1024:
            old = LOG.with_suffix(".log.1")
            old.unlink(missing_ok=True)
            LOG.replace(old)
    except Exception:
        pass
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{level}] {msg}\n")

def atomic_json(path, obj):
    ensure()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def status(state, detail):
    atomic_json(STATUS, {"time": datetime.now().isoformat(timespec="seconds"), "state": state, "detail": detail})

def run_bridge(kind, *args):
    ensure()
    if not BRIDGE.exists():
        raise RuntimeError(f"bridge not found: {BRIDGE}")
    fd, outname = tempfile.mkstemp(prefix="bfc_", suffix=".json", dir=DATA)
    os.close(fd)
    out = Path(outname)
    cmd = [str(BRIDGE), kind, *map(str, args), str(out)]
    try:
        cp = subprocess.run(cmd, creationflags=CREATE_NO_WINDOW, timeout=30, capture_output=True)
        body = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
        diagnostic = (cp.stderr or b"").decode("utf-8", errors="replace").strip()
        if cp.returncode != 0:
            raise RuntimeError(f"bridge {kind} rc={cp.returncode}; {diagnostic[:300]}")
        if "retry_with_current_bambu_studio_auth" in diagnostic:
            log(f"bridge {kind}: recovered using current Bambu Studio auth")
        return body
    finally:
        out.unlink(missing_ok=True)

def api_json(kind, *args):
    body = run_bridge(kind, *args)
    return json.loads(body) if body else {}

def refresh_running_studio():
    """Ask an already-running official Bambu Studio to pull Filament Manager.

    This uses Windows UI Automation to invoke Studio's own CloudBadge button.
    It never starts Studio and never modifies the Studio executable.
    """
    if not STUDIO_REFRESH.exists():
        log("Studio live refresh helper missing", "WARN")
        return "helper_missing"
    try:
        cp = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(STUDIO_REFRESH)
            ],
            creationflags=CREATE_NO_WINDOW,
            timeout=12,
            capture_output=True,
            text=True,
        )
        out = (cp.stdout or "").strip()
        err = (cp.stderr or "").strip()
        msg = out or err or f"rc={cp.returncode}"
        if cp.returncode == 0:
            log(f"Studio Filament Manager live refresh requested: {msg}")
            return "refreshed"
        if cp.returncode == 2:
            log("Studio live refresh skipped: Studio is not running")
            return "studio_not_running"
        if cp.returncode == 4:
            log("Studio live refresh deferred: Filament Manager CloudBadge is not currently visible")
            return "not_visible"
        log(f"Studio live refresh skipped: {msg}", "WARN")
        return f"rc_{cp.returncode}"
    except Exception as e:
        log(f"Studio live refresh failed: {type(e).__name__}: {e}", "WARN")
        return "failed"

def fetch_tasks_for_cycle(l):
    """Fetch enough newest history pages to cover everything since the last scan.

    The cursor is a task id, not a timestamp. If there are unresolved terminal
    jobs, scan back far enough to include the oldest one as well.
    """
    watermark = as_int(l.get("lastSeenTaskId"), 0)
    pending_ids = [as_int(x, 0) for x in l.get("pending", {})]
    pending_ids = [x for x in pending_ids if x > 0]
    target = min([watermark] + pending_ids) if watermark > 0 else (min(pending_ids) if pending_ids else 0)

    found = {}
    pages = 0
    total = None
    for page in range(MAX_TASK_PAGES):
        offset = page * TASK_PAGE_SIZE
        j = api_json("tasks", offset, TASK_PAGE_SIZE)
        pages += 1
        if total is None:
            total = as_int(j.get("total"), 0)
        hits = [x for x in j.get("hits", []) if isinstance(x, dict)]
        for t in hits:
            tid = jid(t.get("id"))
            if tid:
                found[tid] = t

        # On migration from the old companion there is no durable cursor yet.
        # Read only the newest page so historical jobs cannot suddenly be charged.
        if target <= 0:
            break
        if any(as_int(t.get("id"), 0) <= target for t in hits):
            break
        if len(hits) < TASK_PAGE_SIZE:
            break
        if total and offset + len(hits) >= total:
            break

    tasks = list(found.values())
    max_seen = max([as_int(t.get("id"), 0) for t in tasks] or [watermark])
    return tasks, max_seen, pages

def update_spool(spool, new_weight):
    sid = str(spool["id"])
    req = {
        "id": int(sid) if sid.isdigit() else sid,
        "netWeight": int(new_weight),
        "filamentName": spool.get("filamentName") or spool.get("filamentType") or "",
    }
    fd, name = tempfile.mkstemp(prefix="bfc_req_", suffix=".json", dir=DATA)
    os.close(fd)
    p = Path(name)
    try:
        p.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
        run_bridge("update", sid, p)
        return True
    finally:
        p.unlink(missing_ok=True)

def default_ledger():
    return {
        "schema": 3, "initialized": False,
        "processed": {}, "ignored": {}, "partial": {}, "pending": {},
        "residual": {}, "guards": {}, "bindings": {},
        "taskSpools": {}, "extSelection": {}, "extSelectionEvents": {},
        "lastSeenTaskId": 0,
    }

def ledger_load():
    x = load_json(LEDGER, default_ledger())
    if not isinstance(x, dict): x = default_ledger()
    expected_keys = ("processed", "ignored", "partial", "pending", "residual", "guards", "bindings", "taskSpools", "extSelection", "extSelectionEvents")
    needs_upgrade = x.get("schema") != 3 or any(not isinstance(x.get(k), dict) for k in expected_keys)
    for k in expected_keys:
        if not isinstance(x.get(k), dict): x[k] = {}
    x.setdefault("initialized", False)

    if "lastSeenTaskId" not in x:
        known = []
        for bucket in ("processed", "ignored"):
            for tid in x.get(bucket, {}):
                n = as_int(tid, 0)
                if n > 0:
                    known.append(n)
        x["lastSeenTaskId"] = max(known) if known else 0

    x["schema"] = 3
    if needs_upgrade and LEDGER.exists():
        atomic_json(LEDGER, x)
    return x

def jid(v):
    if v is None: return ""
    return str(v)

def as_int(v, default=-1):
    try: return int(v)
    except Exception: return default

def as_float(v, default=0.0):
    try: return float(v)
    except Exception: return default

def color(v):
    if not v: return ""
    s = str(v).upper()
    if not s.startswith("#"): s = "#" + s
    if len(s) == 9: s = s[:7]
    return s

def parse_cloud_time(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

def failed_usage_factor(task, usages):
    """Conservative estimate for a failed single-material print.

    Time ratio is deliberately biased low: subtract five minutes for heat-up,
    homing/calibration and refuse suspicious/very short jobs. Multi-material
    failed jobs are not auto-booked because material use is not uniform by layer.
    """
    if len(usages) != 1:
        return None, "failed multi-material print needs layer-aware accounting"

    start = parse_cloud_time(task.get("startTime"))
    end = parse_cloud_time(task.get("endTime"))
    cost = as_float(task.get("costTime"), 0.0)
    if start is None or end is None or end <= start or cost <= 0:
        return None, "failed print has no reliable timing"

    elapsed = end - start
    raw_ratio = elapsed / cost
    if raw_ratio > FAILED_MAX_RAW_RATIO:
        return None, f"failed print timing is ambiguous (elapsed/estimate={raw_ratio:.2f})"

    effective = max(0.0, elapsed - FAILED_STARTUP_GRACE_SECONDS)
    if effective < FAILED_MIN_EFFECTIVE_SECONDS:
        return None, "failed print ended before reliable extrusion window"

    ratio = effective / cost
    if ratio < FAILED_MIN_RATIO:
        return None, f"failed print estimated material ratio too small ({ratio:.3f})"

    return min(1.0, ratio), (
        f"time estimate: elapsed={elapsed:.0f}s, startup_grace={FAILED_STARTUP_GRACE_SECONDS}s, "
        f"estimated_print={cost:.0f}s, factor={min(1.0, ratio):.4f}"
    )

def set_pending(l, tid, task_status, reason):
    l["pending"][tid] = {
        "status": task_status,
        "reason": reason,
        "time": datetime.now().isoformat(),
    }
    atomic_json(LEDGER, l)

def extract_usage(task):
    legacy = [as_int(x) for x in task.get("amsMapping", [])]
    mapping2 = task.get("amsMapping2") or []
    details = task.get("amsDetailMapping") or []
    used_idx = set()
    out = []

    for di, d in enumerate(details):
        grams = as_float(d.get("weight"))
        if not math.isfinite(grams) or grams <= 0: continue
        old = as_int(d.get("ams"))
        idx = next((i for i, x in enumerate(legacy) if i not in used_idx and x == old), -1)
        if old == 255:
            # EXT uses the virtual 255/0 source even when amsMapping is all -1.
            ext = [i for i, x in enumerate(mapping2)
                   if i not in used_idx and as_int(x.get("amsId"), -1) == 255
                   and as_int(x.get("slotId"), -1) == 0]
            if len(ext) != 1:
                return [], "cannot identify unique EXT 255/0 source"
            idx = ext[0]
        elif idx < 0 and di < len(mapping2): idx = di
        # In EXT jobs amsMapping can be all -1 while amsMapping2 has
        # exactly one real input (255/0) at a different index.
        # Recover only for single-material tasks with one unambiguous input.
        def valid_input(i):
            if i < 0 or i >= len(mapping2) or i in used_idx: return False
            am = as_int(mapping2[i].get("amsId"), -1)
            sl = as_int(mapping2[i].get("slotId"), -1)
            return am >= 0 and sl >= 0 and sl != 255 and (am != 255 or sl == 0)
        if not valid_input(idx):
            matches = [i for i in range(len(mapping2)) if valid_input(i)]
            if len(details) == 1 and len(matches) == 1:
                idx = matches[0]
            else:
                return [], "cannot safely align amsDetailMapping with amsMapping2"
        m = mapping2[idx]
        ams, slot = as_int(m.get("amsId")), as_int(m.get("slotId"))
        # EXT main spool is represented by Bambu as amsId=255, slotId=0.
        # Treat exactly 255/0 as a real source. 255/255 still means
        # unused/unmapped and must not be accounted.
        is_ext = (ams == 255 and slot == 0)
        if ams < 0 or slot < 0 or (ams == 255 and not is_ext) or slot == 255:
            return [], "unused/unmapped filament source"
        used_idx.add(idx)
        out.append({
            "ams": ams, "slot": slot, "grams": grams,
            "filamentId": d.get("filamentId") or "",
            "filamentType": d.get("filamentType") or "",
            "color": color(d.get("targetColor") or d.get("sourceColor"))
        })

    if not out:
        total = as_float(task.get("weight"))
        valid = []
        for i, m in enumerate(mapping2):
            ams = as_int(m.get("amsId"))
            slot = as_int(m.get("slotId"))
            is_ext = (ams == 255 and slot == 0)
            if (0 <= ams and 0 <= slot and slot != 255 and (ams != 255 or is_ext)):
                valid.append((i, m))
        if total <= 0 or len(valid) != 1:
            return [], "no safe per-material usage data"
        _, m = valid[0]
        out = [{"ams": as_int(m["amsId"]), "slot": as_int(m["slotId"]),
                "grams": total, "filamentId": "", "filamentType": "", "color": ""}]

    agg = {}
    for u in out:
        k = (u["ams"], u["slot"])
        if k not in agg: agg[k] = dict(u)
        else: agg[k]["grams"] += u["grams"]
    out = list(agg.values())

    declared = as_float(task.get("weight"))
    total = sum(x["grams"] for x in out)
    warn = ""
    if declared > 0 and abs(declared-total) > max(1.0, declared*0.02):
        warn = f"detail sum {total:.2f} differs from task weight {declared:.2f}"
    return out, warn

def spool_id(s): return str(s.get("id", ""))

def exact_slot(s, dev, u):
    return (str(s.get("devId") or "") == dev and
            as_int(s.get("amsId"), -999) == u["ams"] and
            str(s.get("slotId") or "") == str(u["slot"]))

def find_spool(spools, dev, u, bound_sid=None):
    exact = [s for s in spools if exact_slot(s, dev, u)]
    if len(exact) == 1: return exact[0], "exact"
    if len(exact) > 1: return None, "multiple spools bound to same AMS slot"

    if bound_sid:
        bound = next((s for s in spools if spool_id(s) == str(bound_sid)), None)
        if bound:
            sdev = str(bound.get("devId") or "")
            same_or_unbound = (not sdev) or exact_slot(bound, dev, u)
            fid_ok = (not u["filamentId"] or not bound.get("filamentId") or bound.get("filamentId") == u["filamentId"])
            type_ok = (not u["filamentType"] or not bound.get("filamentType") or bound.get("filamentType") == u["filamentType"])
            color_ok = (not u["color"] or not bound.get("color") or color(bound.get("color")) == u["color"])
            weight_ok = as_float(bound.get("netWeight"), -1) < 0 or as_float(bound.get("netWeight"), -1) + 0.01 >= u["grams"]
            if same_or_unbound and fid_ok and type_ok and color_ok and weight_ok:
                return bound, "local slot binding"

    candidates = []
    for s in spools:
        if str(s.get("devId") or ""): continue
        if u["filamentId"] and s.get("filamentId") and s.get("filamentId") != u["filamentId"]: continue
        if u["filamentType"] and s.get("filamentType") and s.get("filamentType") != u["filamentType"]: continue
        if u["color"] and s.get("color") and color(s.get("color")) != u["color"]: continue
        w = as_float(s.get("netWeight"), -1)
        if w >= 0 and w + 0.01 < u["grams"]: continue
        candidates.append(s)
    if len(candidates) == 1: return candidates[0], "unique material/color fallback"
    if not candidates: return None, "no matching Filament Manager spool"
    return None, "ambiguous unbound spools; bind the correct spool to the AMS slot"

def spool_matches_material(spool, usage):
    """Never charge a pinned spool if its identity conflicts with task material."""
    for key in ("filamentId", "filamentType"):
        if usage.get(key) and spool.get(key) and usage[key] != spool[key]:
            return False
    return (not usage.get("color") or not spool.get("color")
            or usage["color"] == color(spool["color"]))


def explicit_ext_spool_at_start(ledger, device_id, task):
    """Use time-stamped explicit EXT choices, including for offline prints.

    A choice made after printing started NEVER applies retroactively.
    """
    started = parse_cloud_time(task.get("startTime"))
    if started is None:
        return ""
    events = ledger.get("extSelectionEvents", {}).get(device_id, [])
    matching = [x for x in events if as_float(x.get("atEpoch"), -1) <= started]
    if not matching:
        return ""
    newest = max(matching, key=lambda x: as_float(x.get("atEpoch"), -1))
    return str(newest.get("spoolId") or "")


def capture_task_spools(tasks, spools, ledger):
    """Freeze a uniquely identified spool for each RUNNING task before completion.

    A slot-level binding can change after the print. Task snapshots never do.
    A task discovered only after finishing is NOT backdated to the current EXT
    selection; material-only unique fallback remains available in accounting.
    """
    changed = False
    for task in tasks:
        tid = jid(task.get("id"))
        if (not tid or tid in ledger["processed"] or tid in ledger["ignored"]
                or as_int(task.get("status")) not in (0, 1)
                or parse_cloud_time(task.get("startTime")) is None):
            continue
        dev = str(task.get("deviceId") or "")
        if not dev:
            continue
        usages, _ = extract_usage(task)
        if not usages:
            # Capture identity while printing even when the cloud has not
            # populated estimated weight yet. This weight is never charged.
            metadata = [dict(d, weight=0.01) for d in (task.get("amsDetailMapping") or [])
                        if isinstance(d, dict) and (d.get("filamentId") or d.get("filamentType"))]
            if metadata:
                probe = dict(task, amsDetailMapping=metadata, weight=0.01 * len(metadata))
                usages, _ = extract_usage(probe)
        if not usages:
            continue
        for u in usages:
            slot_key = f"{dev}|{u['ams']}|{u['slot']}"
            if slot_key in ledger["taskSpools"].get(tid, {}):
                continue
            if u["ams"] == 255 and u["slot"] == 0:
                # EXT has no reliable cloud-side physical spool binding.
                # Only a user-selected EXT spool or a unique material/color
                # match can identify its spool at print start.
                preferred = explicit_ext_spool_at_start(ledger, dev, task)
            else:
                preferred = ledger["bindings"].get(slot_key)
            if u["ams"] == 255 and u["slot"] == 0 and preferred:
                spool = next((s for s in spools if spool_id(s) == preferred
                              and spool_matches_material(s, u)), None)
                reason = "explicit EXT spool selected before print"
            else:
                spool, reason = find_spool(spools, dev, u, preferred)
            if not spool or not spool_matches_material(spool, u):
                continue
            ledger["taskSpools"].setdefault(tid, {})[slot_key] = {
                "spoolId": spool_id(spool),
                "capturedAt": datetime.now().isoformat(),
                "startTime": task.get("startTime"),
                "method": ("explicit_ext_selection" if preferred
                           and u["ams"] == 255 and spool_id(spool) == preferred
                           else reason),
                "filamentId": u["filamentId"], "filamentType": u["filamentType"],
                "color": u["color"],
            }
            changed = True
            log(f"task={tid} spool ID SNAPSHOT {slot_key} -> {spool_id(spool)} ({reason})")
    if changed:
        atomic_json(LEDGER, ledger)
    return changed


def select_ext_spool(spool_id_value, device_id=""):
    """Explicit EXT selection, used when identical same-color spools coexist."""
    ledger = ledger_load()
    spools = [x for x in api_json("spools").get("hits", []) if isinstance(x, dict)]
    selected = next((s for s in spools if spool_id(s) == str(spool_id_value)), None)
    if not selected:
        raise ValueError(f"Unknown spool ID {spool_id_value}")
    if not device_id:
        devices = {str(s.get("devId")) for s in spools if s.get("devId")}
        if len(devices) != 1:
            raise ValueError("Specify deviceId when zero or multiple devices are available")
        device_id = next(iter(devices))
    ledger["extSelection"][device_id] = {
        "spoolId": spool_id(selected),
        "selectedAt": datetime.now().isoformat(),
        "filamentId": selected.get("filamentId") or "",
        "filamentType": selected.get("filamentType") or "",
        "color": color(selected.get("color")),
    }
    ledger["extSelectionEvents"].setdefault(device_id, []).append({
        "spoolId": spool_id(selected), "atEpoch": time.time(),
        "selectedAt": datetime.now().isoformat()
    })
    atomic_json(LEDGER, ledger)
    log(f"EXT selected device={device_id} spoolId={spool_id(selected)} "
        f"type={selected.get('filamentType')} color={selected.get('color')}")
    return device_id


def bootstrap(tasks, l):
    n = 0
    for t in tasks:
        st = as_int(t.get("status"), 0)
        if st not in (2,3): continue
        tid = jid(t.get("id"))
        if not tid or tid in l["processed"] or tid in l["ignored"]: continue
        l["ignored"][tid] = {"reason":"preexisting_at_install","status":st,"time":datetime.now().isoformat()}
        n += 1
    l["initialized"] = True
    l["schema"] = 3
    l["lastSeenTaskId"] = max([as_int(t.get("id"), 0) for t in tasks] or [as_int(l.get("lastSeenTaskId"), 0)])
    atomic_json(LEDGER, l)
    log(f"bootstrap: ignored {n} historical terminal tasks")
    status("running", "Bootstrap complete; waiting for new print jobs")

def reconcile_guards(spools, l):
    now = int(time.time())
    changed = False
    byid = {spool_id(s): s for s in spools}
    for sid in list(l["guards"]):
        g = l["guards"][sid]
        if as_int(g.get("until"), 0) <= now:
            del l["guards"][sid]; changed = True; continue
        s = byid.get(sid)
        if not s: continue
        cur, exp = round(as_float(s.get("netWeight"), -1)), as_int(g.get("expected"), -1)
        if cur < 0 or exp < 0: continue
        if cur > exp + 1:
            try:
                update_spool(s, exp)
                s["netWeight"] = exp
                log(f"re-applied protected weight spool={sid}: {cur}->{exp}")
            except Exception as e:
                log(f"guard update failed spool={sid}: {e}", "ERROR")
        elif cur < exp:
            g["expected"] = cur; changed = True
    if changed: atomic_json(LEDGER, l)

def cycle(force_bootstrap=False):
    ensure()
    try:
        l = ledger_load()
        tasks, max_seen_task_id, scanned_pages = fetch_tasks_for_cycle(l)
        tasks.sort(key=lambda x: as_int(x.get("id"), 0))
        if force_bootstrap or not l.get("initialized"):
            bootstrap(tasks, l); return 0

        sj = api_json("spools")
        spools = [x for x in sj.get("hits", []) if isinstance(x, dict)]

        # Learn currently mounted slot -> spool bindings while they are visible.
        # This is especially important for EXT (virtual slot 255/0): the task
        # history does not contain spool_id, but the user may have explicitly
        # selected a Filament Manager spool before printing.
        bindings_changed = False
        for s in spools:
            dev = str(s.get("devId") or "")
            if not dev or not s.get("inPrinter"): continue
            ams = as_int(s.get("amsId"), -1)
            slot = as_int(s.get("slotId"), -1)
            sid = spool_id(s)
            if ams < 0 or slot < 0 or not sid: continue
            key = f"{dev}|{ams}|{slot}"
            if l["bindings"].get(key) != sid:
                l["bindings"][key] = sid
                bindings_changed = True
                log(f"mounted slot binding learned {key} -> spool={sid}")
        if bindings_changed:
            atomic_json(LEDGER, l)

        # Pin spool IDs for active jobs BEFORE their completion and any
        # subsequent slot changes. This is durable across restarts.
        capture_task_spools(tasks, spools, l)
        reconcile_guards(spools, l)

        processed_now = 0
        pending = 0
        for t in tasks:
            tid = jid(t.get("id"))
            if not tid or tid in l["processed"] or tid in l["ignored"]: continue
            st = as_int(t.get("status"), 0)
            if st not in (2, 3):
                continue

            usages, warn = extract_usage(t)
            if warn:
                log(f"task={tid}: {warn}", "WARN")
            if not usages:
                pending += 1
                set_pending(l, tid, st, "no safe filament usage data")
                continue

            accounting_method = "finished_full"
            usage_factor = 1.0
            if st == 3:
                factor, estimate_detail = failed_usage_factor(t, usages)
                if factor is None:
                    l["ignored"][tid] = {
                        "reason": "failed_print_not_accounted",
                        "detail": estimate_detail,
                        "status": 3,
                        "time": datetime.now().isoformat(),
                    }
                    l["pending"].pop(tid, None)
                    atomic_json(LEDGER, l)
                    log(f"task={tid}: failed print not charged: {estimate_detail}", "INFO")
                    continue
                usage_factor = factor
                accounting_method = "failed_single_material_time_estimate"
                for u in usages:
                    u["grams"] *= usage_factor
                log(f"task={tid}: failed print partial accounting; {estimate_detail}", "INFO")

            dev = str(t.get("deviceId") or "")
            if not dev:
                log(f"task={tid}: no deviceId", "WARN")
                pending += 1
                set_pending(l, tid, st, "missing deviceId")
                continue

            work = {}
            mapping_ok = True
            for u in usages:
                bind_key = f"{dev}|{u['ams']}|{u['slot']}"
                pinned = l["taskSpools"].get(tid, {}).get(bind_key)
                if pinned:
                    s = next((sp for sp in spools
                              if spool_id(sp) == str(pinned.get("spoolId"))), None)
                    if s and not spool_matches_material(s, u):
                        s = None
                    why = "task spool ID snapshot" if s else "pinned spool missing or material changed"
                else:
                    # Never use a recent EXT binding for an old print which was
                    # not observed running: it might belong to a later print.
                    if u["ams"] == 255 and u["slot"] == 0:
                        preferred = explicit_ext_spool_at_start(l, dev, t)
                        if preferred:
                            s = next((sp for sp in spools
                                      if spool_id(sp) == preferred
                                      and spool_matches_material(sp, u)), None)
                            why = ("EXT explicitly selected before print"
                                   if s else "historically selected EXT spool missing/mismatched")
                        else:
                            s, why = find_spool(spools, dev, u, None)
                    else:
                        s, why = find_spool(spools, dev, u, l["bindings"].get(bind_key))
                if not s:
                    reason = f"AMS={u['ams']} slot={u['slot']}: {why}"
                    log(f"task={tid} {reason}", "WARN")
                    pending += 1
                    set_pending(l, tid, st, reason)
                    mapping_ok = False
                    break
                sid = spool_id(s)
                if l["bindings"].get(bind_key) != sid:
                    l["bindings"][bind_key] = sid
                    atomic_json(LEDGER, l)
                    log(f"slot binding learned {bind_key} -> spool={sid} via {why}")
                w = work.setdefault(sid, {"spool":s,"grams":0.0,"ams":u["ams"],"slot":u["slot"]})
                w["grams"] += u["grams"]
                if why != "exact": log(f"task={tid}: fallback spool={sid}: {why}")
            if not mapping_ok or not work: continue

            all_ok = True
            for sid, w in work.items():
                if sid in l["partial"].get(tid, {}): continue
                residual = as_float(l["residual"].get(sid), 0.0)
                effective = w["grams"] + residual
                deduct = max(0, math.floor(effective + 0.5))
                new_residual = effective - deduct
                old = round(as_float(w["spool"].get("netWeight"), -1))
                if old < 0:
                    log(f"task={tid} spool={sid}: invalid netWeight", "ERROR"); all_ok=False; break
                new = max(0, old - deduct)
                try:
                    update_spool(w["spool"], new)
                except Exception as e:
                    log(f"task={tid} spool={sid}: update failed: {e}", "ERROR"); all_ok=False; break
                l["residual"][sid] = new_residual
                l["partial"].setdefault(tid, {})[sid] = {
                    "grams":w["grams"],"deductedIntegerGrams":deduct,
                    "oldNetWeight":old,"newNetWeight":new,
                    "amsId":w["ams"],"slotId":w["slot"],
                    "accountingMethod": accounting_method,
                    "usageFactor": usage_factor,
                    "time":datetime.now().isoformat()
                }
                l["guards"][sid] = {"expected":new,"until":int(time.time())+GUARD_SECONDS,"taskId":tid}
                atomic_json(LEDGER, l)
                w["spool"]["netWeight"] = new
                log(f"task={tid} spool={sid} AMS={w['ams']} slot={w['slot']} used={w['grams']:.2f}g netWeight {old}->{new}g")

            if all_ok and all(sid in l["partial"].get(tid,{}) for sid in work):
                l["processed"][tid] = {
                    "time":datetime.now().isoformat(),
                    "title":t.get("title") or "",
                    "deviceId":dev,
                    "taskStatus": st,
                    "accountingMethod": accounting_method,
                    "usageFactor": usage_factor,
                    "totalWeight":as_float(t.get("weight")),
                    "chargedWeight":sum(w["grams"] for w in work.values()),
                }
                l["partial"].pop(tid, None)
                l["pending"].pop(tid, None)
                atomic_json(LEDGER, l)
                processed_now += 1
                log(f"accounting complete task={tid} title={t.get('title') or ''}")

        l["lastSeenTaskId"] = max(as_int(l.get("lastSeenTaskId"), 0), max_seen_task_id)
        l["schema"] = 3
        atomic_json(LEDGER, l)

        studio_refresh = "not_needed"
        if processed_now > 0:
            studio_refresh = refresh_running_studio()

        status(
            "running",
            f"OK; processed={processed_now}; pending={pending}; "
            f"history_pages={scanned_pages}; studio_refresh={studio_refresh}"
        )
        return 0
    except Exception as e:
        log(f"cycle failed: {type(e).__name__}: {e}", "ERROR")
        status("error", str(e))
        return 2

if __name__ == "__main__":
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\BambuFilamentCompanionCycle")
    if ctypes.windll.kernel32.GetLastError() == 183:
        raise SystemExit(0)
    ensure()
    if "--select-ext" in sys.argv:
        i = sys.argv.index("--select-ext")
        if len(sys.argv) <= i + 1:
            raise SystemExit("Usage: companion.py --select-ext SPOOL_ID [DEVICE_ID]")
        device = sys.argv[i + 2] if len(sys.argv) > i + 2 else ""
        print("EXT selected for device", select_ext_spool(sys.argv[i + 1], device))
        raise SystemExit(0)
    if "--bootstrap" in sys.argv: raise SystemExit(cycle(True))
    raise SystemExit(cycle(False))
