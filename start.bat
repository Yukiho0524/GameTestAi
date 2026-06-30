@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   雷電手遊自動化測試系統  啟動中...
echo ============================================

REM 1) 檢查 Python 啟動器 py
where py >nul 2>nul
if errorlevel 1 (
    echo [錯誤] 找不到 Python 啟動器 ^(py^)，請先安裝 Python 3 並勾選 Add to PATH。
    echo        下載：https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 2) 檢查必要套件，缺了就依 requirements.txt 安裝
echo [檢查] 驗證 Python 套件 ...
py -c "import cv2, numpy, yaml, openpyxl" >nul 2>nul
if errorlevel 1 (
    echo [安裝] 偵測到缺少套件，開始安裝 requirements.txt ...
    py -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [錯誤] 套件安裝失敗，請檢查網路連線或 pip 設定。
        pause
        exit /b 1
    )
) else (
    echo [OK] 套件齊全。
)

REM 3) 執行主程式：無參數開圖形控制台；帶參數則直接傳給 run.py
REM    例：start.bat devices  /  start.bat test scripts\20260630_01.yaml --once
echo [啟動] 主程式 ...
if "%~1"=="" (
    py run.py gui
) else (
    py run.py %*
)

if errorlevel 1 (
    echo.
    echo [注意] 主程式以非零狀態結束。
    pause
)
endlocal
