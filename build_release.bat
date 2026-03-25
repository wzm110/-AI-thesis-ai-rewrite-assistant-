@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo [1/4] 检查 Python...
python --version >nul 2>nul
if errorlevel 1 (
  echo 未检测到 Python，请先安装 Python 3.10+ 并勾选 Add Python to PATH。
  pause
  exit /b 1
)

echo [2/4] 安装打包依赖...
python -m pip install --upgrade pip
python -m pip install pyinstaller -i https://pypi.org/simple
if errorlevel 1 (
  echo 安装 pyinstaller 失败，请检查网络后重试。
  pause
  exit /b 1
)

echo [3/4] 生成 EXE...
pyinstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --name "论文降AI助手" ^
  --hidden-import paper_rewrite ^
  --hidden-import paper_rewrite.paths ^
  --hidden-import paper_rewrite.config ^
  --hidden-import paper_rewrite.core ^
  --hidden-import paper_rewrite.llm ^
  --hidden-import paper_rewrite.prompt ^
  --hidden-import paper_rewrite.docx_extract ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --add-data "prompts;prompts" ^
  --add-data "default.yaml.example;." ^
  server.py
if errorlevel 1 (
  echo 打包失败，请查看输出错误信息。
  pause
  exit /b 1
)

echo [4/4] 组装发行目录...
if exist "dist\release" rmdir /s /q "dist\release"
mkdir "dist\release"
copy /y "dist\论文降AI助手.exe" "dist\release\论文降AI助手.exe" >nul
copy /y "run_exe.bat" "dist\release\run_exe.bat" >nul
copy /y "README.md" "dist\release\README.md" >nul
copy /y "default.yaml.example" "dist\release\default.yaml.example" >nul
if exist "prompts" xcopy /e /i /y "prompts" "dist\release\prompts" >nul

echo.
echo 打包完成：dist\release
echo 交付给用户：让用户双击 run_exe.bat 即可启动。
pause
endlocal
