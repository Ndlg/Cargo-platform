param(
    [string]$BackupDirectory,
    [string]$Version
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$envFile = Join-Path $repoRoot ".env"
$envTemplate = Join-Path $repoRoot "deploy.env.example"
$installMarker = Join-Path $repoRoot ".cargo-platform-install-pending"

function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line.StartsWith("$Name=", [System.StringComparison]::Ordinal)) {
            return $line.Substring($Name.Length + 1)
        }
    }
    return $null
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $output = [System.Collections.Generic.List[string]]::new()
    $found = $false
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line.StartsWith("$Name=", [System.StringComparison]::Ordinal)) {
            $output.Add("$Name=$Value")
            $found = $true
        }
        else {
            $output.Add($line)
        }
    }
    if (-not $found) {
        $output.Add("$Name=$Value")
    }

    $temporaryFile = "$Path.tmp.$PID"
    [System.IO.File]::WriteAllLines($temporaryFile, $output, [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporaryFile -Destination $Path -Force
}

function New-RandomHexSecret {
    return [Convert]::ToHexString(
        [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    ).ToLowerInvariant()
}

function Get-InstalledContainerCount {
    $count = 0
    foreach ($containerName in @(
        "cargo-platform-waybill-parser",
        "cargo-platform-backend",
        "cargo-platform-tenant-ui",
        "cargo-platform-admin-ui"
    )) {
        $containerId = @(& docker ps -a -q --filter "name=^/${containerName}$") | Select-Object -First 1
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to inspect existing container: $containerName"
        }
        if ($null -eq $containerId) {
            $containerId = ""
        }
        else {
            $containerId = ([string]$containerId).Trim()
        }
        if ($containerId) {
            $count += 1
        }
    }
    return $count
}

function Assert-NoActiveCapture {
    param([Parameter(Mandatory = $true)][string]$BackendImage)

    $activeCaptureProbe = @'
import sqlite3
database = sqlite3.connect("file:/data/cargo-platform.db?mode=ro", uri=True)
row = database.execute("SELECT status FROM capture_tasks WHERE status IN ('collecting','stopping') AND is_deleted = 0 LIMIT 1").fetchone()
print(row[0] if row else "")
'@
    $activeCaptureStatus = ([string](& docker run --rm --entrypoint python `
        --mount "type=volume,src=cargo-platform-data,dst=/data,readonly" `
        $BackendImage -c $activeCaptureProbe)).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to verify whether a capture task is active."
    }
    if ($activeCaptureStatus -in @("collecting", "stopping")) {
        throw "Active capture task exists ($activeCaptureStatus); stop collection before deployment."
    }
}

function Assert-DatabaseIntegrity {
    param([Parameter(Mandatory = $true)][string]$BackendImage)

    $databaseIntegrityProbe = @'
from pathlib import Path
import sqlite3

database_path = Path("/data/cargo-platform.db")
if not database_path.is_file():
    raise RuntimeError("production database is missing")
database = sqlite3.connect("file:/data/cargo-platform.db?mode=ro", uri=True)
if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    raise RuntimeError("database integrity check failed")
for table in ("capture_tasks", "raw_capture_records", "products"):
    database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
database.close()
print("ok")
'@
    $integrityResult = ([string](& docker run --rm --entrypoint python `
        --mount "type=volume,src=cargo-platform-data,dst=/data,readonly" `
        $BackendImage -c $databaseIntegrityProbe)).Trim()
    if ($LASTEXITCODE -ne 0 -or $integrityResult -ne "ok") {
        throw "Target database integrity verification failed."
    }
}

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

$composeFiles = @("-f", "docker-compose.release.yml")
$services = @("waybill-parser", "backend", "tenant-ui", "platform-admin-ui")
$deploymentStamp = Get-Date -Format "yyyyMMdd-HHmmss"
if (-not (Test-Path -LiteralPath $envTemplate -PathType Leaf)) {
    throw "deploy.env.example was not found."
}
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = Get-EnvValue -Path $envTemplate -Name "CARGO_PLATFORM_VERSION"
}
if ([string]::IsNullOrWhiteSpace($Version)) {
    throw "deploy.env.example has no CARGO_PLATFORM_VERSION."
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Version must be a semantic version."
}
if ([string]::IsNullOrWhiteSpace($BackupDirectory)) {
    $BackupDirectory = Join-Path (Split-Path $repoRoot -Parent) "cargo-platform-deploy-backups"
}

$deploymentMutexName = "cargo-platform-deploy-mutex"
$mutexImageReference = "ghcr.io/ndlg/cargo-platform-backend:$Version"
& docker pull $mutexImageReference *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to pull the target backend image required for the deployment mutex."
}
$mutexImage = @(& docker image inspect --format "{{.Id}}" $mutexImageReference) |
    Select-Object -First 1
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$mutexImage)) {
    throw "Failed to inspect the target backend image required for the deployment mutex."
}
$mutexImage = ([string]$mutexImage).Trim()
& docker run -d --name $deploymentMutexName --entrypoint python `
    $mutexImage -c "import threading; threading.Event().wait()" *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to acquire the deployment mutex; another deployment may be running. If a previous Windows deployment was forcibly terminated, inspect the deployment state, then run 'docker rm -f cargo-platform-deploy-mutex' before retrying."
}
$deploymentMutexAcquired = $true
$preserveDeploymentMutex = $false
$deploymentLockName = "cargo-platform-deploy-db-lock"
$deploymentLockAcquired = $false

try {
$installedContainerCount = Get-InstalledContainerCount
& docker volume inspect cargo-platform-data *> $null
$volumeExists = $LASTEXITCODE -eq 0
$resumeInstall = $false
$needsNewInstallMarker = $false
$needsEnvCopy = $false
if ($volumeExists) {
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        throw "Existing data volume cargo-platform-data has no .env; refusing deployment."
    }
    if (Test-Path -LiteralPath $installMarker -PathType Leaf) {
        $isUpgrade = $false
        $resumeInstall = $true
    }
    else {
        $isUpgrade = $true
    }
}
else {
    $isUpgrade = $false
    if ($installedContainerCount -ne 0) {
        throw "Existing containers have no cargo-platform-data volume; refusing deployment."
    }
    if (Test-Path -LiteralPath $installMarker -PathType Leaf) {
        if (-not (Test-Path -LiteralPath $envFile)) {
            $needsEnvCopy = $true
        }
    }
    else {
        if (Test-Path -LiteralPath $envFile) {
            throw "Existing .env was found but cargo-platform-data is missing and no install marker exists; refusing an empty replacement."
        }
        $needsNewInstallMarker = $true
        $needsEnvCopy = $true
    }
}

if ($resumeInstall -and $volumeExists) {
    $pendingVersion = Get-EnvValue -Path $envFile -Name "CARGO_PLATFORM_VERSION"
    if ([string]::IsNullOrWhiteSpace($pendingVersion)) {
        throw "Pending first installation has no CARGO_PLATFORM_VERSION; refusing an unsafe resume."
    }
    if ($pendingVersion -ne $Version) {
        throw "Pending first installation has data for $pendingVersion; rerun with -Version $pendingVersion to complete the original version before upgrading."
    }
}

New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
$BackupDirectory = (Resolve-Path $BackupDirectory).Path

if (-not $isUpgrade) {
    if ($needsNewInstallMarker) {
        New-Item -ItemType File -Path $installMarker -Force | Out-Null
    }
    if ($needsEnvCopy) {
        Copy-Item -LiteralPath $envTemplate -Destination $envFile
    }
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        throw ".env is not a regular file."
    }
    Set-EnvValue -Path $envFile -Name "CARGO_PLATFORM_VERSION" -Value $Version
    foreach ($secretName in @("SECRET_KEY", "COLLECTOR_TOKEN_HASH_KEY", "INITIAL_SETUP_TOKEN")) {
        if ([string]::IsNullOrWhiteSpace((Get-EnvValue -Path $envFile -Name $secretName))) {
            Set-EnvValue -Path $envFile -Name $secretName -Value (New-RandomHexSecret)
        }
    }
}

foreach ($environmentName in @(
    "SECRET_KEY",
    "COLLECTOR_TOKEN_HASH_KEY",
    "COLLECTOR_TOKEN_PREVIOUS_HASH_KEY",
    "INITIAL_SETUP_TOKEN",
    "CORS_ORIGINS"
)) {
    [Environment]::SetEnvironmentVariable($environmentName, $null, "Process")
}
$env:CARGO_PLATFORM_VERSION = $Version

Write-Host "Using compose file: docker-compose.release.yml"
Write-Host "Business services: $($services -join ', ')"
Write-Host "Data volume: cargo-platform-data"
Write-Host "Release version: $Version"
Write-Host ""

& docker compose @composeFiles config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose configuration is invalid."
}

if ($resumeInstall) {
    & docker compose @composeFiles rm -f -s @services *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clean incomplete first-install containers."
    }
}

$composeConfig = (& docker compose @composeFiles config --format json) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Failed to resolve the backend image."
}
$backendImage = [string]$composeConfig.services.backend.image
$releaseVersion = [string]$composeConfig.services.backend.environment.APP_VERSION
if ([string]::IsNullOrWhiteSpace($releaseVersion)) {
    throw "Resolved release configuration has no APP_VERSION."
}
if ($releaseVersion -ne $Version) {
    throw "Resolved release version $releaseVersion does not match requested version $Version."
}
$targetImages = [ordered]@{}
foreach ($service in $services) {
    $targetImage = [string]$composeConfig.services.$service.image
    if (-not $targetImage.EndsWith(":$releaseVersion", [System.StringComparison]::Ordinal)) {
        throw "Service $service does not use release version ${releaseVersion}: $targetImage"
    }
    $targetImages[$service] = $targetImage
}

$previousImages = [ordered]@{}
$previousImageReferences = [ordered]@{}
$previousAppVersions = [ordered]@{}
$previousContainerIds = [ordered]@{}
foreach ($service in $services) {
    $containerId = @(& docker compose @composeFiles ps -a -q $service) | Select-Object -First 1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect current service: $service"
    }
    if ($null -eq $containerId) {
        $containerId = ""
    }
    else {
        $containerId = ([string]$containerId).Trim()
    }
    if ($containerId) {
        $previousContainerIds[$service] = $containerId
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
if ($isUpgrade -and $previousImages.Count -ne $services.Count) {
    throw "Existing release is incomplete; refusing an upgrade until all four services are present."
}
if (-not $isUpgrade -and $previousImages.Count -ne 0) {
    throw "Incomplete first-install containers could not be removed safely."
}

$envBackupFile = $null
$previousEnvVersion = $null
$previousEnvValues = [ordered]@{}
if ($isUpgrade) {
    $previousEnvVersion = Get-EnvValue -Path $envFile -Name "CARGO_PLATFORM_VERSION"
    if ([string]::IsNullOrWhiteSpace($previousEnvVersion)) {
        throw "Existing .env has no CARGO_PLATFORM_VERSION; refusing deployment."
    }
    foreach ($environmentName in @(
        "CARGO_PLATFORM_VERSION",
        "SECRET_KEY",
        "COLLECTOR_TOKEN_HASH_KEY",
        "COLLECTOR_TOKEN_PREVIOUS_HASH_KEY",
        "INITIAL_SETUP_TOKEN",
        "CORS_ORIGINS"
    )) {
        $previousEnvValues[$environmentName] = Get-EnvValue -Path $envFile -Name $environmentName
    }
    $envBackupFile = Join-Path $BackupDirectory ".env-$deploymentStamp"
    Copy-Item -LiteralPath $envFile -Destination $envBackupFile -Force
    Assert-NoActiveCapture -BackendImage $previousImages["backend"]
}

Write-Host "Pulling version-matched release images..."
& docker compose @composeFiles pull @services
if ($LASTEXITCODE -ne 0) {
    throw "Release image pull failed."
}

$targetImageIds = [ordered]@{}
foreach ($service in $services) {
    $targetImageIds[$service] = ([string](& docker image inspect --format "{{.Id}}" $targetImages[$service])).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to resolve target image ID for service: $service"
    }
}

$rollbackFile = $null
if ($isUpgrade) {
    $rollbackFile = Join-Path $BackupDirectory "docker-compose.rollback-$deploymentStamp.yml"
    $rollbackLines = @("services:")
    foreach ($service in $previousImages.Keys) {
        $escapedImage = ([string]$previousImages[$service]).Replace("'", "''")
        $rollbackLines += "  ${service}:"
        $rollbackLines += "    image: '$escapedImage'"
        if ($service -eq "backend") {
            $rollbackLines += "    healthcheck:"
            $rollbackLines += '      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen(''http://127.0.0.1:8000/api/v1/health'', timeout=3)"]'
        }
        if ($previousAppVersions.Contains($service)) {
            $escapedVersion = ([string]$previousAppVersions[$service]).Replace("'", "''")
            $rollbackLines += "    environment:"
            $rollbackLines += "      APP_VERSION: '$escapedVersion'"
        }
    }
    [System.IO.File]::WriteAllLines($rollbackFile, $rollbackLines, [System.Text.UTF8Encoding]::new($false))
}

$snapshotRecord = $null
$oldServicesStopped = $false
try {
    if ($isUpgrade) {
        Assert-NoActiveCapture -BackendImage $previousImages["backend"]

        $databaseLockProbe = @'
import sqlite3
import threading

database = sqlite3.connect("/data/cargo-platform.db", timeout=30, isolation_level=None)
database.execute("BEGIN EXCLUSIVE")
row = database.execute("SELECT status FROM capture_tasks WHERE status IN ('collecting','stopping') AND is_deleted = 0 LIMIT 1").fetchone()
print("LOCKED:" + (row[0] if row else ""), flush=True)
threading.Event().wait()
'@
        & docker run -d --name $deploymentLockName --entrypoint python `
            --mount "type=volume,src=cargo-platform-data,dst=/data" `
            $previousImages["backend"] -c $databaseLockProbe *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to acquire the deployment database lock."
        }
        $deploymentLockAcquired = $true
        $deploymentLockStatus = $null
        for ($attempt = 0; $attempt -lt 35; $attempt += 1) {
            $lockLine = @(& docker logs $deploymentLockName 2>$null) | Select-Object -Last 1
            if ($null -eq $lockLine) {
                $lockLine = ""
            }
            else {
                $lockLine = ([string]$lockLine).Trim()
            }
            if ($lockLine.StartsWith("LOCKED:", [System.StringComparison]::Ordinal)) {
                $deploymentLockStatus = $lockLine.Substring("LOCKED:".Length).Trim()
                break
            }
            $lockRunning = @(& docker inspect --format "{{.State.Running}}" $deploymentLockName 2>$null) |
                Select-Object -First 1
            if ($null -eq $lockRunning) {
                $lockRunning = ""
            }
            else {
                $lockRunning = ([string]$lockRunning).Trim()
            }
            if ($lockRunning -ne "true") {
                break
            }
            Start-Sleep -Seconds 1
        }
        if ($null -eq $deploymentLockStatus) {
            throw "Failed to confirm the deployment database lock."
        }
        if ($deploymentLockStatus -in @("collecting", "stopping")) {
            throw "Active capture task exists ($deploymentLockStatus); stop collection before deployment."
        }

        Write-Warning "Do not interrupt this PowerShell upgrade. Automatic Ctrl-C or host-termination recovery is not guaranteed; deploy_server.sh is the official release upgrade entrypoint."
        Write-Host "Manual recovery .env backup: $envBackupFile"
        Write-Host "Manual recovery compose:    $rollbackFile"
        $oldServicesStopped = $true
        & docker compose @composeFiles stop tenant-ui platform-admin-ui
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to stop the previous user entry points safely."
        }
        & docker compose @composeFiles stop backend waybill-parser
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to stop the previous backend services safely."
        }
        & docker rm -f $deploymentLockName *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to release the deployment database lock."
        }
        $deploymentLockAcquired = $false

        & docker run --rm --entrypoint python `
            --mount "type=volume,src=cargo-platform-data,dst=/data,readonly" `
            $previousImages["backend"] `
            -c "import os; raise SystemExit(0 if os.path.isfile('/data/cargo-platform.db') else 3)"
        $databaseProbeExitCode = $LASTEXITCODE
        if ($databaseProbeExitCode -eq 3) {
            throw "Production database cargo-platform.db does not exist; refusing deployment."
        }
        if ($databaseProbeExitCode -ne 0) {
            throw "Failed to inspect the existing release database."
        }

        Write-Host "Creating verified database snapshot..."
        $snapshotArgs = @{
            Action = "Backup"
            VolumeName = "cargo-platform-data"
            BackendImage = $previousImages["backend"]
        }
        if (-not [string]::IsNullOrWhiteSpace($BackupDirectory)) {
            $snapshotArgs.BackupDirectory = $BackupDirectory
        }
        $snapshotOutput = & (Join-Path $PSScriptRoot "sqlite_volume_snapshot.ps1") @snapshotArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Verified database snapshot failed; containers were not recreated."
        }
        $snapshotRecord = $snapshotOutput | Select-Object -Last 1 | ConvertFrom-Json
        Write-Host ($snapshotRecord | ConvertTo-Json -Compress)
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
}
catch {
    $preDeploymentError = $_
    if ($deploymentLockAcquired) {
        & docker rm -f $deploymentLockName *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Pre-deployment verification failed and the database lock could not be released. Original error: $preDeploymentError"
        }
        $deploymentLockAcquired = $false
    }
    if ($oldServicesStopped) {
        & docker compose @composeFiles start @services
        if ($LASTEXITCODE -ne 0) {
            throw "Pre-deployment verification failed and the previous services could not be restarted. Original error: $preDeploymentError"
        }
        $oldServicesStopped = $false
    }
    throw $preDeploymentError
}

Write-Host "Recreating business containers..."
$deploymentStarted = $false
try {
    if ($isUpgrade) {
        Set-EnvValue -Path $envFile -Name "CARGO_PLATFORM_VERSION" -Value $Version
    }
    else {
        if (-not (Test-Path -LiteralPath $installMarker -PathType Leaf)) {
            throw "First-install marker is missing."
        }
        if (-not $volumeExists) {
            & docker volume create cargo-platform-data *> $null
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to create data volume cargo-platform-data."
            }
            $volumeExists = $true
        }
    }

    $deploymentStarted = $true
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
    Assert-DatabaseIntegrity -BackendImage $targetImageIds["backend"]
    foreach ($url in "http://127.0.0.1:5173/", "http://127.0.0.1:5174/") {
        $response = Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing
        if ($response.StatusCode -ne 200) {
            throw "UI readiness check failed: $url"
        }
    }
}
catch {
    $deploymentError = $_
    if ($isUpgrade) {
        $rollbackErrors = [System.Collections.Generic.List[string]]::new()
        $environmentRestored = $false
        $imagesRestored = $true
        $readinessRestored = $false

        try {
            if ([string]::IsNullOrWhiteSpace([string]$envBackupFile) -or
                -not (Test-Path -LiteralPath $envBackupFile -PathType Leaf)) {
                throw "The previous .env backup is missing."
            }
            $backupHash = (Get-FileHash -LiteralPath $envBackupFile -Algorithm SHA256).Hash
            Copy-Item -LiteralPath $envBackupFile -Destination $envFile -Force -ErrorAction Stop
            if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
                throw "The restored .env is not a regular file."
            }
            $restoredHash = (Get-FileHash -LiteralPath $envFile -Algorithm SHA256).Hash
            if ($restoredHash -ne $backupHash) {
                throw "The restored .env does not match its backup."
            }
            $restoredVersion = Get-EnvValue -Path $envFile -Name "CARGO_PLATFORM_VERSION"
            if ([string]::IsNullOrWhiteSpace($restoredVersion) -or $restoredVersion -ne $previousEnvVersion) {
                throw "The previous .env version could not be verified."
            }
            $environmentRestored = $true
        }
        catch {
            $rollbackErrors.Add("Environment restore failed: $($_.Exception.Message)")
        }

        foreach ($environmentName in $previousEnvValues.Keys) {
            try {
                [Environment]::SetEnvironmentVariable(
                    $environmentName,
                    [string]$previousEnvValues[$environmentName],
                    "Process"
                )
            }
            catch {
                $imagesRestored = $false
                $rollbackErrors.Add("Rollback compose environment failed for ${environmentName}: $($_.Exception.Message)")
            }
        }

        Write-Warning "Deployment failed; restoring the previous four images. Database was not rolled back."
        try {
            & docker compose --env-file $envTemplate @composeFiles -f $rollbackFile `
                up -d --no-deps --wait --wait-timeout 180 @services
            if ($LASTEXITCODE -ne 0) {
                throw "The previous image compose operation failed."
            }
        }
        catch {
            $imagesRestored = $false
            $rollbackErrors.Add("Image restore failed: $($_.Exception.Message)")
        }
        foreach ($service in $services) {
            try {
                $containerId = @(& docker compose --env-file $envTemplate @composeFiles ps -q $service) |
                    Select-Object -First 1
                if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$containerId)) {
                    throw "No restored container was found."
                }
                $containerId = ([string]$containerId).Trim()
                $restoredImage = @(& docker inspect --format "{{.Image}}" $containerId) |
                    Select-Object -First 1
                if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$restoredImage)) {
                    throw "The restored image could not be inspected."
                }
                $restoredImage = ([string]$restoredImage).Trim()
                if ($restoredImage -ne $previousImages[$service]) {
                    throw "The running image does not match the previous image."
                }
            }
            catch {
                $imagesRestored = $false
                $rollbackErrors.Add("Image rollback verification failed for ${service}: $($_.Exception.Message)")
            }
        }
        $rollbackProbe = @'
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import urllib.request

database_path = Path("/data/cargo-platform.db")
if not database_path.is_file():
    raise RuntimeError("production database is missing")
database = sqlite3.connect("file:/data/cargo-platform.db?mode=ro", uri=True)
if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    raise RuntimeError("database integrity check failed")
for table in ("capture_tasks", "raw_capture_records", "products", "users", "collectors"):
    database.execute(f"SELECT count(*) FROM {table}").fetchone()
database.close()

storage = Path(os.environ.get("STORAGE_ROOT", "/data/workspaces"))
storage.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(dir=storage, prefix=".rollback-ready-", delete=True) as probe:
    probe.write(b"ok")
    probe.flush()

with urllib.request.urlopen("http://waybill-parser:8010/health", timeout=5) as response:
    parser = json.load(response)
if parser.get("status") != "ok":
    raise RuntimeError("parser health check failed")

with urllib.request.urlopen("http://127.0.0.1:8000/api/v1/ready", timeout=5) as response:
    backend = json.load(response)
if backend.get("status") != "ready":
    raise RuntimeError("backend readiness check failed")
'@
        try {
            $rollbackProbeBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($rollbackProbe))
            & docker exec -e "ROLLBACK_READINESS_PROBE=$rollbackProbeBase64" cargo-platform-backend `
                python -c "import base64, os; exec(base64.b64decode(os.environ['ROLLBACK_READINESS_PROBE']))"
            if ($LASTEXITCODE -ne 0) {
                throw "The restored database or services did not pass readiness checks."
            }
            $readinessRestored = $true
        }
        catch {
            $rollbackErrors.Add("Rollback readiness verification failed: $($_.Exception.Message)")
        }

        if ($environmentRestored -and $imagesRestored -and $readinessRestored -and $rollbackErrors.Count -eq 0) {
            Write-Warning "Deployment failed; previous release restored and verified."
            throw $deploymentError
        }

        $preserveDeploymentMutex = $true
        $rollbackDetails = $rollbackErrors -join "; "
        throw "Deployment failed and rollback was incomplete; deployment mutex retained for fail-closed recovery. Inspect the deployment state before running 'docker rm -f cargo-platform-deploy-mutex'. Original error: $deploymentError Rollback errors: $rollbackDetails"
    }
    else {
        & docker compose @composeFiles rm -f -s @services
    }
    throw $deploymentError
}

if (Test-Path -LiteralPath $installMarker -PathType Leaf) {
    Remove-Item -LiteralPath $installMarker -Force
}
}
finally {
    $cleanupFailure = $null
    if ($deploymentLockAcquired) {
        & docker rm -f $deploymentLockName *> $null
        if ($LASTEXITCODE -ne 0) {
            $cleanupFailure = "The deployment database lock could not be removed."
        }
        else {
            $deploymentLockAcquired = $false
        }
    }
    if ($deploymentMutexAcquired -and -not $preserveDeploymentMutex) {
        & docker rm -f $deploymentMutexName *> $null
        if ($LASTEXITCODE -ne 0) {
            $cleanupFailure = "The deployment mutex could not be removed."
        }
        else {
            $deploymentMutexAcquired = $false
        }
    }
    if ($cleanupFailure) {
        throw $cleanupFailure
    }
}

Write-Host ""
Write-Host "Container status:"
& docker compose @composeFiles ps

Write-Host ""
Write-Host "Deployment manifest: $manifestFile"
if ($rollbackFile) {
    Write-Host "Rollback compose:    $rollbackFile"
}
Write-Host "Tenant UI:      http://127.0.0.1:5173/"
Write-Host "Tenant Admin:   http://127.0.0.1:5173/admin"
Write-Host "Platform Admin: http://127.0.0.1:5174/admin"
Write-Host "Backend API:    http://127.0.0.1:8000/api/v1/health"
