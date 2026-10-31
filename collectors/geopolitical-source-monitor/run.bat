@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

set "PY_EXE=%~dp0..\..\.venv\Scripts\python.exe"
if exist "%PY_EXE%" goto python_ready

set "PY_EXE="
if defined RM_PY if /I "%RM_PY:~-4%"==".exe" if exist "%RM_PY%" set "PY_EXE=%RM_PY%"
if defined PY_EXE goto python_ready

for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PY_EXE set "PY_EXE=%%P"

:python_ready
if not defined PY_EXE (
  echo [ERROR] 未找到可用的 python.exe。
  exit /b 9009
)
if not exist "%PY_EXE%" (
  echo [ERROR] Python 不存在：%PY_EXE%
  exit /b 9009
)

if not exist "output" mkdir "output"
echo [多源地缘新闻监测] Python: %PY_EXE%

"%PY_EXE%" geopolitical_source_monitor.py --selftest
if errorlevel 1 exit /b 1

"%PY_EXE%" geopolitical_source_monitor.py %*
exit /b %ERRORLEVEL%
