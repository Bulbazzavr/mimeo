@echo off
rem mimeo - install and run with one file (Z-76).
rem First run downloads everything into runtime\ (about 19 GB: llama.cpp, Gemma 4 12B,
rem stable-diffusion.cpp, Z-Image-Turbo, its text encoder and VAE), checks every file
rem by sha256, starts the model servers and opens the web interface.
rem Needs Windows 10/11 and an NVIDIA GPU with 12 GB. Python 3.11+ is used if found,
rem otherwise the embeddable Python 3.11.9 is downloaded into runtime\python.
rem To stop: close the browser tab, then press Ctrl+C here.
setlocal
cd /d "%~dp0"

set "PY="
if not defined MIMEO_EMBEDDED_PYTHON (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
    if not exist runtime\python\python.exe (
        echo Python 3.11+ not found - downloading embeddable Python 3.11.9, 11 MB ...
        if not exist runtime mkdir runtime
        curl.exe -L --fail -o runtime\python-embed.zip https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip || goto :fail
        mkdir runtime\python
        tar -xf runtime\python-embed.zip -C runtime\python || goto :fail
        rem The ._pth file replaces sys.path: add the project root so "python -m mimeo" works.
        echo ..\..>> runtime\python\python311._pth
        del runtime\python-embed.zip
    )
    set "PY=%~dp0runtime\python\python.exe"
)

echo.
echo [1/3] Models and servers: download what is missing, check sha256
"%PY%" tools\runtime.py install || goto :fail
echo.
echo [2/3] Starting llama-server and sd-server
"%PY%" tools\runtime.py start || goto :fail
echo.
echo [3/3] Web interface: http://127.0.0.1:8000/
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000/"
"%PY%" web\serve.py
"%PY%" tools\runtime.py stop
exit /b 0

:fail
echo.
echo Setup failed - the reason is printed above. Run start.bat again to resume:
echo downloaded files are kept and checked, interrupted downloads continue.
pause
exit /b 1
