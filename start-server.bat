@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title screen-control launcher

echo.
echo  ============================================================
echo   screen-control launcher
echo  ============================================================
echo.

rem ---------------------------------------------------------------------
rem [0/5] Preflight: python + cloudflared
rem ---------------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found in PATH.
    pause
    exit /b 1
)
if not exist "cloudflared.exe" (
    echo [INFO] cloudflared.exe not found - downloading...
    curl -L -o cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
)
set "TUNNEL=1"
if not exist "cloudflared.exe" (
    echo [WARN] cloudflared unavailable - cloud-agent tunnel will be skipped.
    set "TUNNEL=0"
)

rem ---------------------------------------------------------------------
rem [1/5] Stop leftover instances from a previous run
rem ---------------------------------------------------------------------
echo [1/5] Cleaning up previous instances...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8745" ^| findstr "LISTENING"') do taskkill /PID %%P /F >nul 2>&1
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8751" ^| findstr "LISTENING"') do taskkill /PID %%P /F >nul 2>&1
for /f "tokens=2" %%P in ('tasklist ^| findstr /i "cloudflared"') do taskkill /PID %%P /F >nul 2>&1
ping -n 2 127.0.0.1 >nul

rem ---------------------------------------------------------------------
rem [2/5] Start REST server (port 8745)
rem ---------------------------------------------------------------------
echo [2/5] Starting REST server on port 8745...
start "screen-control REST" /min cmd /c "python server.py >> server.log 2>&1"

rem ---------------------------------------------------------------------
rem [3/5] Start MCP HTTP server (port 8751)
rem ---------------------------------------------------------------------
echo [3/5] Starting MCP HTTP server on port 8751...
start "screen-control MCP" /min cmd /c "python mcp_server.py --http --port 8751 >> mcp-server.log 2>&1"

rem ---------------------------------------------------------------------
rem [4/5] Wait for both servers to come up
rem ---------------------------------------------------------------------
echo [4/5] Waiting for servers...
set /a TRIES=0
:wait_rest
set /a TRIES+=1
if %TRIES% GTR 30 (
    echo [ERROR] REST server did not start - check server.log
    pause
    exit /b 1
)
curl -s -o nul http://127.0.0.1:8745/token
if errorlevel 1 (
    ping -n 2 127.0.0.1 >nul
    goto wait_rest
)
set /a TRIES=0
:wait_mcp
set /a TRIES+=1
if %TRIES% GTR 30 (
    echo [ERROR] MCP server did not start - check mcp-server.log
    pause
    exit /b 1
)
curl -s -o nul http://127.0.0.1:8751/health
if errorlevel 1 (
    ping -n 2 127.0.0.1 >nul
    goto wait_mcp
)
echo        both servers are up.

rem ---------------------------------------------------------------------
rem [5/5] Start cloudflared tunnel (background) and capture its URL
rem ---------------------------------------------------------------------
set "PUBLIC_URL="
if "%TUNNEL%"=="1" (
    echo [5/5] Starting cloudflared tunnel...
    start "screen-control tunnel" /min cmd /c cloudflared.exe tunnel --url http://127.0.0.1:8751 ^> tunnel.log 2^>^&1
)
set /a TRIES=0
:wait_url
if not "%TUNNEL%"=="1" goto after_url
set /a TRIES+=1
if %TRIES% GTR 30 (
    echo [WARN] Tunnel URL not found yet - check tunnel.log
    goto after_url
)
ping -n 2 127.0.0.1 >nul
for /f "delims=" %%L in ('findstr /c:"trycloudflare.com" tunnel.log 2^>nul') do (
    set "LINE=%%L"
    call :extract_url
)
if not defined PUBLIC_URL goto wait_url
echo        tunnel ready.
:after_url
if not defined PUBLIC_URL echo        tunnel NOT running - cloud agents cannot connect until it is.

rem ---------------------------------------------------------------------
rem Summary: everything a cloud agent needs, ready to paste
rem ---------------------------------------------------------------------
set "TOKEN="
set /p TOKEN=<.token 2>nul

echo.
echo  ============================================================
echo   ALL SYSTEMS RUNNING
echo  ============================================================
echo.
echo   Local REST API  : http://127.0.0.1:8745
echo   Local MCP       : http://127.0.0.1:8751/mcp
if defined PUBLIC_URL echo   Public MCP URL  : !PUBLIC_URL!/mcp
echo.
echo   ------------------------------------------------------------
echo   PASTE INTO YOUR CLOUD AGENT  (MCP connector settings)
echo   ------------------------------------------------------------
if defined PUBLIC_URL (
    echo   Endpoint : !PUBLIC_URL!/mcp
) else (
    echo   Endpoint : ^(tunnel not running - restart this script^)
)
echo   Header   : X-Auth-Token: !TOKEN!
echo   Auth     : X-Auth-Token header required  ^(401 otherwise^)
echo   Headerless clients: create a scoped key ^(see below^), then use
echo              !PUBLIC_URL!/mcp/SCOPED-KEY  as the endpoint URL.
echo.
echo   Create a scoped key ^(recommended for headerless connectors^):
echo     curl -X POST http://127.0.0.1:8745/api/keys -H "X-Auth-Token: !TOKEN!" -H "Content-Type: application/json" -d "{\"action\"^:"create\"^,\"name\"^:"spark\"^,\"expires_in_hours\"^:24}"
echo   ------------------------------------------------------------
echo.
echo   Quick test from anywhere:
if defined PUBLIC_URL (
    echo     curl "!PUBLIC_URL!/health"
    echo     curl -H "X-Auth-Token: !TOKEN!" "!PUBLIC_URL!/mcp"
) else (
    echo     curl http://127.0.0.1:8751/health
)
echo.
echo   Revoke the scoped key when done:
echo     curl -X POST http://127.0.0.1:8745/api/keys -H "X-Auth-Token: !TOKEN!" -H "Content-Type: application/json" -d "{\"action\"^:"revoke\"^,\"name\"^:"spark\"}"
echo.
echo   Stop everything: run stop-server.bat
echo   ^(or close the three minimized windows: REST / MCP / tunnel^)
echo.
pause
goto :eof

rem ---------------------------------------------------------------------
rem Subroutine: extract the https://...trycloudflare.com URL from %LINE%
rem ---------------------------------------------------------------------
:extract_url
set "TMP=!LINE:*https://=https://!"
if not defined TMP goto :eof
for /f "tokens=1" %%T in ("!TMP!") do set "PUBLIC_URL=%%T"
goto :eof
