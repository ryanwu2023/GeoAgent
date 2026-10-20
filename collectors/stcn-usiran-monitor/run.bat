@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
if defined RM_PY if exist "%RM_PY%" set "PY=%RM_PY%"
if not defined PY if defined PYTHON if exist "%PYTHON%" set "PY=%PYTHON%"
if not defined PY for %%c in (python.exe py.exe) do if not defined PY for /f "delims=" %%p in ('where %%c 2^>nul') do if not defined PY set "PY=%%p"
if not defined PY exit /b 9009
"%PY%" -u "%~dp0stcn_usiran_monitor.py" --selftest || exit /b 1
"%PY%" -u "%~dp0stcn_usiran_monitor.py" %* || exit /b 1
if not "%RM_NO_PAUSE%"=="1" pause
