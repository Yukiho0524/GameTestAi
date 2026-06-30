@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   LDPlayer Mobile-Game Auto-Test Launcher
echo ============================================

REM 1) Check Python launcher (py)
where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python launcher "py" not found.
    echo         Install Python 3 and tick "Add python.exe to PATH".
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 2) Verify required packages; install from requirements.txt if any is missing
echo [CHECK] Verifying Python packages ...
py -c "import cv2, numpy, yaml, openpyxl" >nul 2>nul
if errorlevel 1 (
    echo [SETUP] Missing packages detected. Installing requirements.txt ...
    py -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Package install failed. Check your network / pip and retry.
        pause
        exit /b 1
    )
)
echo [OK] Packages ready.

REM 3) Run main program.
REM    No arguments  -> open GUI control panel (record + generate inside GUI)
REM    With arguments-> pass straight to run.py
REM    e.g.  start.bat devices
REM          start.bat test scripts\20260630_01.yaml --once
if "%~1"=="" (
    echo [START] Opening control panel ...
    py run.py gui
) else (
    echo [START] Running: run.py %*
    py run.py %*
)
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
    echo.
    echo [WARN] Program exited with code %RC%.
    echo        If the GUI did not open, run  py run.py gui  directly to see the error.
    pause
)
endlocal
