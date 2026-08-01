param(
    [switch]$NoBuild,
    [switch]$SkipHealthCheck,
    [string]$BackupDirectory
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    $dockerPath = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path $dockerPath) {
        $env:PATH = "C:\Program Files\Docker\Docker\resources\bin;$env:PATH"
        $docker = Get-Command docker -ErrorAction SilentlyContinue
    }
}
if (-not $docker) {
    throw "Docker was not found. Please install and start Docker Desktop."
}

$composeFiles = @("-f", "docker-compose.yml", "-f", "docker-compose.release.yml")
$services = @("waybill-parser", "backend", "tenant-ui", "platform-admin-ui")

Write-Host "Using compose files: docker-compose.yml + docker-compose.release.yml"
Write-Host "Business services: $($services -join ', ')"
Write-Host "Data volume: cargo-platform-data"
Write-Host ""

& docker compose @composeFiles config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose configuration is invalid."
}

if (-not $NoBuild) {
    Write-Host "Building business images..."
    & docker compose @composeFiles build @services
    if ($LASTEXITCODE -ne 0) {
        throw "Business image build failed."
    }
}

$composeConfig = (& docker compose @composeFiles config --format json) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Failed to resolve the backend image."
}
$backendImage = [string]$composeConfig.services.backend.image
& docker image inspect $backendImage *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Backend image was not found after build: $backendImage"
}

& docker volume inspect cargo-platform-data *> $null
if ($LASTEXITCODE -eq 0) {
    & docker run --rm --entrypoint python `
        --mount "type=volume,src=cargo-platform-data,dst=/data,readonly" `
        $backendImage `
        -c "import os; raise SystemExit(0 if os.path.isfile('/data/cargo-platform.db') else 3)"
    $databaseProbeExitCode = $LASTEXITCODE
    if ($databaseProbeExitCode -eq 0) {
        Write-Host "Creating verified online database snapshot..."
        $snapshotArgs = @(
            "-Action", "Backup",
            "-VolumeName", "cargo-platform-data",
            "-BackendImage", $backendImage
        )
        if (-not [string]::IsNullOrWhiteSpace($BackupDirectory)) {
            $snapshotArgs += @("-BackupDirectory", $BackupDirectory)
        }
        & (Join-Path $PSScriptRoot "sqlite_volume_snapshot.ps1") @snapshotArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Verified database snapshot failed; containers were not recreated."
        }
    }
    elseif ($databaseProbeExitCode -ne 3) {
        throw "Failed to inspect the existing release database."
    }
}

Write-Host "Recreating business containers..."
& docker compose @composeFiles up -d --no-deps @services
if ($LASTEXITCODE -ne 0) {
    throw "Business container recreation failed."
}

Write-Host ""
Write-Host "Container status:"
& docker compose @composeFiles ps

if (-not $SkipHealthCheck) {
    Write-Host ""
    Write-Host "Checking backend health..."
    $healthUrl = "http://127.0.0.1:8000/api/v1/health"
    $ok = $false
    for ($i = 1; $i -le 30; $i++) {
        try {
            $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
            Write-Host "Backend health OK: $($response | ConvertTo-Json -Compress)"
            $ok = $true
            break
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $ok) {
        throw "Backend health check failed: $healthUrl"
    }
}

Write-Host ""
Write-Host "Tenant UI:      http://127.0.0.1:5173/"
Write-Host "Tenant Admin:   http://127.0.0.1:5173/admin"
Write-Host "Platform Admin: http://127.0.0.1:5174/admin"
Write-Host "Backend API:    http://127.0.0.1:8000/api/v1/health"
