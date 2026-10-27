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

REM ===============================================================
REM  meeting-calendar-monitor 俄乌/美伊关键人物会晤日程
REM
REM  用法：
REM     双击 run.bat              -> 自检 + 抓取 + 对账，跑完停在窗口
REM     run.bat --proxy direct    -> 强制直连（排障用）
REM     run.bat --proxy http://127.0.0.1:7897  -> 指定代理
REM     定时任务请设 RM_NO_PAUSE=1
REM     不想跑完自动弹报告：set RM_OPEN=0
REM
REM  退出码：0 成功 / 1 自检失败 / 2 抓取失败 / 3 对账失败
REM ===============================================================

if not exist "output" mkdir "output"
if not defined RM_OPEN set "RM_OPEN=1"

echo === [1/3] 自检（不联网，验证抽取与状态机口径）===
"%PY%" meeting_monitor.py --selftest
if errorlevel 1 (
    echo.
    echo [!] 自检未通过：代码口径有问题，抓出来的表不可信，已中止。
    echo     这一步不联网，所以不可能是代理或网络问题。
    if not defined NOPAUSE pause
    exit /b 1
)

echo.
echo === [2/3] 抓取（约 1-3 分钟，逐源打印进度）===
"%PY%" meeting_monitor.py %*
if errorlevel 1 (
    echo.
    echo [!] 抓取失败。按顺序排查：
    echo     1^) 出口不通：本机需走代理，国内直连 Yahoo/Google News 会被地域拦截
    echo        先试   run.bat --proxy http://127.0.0.1:7897
    echo        代理端口会漂移，不确定就用任务管理器看代理软件的本地端口
    echo     2^) 想看是不是网络问题：run.bat --proxy direct
    echo     3^) 已抓到的原始载荷在 output\ 下当天的 raw\ 目录，没被覆盖
    if not defined NOPAUSE pause
    exit /b 2
)

echo.
echo === [3/3] 对账（独立复算，不信渲染层的自述）===
"%PY%" tools\verify_report.py --save
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo === 完成 ===
    echo 报告: %CD%\output\LATEST.md
    echo 明细: %CD%\output\LATEST.csv
    echo 对账: %CD%\output\_verify_last.json
    REM 双击运行时自动打开报告；定时任务（NOPAUSE=1）不弹窗
    if "%RM_OPEN%"=="1" if not defined NOPAUSE start "" "output\LATEST.md"
) else (
    echo [!] 对账未全部通过，报告已生成但数字可能不自洽。
    echo     详情: output\_verify_last.json 的 failed 字段
)

if not defined NOPAUSE pause
exit /b %RC%
