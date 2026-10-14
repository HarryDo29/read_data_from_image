@echo off
setlocal
cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 (
    echo Docker was not found. Install Docker Desktop for Windows first.
    pause
    exit /b 1
)

docker compose down
if errorlevel 1 (
    echo Could not stop the app. Check Docker Desktop status.
    pause
    exit /b 1
)

echo App stopped. Saved records remain in the data folder.
pause
