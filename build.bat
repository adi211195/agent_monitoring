@echo off
echo ========================================
echo   Building Monitoring App Executable
echo ========================================
echo.

echo Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed!
    echo Please install Python first from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Installing build dependencies...
python -m pip install pyinstaller psutil pywin32 requests Pillow mss opencv-python --quiet

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to install dependencies!
    pause
    exit /b 1
)

echo.
echo Building executable...
echo.

python -m PyInstaller --name=MonitoringApp --onefile --windowed --clean main_app.py

if %errorlevel% neq 0 (
    echo.
    echo Build failed! Check errors above.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   BUILD SUCCESSFUL!
echo ========================================
echo.
echo Executable location:
echo    dist\MonitoringApp.exe
echo.
echo Run the application:
echo    dist\MonitoringApp.exe
echo.
pause
