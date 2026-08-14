@echo off
echo ========================================
echo   Monitoring App - Quick Starter
echo ========================================
echo.

echo Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed!
    echo.
    echo Please install Python first:
    echo 1. Download Python from https://www.python.org/downloads/
    echo 2. Run installer and CHECK "Add Python to PATH"
    echo 3. Restart your computer
    echo.
    pause
    exit /b 1
)

echo Python found! Installing dependencies...
echo.

python -m pip install psutil pywin32 requests --quiet

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to install dependencies!
    echo Try running this command manually:
    echo python -m pip install psutil pywin32 requests
    pause
    exit /b 1
)

echo.
echo Starting Monitoring Application...
echo.
python main_app.py

pause
