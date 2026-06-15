@echo off
setlocal
cd /d "%~dp0.."

:menu
cls
echo === Cargo Platform Docker Console ===
echo.
echo 1. Start all services
echo 2. Stop all services
echo 3. Show service status
echo 4. Show logs
echo 5. Open tenant UI
echo 6. Open tenant admin
echo 7. Open platform admin
echo 8. Open backend docs
echo 0. Exit
echo.
set /p choice=Choose:

if "%choice%"=="1" call scripts\docker_start.bat & goto menu
if "%choice%"=="2" call scripts\docker_stop.bat & goto menu
if "%choice%"=="3" call scripts\docker_status.bat & goto menu
if "%choice%"=="4" call scripts\docker_logs.bat & goto menu
if "%choice%"=="5" start "" http://127.0.0.1:5173 & goto menu
if "%choice%"=="6" start "" http://127.0.0.1:5173/admin & goto menu
if "%choice%"=="7" start "" http://127.0.0.1:5174/admin & goto menu
if "%choice%"=="8" start "" http://127.0.0.1:8000/docs & goto menu
if "%choice%"=="0" exit /b 0

echo Invalid choice.
pause
goto menu
