@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Auto Script Generator (watch new videos)
echo ============================================
echo   Watches the recording folder. When a NEW
echo   video appears, it extracts frames and calls
echo   Claude Code to analyze, generate the test
echo   script, and push it to git automatically.
echo   Press Ctrl+C to stop.
echo ============================================

where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python launcher "py" not found.
    pause
    exit /b 1
)

py run.py autogen --watch
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
    echo.
    echo [WARN] Exited with code %RC%.
    pause
)
endlocal
