import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import companion as c

DEV = "TEST_A1"
TASK = {
    "id": 90001, "status": 1, "deviceId": DEV,
    "title": "test EXT duplicate spool",
    "startTime": "2026-10-24T08:00:00Z",
    "endTime": None, "weight": 24.0,
    "amsMapping": [-1,-1,-1],
    "amsMapping2": [{"amsId":255,"slotId":255},
                    {"amsId":255,"slotId":0},
                    {"amsId":255,"slotId":255}],
    "amsDetailMapping": [{"ams":255,"weight":24.0,"filamentId":"GFL99",
                          "filamentType":"PLA","targetColor":"663300FF"}]
}
A = {"id": "1001", "netWeight":500, "filamentId":"GFL99",
     "filamentType":"PLA", "color":"#663300",
     "devId":"", "inPrinter":False}
B = dict(A, id="1002", netWeight=600)

class TaskSnapshotTest(unittest.TestCase):
    def new_ledger(self):
        x=c.default_ledger()
        x["initialized"]=True
        x["lastSeenTaskId"]=90000
        return x

    def test_ambiguous_no_snapshot_without_selection(self):
        ledger=self.new_ledger()
        with patch.object(c,"atomic_json"):
            changed=c.capture_task_spools([TASK], [A,B], ledger)
        self.assertFalse(changed)
        self.assertEqual(ledger["taskSpools"], {})

    def test_selected_ext_id_pinned_per_task(self):
        ledger=self.new_ledger()
        ledger["extSelection"][DEV]={"spoolId":"1002"}
        ledger["extSelectionEvents"][DEV]=[{"spoolId":"1002","atEpoch": c.parse_cloud_time("2026-10-24T07:00:00Z")}]
        with patch.object(c,"atomic_json"),patch.object(c,"log"):
            c.capture_task_spools([TASK], [A,B], ledger)
        item=ledger["taskSpools"]["90001"][f"{DEV}|255|0"]
        self.assertEqual(item["spoolId"],"1002")
        # Selection changes later; existing task MUST keep its original ID.
        ledger["extSelection"][DEV]={"spoolId":"1001"}
        with patch.object(c,"atomic_json"),patch.object(c,"log"):
            c.capture_task_spools([TASK], [A,B], ledger)
        self.assertEqual(ledger["taskSpools"]["90001"][f"{DEV}|255|0"]["spoolId"],"1002")

    def test_unique_auto_snapshot(self):
        ledger=self.new_ledger()
        with patch.object(c,"atomic_json"),patch.object(c,"log"):
            c.capture_task_spools([TASK], [A], ledger)
        self.assertEqual(ledger["taskSpools"]["90001"][f"{DEV}|255|0"]["spoolId"],"1001")

    def test_finished_job_not_backdated(self):
        ledger=self.new_ledger()
        ledger["extSelection"][DEV]={"spoolId":"1002"}
        ledger["extSelectionEvents"][DEV]=[{"spoolId":"1002","atEpoch": c.parse_cloud_time("2026-10-24T07:00:00Z")}]
        terminal=dict(TASK,status=2)
        with patch.object(c,"atomic_json"):
            c.capture_task_spools([terminal], [A,B], ledger)
        self.assertFalse(ledger["taskSpools"])

    def test_snapshot_survives_restart_and_charges_right_spool(self):
        ledger=self.new_ledger()
        ledger["extSelection"][DEV]={"spoolId":"1002"}
        ledger["extSelectionEvents"][DEV]=[{"spoolId":"1002","atEpoch": c.parse_cloud_time("2026-10-24T07:00:00Z")}]
        with tempfile.TemporaryDirectory() as td:
            led=Path(td)/"ledger.json"
            stat=Path(td)/"status.json"
            log=Path(td)/"companion.log"
            with (patch.object(c,"LEDGER",led), patch.object(c,"STATUS",stat),
                  patch.object(c,"LOG",log), patch.object(c,"DATA",Path(td)),
                  patch.object(c,"api_json",return_value={"hits":[copy.deepcopy(A),copy.deepcopy(B)]}),
                  patch.object(c,"fetch_tasks_for_cycle") as fetch,
                  patch.object(c,"refresh_running_studio",return_value="mock"),
                  patch.object(c,"update_spool") as update):
                c.atomic_json(led,ledger)
                fetch.return_value=([copy.deepcopy(TASK)],90001,1)
                self.assertEqual(c.cycle(),0)
                after=c.ledger_load()
                self.assertEqual(after["taskSpools"]["90001"][f"{DEV}|255|0"]["spoolId"],"1002")
                # Simulated companion restart & swap to the other spool.
                after["extSelection"][DEV]={"spoolId":"1001"}
                c.atomic_json(led,after)
                finished=dict(TASK,status=2,endTime="2026-10-24T09:00:00Z")
                fetch.return_value=([finished],90001,1)
                self.assertEqual(c.cycle(),0)
                self.assertEqual(update.call_count,1)
                self.assertEqual(str(update.call_args[0][0]["id"]),"1002")
                self.assertEqual(update.call_args[0][1],576)
                self.assertIn("90001",c.ledger_load()["processed"])
                self.assertEqual(c.cycle(),0)
                self.assertEqual(update.call_count,1)

    def test_offline_job_uses_previously_selected_spool(self):
        ledger=self.new_ledger()
        ledger["extSelectionEvents"][DEV]=[{
            "spoolId":"1002", "atEpoch":c.parse_cloud_time("2026-10-24T07:00:00Z")}]
        with tempfile.TemporaryDirectory() as td:
            led=Path(td)/"ledger.json"
            with (patch.object(c,"LEDGER",led),patch.object(c,"STATUS",Path(td)/"status.json"),
                  patch.object(c,"LOG",Path(td)/"log.txt"),patch.object(c,"DATA",Path(td)),
                  patch.object(c,"api_json",return_value={"hits":[copy.deepcopy(A),copy.deepcopy(B)]}),
                  patch.object(c,"fetch_tasks_for_cycle") as fetch,
                  patch.object(c,"refresh_running_studio",return_value="mock"),
                  patch.object(c,"update_spool") as update):
                c.atomic_json(led,ledger)
                terminal=dict(TASK,status=2,endTime="2026-10-24T09:00:00Z")
                fetch.return_value=([terminal],90001,1)
                self.assertEqual(c.cycle(),0)
                self.assertEqual(str(update.call_args[0][0]["id"]),"1002")
                self.assertIn("90001",c.ledger_load()["processed"])

    def test_selection_after_print_start_cannot_retroactively_charge(self):
        ledger=self.new_ledger()
        ledger["extSelectionEvents"][DEV]=[{
            "spoolId":"1002", "atEpoch":c.parse_cloud_time("2026-10-24T09:00:00Z")}]
        self.assertEqual(c.explicit_ext_spool_at_start(ledger,DEV,TASK),"")
        with patch.object(c,"atomic_json"):
            terminal=dict(TASK,status=2)
            c.capture_task_spools([terminal],[A,B],ledger)
        self.assertFalse(ledger["taskSpools"])
    def test_running_with_zero_weight_still_pins_spool_id(self):
        ledger=self.new_ledger()
        early=copy.deepcopy(TASK)
        early["weight"]=0
        early["amsDetailMapping"][0]["weight"]=0
        with patch.object(c,"atomic_json"),patch.object(c,"log"):
            c.capture_task_spools([early],[A],ledger)
        self.assertEqual(ledger["taskSpools"]["90001"][f"{DEV}|255|0"]["spoolId"],"1001")

    def test_explicit_ext_selection_records_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            ledger=c.default_ledger()
            ledger["initialized"]=True
            path=Path(td)/"ledger.json"
            with (patch.object(c,"LEDGER",path),patch.object(c,"DATA",Path(td)),
                  patch.object(c,"LOG",Path(td)/"companion.log"),
                  patch.object(c,"api_json",return_value={"hits":[A,B]})):
                c.atomic_json(path,ledger)
                selected_device=c.select_ext_spool("1002",DEV)
                self.assertEqual(selected_device,DEV)
                loaded=c.ledger_load()
                self.assertEqual(loaded["extSelection"][DEV]["spoolId"],"1002")
                self.assertEqual(len(loaded["extSelectionEvents"][DEV]),1)
                self.assertGreater(loaded["extSelectionEvents"][DEV][0]["atEpoch"],0)
if __name__=="__main__":
    unittest.main(verbosity=2)
