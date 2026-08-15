@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python virtual environment not found. Run install_windows.bat first.
  pause
  exit /b 1
)
if not exist "config.yaml" (
  echo config.yaml not found.
  pause
  exit /b 1
)
start "Google Images Dashboard" ".venv\Scripts\python.exe" -m app.dashboard --config config.yaml --open-browser
echo Dashboard starting at http://127.0.0.1:8765/
