@echo off
REM claudio-ctl.bat - Windows twin of claudio-ctl for the Claudio docker stack.
REM
REM   claudio-ctl.bat status  [agent|web|all]
REM   claudio-ctl.bat start   [agent|web|all]
REM   claudio-ctl.bat stop    [agent|web|all]
REM   claudio-ctl.bat restart [agent|web|all]
REM   claudio-ctl.bat logs    [agent|web|all] [-f]
REM   claudio-ctl.bat build   [agent|web|all]
REM   claudio-ctl.bat health
REM   claudio-ctl.bat down
REM
REM Delegates to the same compose.yaml as the POSIX script. Requires Docker Desktop.
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "ACTION=%~1"
if "%ACTION%"=="" goto :usage

REM Remaining args, joined.
set "SERVICES="
shift
:collect
if "%~1"=="" goto :collected
set "SERVICES=!SERVICES! %~1"
shift
goto :collect
:collected

if /i "%ACTION%"=="status"  goto :do_status
if /i "%ACTION%"=="start"   goto :do_start
if /i "%ACTION%"=="stop"    goto :do_stop
if /i "%ACTION%"=="restart" goto :do_restart
if /i "%ACTION%"=="logs"    goto :do_logs
if /i "%ACTION%"=="build"   goto :do_build
if /i "%ACTION%"=="health"  goto :do_health
if /i "%ACTION%"=="down"    goto :do_down
if /i "%ACTION%"=="help"    goto :usage
echo unknown action '%ACTION%' >&2
goto :usage_fail

:do_status
docker compose ps %SERVICES%
exit /b %ERRORLEVEL%

:do_start
docker compose up -d %SERVICES%
if errorlevel 1 ( echo compose up failed >&2 & exit /b 1 )
echo Waiting for health ...
REM Poll the published health endpoint up to ~90s (45 x 2s).
set /a _tries=0
:poll
curl -fsS -m 3 http://127.0.0.1:8080/healthz >nul 2>&1
if not errorlevel 1 ( echo Stack healthy & exit /b 0 )
set /a _tries+=1
if %_tries% geq 45 ( echo Stack not healthy after 90s >&2 & exit /b 1 )
timeout /t 2 /nobreak >nul
goto :poll

:do_stop
REM Reverse order: web before agent.
if not "%SERVICES%"=="" (
  docker compose stop %SERVICES%
) else (
  docker compose stop web agent
)
exit /b %ERRORLEVEL%

:do_restart
docker compose stop web agent
if errorlevel 1 ( echo compose stop failed >&2 & exit /b 1 )
docker compose up -d %SERVICES%
exit /b %ERRORLEVEL%

:do_logs
docker compose logs --tail 200 %SERVICES%
exit /b %ERRORLEVEL%

:do_build
docker compose build %SERVICES%
exit /b %ERRORLEVEL%

:do_health
curl -fsS -m 3 http://127.0.0.1:8080/healthz
if errorlevel 1 ( echo web->agent unreachable >&2 & exit /b 1 )
echo.
curl -fsS -m 5 -H "Authorization: Bearer %CLAUDIO__SERVER__AUTH_TOKEN%" http://127.0.0.1:8080/api/v1/agent/status
exit /b %ERRORLEVEL%

:do_down
docker compose down
exit /b %ERRORLEVEL%

:usage
echo claudio-ctl.bat - Claudio docker stack control
echo.
echo   status ^| start ^| stop ^| restart ^| logs ^| build ^| health ^| down
echo   optional service argument: agent, web, or all
exit /b 0

:usage_fail
exit /b 2
