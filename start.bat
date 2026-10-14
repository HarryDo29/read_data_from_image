@echo off
setlocal
cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 goto no_docker

docker info >nul 2>&1
if errorlevel 1 goto docker_not_running

echo Building and starting the app. The first build may take a few minutes...
docker compose up --build -d
if errorlevel 1 goto start_failed

timeout /t 3 /nobreak >nul
start "" "http://localhost:8501"
echo App is running at http://localhost:8501
echo Run stop.bat when you want to stop it. Your saved records will remain.
pause
exit /b 0

:no_docker
echo Docker was not found. Install Docker Desktop for Windows first.
pause
exit /b 1

:docker_not_running
echo Docker Desktop is installed but is not running. Start it, then run start.bat again.
pause
exit /b 1

:start_failed
echo Could not start the app. Check the Docker Desktop status and try again.
pause
exit /b 1
