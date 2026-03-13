@echo off
chcp 437 >nul
title SQLMind App

:: ============================================================
::  SQLMind Desktop App  --  launch_app.bat
::  Creates a venv automatically on first run
:: ============================================================

set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%venv"
set "LOGFILE=%APP_DIR%launch_error.log"

echo.
echo  +---------------------------------------------------------+
echo  ^|   SQLMind Desktop App  v3.0                            ^|
echo  ^|   PyQt6 UI  --  Auto-managed venv                      ^|
echo  +---------------------------------------------------------+
echo.

:: ---- Check global Python exists ----------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found in PATH.
    echo  Install Python 3.10+ from https://python.org
    echo  Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Python  : %PYVER%
echo  App dir : %APP_DIR%
echo  venv    : %VENV_DIR%
echo.

:: ---- Create venv if it does not exist ----------------------
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo  [INFO] Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo  [ERROR] Failed to create venv.
        echo  Try: python -m pip install --upgrade pip
        pause
        exit /b 1
    )

    echo  [INFO] Installing app dependencies into venv...
    echo        (PyQt6, psycopg2, pymysql, pandas -- no torch)
    echo.
    "%VENV_DIR%\Scripts\pip" install --upgrade pip >nul 2>&1
    "%VENV_DIR%\Scripts\pip" install -r "%APP_DIR%requirements_app.txt"
    if errorlevel 1 (
        echo.
        echo  [ERROR] pip install failed. Details above.
        echo  Fix the error then delete the venv folder and re-run.
        pause
        exit /b 1
    )
    echo.
    echo  [OK] Dependencies installed.
    echo.
)

:: ---- Verify PyQt6 is present --------------------------------
"%VENV_DIR%\Scripts\python" -c "import PyQt6" 2>nul
if errorlevel 1 (
    echo  [WARN] PyQt6 not found in venv. Reinstalling dependencies...
    "%VENV_DIR%\Scripts\pip" install -r "%APP_DIR%requirements_app.txt"
    echo.
)

:: ---- Launch app ---------------------------------------------
echo  Launching SQLMind...
echo  (errors will be saved to: launch_error.log)
echo.

"%VENV_DIR%\Scripts\python" "%APP_DIR%main.py" 2>"%LOGFILE%"
set EXIT_CODE=%errorlevel%

if %EXIT_CODE% neq 0 (
    echo.
    echo  +---------------------------------------------------------+
    echo  ^|   [ERROR] SQLMind exited with code %EXIT_CODE%                   ^|
    echo  +---------------------------------------------------------+
    echo.
    echo  Error details saved to: %LOGFILE%
    echo.
    echo  --- Last 20 lines of error log: ---
    powershell -Command "Get-Content '%LOGFILE%' -Tail 20" 2>nul
    echo.
    echo  If the log is empty, also run from inside the venv:
    echo    %VENV_DIR%\Scripts\python %APP_DIR%main.py
    echo.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo  SQLMind closed normally.
pause
