param(
  [string]$Version = "0.1.0",
  [string]$Registry = "ghcr.io/ndlg",
  [string]$GitSha = "",
  [string]$CollectorPython = "",
  [switch]$Push
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if ($Push -and (git status --porcelain)) {
  throw "Refusing to publish release images from a dirty worktree."
}
if (-not $GitSha) {
  $GitSha = (git rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve the current Git SHA."
  }
}
$collectorBuildArgs = @{
  Version = $Version
  GitSha = $GitSha
}
if ($CollectorPython) {
  $collectorBuildArgs.PythonExe = $CollectorPython
}
& (Join-Path $PSScriptRoot "build_collector_release.ps1") @collectorBuildArgs

$images = @(
  @{
    Name = "cargo-platform-backend"
    Dockerfile = "backend/Dockerfile"
    Context = "."
    Args = @()
  },
  @{
    Name = "cargo-platform-tenant-ui"
    Dockerfile = "frontend/Dockerfile"
    Context = "frontend"
    Args = @("--build-arg", "BUILD_COMMAND=build:tenant", "--build-arg", "DIST_DIR=dist")
  },
  @{
    Name = "cargo-platform-admin-ui"
    Dockerfile = "frontend/Dockerfile"
    Context = "frontend"
    Args = @("--build-arg", "BUILD_COMMAND=build:server-admin", "--build-arg", "DIST_DIR=dist-server-admin")
  },
  @{
    Name = "cargo-platform-waybill-parser"
    Dockerfile = "services/waybill-parser/Dockerfile"
    Context = "."
    Args = @()
  }
)

foreach ($image in $images) {
  $versionTag = "$Registry/$($image.Name):$Version"
  $latestTag = "$Registry/$($image.Name):latest"
  $args = @(
    "build",
    "-f", $image.Dockerfile,
    "-t", $versionTag,
    "-t", $latestTag
  ) + $image.Args + @($image.Context)

  Write-Host "Building $versionTag"
  docker @args

  if ($Push) {
    Write-Host "Pushing $versionTag"
    docker push $versionTag
    Write-Host "Pushing $latestTag"
    docker push $latestTag
  }
}
