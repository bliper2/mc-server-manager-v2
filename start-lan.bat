@echo off
setlocal
cd /d "%~dp0"

title MC Server Manager (phone and LAN access)
rem Listens on the whole local network so a phone on the same Wi-Fi can open it. Debug mode must stay off.
set MC_MANAGER_DEV=0
set MC_MANAGER_HOST=0.0.0.0

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Running setup first...
    call setup.bat
    if errorlevel 1 exit /b 1
)

echo Starting MC Server Manager for phone access...
echo The phone address is printed below. Sign in with your normal account.
echo If the phone cannot connect, allow Python through the Windows firewall (Private networks).
echo Press Ctrl+C to stop the server.
echo.
".venv\Scripts\python.exe" app.py

if errorlevel 1 (
    echo.
    echo MC Server Manager stopped with an error.
    pause
)
