@echo off
cd /d "%~dp0"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
  echo Google Chrome was not found in the standard install locations.
  pause
  exit /b 1
)
start "Manual Google state capture" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%CD%\profile\manual_state_capture" https://images.google.com/ncr
echo Chrome opened with a dedicated local profile.
echo Browse Google manually. Do not use your everyday Chrome profile.
echo Keep this Chrome window open, then run capture_google_state_windows.bat.
pause
