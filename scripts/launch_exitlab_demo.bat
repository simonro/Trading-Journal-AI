@echo off
setlocal
title Exit Lab demo
cd /d "%~dp0.."

REM Exit Lab demo: synthetic trades on historical market prices, kept in its own
REM database (backend\exitlab_demo.db). Your journal database is never opened.
REM Run setup.bat first. The first run downloads minute prices from Alpaca, so it
REM needs APCA_API_KEY_ID and APCA_API_SECRET_KEY in backend\.env.

if exist ".venv\Scripts" set "PATH=%CD%\.venv\Scripts;%PATH%"

if exist "backend\exitlab_demo.db" goto :start
echo [1/2] Downloading historical minute prices from Alpaca, about two minutes the first time
python scripts\seed_exitlab_demo.py --fetch
if errorlevel 1 goto :fail
echo [2/2] Building the demo database
python scripts\reset_exitlab_demo.py
if errorlevel 1 goto :fail

:start
start "Exit Lab demo - Backend" /d "%CD%\backend" cmd /k "set DATABASE_PATH=exitlab_demo.db&& python -m uvicorn main:app --port 8020"
timeout /t 2 /nobreak >nul
start "Exit Lab demo - Frontend" /d "%CD%\frontend" cmd /k "set PORT=3020&& set REACT_APP_API_URL=http://localhost:8020&& npm start"

echo Exit Lab demo backend starting on http://localhost:8020
echo Exit Lab demo frontend starting on http://localhost:3020
echo Open Exit Lab in the top menu and choose the "Exit Lab demo" account.
echo Close the two server windows to stop the demo.
pause
exit /b 0

:fail
echo.
echo The demo could not be built. Check that setup.bat has run and that backend\.env
echo has your Alpaca key id and secret, then run this file again.
pause
exit /b 1
