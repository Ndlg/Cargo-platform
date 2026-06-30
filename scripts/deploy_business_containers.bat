@echo off
setlocal
cd /d "%~dp0.."

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy_business_containers.ps1" %*
if errorlevel 1 (
  echo.
  echo Business container deployment failed.
  pause
  exit /b 1
)

echo.
echo Business container deployment completed.
pause
