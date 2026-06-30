@echo off
setlocal
call "%~dp0node_env.bat" || exit /b 1
cd /d "%~dp0..\frontend" || exit /b 1

if not exist "package-lock.json" (
  echo frontend/package-lock.json is required. This project uses npm, not pnpm or yarn.
  exit /b 1
)

if exist "pnpm-lock.yaml" (
  echo Unexpected frontend/pnpm-lock.yaml. This project uses npm/package-lock.json only.
  exit /b 1
)

if exist "yarn.lock" (
  echo Unexpected frontend/yarn.lock. This project uses npm/package-lock.json only.
  exit /b 1
)

if not exist "node_modules" (
  echo Installing frontend dependencies with npm ci...
  npm ci --no-audit --fund=false || exit /b 1
)

npm run typecheck
