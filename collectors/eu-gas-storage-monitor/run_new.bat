@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python.exe"
"%PY%" -u "%~dp0eu_gas_storage_monitor.py"
exit /b %ERRORLEVEL%
