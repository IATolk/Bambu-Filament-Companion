param([switch]$Reinstall)

$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$install = Join-Path $env:LOCALAPPDATA 'BambuFilamentCompanion'
$data = Join-Path $install 'data'
$taskName = 'Bambu Filament Companion'

New-Item -ItemType Directory -Force -Path $install,$data | Out-Null

cmd.exe /d /c ('schtasks.exe /End /TN "' + $taskName + '" >nul 2>&1') | Out-Null
Start-Sleep -Milliseconds 500

if (-not (Test-Path (Join-Path $source 'BambuNetworkBridge.exe'))) { throw 'Bridge missing. Run Build-Bridge.cmd first.' }

Copy-Item (Join-Path $source 'BambuNetworkBridge.exe') (Join-Path $install 'BambuNetworkBridge.exe') -Force
Copy-Item (Join-Path $source 'companion.py') (Join-Path $install 'companion.py') -Force
Copy-Item (Join-Path $source 'Refresh-StudioFilament.ps1') (Join-Path $install 'Refresh-StudioFilament.ps1') -Force
Copy-Item (Join-Path $source 'Select-EXT-Spool.ps1') (Join-Path $install 'Select-EXT-Spool.ps1') -Force
Copy-Item (Join-Path $source 'Select-EXT-Spool.cmd') (Join-Path $install 'Select-EXT-Spool.cmd') -Force

$python = (Get-Command python.exe -ErrorAction Stop).Source
$pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source

$ledger = Join-Path $data 'ledger.json'
if (-not (Test-Path $ledger)) {
    & $python (Join-Path $install 'companion.py') --bootstrap
    if ($LASTEXITCODE -ne 0) {
        throw "Initial companion bootstrap failed with code $LASTEXITCODE"
    }
}

$taskCmd = '"' + $pythonw + '" "' + (Join-Path $install 'companion.py') + '"'
schtasks.exe /Create /TN $taskName /SC MINUTE /MO 1 /TR $taskCmd /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to create scheduled task' }

schtasks.exe /Run /TN $taskName | Out-Null

# Ensure the one-minute monitor also runs on battery power.
try {
    $t = Get-ScheduledTask -TaskName $taskName
    $settings = $t.Settings
    $settings.DisallowStartIfOnBatteries = $false
    $settings.StopIfGoingOnBatteries = $false
    Set-ScheduledTask -TaskName $taskName -Settings $settings | Out-Null
} catch {
    Write-Warning "Could not adjust battery settings: $($_.Exception.Message)"
}

Copy-Item $MyInvocation.MyCommand.Path (Join-Path $install 'Install-Companion.ps1') -Force

$repairCmd = Join-Path $source 'Reinstall-Companion.cmd'
$desktop = [Environment]::GetFolderPath('Desktop')
$ws = New-Object -ComObject WScript.Shell

$shortcut = Join-Path $desktop 'Reinstall Bambu Filament Companion.lnk'
$sc = $ws.CreateShortcut($shortcut)
$sc.TargetPath = 'C:\Windows\System32\cmd.exe'
$sc.Arguments = '/c "' + $repairCmd + '"'
$sc.WorkingDirectory = $source
$sc.Description = 'Repair or reinstall Bambu Filament Companion'
$sc.Save()

$logShortcut = Join-Path $desktop 'Bambu Filament Companion Logs.lnk'
$sc2 = $ws.CreateShortcut($logShortcut)
$sc2.TargetPath = 'C:\Windows\explorer.exe'
$sc2.Arguments = '"' + $install + '"'
$sc2.WorkingDirectory = $install
$sc2.Description = 'Open Bambu Filament Companion logs and status'
$sc2.Save()

Write-Host "Installed to: $install"
Write-Host "Scheduled task: $taskName"
Write-Host "Ledger preserved: $ledger"
