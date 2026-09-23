@echo off
REM ============================================================
REM  install_task.bat  -  register / remove the daily Windows task
REM
REM  Usage (double-click here, or run in cmd):
REM      install_task.bat               register daily task (default 09:10)
REM      install_task.bat 08:30         specify a time
REM      install_task.bat remove        uninstall the task
REM      install_task.bat status        show task status
REM
REM  Task name: taco-monitor-daily
REM
REM  Why schtasks instead of the Startup folder:
REM    * supports a fixed time, queryable status, silent run, and a
REM      visible last-result code
REM    * the Startup folder only fires at logon and leaves no trace of
REM      whether it actually ran
REM
REM  NOTES
REM    * The task MUST run with RIM_NO_PAUSE=1, otherwise run.bat's
REM      trailing pause hangs the process and the next trigger is skipped.
REM      run_silent.bat sets that for you.
REM    * Default is "run only when the user is logged on" (/IT), because
REM      the egress path depends on user-session state. Switching to
REM      /RU SYSTEM is not recommended here.
REM
REM  ENCODING: ASCII-only on purpose. See run.bat for the reason.
REM ============================================================

chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

set TASKNAME=taco-monitor-daily
set RUNDIR=%~dp0
REM strip trailing backslash
if "%RUNDIR:~-1%"=="\" set RUNDIR=%RUNDIR:~0,-1%
set RUNBAT=%RUNDIR%\run.bat
set SILENTBAT=%RUNDIR%\run_silent.bat

if /i "%~1"=="remove" goto :remove
if /i "%~1"=="uninstall" goto :remove
if /i "%~1"=="status" goto :status
if /i "%~1"=="list" goto :status

REM ---- time argument ----
set ATIME=09:10
if not "%~1"=="" set ATIME=%~1

echo.
echo === register daily scheduled task ===
echo   task name : %TASKNAME%
echo   run time  : daily %ATIME%
echo   entry     : %SILENTBAT%
echo.

if not exist "%RUNBAT%" (
  echo [ERROR] run.bat not found: %RUNBAT%
  pause
  exit /b 1
)
if not exist "%SILENTBAT%" (
  echo [ERROR] run_silent.bat not found: %SILENTBAT%
  pause
  exit /b 1
)

REM delete any existing task first (avoids duplicate-registration errors)
schtasks /Query /TN "%TASKNAME%" >nul 2>&1
if not errorlevel 1 (
  echo   task already exists, deleting first ...
  schtasks /Delete /TN "%TASKNAME%" /F >nul 2>&1
)

REM /TR points straight at run_silent.bat -- it sets RIM_NO_PAUSE=1,
REM locates Python, and writes the log. Keeping /TR to a single
REM space-free path sidesteps schtasks quote-escaping entirely.
schtasks /Create /TN "%TASKNAME%" /TR "%SILENTBAT%" /SC DAILY /ST %ATIME% /IT /F
if errorlevel 1 (
  echo.
  echo [ERROR] registration failed. Common causes:
  echo    * insufficient privileges -- try "Run as administrator"
  echo    * bad time format -- use HH:MM, e.g. 09:10
  pause
  exit /b 1
)

echo.
echo [DONE] task registered. Manage it with:
echo   schtasks /Query /TN "%TASKNAME%" /V /FO LIST    show details
echo   schtasks /Run   /TN "%TASKNAME%"                run once now
echo   schtasks /Delete /TN "%TASKNAME%" /F            uninstall
echo   or: install_task.bat status / remove
echo.
echo Tip: run it once now to confirm the environment (it runs silently;
echo      check whether output\LATEST.md was refreshed).
echo.
pause
exit /b 0

:remove
echo.
schtasks /Delete /TN "%TASKNAME%" /F
if errorlevel 1 (
  echo [INFO] delete failed, or the task does not exist.
) else (
  echo [DONE] task removed.
)
echo.
pause
exit /b 0

:status
echo.
schtasks /Query /TN "%TASKNAME%" /V /FO LIST 2>nul
if errorlevel 1 (
  echo [INFO] task "%TASKNAME%" is not registered yet.
  echo        Run install_task.bat to register it.
)
echo.
pause
exit /b 0
