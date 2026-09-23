@echo off

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

echo ========================================
echo diplomacy-monitor
echo ========================================
if not exist "%~dp0diplomacy_monitor.py" (
    echo [ERROR] diplomacy_monitor.py not found.
if not defined NOPAUSE pause
    exit /b 1
)
if not exist "%~dp0tools\verify_report.py" (
    echo [ERROR] tools\verify_report.py not found.
if not defined NOPAUSE pause
    exit /b 1
)
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
if not defined NOPAUSE pause
    exit /b 1
)
echo.
echo [1/3] Running self-test...
"%PY%" "%~dp0diplomacy_monitor.py" --selftest
if errorlevel 1 (
    echo [ERROR] Self-test failed.
if not defined NOPAUSE pause
    exit /b 1
)
echo.
echo [2/3] Running diplomacy monitor...
"%PY%" "%~dp0diplomacy_monitor.py"
if errorlevel 1 (
    echo [ERROR] diplomacy_monitor.py failed.
if not defined NOPAUSE pause
    exit /b 1
)
echo.
echo [3/3] Verifying report...
"%PY%" "%~dp0tools\verify_report.py"
if errorlevel 1 (
    echo [ERROR] verify_report.py failed.
if not defined NOPAUSE pause
    exit /b 1
)
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
echo DONE
echo Output: output\LATEST.md
echo ========================================
if not defined NOPAUSE pause
exit /b %RC%
