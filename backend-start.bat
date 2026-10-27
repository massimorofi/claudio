@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ------------------------------------------------------------------
rem Start the Claudio backend (detached) on Windows.
rem Mirrors backend-start.sh: same paths, same health check.
rem
rem Paths are passed to PowerShell and Python through environment
rem variables because '&' inside a repo path (e.g. "Merck Sharp &
rem Dohme LLC") breaks inline quoting in cmd.
rem ------------------------------------------------------------------

set "REPO_DIR=%~dp0"
set "BACKEND_DIR=%REPO_DIR%claudio-backend"
set "VENV=%BACKEND_DIR%\.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "DATA_DIR=%BACKEND_DIR%\data"
set "PID_FILE=%DATA_DIR%\claudio.pid"
set "LOG_DIR=%DATA_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\backend.log"
set "ERR_FILE=%LOG_DIR%\backend.err.log"
set "CONFIG_YAML=%BACKEND_DIR%\config\config.yaml"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

rem -- refuse to start if the pid file points at a live process --------
if exist "%PID_FILE%" (
    set /p "OLD_PID="<"%PID_FILE%"
    tasklist /FI "PID eq !OLD_PID!" 2>nul | find "!OLD_PID!" >nul
    if not errorlevel 1 (
        echo Already running ^(pid !OLD_PID!^)
        exit /b 1
    )
)

rem -- create the venv if missing -------------------------------------
if not exist "%VENV_PY%" (
    echo Creating virtualenv...
    py -3 -m venv "%VENV%" 2>nul || python -m venv "%VENV%"
    if errorlevel 1 goto :fail_setup
    "%VENV_PY%" -m pip install --quiet --upgrade pip
    if errorlevel 1 goto :fail_setup
    "%VENV_PY%" -m pip install --quiet -e "%REPO_DIR%claudio-core"
    if errorlevel 1 goto :fail_setup
    "%VENV_PY%" -m pip install --quiet -e "%BACKEND_DIR%[dev]"
    if errorlevel 1 goto :fail_setup
)

rem -- read host/port from config (defaults 127.0.0.1:8100) ----------
set "CLAUDIO_CONFIG=%CONFIG_YAML%"
for /f "usebackq delims=" %%h in (`"%VENV_PY%" -c "import os,yaml; p=os.environ.get('CLAUDIO_CONFIG',''); cfg=(yaml.safe_load(open(p)) if p and os.path.exists(p) else None) or {}; print(cfg.get('server',{}).get('host','127.0.0.1'))"`) do set "HOST=%%h"
for /f "usebackq delims=" %%p in (`"%VENV_PY%" -c "import os,yaml; p=os.environ.get('CLAUDIO_CONFIG',''); cfg=(yaml.safe_load(open(p)) if p and os.path.exists(p) else None) or {}; print(cfg.get('server',{}).get('port',8100))"`) do set "PORT=%%p"
if "%HOST%"=="" set "HOST=127.0.0.1"
if "%PORT%"=="" set "PORT=8100"

rem -- check the port is free -----------------------------------------
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
    echo Port %PORT% is already in use
    exit /b 1
)

echo Starting Claudio backend on %HOST%:%PORT% ...

rem -- overwrite (not append) the logs on each start ------------------
type nul > "%LOG_FILE%"
type nul > "%ERR_FILE%"

rem -- launch uvicorn in a hidden window, capture the real PID --------
set "CLAUDIO_HOST=%HOST%"
set "CLAUDIO_PORT=%PORT%"
for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$a = @('-m','uvicorn','claudio_backend.app:app','--host',$env:CLAUDIO_HOST,'--port',$env:CLAUDIO_PORT,'--log-level','warning'); $p = Start-Process -FilePath $env:VENV_PY -ArgumentList $a -WindowStyle Hidden -PassThru -RedirectStandardOutput $env:LOG_FILE -RedirectStandardError $env:ERR_FILE; Write-Output $p.Id"`) do set "NEW_PID=%%i"

if "%NEW_PID%"=="" (
    echo Failed to launch the backend process.
    goto :dump_logs
)
> "%PID_FILE%" echo %NEW_PID%

rem -- poll /healthz for ~15s -----------------------------------------
set /a _tries=0
:poll
curl.exe -s -m 2 "http://%HOST%:%PORT%/healthz" >nul 2>&1
if not errorlevel 1 goto :started
tasklist /FI "PID eq %NEW_PID%" 2>nul | find "%NEW_PID%" >nul
if errorlevel 1 goto :died
set /a _tries+=1
if %_tries% LSS 15 (
    ping -n 2 127.0.0.1 >nul
    goto :poll
)
goto :timeout

:started
echo Started ^(pid %NEW_PID%^) - http://%HOST%:%PORT%
exit /b 0

:died
echo Process died during startup. Last log lines:
goto :dump_logs

:timeout
echo Startup timed out after 15s. Last log lines:
goto :dump_logs

:dump_logs
if exist "%LOG_FILE%" (
    echo ===== %LOG_FILE% =====
    powershell -NoProfile -Command "Get-Content -Tail 20 -LiteralPath $env:LOG_FILE"
)
if exist "%ERR_FILE%" (
    echo ===== %ERR_FILE% =====
    powershell -NoProfile -Command "Get-Content -Tail 20 -LiteralPath $env:ERR_FILE"
)
del /f /q "%PID_FILE%" 2>nul
exit /b 1

:fail_setup
echo Could not set up the Python environment.
exit /b 1
