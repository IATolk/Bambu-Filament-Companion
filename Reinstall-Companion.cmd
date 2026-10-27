@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Companion.ps1" -Reinstall
if errorlevel 1 (
  echo.
  echo Reinstall failed.
  pause
)
