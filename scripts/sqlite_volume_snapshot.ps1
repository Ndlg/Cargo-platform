param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Backup", "Restore")]
    [string]$Action,
    [string]$VolumeName = "cargo-platform-data",
    [string]$DatabasePath = "cargo-platform.db",
    [string]$BackendImage = "cargo-platform-backend:latest",
    [string]$BackupDirectory,
    [string]$BackupFile,
    [string]$ExpectedSha256,
    [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$snapshotScript = Resolve-Path (Join-Path $PSScriptRoot "sqlite_snapshot.py")
$normalizedDatabasePath = $DatabasePath.Replace("\", "/").TrimStart("/")
if ([string]::IsNullOrWhiteSpace($normalizedDatabasePath) -or $normalizedDatabasePath.Split("/") -contains "..") {
    throw "DatabasePath must stay inside the Docker volume."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found."
}
& docker volume inspect $VolumeName *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker volume was not found: $VolumeName"
}
& docker image inspect $BackendImage *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Backend image was not found: $BackendImage"
}

if ([string]::IsNullOrWhiteSpace($BackupDirectory)) {
    $BackupDirectory = Join-Path (Split-Path $repoRoot -Parent) "cargo-platform-deploy-backups"
}
New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
$resolvedBackupDirectory = Resolve-Path $BackupDirectory
$toolMount = "type=bind,src=$snapshotScript,dst=/tool/sqlite_snapshot.py,readonly"

if ($Action -eq "Backup") {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $safeVolumeName = $VolumeName -replace '[^A-Za-z0-9_.-]', '_'
    $fileName = "$safeVolumeName-$timestamp.db"
    $dockerArgs = @(
        "run", "--rm", "--entrypoint", "python",
        "--mount", "type=volume,src=$VolumeName,dst=/data,readonly",
        "--mount", $toolMount,
        "--mount", "type=bind,src=$resolvedBackupDirectory,dst=/backup",
        $BackendImage,
        "/tool/sqlite_snapshot.py", "backup",
        "/data/$normalizedDatabasePath", "/backup/$fileName"
    )
    & docker @dockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "SQLite volume backup failed."
    }
    return
}

if (-not $ConfirmRestore) {
    throw "Restore requires -ConfirmRestore and a stopped database container."
}
if ([string]::IsNullOrWhiteSpace($BackupFile) -or -not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
    throw "Restore requires an existing -BackupFile."
}
if ($ExpectedSha256 -notmatch '^[0-9a-fA-F]{64}$') {
    throw "Restore requires the recorded 64-character -ExpectedSha256."
}
$running = @(& docker ps -q --filter "volume=$VolumeName")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to inspect containers using volume $VolumeName."
}
if ($running.Count -gt 0) {
    throw "Refusing restore while a container is using volume ${VolumeName}: $($running -join ', ')"
}

$resolvedBackupFile = Resolve-Path $BackupFile
$restoreDirectory = Split-Path $resolvedBackupFile -Parent
$restoreFileName = Split-Path $resolvedBackupFile -Leaf
$dockerArgs = @(
    "run", "--rm", "--entrypoint", "python",
    "--mount", "type=volume,src=$VolumeName,dst=/data",
    "--mount", $toolMount,
    "--mount", "type=bind,src=$restoreDirectory,dst=/backup,readonly",
    $BackendImage,
    "/tool/sqlite_snapshot.py", "restore",
    "/backup/$restoreFileName", "/data/$normalizedDatabasePath",
    "--expected-sha256", $ExpectedSha256,
    "--confirm", "RESTORE_STOPPED_DATABASE"
)
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    throw "SQLite volume restore failed."
}
