@echo off
rem russia-domestic-monitor 日常运行一条龙：自检 -> 抓取+渲染 -> 对账 -> 审计

REM ===RM-LAUNCHER-PROLOGUE v1 BEGIN=== managed by tools/fix_run_bats.py - do not hand-edit
setlocal
chcp 65001 >nul
cd /d "%~dp0"

REM ---------------------------------------------------------------
REM  Resolve a Python interpreter. Deliberately NOT hardcoded:
REM  a pinned path breaks the day the managed runtime is upgraded,
REM  and it looks like the crawler itself is broken.
REM  Order: RM_PY  ->  PYTHON  ->  managed runtimes (newest dir)  ->  PATH.
REM  Set RM_PY to override, e.g.  set RM_PY=D:\py\python.exe
REM ---------------------------------------------------------------
set "PY="
if defined RM_PY if exist "%RM_PY%" set "PY=%RM_PY%"
if not defined PY if defined PYTHON if exist "%PYTHON%" set "PY=%PYTHON%"
if not defined PY for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if not defined PY if exist "%%~fd\python.exe" set "PY=%%~fd\python.exe"
if not defined PY for %%c in (python.exe py.exe) do if not defined PY for /f "delims=" %%p in ('where %%c 2^>nul') do if not defined PY set "PY=%%p"
if defined PY "%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 set "PY="
if not defined PY (
  echo [ERROR] No usable Python interpreter found.
  echo         Set RM_PY to a python.exe path and retry, e.g.
  echo             set RM_PY=C:\path\to\python.exe
  pause
  exit /b 9009
)

REM  Pause control: set RM_NO_PAUSE=1 for scheduled tasks (legacy names kept).
set "NOPAUSE="
if "%RM_NO_PAUSE%"=="1" set "NOPAUSE=1"
if "%RIM_NO_PAUSE%"=="1" set "NOPAUSE=1"
if "%DSM_NO_PAUSE%"=="1" set "NOPAUSE=1"

echo.
echo === %~n0 : %CD% ===
echo Python: %PY%
echo.
REM ===RM-LAUNCHER-PROLOGUE v1 END===

echo ===== [1/4] 离线自检 =====
"%PY%" russia_domestic_monitor.py --selftest
if errorlevel 1 (
    echo 自检失败，终止。先排查词表/解析器再抓取。
if not defined NOPAUSE pause
    exit /b 1
)
echo ===== [2/4] 抓取 + 渲染 =====
"%PY%" russia_domestic_monitor.py
echo ===== [3/4] 独立对账 =====
"%PY%" tools\verify_report.py
if errorlevel 1 (
    echo 对账发现不一致，检查上方输出。
if not defined NOPAUSE pause
    exit /b 1
)
echo ===== [4/4] 确定性重放审计 =====
"%PY%" tools\audit_content.py
set "RC=%ERRORLEVEL%"
echo ===== 完成：output\LATEST.md =====
if not defined NOPAUSE pause
exit /b %RC%
