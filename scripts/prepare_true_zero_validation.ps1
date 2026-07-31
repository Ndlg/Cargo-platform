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
