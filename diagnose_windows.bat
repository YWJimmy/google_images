@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m app.main --config config.yaml --diagnose
pause
