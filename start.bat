@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo [1/3] 检查 Python...
python --version >nul 2>nul
if errorlevel 1 (
  echo 未检测到 Python，请先安装 Python 3.10+，并勾选“Add Python to PATH”。
  pause
  exit /b 1
)

echo [2/3] 安装/更新依赖...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo 依赖安装失败，请检查网络后重试。
  pause
  exit /b 1
)

echo [3/3] 启动服务...
start "" http://127.0.0.1:8000
python server.py

endlocal
