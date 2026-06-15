@echo off
setlocal
cd /d "%~dp0.."

set "PY_EXE="

if exist ".venv\Scripts\python.exe" (
  set "PY_EXE=.venv\Scripts\python.exe"
  goto run_console
)

if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  set "PY_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  goto run_console
)

for /f "delims=" %%P in ('where python 2^>nul') do (
  if not defined PY_EXE set "PY_EXE=%%P"
)

if not defined PY_EXE (
  echo Python was not found. Cannot start server console.
  echo Please install Python 3.12+ or create .venv first.
  pause
  exit /b 1
)

:run_console
"%PY_EXE%" scripts\server_console.py %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Server console exited with code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
