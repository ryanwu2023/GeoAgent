$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$statePath = Join-Path $PSScriptRoot 'data\server-process.json'
$entryPath = Join-Path $PSScriptRoot 'scripts\serve.py'
$crawlerStatePath = Join-Path $PSScriptRoot 'data\crawler-manager-process.json'
$crawlerEntryPath = Join-Path $PSScriptRoot 'scripts\crawler_manager.py'
try {
    if (Test-Path -LiteralPath $crawlerStatePath) {
        $crawlerSaved = Get-Content -LiteralPath $crawlerStatePath -Raw | ConvertFrom-Json
        $crawlerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($crawlerSaved.processId)" -ErrorAction SilentlyContinue
        if ($crawlerProcess) {
            if ($crawlerSaved.entry -ne $crawlerEntryPath -or -not $crawlerProcess.CommandLine.Contains($crawlerEntryPath)) {
                throw 'Crawler process identity mismatch. Refusing to stop an unrelated process.'
            }
            & taskkill.exe /PID $crawlerSaved.processId /T /F | Out-Null
        }
        Remove-Item -LiteralPath $crawlerStatePath -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'No tracked server. No process was stopped.'; exit 0 }
    $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.processId)" -ErrorAction SilentlyContinue
    if (-not $process) { Remove-Item -LiteralPath $statePath; Write-Host 'Already stopped.'; exit 0 }
    if ($saved.entry -ne $entryPath -or -not $process.CommandLine.Contains($entryPath)) {
        throw 'Process identity mismatch. Refusing to stop an unrelated process.'
    }
    Stop-Process -Id $saved.processId -ErrorAction Stop
    Wait-Process -Id $saved.processId -Timeout 10 -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $statePath
    Write-Host 'Project server and crawler manager stopped. Stored data is unchanged.'
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
