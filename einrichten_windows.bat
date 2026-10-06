@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Blackline 2 - Einrichtung
echo ============================================
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if not exist ".venv\Scripts\python.exe" (
  echo Erstelle Python-Umgebung ...
  %PY% -m venv .venv || goto :fehler
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto :fehler
python -m blackline2.setup_ki %*
echo.
echo Fertig. Start kuenftig per Doppelklick auf "Blackline2_starten.bat".
pause
exit /b 0
:fehler
echo.
echo FEHLER bei der Einrichtung. Bitte Meldungen oben pruefen.
pause
exit /b 1
