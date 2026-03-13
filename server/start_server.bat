@echo off
chcp 437 >nul
title SQLMind API Server

:: ============================================================
::  SQLMind API Server  --  start_server.bat
::  Uses GLOBAL Python (no venv required)
:: ============================================================

:: ---- SET YOUR ADAPTER PATH HERE ----------------------------
:: Point this to your qwen25_sql_adapter folder.
:: Default: adapter folder inside this server/ directory
set "SQLMIND_ADAPTER=%~dp0qwen25_sql_adapter"
:: To use a custom path, replace the line above with:
:: set "SQLMIND_ADAPTER=C:\path\to\your\lora\adapter"
:: ------------------------------------------------------------

set "SERVER_HOST=0.0.0.0"
set "SERVER_PORT=8000"
set "SERVER_DIR=%~dp0"

echo.
echo  +---------------------------------------------------------+
echo  ^|   SQLMind API Server  v3.1                             ^|
echo  ^|   train_v3.jsonl model  --  4-retry auto-correction    ^|
echo  +---------------------------------------------------------+
echo  ^|   Adapter : %SQLMIND_ADAPTER%
echo  ^|   URL     : http://localhost:%SERVER_PORT%
echo  ^|   Docs    : http://localhost:%SERVER_PORT%/docs
echo  +---------------------------------------------------------+
echo.

:: ---- Check Python -------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found in PATH.
    echo  Install Python 3.10+ and make sure it is on PATH.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Python  : %PYVER%

:: ---- Check torch --------------------------------------------
python -c "import torch; print('  torch   : ' + torch.__version__ + ' / CUDA=' + str(torch.cuda.is_available()))" 2>nul
if errorlevel 1 (
    echo  [WARN] PyTorch not found.
    echo  Install for CUDA 12.x:
    echo    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
    echo  Install CPU-only:
    echo    pip install torch torchvision torchaudio
    echo.
    pause
    exit /b 1
)

:: ---- Check fastapi / uvicorn --------------------------------
python -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo  [INFO] Installing server dependencies globally...
    pip install -r "%SERVER_DIR%requirements_server.txt"
    if errorlevel 1 (
        echo  [ERROR] pip install failed. See above for details.
        pause
        exit /b 1
    )
)

:: ---- Check adapter path ------------------------------------
if not exist "%SQLMIND_ADAPTER%" (
    echo.
    echo  [ERROR] Adapter path not found:
    echo    %SQLMIND_ADAPTER%
    echo.
    echo  Options:
    echo    1. Place your adapter files in: %SERVER_DIR%qwen25_sql_adapter\
    echo    2. Edit SQLMIND_ADAPTER in this .bat file to point to your adapter
    echo.
    pause
    exit /b 1
)

:: ---- Start server ------------------------------------------
echo.
echo  Starting server... (press Ctrl+C to stop)
echo.
cd /d "%SERVER_DIR%"
python api_server.py --adapter "%SQLMIND_ADAPTER%" --host "%SERVER_HOST%" --port "%SERVER_PORT%"

echo.
echo  Server stopped.
pause
