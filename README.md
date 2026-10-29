# Bambu Filament Companion

**Unofficial Windows companion for Bambu Lab printers and Bambu Studio Filament Manager.**

Bambu Filament Companion records material consumption from print jobs and updates spool weights in Bambu Studio's cloud Filament Manager. It is designed for a Bambu Lab A1 with AMS Lite and can track an external spool (EXT). The Python companion periodically checks cloud print tasks; a small Windows C++ bridge calls the Bambu Studio networking plugin installed on the user's own computer.

This project is independent of Bambu Lab and is **not officially supported** by Bambu Lab.

## What it does

- Retrieves the print history and filament inventory via the installed Bambu Studio networking component and the user's existing Bambu Studio login.
- Processes completed print jobs, attempting to deduct consumed grams exactly once per task.
- Associates an AMS slot/EXT spool with the job when the print is first observed, so swapping spools later cannot cause deduction from a different spool.
- Never guesses between indistinguishable physical EXT spools. If there is no reliable mapping, the task stays pending rather than deducting material from the wrong spool.
- Can request a Filament Manager refresh in a running Bambu Studio window.
- Runs once a minute as a Windows Scheduled Task, with a local ledger for deduplication and recovery.

## Requirements

- Windows 10/11 x64.
- Python 3 available on PATH (including `pythonw.exe`). Python standard library only; no PyPI dependencies.
- The official **Bambu Studio** application, installed and signed in to Bambu Cloud. Its `bambu_networking.dll` and certificate files must be present. This repository does **not** bundle or redistribute them.
- **Visual Studio 2022 or later / Build Tools for Visual Studio**, with *Desktop development with C++* (MSVC x64 toolchain), to compile the bridge.
- Access to Bambu Cloud and a configured Filament Manager inventory.

## Build and install

1. Clone or download this repository.
2. Run `Build-Bridge.cmd` on Windows to compile `BambuNetworkBridge.cpp` into `BambuNetworkBridge.exe` in the same directory. The build script locates Visual Studio using `vswhere.exe`.
3. Open Bambu Studio, sign in and verify that the Filament Manager has the correct spools and remaining weights.
4. Run `Install-Companion.cmd`. The installer copies files to `%LOCALAPPDATA%\BambuFilamentCompanion`, initializes the local ledger if needed and creates a Scheduled Task named `Bambu Filament Companion` running every minute.
5. Check `%LOCALAPPDATA%\BambuFilamentCompanion\companion.log` and `status.json` for status. Inspect changes in Filament Manager.

Run `Reinstall-Companion.cmd` after updating the source/build; this preserves the existing ledger. Run `Uninstall-Companion.cmd` to remove the Scheduled Task. Uninstall intentionally **keeps the local data and ledger**.

### EXT spool selection

If two or more physical EXT spools share the same material/color, run `Select-EXT-Spool.cmd` **before starting the print**. It lists compatible cloud inventory spools and records the exact selected spool ID with a timestamp. Change the selection whenever the physical EXT spool changes. An explicit selection made during a still-running task can be pinned while the task is active. Selecting a spool after the task has completed is not applied retroactively. Select the physical spool before starting the print whenever possible.

If a spool was not identified unambiguously, the companion intentionally does not deduct its weight automatically; manual reconciliation may be required.

### Tests

```powershell
python -m unittest discover -s . -p "test_*.py" -v
```

These unit tests use synthetic tasks and mocks; they do not call Bambu Cloud or change your actual spool inventory.

## Privacy and security

The program runs locally using the **user's existing Bambu Studio authentication**, which may be copied from the current Bambu Studio configuration into private local companion configuration at runtime. This is sensitive session material: do not upload, share or commit your installed companion directory or any `BambuNetworkEngine.conf`, `data/`, `network/`, `network_log/`, logs, spool records or backups.

No usernames, passwords, API tokens, account records, printer identifiers, session files or Bambu networking binaries are provided in this repository. **Do not copy your local auth files into this source tree.** The companion does interact with the user's Bambu Cloud account to update filament spool weights.

## Limitations and cautions

- This is an experimental integration with Bambu Studio networking interfaces, which can change in a future Bambu Studio release.
- Filament usage and slot identification depend on the task metadata provided by Bambu Cloud. Some older/offline prints cannot be matched reliably.
- If a spool is missing or ambiguous, no automatic fallback to a different spool is attempted.
- The job-specific spool ID functionality has automated tests; a live new-print end-to-end validation was not available at the time of the 2026-10-24 implementation.
- This is not an official Bambu Lab feature. Check inventory adjustments against actual spool usage before relying on it for critical stock management.

## License and upstream

Distributed under **GNU Affero General Public License v3.0 (AGPL-3.0)**; see [LICENSE](LICENSE). The tool is intended to interoperate with the *separately installed* official Bambu Studio and its networking plugin. No copy of Bambu Studio's DLL, installation files, or full codebase is included here. The official [Bambu Studio](https://github.com/bambulab/BambuStudio) project publishes its source under AGPL-3.0.

---

### Русский

Неофициальный компаньон для Windows: после печати обновляет остатки катушек в Filament Manager через установленный Bambu Studio. Поддерживает AMS Lite и внешнюю катушку EXT. Если катушку определить однозначно нельзя, автоматического списания не будет.

Установка: нужен Python 3, официальный Bambu Studio с авторизацией в Bambu Cloud и MSVC Build Tools. Сначала запустите `Build-Bridge.cmd`, затем `Install-Companion.cmd`. Для неоднозначной EXT-катушки используйте `Select-EXT-Spool.cmd` **до начала печати**. Локальные файлы авторизации и журнал списаний находятся в `%LOCALAPPDATA%\BambuFilamentCompanion` - никогда не публикуйте их в GitHub.
