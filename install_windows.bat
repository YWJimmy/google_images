@echo off
cd /d "%~dp0"
python -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist "config.yaml" copy "config.example.yaml" "config.yaml"
echo.
echo Installation complete.
echo Make sure Google Chrome is installed, then run validate_windows.bat.
pause
