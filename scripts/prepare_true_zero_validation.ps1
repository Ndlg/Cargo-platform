param(
    [ValidateSet("candidate", "rollback")]
    [string]$Stage = "candidate",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$required = @(
    "AI_RECOGNITION_INTERNAL_TOKEN",
    "VALIDATION_APP_VERSION",
    "VALIDATION_BACKEND_IMAGE",
    "VALIDATION_PARSER_IMAGE",
    "VALIDATION_AI_IMAGE",
    "VALIDATION_UI_IMAGE",
    "VALIDATION_PLATFORM_VOLUME",
    "VALIDATION_REDIS_VOLUME",
    "VALIDATION_AI_SESSION_VOLUME"
)
foreach ($name in $required) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "Missing environment variable: $name"
    }
}

$volumeNames = @(
    $env:VALIDATION_PLATFORM_VOLUME,
    $env:VALIDATION_REDIS_VOLUME,
    $env:VALIDATION_AI_SESSION_VOLUME
)
if ($volumeNames -contains "cargo-platform-data") {
    throw "The 5173 data volume cargo-platform-data is forbidden."
}
foreach ($volumeName in $volumeNames) {
    if (-not $volumeName.StartsWith("cargo-platform-validation-", [StringComparison]::Ordinal)) {
        throw "Validation volume has an unsafe name: $volumeName"
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
