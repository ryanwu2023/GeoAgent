$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$statePath = Join-Path $PSScriptRoot 'data\server-process.json'
$entryPath = Join-Path $PSScriptRoot 'scripts\serve.py'
try {
    if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'No tracked server. No process was stopped.'; exit 0 }
    $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.processId)" -ErrorAction SilentlyContinue
    if (-not $process) { Remove-Item -LiteralPath $statePath; Write-Host 'Already stopped.'; exit 0 }
    if ($saved.entry -ne $entryPath -or -not $process.CommandLine.Contains($entryPath) -or $process.CreationDate.ToUniversalTime().ToString('o') -ne $saved.createdAt) {
        throw 'Process identity mismatch. Refusing to stop an unrelated process.'
    }
    Stop-Process -Id $saved.processId -ErrorAction Stop
    Wait-Process -Id $saved.processId -Timeout 10 -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $statePath
    Write-Host 'Project server stopped. Crawler jobs and stored data are unchanged.'
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
