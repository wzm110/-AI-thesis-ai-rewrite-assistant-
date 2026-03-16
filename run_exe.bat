@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

if not exist ".\论文降AI助手.exe" (
  echo 未找到 论文降AI助手.exe，请先执行 build_release.bat。
  pause
  exit /b 1
)

start "" http://127.0.0.1:8000
".\论文降AI助手.exe"

endlocal
