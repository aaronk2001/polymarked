@echo off
title PolyMarked
cd /d "%~dp0.."

set "POLYMARKED_OPEN_BROWSER=1"

if not exist "data" mkdir data

echo [1/2] Running database migrations...
"%USERPROFILE%\.local\bin\uv.exe" run alembic upgrade head
if errorlevel 1 goto :err

echo [2/2] Starting PolyMarked (the browser opens automatically when ready)...
echo.
echo Telegram: send /start to your configured bot.
echo Close this window to stop the app.
echo.

"%USERPROFILE%\.local\bin\uv.exe" run python -m polymarket_agent_app
if errorlevel 1 goto :err
goto :end

:err
echo.
echo *** Error during startup. Scroll up for details. ***
:end
echo.
pause
