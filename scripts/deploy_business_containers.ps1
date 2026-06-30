param(
    [switch]$NoBuild,
    [switch]$SkipHealthCheck
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

Write-Host "Ensuring Redis is running..."
& docker compose @composeFiles up -d redis

if (-not $NoBuild) {
    Write-Host "Building business images..."
    & docker compose @composeFiles build @services
}

Write-Host "Recreating business containers..."
& docker compose @composeFiles up -d --no-deps @services

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
