@echo off
REM ============================================================
REM  run_silent.bat  -  scheduled-task entry (silent, no pause)
REM
REM  Differences from run.bat:
REM    1. forces RIM_NO_PAUSE=1 (otherwise the trailing pause hangs
REM       the scheduled task forever, and the next trigger is skipped)
REM    2. auto-locates Python (a scheduled task's PATH may lack python)
REM    3. appends this run's output to logs\run.log for after-the-fact
REM       accountability
REM
REM  The scheduled task's /TR points directly at THIS file, so the
REM  schtasks command line never needs nested escaped quotes -- that is
REM  one of the most common Windows scheduled-task pitfalls.
REM
REM  ENCODING: ASCII-only on purpose. Chinese text in a .bat is decoded
REM  as GBK by cmd.exe while the file is UTF-8; long CJK comment lines
REM  can shift byte alignment and swallow the CRLF, producing
REM  "The syntax of the command is incorrect" at a valid-looking line.
REM  Chinese documentation lives in README.md instead.
REM ============================================================

chcp 65001 >nul
setlocal

cd /d "%~dp0"

REM ---- 1. no pause ----
set RIM_NO_PAUSE=1

REM ---- 2. locate Python ----
REM priority: PYTHON already set > python on PATH
if not "%PYTHON%"=="" goto :have_py
set "PYTHON=python"

:have_py
REM ---- 3. log ----
if not exist "logs" mkdir "logs"
set "LOGFILE=logs\run.log"

echo. >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"
echo [run_silent] start  python=%PYTHON% >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"

call "%~dp0run.bat" >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"

echo [run_silent] exit=%RC% >> "%LOGFILE%"
exit /b %RC%
