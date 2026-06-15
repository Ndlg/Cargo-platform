@echo off
setlocal
cd /d "%~dp0.."

set "DOCKER_EXE="
for /f "delims=" %%D in ('where docker 2^>nul') do (
  if not defined DOCKER_EXE set "DOCKER_EXE=%%D"
)
if not defined DOCKER_EXE if exist "C:\Program Files\Docker\Docker\resources\bin\docker.exe" (
  set "DOCKER_EXE=C:\Program Files\Docker\Docker\resources\bin\docker.exe"
)
if not defined DOCKER_EXE (
  echo Docker was not found. Please install and start Docker Desktop.
  pause
  exit /b 1
)

set "PATH=C:\Program Files\Docker\Docker\resources\bin;%PATH%"

"%DOCKER_EXE%" compose version >nul 2>nul
if errorlevel 1 (
  echo Docker Compose was not found. Please update Docker Desktop.
  pause
  exit /b 1
)

echo Starting cargo-platform with Docker...
"%DOCKER_EXE%" compose up -d --build
if errorlevel 1 (
  echo Docker start failed.
  pause
  exit /b 1
)

echo.
echo Docker services started.
echo Tenant UI:       http://127.0.0.1:5173
echo Tenant Admin:    http://127.0.0.1:5173/admin
echo Platform Admin:  http://127.0.0.1:5174/admin
echo Backend Docs:    http://127.0.0.1:8000/docs
echo.
pause
