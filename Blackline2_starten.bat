@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Bitte zuerst "einrichten_windows.bat" ausfuehren.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m blackline2 %*
