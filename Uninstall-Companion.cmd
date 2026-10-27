@echo off
schtasks.exe /Delete /TN "Bambu Filament Companion" /F >nul 2>&1
echo Scheduled task removed.
echo Data and ledger were NOT deleted from %%LOCALAPPDATA%%\BambuFilamentCompanion.
pause
