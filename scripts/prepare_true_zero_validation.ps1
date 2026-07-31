param(
    [ValidateSet("candidate", "rollback")]
    [string]$Stage = "candidate",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$candidateNames = @(
    "VALIDATION_APP_VERSION",
    "VALIDATION_BACKEND_IMAGE",
    "VALIDATION_PARSER_IMAGE",
    "VALIDATION_AI_IMAGE",
    "VALIDATION_UI_IMAGE",
    "VALIDATION_PLATFORM_VOLUME",
    "VALIDATION_REDIS_VOLUME",
    "VALIDATION_AI_SESSION_VOLUME"
)
$rollbackNames = @(
    "ROLLBACK_APP_VERSION",
    "ROLLBACK_BACKEND_IMAGE",
    "ROLLBACK_PARSER_IMAGE",
    "ROLLBACK_AI_IMAGE",
    "ROLLBACK_UI_IMAGE",
    "ROLLBACK_PLATFORM_VOLUME",
    "ROLLBACK_REDIS_VOLUME",
    "ROLLBACK_AI_SESSION_VOLUME"
)
foreach ($name in @("AI_RECOGNITION_INTERNAL_TOKEN") + $candidateNames + $rollbackNames) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "Missing environment variable: $name"
    }
}

$candidateVolumes = @(
    $env:VALIDATION_PLATFORM_VOLUME,
    $env:VALIDATION_REDIS_VOLUME,
    $env:VALIDATION_AI_SESSION_VOLUME
)
$rollbackVolumes = @(
    $env:ROLLBACK_PLATFORM_VOLUME,
    $env:ROLLBACK_REDIS_VOLUME,
    $env:ROLLBACK_AI_SESSION_VOLUME
)
$allVolumes = @($candidateVolumes + $rollbackVolumes)
if (($allVolumes | Sort-Object -Unique).Count -ne $allVolumes.Count) {
    throw "Candidate and rollback volumes must be six distinct volumes."
}
$roleChecks = @(
    @($env:VALIDATION_PLATFORM_VOLUME, "^cargo-platform-validation-zero-platform-.+$", "candidate platform"),
    @($env:VALIDATION_REDIS_VOLUME, "^cargo-platform-validation-zero-redis-.+$", "candidate redis"),
    @($env:VALIDATION_AI_SESSION_VOLUME, "^cargo-platform-validation-zero-ai-.+$", "candidate AI"),
    @($env:ROLLBACK_PLATFORM_VOLUME, "^cargo-platform-validation-(?:(?:adaptive-)?data|ai-data)-.+$", "rollback platform"),
    @($env:ROLLBACK_REDIS_VOLUME, "^cargo-platform-validation-(?:adaptive-)?redis-.+$", "rollback redis"),
    @($env:ROLLBACK_AI_SESSION_VOLUME, "^cargo-platform-validation-(?:adaptive-)?ai-sessions-.+$", "rollback AI")
)
foreach ($check in $roleChecks) {
    if ($check[0] -notmatch $check[1]) {
        throw "Unsafe $($check[2]) volume name: $($check[0])"
    }
}
if ($allVolumes -contains "cargo-platform-data") {
    throw "The 5173 data volume cargo-platform-data is forbidden."
}

& docker volume inspect @allVolumes | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "One or more candidate/rollback volumes do not exist."
}

function Assert-VolumeNotMounted {
    param(
        [Parameter(Mandatory)]
        [string]$VolumeName,
        [Parameter(Mandatory)]
        [string]$Role
    )

    $containerIds = @(
        & docker ps --all --quiet --filter "volume=$VolumeName"
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect containers using the $Role volume."
    }
    $containerIds = @(
        $containerIds | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($containerIds.Count -ne 0) {
        throw (
            "$Role volume must not be mounted by any container before apply: " +
            ($containerIds -join ", ")
        )
    }
}

function Assert-VolumeEmpty {
    param(
        [Parameter(Mandatory)]
        [string]$VolumeName,
        [Parameter(Mandatory)]
        [string]$Role
    )

    $probe = (
        "import os, sys; " +
        "entries = sorted(entry.name for entry in os.scandir('/volume')); " +
        "print('\n'.join(entries[:20])); " +
        "sys.exit(1 if entries else 0)"
    )
    $probeOutput = @(
        & docker run --rm --pull never --network none --read-only `
            --cap-drop ALL --security-opt no-new-privileges `
            --mount "type=volume,source=$VolumeName,target=/volume,readonly" `
            --entrypoint python `
            $env:VALIDATION_BACKEND_IMAGE `
            -c $probe 2>&1
    )
    if ($LASTEXITCODE -ne 0) {
        $details = @(
            $probeOutput |
                ForEach-Object { "$_" } |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                Select-Object -First 20
        ) -join ", "
        if ([string]::IsNullOrWhiteSpace($details)) {
            $details = "volume probe failed without output"
        }
        throw "$Role volume must be empty and readable before apply: $details"
    }
}

if ($Stage -eq "rollback") {
    $env:VALIDATION_APP_VERSION = $env:ROLLBACK_APP_VERSION
    $env:VALIDATION_BACKEND_IMAGE = $env:ROLLBACK_BACKEND_IMAGE
    $env:VALIDATION_PARSER_IMAGE = $env:ROLLBACK_PARSER_IMAGE
    $env:VALIDATION_AI_IMAGE = $env:ROLLBACK_AI_IMAGE
    $env:VALIDATION_UI_IMAGE = $env:ROLLBACK_UI_IMAGE
    $env:VALIDATION_PLATFORM_VOLUME = $env:ROLLBACK_PLATFORM_VOLUME
    $env:VALIDATION_REDIS_VOLUME = $env:ROLLBACK_REDIS_VOLUME
    $env:VALIDATION_AI_SESSION_VOLUME = $env:ROLLBACK_AI_SESSION_VOLUME
} else {
    $candidateScratchVolumes = @(
        @($env:VALIDATION_REDIS_VOLUME, "Candidate Redis"),
        @($env:VALIDATION_AI_SESSION_VOLUME, "Candidate AI session")
    )
    foreach ($scratchVolume in $candidateScratchVolumes) {
        Assert-VolumeNotMounted `
            -VolumeName $scratchVolume[0] `
            -Role $scratchVolume[1]
    }
    foreach ($scratchVolume in $candidateScratchVolumes) {
        Assert-VolumeEmpty `
            -VolumeName $scratchVolume[0] `
            -Role $scratchVolume[1]
    }

    $verificationScript = Join-Path $repoRoot "scripts\ai_validation_dataset.py"
    & docker run --rm --network none --read-only `
        --mount "type=volume,source=$env:VALIDATION_PLATFORM_VOLUME,target=/data,readonly" `
        --mount "type=bind,source=$verificationScript,target=/verify.py,readonly" `
        --entrypoint python `
        $env:VALIDATION_BACKEND_IMAGE `
        /verify.py `
        --verify-database /data/cargo-platform.db `
        --asset-root /data/workspaces
    if ($LASTEXITCODE -ne 0) {
        throw "True-zero database or asset verification failed."
    }
}

$composeArgs = @(
    "compose",
    "-f", "ops/validation-stages/20260728-night/docker-compose.yml",
    "-f", "ops/validation-stages/20260729-ai/docker-compose.override.yml",
    "-f", "ops/validation-stages/20260729-ai/docker-compose.true-zero.yml"
)

Write-Host "6173 stage: $Stage"
Write-Host "Platform volume: $env:VALIDATION_PLATFORM_VOLUME"
Write-Host "AI session volume: $env:VALIDATION_AI_SESSION_VOLUME"
Write-Host "Redis volume: $env:VALIDATION_REDIS_VOLUME"
Write-Host "Backend image: $env:VALIDATION_BACKEND_IMAGE"
Write-Host "Parser image: $env:VALIDATION_PARSER_IMAGE"
Write-Host "AI image: $env:VALIDATION_AI_IMAGE"
Write-Host "UI image: $env:VALIDATION_UI_IMAGE"

& docker @composeArgs config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose validation failed."
}
if (-not $Apply) {
    Write-Host "Dry run only. Re-run with -Apply to recreate the isolated 6173 services."
    exit 0
}

& docker @composeArgs up -d --force-recreate redis waybill-parser ai-recognition backend ui
if ($LASTEXITCODE -ne 0) {
    throw "6173 stage switch failed."
}
