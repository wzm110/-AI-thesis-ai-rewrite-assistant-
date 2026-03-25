@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ===== 发布前体检开始 =====

echo [1/6] Python 语法检查...
python -m py_compile "server.py" "paper_rewrite\__init__.py" "paper_rewrite\paths.py" "paper_rewrite\config.py" "paper_rewrite\core.py" "paper_rewrite\llm.py" "paper_rewrite\prompt.py" "paper_rewrite\docx_extract.py"
if errorlevel 1 (
  echo Python 源码语法检查失败，请先修复。
  pause
  exit /b 1
)

echo [2/6] 关键分段逻辑检查...
python -c "import server;ts=server.build_tasks_from_thesis();mx=max(len(t.original_text) for t in ts);ov=sum(1 for t in ts if len(t.original_text)>250);print('tasks=',len(ts),'max_len=',mx,'over250=',ov);import sys;sys.exit(0 if ov==0 else 1)"
if errorlevel 1 (
  echo 分段检查失败：存在超过250字符的分段。
  pause
  exit /b 1
)

echo [3/6] 检查 default.yaml 是否包含明显 API Key...
python -c "import re,sys,pathlib;p=pathlib.Path('default.yaml');txt=p.read_text(encoding='utf-8') if p.exists() else '';hit=bool(re.search(r'api_key:\s*sk-[A-Za-z0-9]',txt));print('default.yaml_exists=',p.exists(),'suspected_key=',hit);sys.exit(1 if hit else 0)"
if errorlevel 1 (
  echo 检测到 default.yaml 里可能有真实 key，请清理后再发布。
  pause
  exit /b 1
)

echo [4/6] 清理 logs / outputs ...
if exist "logs" (
  del /q "logs\*" >nul 2>nul
)
if exist "outputs" (
  del /q "outputs\*" >nul 2>nul
)

echo [5/6] 核心文件存在性检查...
if not exist "README.md" echo 缺少 README.md & exit /b 1
if not exist "LICENSE" echo 缺少 LICENSE & exit /b 1
if not exist "default.yaml.example" echo 缺少 default.yaml.example & exit /b 1
if not exist "start.bat" echo 缺少 start.bat & exit /b 1
if not exist "build_release.bat" echo 缺少 build_release.bat & exit /b 1

echo [6/6] 完成
echo ===== 体检通过，可发布到 GitHub =====
pause
endlocal
