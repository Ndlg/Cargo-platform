@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

where pwsh.exe >nul 2>nul
if errorlevel 1 (
  echo PowerShell 7 was not found. Run scripts\build_collector_release.ps1 with PowerShell 7.
  exit /b 1
)

pwsh.exe -NoProfile -File "%CD%\scripts\build_collector_release.ps1" %*
exit /b %ERRORLEVEL%
