@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title NIET Exam - Load Balanced Server Cluster (Port 5000)

echo ================================================================
echo   NIET EXAM SYSTEM - LOAD BALANCED CLUSTER (PORT 5000)
echo ================================================================
echo.

:: 1. Detect Python executable
IF EXIST ".\.venv\Scripts\python.exe" (
    SET "PY_EXE=.\.venv\Scripts\python.exe"
) ELSE (
    SET "PY_EXE=python"
)

:: 2. Find Teacher LAN IP
echo [1/4] Detecting Local Network IP Address...
set "TEACHER_IP=127.0.0.1"
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
    set "RAW_IP=%%A"
    set "RAW_IP=!RAW_IP: =!"
    if not "!RAW_IP!"=="" (
        set "TEACHER_IP=!RAW_IP!"
        echo       Detected IP: !TEACHER_IP!
    )
)
echo.

:: 3. Start 5 Python Workers (5001-5005)
echo [2/4] Starting 5 Backend Worker Instances (5001-5005)...
start "Server 5001" /min %PY_EXE% app.py --port=5001
start "Server 5002" /min %PY_EXE% app.py --port=5002
start "Server 5003" /min %PY_EXE% app.py --port=5003
start "Server 5004" /min %PY_EXE% app.py --port=5004
start "Server 5005" /min %PY_EXE% app.py --port=5005
echo       5 Backend Workers started on 127.0.0.1 (ports 5001-5005).
echo.

:: 4. Deploy Nginx to ASCII path (C:\niet_nginx) to bypass Windows folder emoji bugs
echo [3/4] Preparing Nginx Load Balancer...
SET "NGINX_DIR=C:\niet_nginx"
IF NOT EXIST "%NGINX_DIR%\nginx.exe" (
    echo       Deploying Nginx to %NGINX_DIR%...
    robocopy nginx "%NGINX_DIR%" /E >nul
)

copy /Y nginx.conf "%NGINX_DIR%\conf\nginx.conf" >nul
echo       Nginx configuration applied.
echo.

:: 5. Launch Nginx on Port 5000
echo [4/4] Starting Nginx Load Balancer on Port 5000...
cd /d "%NGINX_DIR%"
taskkill /F /IM nginx.exe >nul 2>&1
ping -n 2 127.0.0.1 >nul
start "" nginx.exe
cd /d "%~dp0"
ping -n 3 127.0.0.1 >nul

echo.
echo ================================================================
echo   SUCCESS: CLUSTER LOAD BALANCER IS ACTIVE ON PORT 5000!
echo ================================================================
echo.
echo   * PUBLIC ACCESS URL FOR ALL STUDENTS ^& TEACHERS:
echo     Localhost: http://localhost:5000
echo     LAN / Lab: http://!TEACHER_IP!:5000
echo.
echo   * All student and teacher traffic connects strictly to Port 5000.
echo   * Nginx automatically balances every request across workers 5001-5005.
echo.
echo   * To STOP the entire system, run stop_servers.bat
echo ================================================================
echo.
pause
