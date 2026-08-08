param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')]
  [string]$Version,
  [string]$Registry = "ghcr.io/ndlg",
  [string]$GitSha = "",
  [string]$CollectorPython = "",
  [switch]$Push
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if ($Push) {
  throw "Direct image publishing is disabled. Push a v*.*.* Git tag so release-images.yml can publish one verified multi-architecture release."
}
if (-not $GitSha) {
  $GitSha = (git rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve the current Git SHA."
  }
}
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

$collectorBuildArgs = @{
  Version = $Version
  GitSha = $GitSha
}
if ($CollectorPython) {
  $collectorBuildArgs.PythonExe = $CollectorPython
}
& (Join-Path $PSScriptRoot "build_collector_release.ps1") @collectorBuildArgs

foreach ($image in $images) {
  $versionTag = "$Registry/$($image.Name):$Version"
  $args = @(
    "build",
    "-f", $image.Dockerfile,
    "-t", $versionTag
  ) + $image.Args + @($image.Context)

  Write-Host "Building $versionTag"
  & docker @args
  if ($LASTEXITCODE -ne 0) {
    throw "Image build failed: $versionTag"
  }

}
