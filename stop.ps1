$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$statePath = Join-Path $PSScriptRoot 'data\server-process.json'
$entryPath = Join-Path $PSScriptRoot 'scripts\serve.py'
$crawlerStatePath = Join-Path $PSScriptRoot 'data\crawler-manager-process.json'
$crawlerEntryPath = Join-Path $PSScriptRoot 'scripts\crawler_manager.py'
$internalMessageStatePath = Join-Path $PSScriptRoot 'data\ht-macro-service-process.json'
$internalMessageEntryPath = Join-Path $PSScriptRoot 'scripts\ht_macro_service.py'

function Read-JsonUtf8([string]$Path) {
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    return $text | ConvertFrom-Json
}

try {
    if (Test-Path -LiteralPath $internalMessageStatePath) {
        $serviceSaved = Read-JsonUtf8 $internalMessageStatePath
        $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($serviceSaved.processId)" -ErrorAction SilentlyContinue
        if ($serviceProcess) {
            if ($serviceSaved.entry -ne $internalMessageEntryPath -or -not $serviceProcess.CommandLine.Contains($internalMessageEntryPath) -or $serviceProcess.CreationDate.ToUniversalTime() -ne ([datetime]$serviceSaved.createdAt).ToUniversalTime()) {
                throw 'Internal message process identity mismatch. Refusing to stop an unrelated process.'
            }
            & taskkill.exe /PID $serviceSaved.processId /T /F | Out-Null
        }
        Remove-Item -LiteralPath $internalMessageStatePath -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $crawlerStatePath) {
        $crawlerSaved = Read-JsonUtf8 $crawlerStatePath
        $crawlerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($crawlerSaved.processId)" -ErrorAction SilentlyContinue
        if ($crawlerProcess) {
            if ($crawlerSaved.entry -ne $crawlerEntryPath -or -not $crawlerProcess.CommandLine.Contains($crawlerEntryPath) -or $crawlerProcess.CreationDate.ToUniversalTime() -ne ([datetime]$crawlerSaved.createdAt).ToUniversalTime()) {
                throw 'Crawler process identity mismatch. Refusing to stop an unrelated process.'
            }
            & taskkill.exe /PID $crawlerSaved.processId /T /F | Out-Null
        }
        Remove-Item -LiteralPath $crawlerStatePath -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'No tracked server. No process was stopped.'; exit 0 }
    $saved = Read-JsonUtf8 $statePath
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.processId)" -ErrorAction SilentlyContinue
    if (-not $process) { Remove-Item -LiteralPath $statePath; Write-Host 'Already stopped.'; exit 0 }
    if ($saved.entry -ne $entryPath -or -not $process.CommandLine.Contains($entryPath) -or $process.CreationDate.ToUniversalTime() -ne ([datetime]$saved.createdAt).ToUniversalTime()) {
        throw 'Process identity mismatch. Refusing to stop an unrelated process.'
    }
    Stop-Process -Id $saved.processId -ErrorAction Stop
    Wait-Process -Id $saved.processId -Timeout 10 -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $statePath
    Write-Host 'Project server, crawler manager and internal message service stopped. Stored data is unchanged.'
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
