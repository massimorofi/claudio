@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ------------------------------------------------------------------
rem Stop the Claudio backend on Windows (polite, then forced).
rem Mirrors backend-stop.sh. /T is required because the venv's uvicorn
rem launcher starts a child python.exe; killing only the parent would
rem orphan it. A hidden console process usually cannot be closed
rem politely, so expect the forced kill.
rem ------------------------------------------------------------------

set "REPO_DIR=%~dp0"
set "PID_FILE=%REPO_DIR%claudio-backend\data\claudio.pid"

if not exist "%PID_FILE%" (
    echo Not running ^(no pid file^)
    exit /b 0
)

set /p "PID="<"%PID_FILE%"

tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 (
    echo Stale pid file ^(process %PID% not alive^); removing
    del /f /q "%PID_FILE%" 2>nul
    exit /b 0
)

echo Stopping ^(pid %PID%^)...
taskkill /PID %PID% /T >nul 2>&1

rem -- wait up to ~20s for a graceful drain ---------------------------
set /a _tries=0
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto :stopped
set /a _tries+=1
if %_tries% GEQ 20 goto :force
ping -n 2 127.0.0.1 >nul
goto :wait

:force
echo Grace period elapsed; sending forced kill
taskkill /PID %PID% /T /F >nul 2>&1

:stopped
del /f /q "%PID_FILE%" 2>nul
echo Stopped
exit /b 0
