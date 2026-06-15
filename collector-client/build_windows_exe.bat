@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

set "PY_EXE="
if not defined PY_EXE (
  if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    set "PY_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  )
)
if not defined PY_EXE if exist ".venv\Scripts\python.exe" set "PY_EXE=.venv\Scripts\python.exe"
if not defined PY_EXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    echo %%P | findstr /i /c:"\\WindowsApps\\python.exe" >nul
    if errorlevel 1 if not defined PY_EXE set "PY_EXE=%%P"
  )
)

if not defined PY_EXE (
  echo Python was not found. Build the collector exe on a development machine with Python.
  exit /b 1
)

set "COLLECTOR_ICON=%CD%\collector-client\assets\cargo-platform-collector.ico"
if not exist "%COLLECTOR_ICON%" (
  echo Collector icon was not found: %COLLECTOR_ICON%
  exit /b 1
)

"%PY_EXE%" -m pip install --upgrade pyinstaller
if errorlevel 1 exit /b 1

if exist "collector-client\dist\Cargo Platform 采集器.exe" del /f /q "collector-client\dist\Cargo Platform 采集器.exe"

echo Building Cargo Platform 采集器.exe...
"%PY_EXE%" -m PyInstaller ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "Cargo Platform 采集器" ^
  --icon "%COLLECTOR_ICON%" ^
  --distpath collector-client\dist ^
  --workpath storage\collector-build ^
  --specpath storage\collector-build ^
  collector-client\client.py

set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" (
  echo Built collector-client\dist\Cargo Platform 采集器.exe
) else (
  echo Collector build failed with code %EXIT_CODE%.
)
exit /b %EXIT_CODE%
