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

Write-Host "Using compose files: docker-compose.yml + docker-compose.release.yml"
Write-Host "Service: waybill-parser"
Write-Host ""

& docker compose @composeFiles config --quiet

Write-Host "Building waybill-parser image..."
& docker compose @composeFiles build waybill-parser

Write-Host "Recreating waybill-parser container..."
& docker compose @composeFiles up -d --no-deps waybill-parser

Write-Host ""
Write-Host "Checking waybill-parser health..."
$healthUrl = "http://127.0.0.1:8010/api/v1/health"
$ok = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
        Write-Host "Waybill parser health OK: $($response | ConvertTo-Json -Compress)"
        $ok = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $ok) {
    throw "Waybill parser health check failed: $healthUrl"
}

Write-Host ""
Write-Host "Waybill Parser API: $healthUrl"
