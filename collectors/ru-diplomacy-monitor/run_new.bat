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

echo.
echo ============================================================
echo ru-diplomacy-monitor
echo ============================================================
echo.
REM ------------------------------------------------------------
REM Check Python
REM ------------------------------------------------------------
if not exist "%PY%" (
    echo [ERROR] Python executable not found:
    echo %PY%
    goto :err
)
"%PY%" -c "import sys; print('Python', sys.version.split()[0])"
if errorlevel 1 (
    echo [ERROR] Python failed to start.
    goto :err
)
REM ------------------------------------------------------------
REM 1. Self test
REM ------------------------------------------------------------
echo.
echo [1/4] Running offline self-test...
"%PY%" -u "%~dp0ru_diplomacy_monitor.py" --selftest
if errorlevel 1 goto :err
REM ------------------------------------------------------------
REM 2. Fetch and generate report
REM ------------------------------------------------------------
echo.
echo [2/4] Fetching sources and generating report...
"%PY%" -u "%~dp0ru_diplomacy_monitor.py"
if errorlevel 1 goto :err
REM ------------------------------------------------------------
REM 3. Verify report
REM ------------------------------------------------------------
echo.
echo [3/4] Verifying report...
"%PY%" -u "%~dp0tools\verify_report.py"
if errorlevel 1 goto :err
REM ------------------------------------------------------------
REM 4. Audit content
REM ------------------------------------------------------------
echo.
echo [4/4] Auditing report content...
"%PY%" -u "%~dp0tools\audit_content.py"
if errorlevel 1 goto :err
REM ------------------------------------------------------------
REM Done
REM ------------------------------------------------------------
echo.
echo ============================================================
echo ALL DONE
echo Report: output\LATEST.md
echo ============================================================
echo.
if not "%RIM_NO_PAUSE%"=="1" (
    if exist "%~dp0output\LATEST.md" (
        start "" notepad "%~dp0output\LATEST.md"
    )
if not defined NOPAUSE pause
)
exit /b 0
:err
echo.
echo ============================================================
echo FAILED
echo Check the error message above before using the report.
echo ============================================================
echo.
if not defined NOPAUSE pause
exit /b 1
