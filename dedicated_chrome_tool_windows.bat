@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到 Python 虚拟环境，请先运行 install_windows.bat。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m app.chrome_profile_tool interactive
if errorlevel 1 echo 操作未完成，请检查上方错误信息。
pause
