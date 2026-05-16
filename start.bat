@echo off
chcp 65001 >nul
title KBOT - Kalshi Trading Bot
color 0A

echo.
echo  ╔══════════════════════════════════════════════════════════╗
echo  ║                 KBOT — Kalshi Trading Bot                ║
echo  ║            Autonomous. Zero-prompt. Paper-safe.          ║
echo  ╚══════════════════════════════════════════════════════════╝
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://python.org
    pause
    exit /b 1
)

:: Activate venv if it exists, otherwise install deps
if exist venv\Scripts\activate.bat (
    echo [KBOT] Activating virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo [KBOT] Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo [KBOT] Installing dependencies...
    pip install -r requirements.txt --quiet
)

:: Check .env exists
if not exist .env (
    echo [ERROR] .env file not found!
    echo [ERROR] Copy .env.example to .env and fill in your values.
    pause
    exit /b 1
)

:: Check private key
if not exist private_key.pem (
    echo [WARNING] private_key.pem not found at root.
    echo [WARNING] Make sure KALSHI_PRIVATE_KEY_PATH in .env points to your key.
)

echo.
echo [KBOT] Starting bot engine + dashboard...
echo [KBOT] Dashboard: http://localhost:5000
echo [KBOT] Press Ctrl+C to stop.
echo.

:: Watchdog loop — restarts bot if it crashes
:loop
python -m bot.main
if errorlevel 1 (
    echo.
    echo [WATCHDOG] Bot exited with error. Restarting in 10 seconds...
    timeout /t 10 /nobreak >nul
    goto loop
)
echo.
echo [KBOT] Bot stopped cleanly.
pause
