@echo off
setlocal
cd /d "%~dp0.."

where pwsh.exe >nul 2>nul
if errorlevel 1 (
  echo PowerShell 7 is required.
  pause
  exit /b 1
)

pwsh.exe -NoProfile -File "%~dp0deploy_business_containers.ps1" %*
if errorlevel 1 (
  echo.
  echo Business container deployment failed.
  pause
  exit /b 1
)

echo.
echo Business container deployment completed.
pause
