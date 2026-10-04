@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -c "import PyInstaller, PySide6, psutil, requests, platformdirs" >nul 2>nul
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe scripts\package.py --onefile --bundle-ffmpeg %*
exit /b %errorlevel%
