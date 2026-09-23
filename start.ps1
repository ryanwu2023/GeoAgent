param([switch]$NoBrowser, [switch]$Build)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$statePath = Join-Path $PSScriptRoot 'data\server-process.json'
$entryPath = Join-Path $PSScriptRoot 'scripts\serve.py'
$url = 'http://127.0.0.1:8000/'
try {
    $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        if (Test-Path -LiteralPath $statePath) {
            $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.processId)" -ErrorAction SilentlyContinue
            if ($existing -and $existing.CommandLine.Contains($entryPath) -and $existing.CreationDate.ToUniversalTime().ToString('o') -eq $saved.createdAt -and $listener.OwningProcess -contains $saved.processId) {
                Write-Host "Project is already running: $url"
                if (-not $NoBrowser) { Start-Process $url }
                exit 0
            }
        }
        throw 'Port 8000 is occupied by an untracked process. No process was stopped.'
    }
    $python = (Get-Command python -ErrorAction Stop).Source
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
    $process = Start-Process -FilePath $python -ArgumentList @('-u', ('"' + $entryPath + '"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'server.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'server.stderr.log') -PassThru
    $identity = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)"
    @{processId=$process.Id;createdAt=$identity.CreationDate.ToUniversalTime().ToString('o');entry=$entryPath;url=$url} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    $ready = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        $process.Refresh()
        if ($process.HasExited) { throw 'Server exited. See server.stderr.log.' }
        try {
            $status = Invoke-RestMethod -Uri ($url+'api/status') -TimeoutSec 3
            if ($status.rule_version) { $ready=$true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'Server is starting slowly. See logs; stop.ps1 can stop the tracked process.' }
    Write-Host "Project started: $url"
    Write-Host 'Stop: stop.bat / stop.ps1. Logs: server.stdout.log / server.stderr.log'
    if (-not $NoBrowser) { Start-Process $url }
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
