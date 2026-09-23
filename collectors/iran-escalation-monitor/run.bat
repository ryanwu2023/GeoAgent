@echo off
REM ============================================================
REM  iran-escalation-monitor  -  Windows 一键运行
REM  伊朗战备与升级动态监测（六大关注域 x 当事方对照）
REM
REM  注意
REM   * **不要**传 --direct 之类的绕过代理开关。程序会自动探测系统
REM     代理并打印实际出口；写成直连会让所有源全挂，而症状与
REM     「被 CDN 风控拦截」一模一样，极难排查。
REM   * **不要**把代理端口写死（实测同一天内会从 7897 漂到 51681/63299）。
REM     系统探测到的端口也可能已经失效（表现为 Tunnel 502），
REM     这时用 RM_PROXY 临时覆盖**一次**即可，不要写进计划任务。
REM     例：  set RM_PROXY=http://127.0.0.1:7897
REM   * 改了词表或解析逻辑后，先跑 --recompute 把累积总档全量重算，
REM     否则档案里会混着两套口径（输出看起来完全正常）。
REM   * 计划任务里请设 RIM_NO_PAUSE=1，否则最后一句 pause 会永久挂住。
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
echo === iran-escalation-monitor ===
echo.
"%PY%" -c "import sys;print('Python', sys.version.split()[0])" 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python。请安装 Python 3.9+ 或设置 PYTHON 环境变量。
if not defined NOPAUSE pause
  exit /b 1
)
REM --proxy 是**排障用的显式覆盖**，只在设置了 RM_PROXY 时才传（opt-in）
set PROXYARG=
if not "%RM_PROXY%"=="" set PROXYARG=--proxy %RM_PROXY%
echo [1/4] 离线自检（不联网，520+ 项）...
"%PY%" iran_monitor.py --selftest
if errorlevel 1 (
  echo [错误] 自检未通过，已中止。请先修复词表 / 解析问题。
if not defined NOPAUSE pause
  exit /b 1
)
echo.
echo [2/4] 抓取公开源并生成报告 ...
if not "%PROXYARG%"=="" echo       （代理出口**显式覆盖**：%RM_PROXY% —— 仅供排障）
"%PY%" iran_monitor.py %PROXYARG% %*
if errorlevel 1 (
  echo [错误] 运行失败，详见上方信息。
if not defined NOPAUSE pause
  exit /b 1
)
echo.
echo [3/4] 回源对账：把报告里的每个数字从落盘数据独立复算 ...
"%PY%" tools\verify_report.py --save
if errorlevel 1 (
  echo.
  echo [错误] 对账不一致 —— 报告里有数字无法由落盘数据复算。
  echo         程序日志全绿不代表数字是对的。请先修，不要直接发布报告。
if not defined NOPAUSE pause
  exit /b 1
)
echo.
echo [4/4] 完成。产出：
echo   output\LATEST.md                 主报告（11 节 + 附录）
echo   output\LATEST-crosscheck.md      交叉验证明细（含判定依据）
echo   output\_source_status.json       每源状态、解析/产出条数、实际出口代理、TLS 降级清单
echo   output\_gate_audit.json          被折叠记录的逐条留档 + 全量分类计数
echo   output\_verify_last.json         最近一次回源对账结果（门禁留痕）
echo   output\ALL-records.jsonl         累积总档（永不淘汰）
echo.
echo   报告结构：0 阅读须知 / 1 一页速览 / 2 源状态 / 3 六大关注域 /
echo             4 升级前瞻（意向指标，非预测）/ 5 关键实体 / 6 交叉验证矩阵 /
echo             7 同期对照原文 / 8 公开升级信号活跃度 / 9 声明类型 /
echo             10 数据源与合规 / 11 已知偏差
echo.
if not "%RIM_NO_PAUSE%"=="1" (
  if exist "output\LATEST.md" start "" notepad "output\LATEST.md"
if not defined NOPAUSE pause
)
exit /b 0
:maybe_pause
if not defined NOPAUSE pause
exit /b 0
