@echo off
REM ============================================================
REM  defense-spend-monitor  -  one-click run (Windows)
REM  Public-source monitor for US defense spending & munitions.
REM
REM  NOTES
REM   * Do NOT pass --direct. It bypasses the working system proxy
REM     and makes every source fail, looking exactly like a
REM     "blocked by CDN" problem. The script auto-detects the
REM     proxy and prints the real exit each run.
REM   * Do NOT hardcode a proxy port. The port changes
REM     (observed 7897 -> 63299 within one day).
REM   * FIRST TIME ONLY: run `run.bat --backfill` once, to build the
REM     full monthly history (pre-war baseline -> today).
REM     Afterwards plain `run.bat` does the daily incremental update.
REM   * For scheduled tasks set DSM_NO_PAUSE=1, otherwise the final
REM     `pause` will hang the task forever.
REM ============================================================

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
echo === defense-spend-monitor ===
echo.
"%PY%" -c "import sys;print('Python', sys.version.split()[0])" 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.9+ or set PYTHON env var.
if not defined NOPAUSE pause
  exit /b 1
)
echo [1/4] offline self-test ...
"%PY%" spend_monitor.py --selftest
if errorlevel 1 (
  echo [ERROR] self-test failed. Aborting.
if not defined NOPAUSE pause
  exit /b 1
)
echo.
echo [2/4] collecting from public sources ...
"%PY%" spend_monitor.py %*
if errorlevel 1 (
  echo [ERROR] run failed. See messages above.
if not defined NOPAUSE pause
  exit /b 1
)
echo.
echo [3/4] verifying numbers back against the official APIs ...
echo        (data-quality gate: any mismatch returns a non-zero exit code)
"%PY%" tools\verify_numbers.py --skip-months
if errorlevel 1 (
  echo [WARN] number verification reported a mismatch. Review the output above.
  echo        The daily files were still written, but treat the numbers as suspect.
if not defined NOPAUSE pause
  exit /b 1
)
echo.
echo [4/4] done. Outputs:
echo   output\LATEST.md                  daily digest
echo   output\LATEST-trend.md            monthly trend  (spending / munitions / events)
echo   output\history\series.jsonl       machine-readable series
echo   output\_event_filter_audit.json   what the event gates dropped
echo   output\_title_gate_audit.json     what the title gate dropped
echo.
if not "%DSM_NO_PAUSE%"=="1" (
  if exist "output\LATEST.md" start "" notepad "output\LATEST.md"
if not defined NOPAUSE pause
)
exit /b 0
:maybe_pause
if not defined NOPAUSE pause
exit /b 0
