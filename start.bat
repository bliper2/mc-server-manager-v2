@echo off
setlocal
cd /d "%~dp0"

title MC Server Manager
set MC_MANAGER_DEV=1

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Running setup first...
    call setup.bat
    if errorlevel 1 exit /b 1
)

echo Starting MC Server Manager...
echo Open http://127.0.0.1:5000/ in your browser.
echo Press Ctrl+C to stop the server.
echo.
".venv\Scripts\python.exe" app.py

if errorlevel 1 (
    echo.
    echo MC Server Manager stopped with an error.
    pause
)
