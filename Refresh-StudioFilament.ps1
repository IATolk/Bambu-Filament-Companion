$ErrorActionPreference = 'Stop'

$p = Get-Process bambu-studio -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) {
    Write-Output 'STUDIO_NOT_RUNNING'
    exit 2
}

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
if (-not $root) {
    Write-Output 'STUDIO_WINDOW_NOT_FOUND'
    exit 3
}

$buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button
)
$buttons = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)

$target = $null
for ($i = 0; $i -lt $buttons.Count; $i++) {
    $b = $buttons.Item($i)
    try { $name = $b.Current.Name } catch { continue }

    # CloudBadge aria-label/title. English is currently used even with the
    # Russian Studio locale; the extra patterns make this tolerant of later
    # translations.
    if ($name -like 'Last synced:*' -or
        $name -eq 'Sync filament info' -or
        $name -like 'Последн*синхрон*' -or
        $name -like 'Синхрониз*филамент*' -or
        $name -like 'Синхрониз*пластик*') {
        $target = $b
        break
    }
}

if (-not $target) {
    Write-Output 'FILAMENT_CLOUD_REFRESH_NOT_VISIBLE'
    exit 4
}

$invoke = $null
if (-not $target.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$invoke)) {
    Write-Output 'FILAMENT_CLOUD_REFRESH_NOT_INVOKABLE'
    exit 5
}

$before = $target.Current.Name
$invoke.Invoke()
Write-Output ('REFRESHED ' + $before)
exit 0
