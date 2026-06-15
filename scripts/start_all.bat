@echo off
setlocal
cd /d "%~dp0.."

start "cargo-platform-backend" cmd /k scripts\start_backend.bat
start "cargo-platform-frontend" cmd /k scripts\start_frontend_dev.bat
