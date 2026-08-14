@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run install_windows.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m app.main --config config.yaml --capture-state --cdp-endpoint http://127.0.0.1:9222
if errorlevel 1 (
  echo State was not captured. Review the error above.
) else (
  echo State saved under private\ and excluded from Git.
)
pause
