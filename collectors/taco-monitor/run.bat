@echo off
REM ============================================================
REM  taco-monitor  -  Windows one-click run (TACO Stress Index)
REM
REM  Pipeline: selftest (offline) -> fetch + compute -> done
REM  Any step failing stops the pipeline immediately.
REM
REM  NOTES
REM   * Egress is auto-tested: direct first, then system proxy.
REM     The http_proxy/https_proxy env vars on this machine point at a
REM     local port that is listening but has no upstream; the program
REM     ignores them explicitly. Never hardcode a proxy port.
REM   * To force a proxy for troubleshooting, override ONCE via RM_PROXY:
REM       set RM_PROXY=http://127.0.0.1:7897
REM     Do NOT put that into a scheduled task.
REM   * USSWIT1 uses a PUBLIC substitute (FRED T5YIE). The program does
REM     NOT read the real USSWIT1 column from the Dongwu workbook.
REM   * For scheduled tasks set RIM_NO_PAUSE=1, otherwise the trailing
REM     pause will hang the process forever.
REM   * After editing config or formula: run --selftest first, then a real
REM     run, and confirm the reconciliation result is PASS.
REM
REM  ENCODING: this file is intentionally ASCII-only. Chinese text in a
REM  .bat is decoded as GBK by cmd.exe while the file is UTF-8, and long
REM  CJK comment lines can shift the byte alignment enough to swallow the
REM  CRLF -- cmd then reports "The syntax of the command is incorrect" at
REM  a line that looks perfectly valid. Keep .bat ASCII-only; put Chinese
REM  documentation in README.md (UTF-8) instead.
REM ============================================================

chcp 65001 >nul
setlocal

cd /d "%~dp0"

set PY=%PYTHON%
if "%PY%"=="" set PY=python

echo.
echo === taco-monitor ===
echo.

%PY% -c "import sys;print('Python', sys.version.split()[0])" 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.9+ or set PYTHON env var.
  call :maybe_pause
  exit /b 1
)

REM --proxy is an explicit override for troubleshooting only (opt-in)
set PROXYARG=
if not "%RM_PROXY%"=="" set PROXYARG=--proxy %RM_PROXY%

echo [1/3] Offline selftest (no network, 70 checks) ...
%PY% taco_monitor.py --selftest
if errorlevel 1 (
  echo [ERROR] Selftest failed, aborted. Fix config / formula / parser first.
  call :maybe_pause
  exit /b 1
)

echo.
echo [2/3] Fetch public sources and compute the TACO index ...
if not "%PROXYARG%"=="" echo       (proxy egress EXPLICITLY overridden: %RM_PROXY% -- troubleshooting only)
%PY% taco_monitor.py %PROXYARG% %*
if errorlevel 1 (
  echo [ERROR] Run failed, see output above.
  call :maybe_pause
  exit /b 1
)

echo.
echo [3/3] Done. Artifacts:
echo   output\LATEST.md                  main report (8 sections)
echo   output\taco-dashboard.html        dashboard (self-contained, inline SVG)
echo   output\taco-latest.csv            latest daily table
echo   output\taco_index.xlsx            spreadsheet (4 sheets)
echo   output\YYYY-MM-DD\raw\            raw payloads archived (offline replay)
echo   output\history\                   cumulative history (an asset, never pruned)
echo   output\_source_status.json        per-source status / egress / effective UA
echo   output\_verify_last.json          reconciliation record (gate evidence)
echo.

if not "%RIM_NO_PAUSE%"=="1" (
  if exist "output\LATEST.md" start "" notepad "output\LATEST.md"
  pause
)
exit /b 0

:maybe_pause
if not "%RIM_NO_PAUSE%"=="1" pause
exit /b 0
