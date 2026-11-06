@echo off
cd /d "%~dp0"
title NIET Exam - Stopping Server Cluster

echo ================================================================
echo   STOPPING NIET EXAM LOAD BALANCED CLUSTER
echo ================================================================
echo.

echo [1/2] Stopping Nginx Load Balancer...
IF EXIST "C:\niet_nginx\nginx.exe" (
    cd /d "C:\niet_nginx"
    nginx.exe -s stop >nul 2>&1
    cd /d "%~dp0"
)
taskkill /F /IM nginx.exe >nul 2>&1

echo [2/2] Stopping all Backend Application Server Workers...
taskkill /F /FI "WINDOWTITLE eq Server 500*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Server 5001" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Server 5002" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Server 5003" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Server 5004" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Server 5005" >nul 2>&1

echo.
echo ================================================================
echo   SUCCESS: All 5 workers and Nginx Load Balancer have stopped.
echo ================================================================
echo.
pause
