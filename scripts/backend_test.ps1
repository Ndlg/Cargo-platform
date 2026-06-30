$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$backendDir = Join-Path $repoRoot "backend"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

function Resolve-BasePython {
    $candidates = @()

    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    }
    $codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    $candidates += $codexPython

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand -and ($pythonCommand.Source -notlike "*\WindowsApps\python.exe")) {
        return $pythonCommand.Source
    }

    throw "Python 3.12 was not found. Install Python 3.12 or use the Codex bundled runtime."
}

if (-not (Test-Path $venvPython)) {
    $basePython = Resolve-BasePython
    Write-Host "Creating backend virtual environment with $basePython..."
    & $basePython -m venv (Join-Path $repoRoot ".venv")
}

Write-Host "Installing backend dependencies..."
& $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $backendDir "requirements.txt")

$env:PYTHONPATH = $backendDir

if (-not $env:DATABASE_URL) {
    $pytestCacheDir = Join-Path $repoRoot ".pytest_cache"
    New-Item -ItemType Directory -Force -Path $pytestCacheDir | Out-Null
    $testDb = Join-Path $pytestCacheDir "backend_test.db"
    if (Test-Path $testDb) {
        Remove-Item -LiteralPath $testDb -Force
    }
    $env:DATABASE_URL = "sqlite:///$($testDb.Replace('\', '/'))"
}

if (-not $env:STORAGE_ROOT) {
    $env:STORAGE_ROOT = (Join-Path $repoRoot "storage\pytest").Replace('\', '/')
}

if (-not $env:AUTO_CREATE_TABLES) {
    $env:AUTO_CREATE_TABLES = "true"
}

if ($args.Count -eq 0) {
    & $venvPython -m pytest (Join-Path $backendDir "tests")
}
else {
    & $venvPython -m pytest @args
}
