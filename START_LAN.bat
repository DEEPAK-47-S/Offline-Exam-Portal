@echo off
setlocal
title Exam Code Lab - LAN Server
echo.
echo ================================================================
echo   EXAM CODE LAB - OFFLINE CLASSROOM SERVER
echo ================================================================
echo.
echo Keep this window open during the examination.
echo Connect every student laptop to the SAME Wi-Fi router or LAN.
echo On student laptops open: http://TEACHER-IP:5000
echo.
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /c:"IPv4 Address"') do echo Teacher computer IP:%%A
echo.
echo Starting LAN Server...
python app.py
pause
