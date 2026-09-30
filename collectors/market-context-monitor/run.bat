@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%PYTHON%"
if not defined PY set "PY=python"
"%PY%" "%~dp0..\..\scripts\supplement_market.py"
exit /b %ERRORLEVEL%
