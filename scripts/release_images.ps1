param(
  [string]$Version = "0.1.0",
  [string]$Registry = "ghcr.io/ndlg",
  [switch]$Push
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

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
