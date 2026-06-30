@echo off
rem Ensure node/npm are available for project scripts, even when Codex was opened
rem before Node.js was installed and the process PATH is stale.

where npm >nul 2>nul
if %errorlevel%==0 exit /b 0

if exist "%ProgramFiles%\nodejs\npm.cmd" (
  set "PATH=%ProgramFiles%\nodejs;%PATH%"
)

where npm >nul 2>nul
if %errorlevel%==0 exit /b 0

set "NODEJS_X86=%ProgramFiles(x86)%"
if not "%NODEJS_X86%"=="" (
  if exist "%NODEJS_X86%\nodejs\npm.cmd" (
    set "PATH=%NODEJS_X86%\nodejs;%PATH%"
  )
)

where npm >nul 2>nul
if %errorlevel%==0 exit /b 0

echo Node.js/npm was not found.
echo Install Node.js LTS, then reopen Codex or run scripts through this helper.
exit /b 1
