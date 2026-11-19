@echo off
REM Double-click this file to start the EA-BLE Flask app.
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py"
if not exist venv (
  echo Creating virtual environment...
  %PY% -m venv venv
)
call venv\Scripts\activate
echo Installing Flask (first time only)...
pip install -q -r requirements.txt
echo.
echo ===== App running: open http://127.0.0.1:5000 =====
echo ===== Keep this window OPEN. Press Ctrl+C to stop. =====
start "" http://127.0.0.1:5000
python app.py
pause
