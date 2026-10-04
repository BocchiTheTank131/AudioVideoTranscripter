@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Create .venv and install requirements-dev.txt first.
  exit /b 1
)
.venv\Scripts\python.exe scripts\package.py --onefile --bundle-ffmpeg %*
exit /b %errorlevel%
