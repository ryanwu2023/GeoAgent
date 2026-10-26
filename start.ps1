param([switch]$NoBrowser, [switch]$Build, [int]$Port = 8000, [string]$BindHost = '127.0.0.1', [switch]$NoCrawlers, [switch]$NoInternalMessages)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$statePath = Join-Path $PSScriptRoot 'data\server-process.json'
$entryPath = Join-Path $PSScriptRoot 'scripts\serve.py'
$url = "http://$BindHost`:$Port/"
$crawlerStatePath = Join-Path $PSScriptRoot 'data\crawler-manager-process.json'
$crawlerEntryPath = Join-Path $PSScriptRoot 'scripts\crawler_manager.py'
$internalMessageStatePath = Join-Path $PSScriptRoot 'data\ht-macro-service-process.json'
$internalMessageEntryPath = Join-Path $PSScriptRoot 'scripts\ht_macro_service.py'

function Read-JsonUtf8([string]$Path) {
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    return $text | ConvertFrom-Json
}

function Write-JsonUtf8Bom([object]$Value, [string]$Path) {
    $json = $Value | ConvertTo-Json
    $utf8Bom = New-Object System.Text.UTF8Encoding($true)
    [System.IO.File]::WriteAllText($Path, $json, $utf8Bom)
}

function Start-CrawlerManager([string]$PythonPath) {
    if ($NoCrawlers) { return }
    if (Test-Path -LiteralPath $crawlerStatePath) {
        try {
            $crawlerSaved = Read-JsonUtf8 $crawlerStatePath
            $crawlerExisting = Get-CimInstance Win32_Process -Filter "ProcessId=$($crawlerSaved.processId)" -ErrorAction SilentlyContinue
            if ($crawlerExisting -and $crawlerSaved.entry -eq $crawlerEntryPath -and $crawlerExisting.CommandLine.Contains($crawlerEntryPath) -and $crawlerExisting.CreationDate.ToUniversalTime() -eq ([datetime]$crawlerSaved.createdAt).ToUniversalTime()) {
                Write-Host "Crawler manager is already running: PID $($crawlerSaved.processId)"
                return
            }
        } catch {}
        Remove-Item -LiteralPath $crawlerStatePath -ErrorAction SilentlyContinue
    }
    $crawlerProcess = Start-Process -FilePath $PythonPath -ArgumentList @('-u', ('"' + $crawlerEntryPath + '"'), '--import-url', ($url+'api/import'), '--interval-sec', '86400') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'crawler-manager.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'crawler-manager.stderr.log') -PassThru
    $crawlerIdentity = Get-CimInstance Win32_Process -Filter "ProcessId=$($crawlerProcess.Id)"
    Write-JsonUtf8Bom @{processId=$crawlerProcess.Id;entry=$crawlerEntryPath;createdAt=$crawlerIdentity.CreationDate.ToUniversalTime().ToString('o');startedAt=(Get-Date).ToUniversalTime().ToString('o')} $crawlerStatePath
    Write-Host "Crawler manager started in background: PID $($crawlerProcess.Id)"
}

function Start-InternalMessageService([string]$PythonPath) {
    if ($NoInternalMessages) { return }
    if (Test-Path -LiteralPath $internalMessageStatePath) {
        try {
            $saved = Read-JsonUtf8 $internalMessageStatePath
            $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.processId)" -ErrorAction SilentlyContinue
            if ($existing -and $saved.entry -eq $internalMessageEntryPath -and $existing.CommandLine.Contains($internalMessageEntryPath) -and $existing.CreationDate.ToUniversalTime() -eq ([datetime]$saved.createdAt).ToUniversalTime()) {
                Write-Host "Internal message service is already running: PID $($saved.processId)"
                return
            }
        } catch {}
        Remove-Item -LiteralPath $internalMessageStatePath -ErrorAction SilentlyContinue
    }
    $serviceProcess = Start-Process -FilePath $PythonPath -ArgumentList @('-u', ('"' + $internalMessageEntryPath + '"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'ht-macro-service.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'ht-macro-service.stderr.log') -PassThru
    Start-Sleep -Milliseconds 250
    $serviceProcess.Refresh()
    if ($serviceProcess.HasExited) { throw 'Internal message service exited. See ht-macro-service.stderr.log.' }
    $identity = Get-CimInstance Win32_Process -Filter "ProcessId=$($serviceProcess.Id)"
    Write-JsonUtf8Bom @{processId=$serviceProcess.Id;entry=$internalMessageEntryPath;createdAt=$identity.CreationDate.ToUniversalTime().ToString('o');startedAt=(Get-Date).ToUniversalTime().ToString('o')} $internalMessageStatePath
    Write-Host "Internal message service started in background: PID $($serviceProcess.Id)"
}

try {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        if (Test-Path -LiteralPath $statePath) {
            $saved = Read-JsonUtf8 $statePath
            $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.processId)" -ErrorAction SilentlyContinue
            if ($existing -and $existing.CommandLine.Contains($entryPath) -and $existing.CreationDate.ToUniversalTime() -eq ([datetime]$saved.createdAt).ToUniversalTime() -and $saved.url -eq $url) {
                try {
                    # /api/status scans snapshot and translation state and can
                    # legitimately take longer on a large corpus.  The small
                    # health endpoint is the correct process-readiness probe.
                    $health = Invoke-RestMethod -Uri ($url+'healthz') -TimeoutSec 3
                    if ($health.status -eq 'ok') {
                        Write-Host "Project is already running: $url"
                        $existingPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
                        if (Test-Path -LiteralPath $existingPython) { Start-CrawlerManager $existingPython }
                        if (Test-Path -LiteralPath $existingPython) { Start-InternalMessageService $existingPython }
                        if (-not $NoBrowser) { Start-Process $url }
                        exit 0
                    }
                } catch {}
            }
        }
        throw "Port $Port is occupied by an untracked process. No process was stopped."
    }
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $bootstrap = Join-Path $PSScriptRoot 'scripts\bootstrap_env.py'
        $systemPython = (Get-Command python -ErrorAction Stop).Source
        & $systemPython $bootstrap
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) { throw 'Project .venv creation failed.' }
    }
    $python = $venvPython
    & $python -c 'import fastapi, uvicorn, httpx'
    if ($LASTEXITCODE -ne 0) { throw 'Dependencies missing. Run: python -m pip install -r requirements.txt' }
    if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
    if ($Build -or -not (Test-Path -LiteralPath 'dist\index.html')) {
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'data') -Force | Out-Null
    $process = Start-Process -FilePath $python -ArgumentList @('-u', ('"' + $entryPath + '"'), '--host', $BindHost, '--port', "$Port") -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'server.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'server.stderr.log') -PassThru
    $identity = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)"
    Write-JsonUtf8Bom @{processId=$process.Id;createdAt=$identity.CreationDate.ToUniversalTime().ToString('o');entry=$entryPath;url=$url} $statePath
    $ready = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        $process.Refresh()
        if ($process.HasExited) { throw 'Server exited. See server.stderr.log.' }
        try {
            $health = Invoke-RestMethod -Uri ($url+'healthz') -TimeoutSec 3
            if ($health.status -eq 'ok') { $ready=$true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'Server is starting slowly. See logs; stop.ps1 can stop the tracked process.' }
    Write-Host "Project started: $url"
    Start-CrawlerManager $python
    Start-InternalMessageService $python
    Write-Host 'Stop: stop.bat / stop.ps1. Logs: server.stdout.log / server.stderr.log'
    if (-not $NoBrowser) { Start-Process $url }
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
