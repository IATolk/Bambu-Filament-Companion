$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridge = Join-Path $base 'BambuNetworkBridge.exe'
$python = (Get-Command python.exe -ErrorAction Stop).Source
$companion = Join-Path $base 'companion.py'
$tmp = Join-Path $env:TEMP ('BambuExtSpools_'+[guid]::NewGuid().ToString('N')+'.json')
try {
    & $bridge spools $tmp
    if ($LASTEXITCODE -ne 0) {throw "Cannot read Filament Manager cloud inventory (rc=$LASTEXITCODE)"}
    $response = Get-Content $tmp -Raw -Encoding UTF8 | ConvertFrom-Json
    $spools = @($response.hits | Where-Object {[int]$_.netWeight -gt 0} |
        Sort-Object filamentType,color,note)
    if ($spools.Count -eq 0) { throw 'There are no nonempty spools in cloud inventory.' }

    $ledgerFile = Join-Path $base 'data\ledger.json'
    if (Test-Path $ledgerFile) {
        $ledger = Get-Content $ledgerFile -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host 'Current EXT selection (by device):'
        if ($ledger.extSelection) {
            $ledger.extSelection.PSObject.Properties | ForEach-Object {
                Write-Host ($_.Name + ' -> spool ID ' + $_.Value.spoolId)
            }
        } else { Write-Host 'None' }
    }

    Write-Host ''
    Write-Host 'Select the physical spool currently installed in EXT.'
    Write-Host 'Re-select whenever you replace the physical EXT spool.'
    for ($i = 0; $i -lt $spools.Count; $i++) {
        $s=$spools[$i]
        Write-Host ("{0,2}. ID {1} | {2} | {3} | {4}g | {5}" -f
            ($i+1),$s.id,$s.filamentType,$s.color,$s.netWeight,$s.note)
    }
    $responseIndex = Read-Host 'Spool number (blank = cancel)'
    if (!$responseIndex) {return}
    $index = 0
    if (-not [int]::TryParse($responseIndex,[ref]$index) -or $index -lt 1 -or $index -gt $spools.Count) {
        throw 'Invalid spool number.'
    }
    $selected = $spools[$index-1]
    & $python $companion --select-ext ([string]$selected.id)
    if ($LASTEXITCODE -ne 0) { throw 'Failed to save EXT selection' }
    Write-Host ('Saved spool ID '+$selected.id+'. Future EXT jobs will use this ID.')
} finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}
