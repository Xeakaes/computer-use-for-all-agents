@echo off
setlocal
cd /d "%~dp0"
title screen-control stopper

echo Stopping screen-control...

rem Kill whatever listens on our ports
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8745" ^| findstr "LISTENING"') do taskkill /PID %%P /F >nul 2>&1
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8751" ^| findstr "LISTENING"') do taskkill /PID %%P /F >nul 2>&1

rem Kill the tunnel
for /f "tokens=2" %%P in ('tasklist ^| findstr /i "cloudflared"') do taskkill /PID %%P /F >nul 2>&1

echo Done. REST server, MCP server and tunnel are stopped.
pause
