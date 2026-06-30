$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$frontendDir = Join-Path $repoRoot "frontend"

function Add-NodePathIfNeeded {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        return
    }

    $candidates = @()
    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles "nodejs"
    }
    $programFilesX86 = ${env:ProgramFiles(x86)}
    if ($programFilesX86) {
        $candidates += Join-Path $programFilesX86 "nodejs"
    }

    foreach ($candidate in $candidates) {
        if (Test-Path (Join-Path $candidate "npm.cmd")) {
            $env:PATH = "$candidate;$env:PATH"
            return
        }
    }
}

Add-NodePathIfNeeded

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "Node.js/npm was not found. Install Node.js LTS, then reopen Codex or run scripts through this helper."
}

Push-Location $frontendDir
try {
    if (-not (Test-Path "package-lock.json")) {
        throw "frontend/package-lock.json is required. This project uses npm, not pnpm or yarn."
    }
    if (Test-Path "pnpm-lock.yaml") {
        throw "Unexpected frontend/pnpm-lock.yaml. This project uses npm/package-lock.json only."
    }
    if (Test-Path "yarn.lock") {
        throw "Unexpected frontend/yarn.lock. This project uses npm/package-lock.json only."
    }

    if (-not (Test-Path "node_modules")) {
        Write-Host "Installing frontend dependencies with npm ci..."
        npm ci --no-audit --fund=false
    }

    npm run typecheck
}
finally {
    Pop-Location
}
