@echo off
rem mimeo - local web interface. Double-click to start.
rem The browser opens by itself in 2 seconds: http://127.0.0.1:8000/
rem To stop: close this window or press Ctrl+C.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Python not found. Install Python 3.11 from python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
)
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000/"
python web\serve.py
pause
