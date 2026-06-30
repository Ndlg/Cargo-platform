@echo off
setlocal
call "%~dp0node_env.bat" || exit /b 1
cd /d "%~dp0..\frontend"

if not exist "node_modules" (
  npm ci --no-audit --fund=false
)

npm run build:server-admin
