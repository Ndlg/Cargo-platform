[CmdletBinding()]
param(
  [ValidatePattern('^[0-9A-Za-z][0-9A-Za-z._+-]*$')]
  [string]$Version = "development",

  [string]$GitSha = "",

  [string]$PythonExe = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$CollectorDir = Join-Path $Root "collector-client"
$BuildRoot = Join-Path $Root "storage\collector-build"
$BuildVenv = Join-Path $BuildRoot "venv"
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
$GeneratedDir = Join-Path $BuildRoot "generated"
$WorkDir = Join-Path $BuildRoot "work"
$SpecDir = Join-Path $BuildRoot "spec"
$DistDir = Join-Path $CollectorDir "dist"
$ExePath = Join-Path $DistDir "Cargo Platform 采集器.exe"
$ManifestPath = Join-Path $DistDir "collector-manifest.json"
$IconPath = Join-Path $CollectorDir "assets\cargo-platform-collector.ico"
$LockPath = Join-Path $CollectorDir "requirements-build.lock"
$ClientPath = Join-Path $CollectorDir "client.py"
$WindowsHostPath = Join-Path $CollectorDir "windows_host.py"
$RequiredPythonVersion = "3.12.13"

function Resolve-BasePython {
  param([string]$RequestedPython)

  if ($RequestedPython) {
    if (-not (Test-Path -LiteralPath $RequestedPython -PathType Leaf)) {
      throw "Python executable was not found: $RequestedPython"
    }
    return [IO.Path]::GetFullPath($RequestedPython)
  }

  $candidates = @(
    (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"),
    (Join-Path $Root ".venv\Scripts\python.exe")
  )
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      return [IO.Path]::GetFullPath($candidate)
    }
  }

  $command = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($command -and $command.Source -notlike "*\WindowsApps\python.exe") {
    return $command.Source
  }
  throw "Python was not found. Install Python 3.12 or pass -PythonExe explicitly."
}

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
  )

  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
  }
}

foreach ($requiredPath in @($IconPath, $LockPath, $ClientPath, $WindowsHostPath)) {
  if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
    throw "Required collector build input was not found: $requiredPath"
  }
}

if (-not $GitSha) {
  $GitSha = (git -C $Root rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve the current Git SHA."
  }
}
if ($GitSha -notmatch '^[0-9a-fA-F]{40}$') {
  throw "GitSha must be a full 40-character hexadecimal commit id."
}

$BasePython = Resolve-BasePython $PythonExe
$BasePythonVersion = (& $BasePython -c "import platform; print(platform.python_version())").Trim()
if ($BasePythonVersion -ne $RequiredPythonVersion) {
  throw "Collector releases require Python $RequiredPythonVersion; found $BasePythonVersion."
}
New-Item -ItemType Directory -Force -Path $BuildRoot, $GeneratedDir, $WorkDir, $SpecDir, $DistDir | Out-Null
Invoke-Checked $BasePython -m venv --clear $BuildVenv
Invoke-Checked $BuildPython -m pip install --disable-pip-version-check --no-input --requirement $LockPath

$ClientVersion = "$Version+$($GitSha.Substring(0, 12).ToLowerInvariant())"
$GeneratedModule = Join-Path $GeneratedDir "collector_build_info.py"
@"
CLIENT_VERSION = "$ClientVersion"
RELEASE_VERSION = "$Version"
GIT_SHA = "$($GitSha.ToLowerInvariant())"
"@ | Set-Content -LiteralPath $GeneratedModule -Encoding utf8NoBOM

Remove-Item -LiteralPath $ExePath, $ManifestPath -Force -ErrorAction SilentlyContinue
Invoke-Checked $BuildPython -m PyInstaller `
  --clean `
  --noconfirm `
  --onefile `
  --windowed `
  --noupx `
  --name "Cargo Platform 采集器" `
  --icon $IconPath `
  --distpath $DistDir `
  --workpath $WorkDir `
  --specpath $SpecDir `
  --paths $GeneratedDir `
  --hidden-import collector_build_info `
  --hidden-import windows_host `
  $ClientPath

if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
  throw "Collector EXE was not produced: $ExePath"
}
$ExeInfo = Get-Item -LiteralPath $ExePath
if ($ExeInfo.Length -lt 1MB) {
  throw "Collector EXE is unexpectedly small: $($ExeInfo.Length) bytes"
}
$Header = [byte[]]::new(2)
$Stream = [IO.File]::OpenRead($ExePath)
try {
  if ($Stream.Read($Header, 0, 2) -ne 2 -or $Header[0] -ne 0x4d -or $Header[1] -ne 0x5a) {
    throw "Collector release is not a Windows MZ executable."
  }
} finally {
  $Stream.Dispose()
}

$SmokeHome = Join-Path $BuildRoot "smoke-home"
New-Item -ItemType Directory -Force -Path $SmokeHome | Out-Null
$PreviousCollectorHome = $env:CARGO_PLATFORM_COLLECTOR_HOME
try {
  $env:CARGO_PLATFORM_COLLECTOR_HOME = $SmokeHome
  $SmokeProcess = Start-Process -FilePath $ExePath -ArgumentList @("--check", "--no-log-file") -WindowStyle Hidden -Wait -PassThru
  if ($SmokeProcess.ExitCode -ne 0) {
    throw "Collector EXE --check failed with exit code $($SmokeProcess.ExitCode)."
  }
} finally {
  $env:CARGO_PLATFORM_COLLECTOR_HOME = $PreviousCollectorHome
}

$PythonVersion = (& $BuildPython -c "import platform; print(platform.python_version())").Trim()
$PyInstallerVersion = (& $BuildPython -c "import importlib.metadata as m; print(m.version('pyinstaller'))").Trim()
$Sha256 = (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash.ToLowerInvariant()
$Manifest = [ordered]@{
  schema_version = 1
  artifact = "Cargo Platform 采集器.exe"
  release_version = $Version
  client_version = $ClientVersion
  git_sha = $GitSha.ToLowerInvariant()
  python_version = $PythonVersion
  pyinstaller_version = $PyInstallerVersion
  size = $ExeInfo.Length
  sha256 = $Sha256
}
$Manifest | ConvertTo-Json | Set-Content -LiteralPath $ManifestPath -Encoding utf8NoBOM

$WrittenManifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
if ($WrittenManifest.sha256 -ne (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash.ToLowerInvariant()) {
  throw "Collector manifest verification failed after writing."
}

Write-Host "Collector release built and verified."
Write-Host "EXE: $ExePath"
Write-Host "Manifest: $ManifestPath"
Write-Host "Version: $ClientVersion"
Write-Host "SHA-256: $Sha256"
