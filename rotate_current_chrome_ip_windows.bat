@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m app.current_chrome_ip %*
) else (
    python -m app.current_chrome_ip %*
)

set EXIT_CODE=%ERRORLEVEL%
echo.
if %EXIT_CODE% EQU 0 (
    echo Current dedicated Chrome egress switched successfully.
) else (
    echo Egress switch failed. Exit code: %EXIT_CODE%
)
exit /b %EXIT_CODE%
