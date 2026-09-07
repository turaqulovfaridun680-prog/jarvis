@echo off
setlocal
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" -m jarvis_bot
) else (
  python -m jarvis_bot
)
