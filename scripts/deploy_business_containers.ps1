param(
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
$deploymentStamp = Get-Date -Format "yyyyMMdd-HHmmss"
if ([string]::IsNullOrWhiteSpace($BackupDirectory)) {
    $BackupDirectory = Join-Path (Split-Path $repoRoot -Parent) "cargo-platform-deploy-backups"
}
New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
$BackupDirectory = (Resolve-Path $BackupDirectory).Path

Write-Host "Using compose files: docker-compose.yml + docker-compose.release.yml"
Write-Host "Business services: $($services -join ', ')"
Write-Host "Data volume: cargo-platform-data"
Write-Host ""

& docker compose @composeFiles config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose configuration is invalid."
}

Write-Host "Pulling version-matched release images..."
& docker compose @composeFiles pull @services
if ($LASTEXITCODE -ne 0) {
    throw "Release image pull failed."
}

$composeConfig = (& docker compose @composeFiles config --format json) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Failed to resolve the backend image."
}
$backendImage = [string]$composeConfig.services.backend.image
& docker image inspect $backendImage *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Backend image was not found after pull: $backendImage"
}

$releaseVersion = [string]$composeConfig.services.backend.environment.APP_VERSION
if ([string]::IsNullOrWhiteSpace($releaseVersion)) {
    throw "Resolved release configuration has no APP_VERSION."
}
$targetImages = [ordered]@{}
$targetImageIds = [ordered]@{}
foreach ($service in $services) {
    $targetImage = [string]$composeConfig.services.$service.image
    if (-not $targetImage.EndsWith(":$releaseVersion", [System.StringComparison]::Ordinal)) {
        throw "Service $service does not use release version ${releaseVersion}: $targetImage"
    }
    $targetImages[$service] = $targetImage
    $targetImageIds[$service] = ([string](& docker image inspect --format "{{.Id}}" $targetImage)).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to resolve target image ID for service: $service"
    }
}

$previousImages = [ordered]@{}
$previousImageReferences = [ordered]@{}
$previousAppVersions = [ordered]@{}
foreach ($service in $services) {
    $containerId = @(& docker compose @composeFiles ps -a -q $service) | Select-Object -First 1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect current service: $service"
    }
    $containerId = ([string]$containerId).Trim()
    if ($containerId) {
        $previousImageReferences[$service] = (& docker inspect --format "{{.Config.Image}}" $containerId).Trim()
        $previousImages[$service] = (& docker inspect --format "{{.Image}}" $containerId).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to inspect current image for service: $service"
        }
        $appVersion = @(& docker inspect --format "{{range .Config.Env}}{{println .}}{{end}}" $containerId) |
            Where-Object { $_ -like "APP_VERSION=*" } |
            Select-Object -First 1
        if ($appVersion) {
            $previousAppVersions[$service] = ([string]$appVersion).Substring("APP_VERSION=".Length)
        }
    }
}
if ($previousImages.Count -gt 0 -and $previousImages.Count -ne $services.Count) {
    throw "Existing release is incomplete; refusing an upgrade until all four services are present."
}

$rollbackFile = Join-Path $BackupDirectory "docker-compose.rollback-$deploymentStamp.yml"
$rollbackLines = @("services:")
foreach ($service in $previousImages.Keys) {
    $escapedImage = ([string]$previousImages[$service]).Replace("'", "''")
    $rollbackLines += "  ${service}:"
    $rollbackLines += "    image: '$escapedImage'"
    $rollbackLines += "    healthcheck:"
    $rollbackLines += "      disable: true"
    if ($previousAppVersions.Contains($service)) {
        $escapedVersion = ([string]$previousAppVersions[$service]).Replace("'", "''")
        $rollbackLines += "    environment:"
        $rollbackLines += "      APP_VERSION: '$escapedVersion'"
    }
}
[System.IO.File]::WriteAllLines($rollbackFile, $rollbackLines, [System.Text.UTF8Encoding]::new($false))

$snapshotRecord = $null
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
        $snapshotOutput = & (Join-Path $PSScriptRoot "sqlite_volume_snapshot.ps1") @snapshotArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Verified database snapshot failed; containers were not recreated."
        }
        $snapshotRecord = $snapshotOutput | Select-Object -Last 1 | ConvertFrom-Json
        Write-Host ($snapshotRecord | ConvertTo-Json -Compress)
    }
    elseif ($databaseProbeExitCode -ne 3) {
        throw "Failed to inspect the existing release database."
    }
}
else {
    & docker volume create cargo-platform-data | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the external data volume."
    }
}

$deploymentManifest = [ordered]@{
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    release_version = $releaseVersion
    rollback_compose = $rollbackFile
    previous_image_references = $previousImageReferences
    previous_image_ids = $previousImages
    previous_app_versions = $previousAppVersions
    target_images = $targetImages
    target_image_ids = $targetImageIds
    database_snapshot = $snapshotRecord
}
$manifestFile = Join-Path $BackupDirectory "deployment-$deploymentStamp.json"
[System.IO.File]::WriteAllText(
    $manifestFile,
    ($deploymentManifest | ConvertTo-Json -Depth 8),
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Recreating business containers..."
try {
    & docker compose @composeFiles up -d --wait --wait-timeout 180 @services
    if ($LASTEXITCODE -ne 0) {
        throw "Business container recreation or readiness check failed."
    }

    $backendReady = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/ready" -TimeoutSec 5
    $parserReady = Invoke-RestMethod -Uri "http://127.0.0.1:8010/health" -TimeoutSec 5
    if ($backendReady.status -ne "ready" -or $backendReady.version -ne $releaseVersion) {
        throw "Backend readiness or version check failed."
    }
    if ($parserReady.status -ne "ok" -or $parserReady.version -ne $releaseVersion) {
        throw "Parser readiness or version check failed."
    }
    foreach ($service in $services) {
        $containerId = @(& docker compose @composeFiles ps -q $service) | Select-Object -First 1
        $containerId = ([string]$containerId).Trim()
        $runningImage = (& docker inspect --format "{{.Image}}" $containerId).Trim()
        if ($LASTEXITCODE -ne 0 -or $runningImage -ne $targetImageIds[$service]) {
            throw "Running image does not match the release manifest for service ${service}."
        }
    }
    foreach ($url in "http://127.0.0.1:5173/", "http://127.0.0.1:5174/") {
        $response = Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing
        if ($response.StatusCode -ne 200) {
            throw "UI readiness check failed: $url"
        }
    }
}
catch {
    $deploymentError = $_
    if ($previousImages.Count -eq $services.Count) {
        Write-Warning "Deployment failed; restoring the previous four images. Database was not rolled back."
        & docker compose @composeFiles -f $rollbackFile up -d --no-deps --wait --wait-timeout 180 @services
        if ($LASTEXITCODE -ne 0) {
            throw "Deployment failed and the previous images could not be restored. Original error: $deploymentError"
        }
        foreach ($service in $services) {
            $containerId = @(& docker compose @composeFiles ps -q $service) | Select-Object -First 1
            $containerId = ([string]$containerId).Trim()
            $restoredImage = (& docker inspect --format "{{.Image}}" $containerId).Trim()
            if ($LASTEXITCODE -ne 0 -or $restoredImage -ne $previousImages[$service]) {
                throw "Deployment failed and rollback verification failed for service ${service}. Original error: $deploymentError"
            }
        }
    }
    else {
        & docker compose @composeFiles down --remove-orphans
    }
    throw $deploymentError
}

Write-Host ""
Write-Host "Container status:"
& docker compose @composeFiles ps

Write-Host ""
Write-Host "Deployment manifest: $manifestFile"
Write-Host "Rollback compose:    $rollbackFile"
Write-Host "Tenant UI:      http://127.0.0.1:5173/"
Write-Host "Tenant Admin:   http://127.0.0.1:5173/admin"
Write-Host "Platform Admin: http://127.0.0.1:5174/admin"
Write-Host "Backend API:    http://127.0.0.1:8000/api/v1/health"
