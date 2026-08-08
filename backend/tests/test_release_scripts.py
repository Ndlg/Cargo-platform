import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_posix_deploy_entrypoint_is_executable_in_git() -> None:
    index_entry = subprocess.check_output(
        ["git", "ls-files", "--stage", "scripts/deploy_server.sh"],
        cwd=ROOT,
        text=True,
    ).strip()

    assert index_entry.startswith("100755 ")


def test_customer_delivery_audit_uses_export_coverage_partition() -> None:
    source = (ROOT / "scripts" / "customer_delivery_audit.ps1").read_text(encoding="utf-8")

    assert 'Name "normal_export_count"' in source
    assert 'Name "exception_export_count"' in source
    assert "Report preview rows (" not in source
    assert "-and (Has-BusinessValue $salesAttr1)" not in source


def test_sqlite_volume_restore_refuses_running_volume() -> None:
    source = (ROOT / "scripts" / "sqlite_volume_snapshot.ps1").read_text(encoding="utf-8")

    assert "sqlite_snapshot.py" in source
    assert 'docker ps -q --filter "volume=$VolumeName"' in source
    assert "RESTORE_STOPPED_DATABASE" in source
    assert "Join-Path $resolvedBackupDirectory $fileName" in source


def test_development_compose_keeps_backend_and_parser_on_one_version() -> None:
    source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")

    assert source.count("APP_VERSION: ${CARGO_PLATFORM_VERSION:-1.0.1}") == 2


def test_business_deploy_takes_verified_snapshot_before_recreate() -> None:
    source = (ROOT / "scripts" / "deploy_business_containers.ps1").read_text(encoding="utf-8")

    backup_index = source.index("sqlite_volume_snapshot.ps1")
    recreate_index = source.index('compose @composeFiles up -d')
    assert backup_index < recreate_index
    assert "pull @services" in source
    assert "--wait" in source
    assert "restoring the previous four images" in source
    assert "composeConfig.services.backend.environment.APP_VERSION" in source
    assert "previous_app_versions" in source
    assert "previous_image_ids" in source
    assert "target_image_ids" in source
    assert "Existing release is incomplete" in source
    assert "rollback verification failed" in source
    assert "PRAGMA integrity_check" in source
    assert "file:/data/cargo-platform.db?mode=ro" in source
    assert "database_path.is_file()" in source
    assert "NamedTemporaryFile" in source
    assert "http://waybill-parser:8010/health" in source
    assert "Rollback readiness verification failed" in source
    assert '$composeFiles = @("-f", "docker-compose.release.yml")' in source
    assert "Active capture task exists" in source
    assert source.index("Active capture task exists") < source.index("pull @services")
    assert "Production database cargo-platform.db does not exist" in source
    assert "docker volume create cargo-platform-data" in source
    assert "disable: true" not in source
    assert "http://127.0.0.1:8000/api/v1/health" in source
    assert "$snapshotArgs = @{" in source
    assert 'Action = "Backup"' in source
    assert 'VolumeName = "cargo-platform-data"' in source


def test_windows_business_deploy_preserves_install_and_upgrade_state() -> None:
    source = (ROOT / "scripts" / "deploy_business_containers.ps1").read_text(encoding="utf-8")

    assert "[string]$Version" in source
    assert 'Join-Path $repoRoot "deploy.env.example"' in source
    assert 'Get-EnvValue -Path $envTemplate -Name "CARGO_PLATFORM_VERSION"' in source

    assert '.cargo-platform-install-pending' in source
    guard_index = source.index("Existing .env was found but cargo-platform-data is missing")
    marker_index = source.index("New-Item -ItemType File -Path $installMarker")
    volume_index = source.index("docker volume create cargo-platform-data")
    assert guard_index < marker_index < volume_index
    marker_removal_index = source.index("Remove-Item -LiteralPath $installMarker")
    assert source.index("catch {") < marker_removal_index

    assert 'Copy-Item -LiteralPath $envFile -Destination $envBackupFile -Force' in source
    assert 'Copy-Item -LiteralPath $envBackupFile -Destination $envFile -Force' in source
    assert source.index('Copy-Item -LiteralPath $envBackupFile -Destination $envFile -Force') < source.index(
        "restoring the previous four images"
    )
    target_env_update = source.rindex(
        'Set-EnvValue -Path $envFile -Name "CARGO_PLATFORM_VERSION" -Value $Version'
    )
    assert source.index('Copy-Item -LiteralPath $envFile -Destination $envBackupFile -Force') < target_env_update

    assert "function Assert-NoActiveCapture" in source
    assert '--mount "type=volume,src=cargo-platform-data,dst=/data,readonly"' in source
    assert 'docker exec $previousContainerIds["backend"] python -c $activeCaptureProbe' not in source
    assert 'BackendImage = $previousImages["backend"]' in source
    first_check = source.index("Assert-NoActiveCapture -BackendImage")
    pull_index = source.index("pull @services")
    second_check = source.index("Assert-NoActiveCapture -BackendImage", first_check + 1)
    snapshot_index = source.index("sqlite_volume_snapshot.ps1")
    recreate_index = source.index("compose @composeFiles up -d")
    assert first_check < pull_index < second_check < snapshot_index < recreate_index

    lowered = source.casefold()
    assert "down -v" not in lowered
    assert "--volumes" not in lowered
    assert "docker volume rm" not in lowered
    assert "docker volume remove" not in lowered


def test_windows_first_install_failure_removes_only_release_services() -> None:
    source = (ROOT / "scripts" / "deploy_business_containers.ps1").read_text(encoding="utf-8")

    assert "down --remove-orphans" not in source
    assert source.count("compose @composeFiles rm -f -s @services") >= 2


def test_windows_target_success_gate_checks_the_business_database() -> None:
    source = (ROOT / "scripts" / "deploy_business_containers.ps1").read_text(encoding="utf-8")

    assert "function Assert-DatabaseIntegrity" in source
    assert 'for table in ("capture_tasks", "raw_capture_records", "products")' in source
    target_gate = source.index('Assert-DatabaseIntegrity -BackendImage $targetImageIds["backend"]')
    recreate_index = source.index("compose @composeFiles up -d")
    rollback_index = source.index("catch {", target_gate)
    assert recreate_index < target_gate < rollback_index


def test_release_compose_requires_one_version_and_runtime_guards() -> None:
    source = (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8-sig")

    assert source.count("${CARGO_PLATFORM_VERSION:?set CARGO_PLATFORM_VERSION}") == 6
    assert ":latest" not in source
    assert source.count("healthcheck:") == 4
    assert source.count("logging: *default-logging") == 4
    assert "external: true" in source


def test_release_builds_four_immutable_images_after_quality_gate() -> None:
    script = (ROOT / "scripts" / "release_images.ps1").read_text(encoding="utf-8-sig")
    workflow = (ROOT / ".github" / "workflows" / "release-images.yml").read_text(encoding="utf-8-sig")

    assert "cargo-platform-waybill-parser" in script
    assert ":latest" not in script
    assert "Parameter(Mandatory = $true)" in script
    assert "needs: [version, collector, server-check, quality]" in workflow
    assert "group: cargo-platform-release-images" in workflow
    assert 'VERSION="${{ github.event.inputs.version }}"' not in workflow
    assert "REQUESTED_VERSION:" in workflow
    assert "Direct image publishing is disabled" in script
    assert "docker push" not in script
    assert "manifest unknown|no such manifest|not found" in workflow
    assert "Unable to verify release tag" in workflow
    assert "python -m pytest backend/tests -q" in workflow
    assert "npm audit --omit=dev --audit-level=high" in workflow
    assert "docker/setup-qemu-action@v3" in workflow
    assert "platforms: linux/amd64,linux/arm64" in workflow
    assert "push-by-digest=true" in workflow
    assert "needs: [version, build]" in workflow
    assert "docker buildx imagetools create" in workflow
    assert "needs: [version, collector, server-check, publish]" in workflow
    assert "\n  server:\n" not in workflow
    assert "build_server_release.py" in workflow
    assert "server-release-${{ needs.version.outputs.value }}" in workflow
    assert "contents: write" in workflow
    assert "gh release create" in workflow
    assert "Refusing to overwrite existing GitHub Release" in workflow
    assert ":latest" not in workflow


def test_release_dependencies_exclude_retired_jose_and_vulnerable_pillow() -> None:
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")

    assert "python-jose" not in requirements.casefold()
    assert "passlib" not in requirements.casefold()
    assert "pytest" not in requirements.casefold()
    assert "pillow>=12.3,<13.0" in requirements.casefold()


WINDOWS_DEPLOY_RUNNER = r'''
$ErrorActionPreference = "Stop"

function Invoke-FakeDocker {
    $dockerArgs = @($args | ForEach-Object { [string]$_ })
    $json = ConvertTo-Json -InputObject $dockerArgs -Compress
    [System.IO.File]::AppendAllText(
        $env:FAKE_CALL_LOG,
        "$json`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    $global:LASTEXITCODE = 0
    $joined = $dockerArgs -join " "
    $service = $null
    foreach ($candidate in @("waybill-parser", "backend", "tenant-ui", "platform-admin-ui")) {
        if ($joined.Contains($candidate)) {
            $service = $candidate
            break
        }
    }

    if ($dockerArgs[0] -eq "pull") {
        if ($env:FAKE_COMPLETE_INSTALL_DURING_BACKEND_PULL -eq "1") {
            New-Item -ItemType File -Path "$env:FAKE_STATE_DIR/concurrent-install-complete" -Force |
                Out-Null
            $concurrentEnv = @(
                "CARGO_PLATFORM_VERSION=1.0.0",
                "SECRET_KEY=concurrent-secret",
                "COLLECTOR_TOKEN_HASH_KEY=concurrent-token-key",
                "COLLECTOR_TOKEN_PREVIOUS_HASH_KEY=concurrent-previous-key",
                "INITIAL_SETUP_TOKEN=concurrent-setup-token",
                "CORS_ORIGINS=http://concurrent.example",
                ""
            )
            [System.IO.File]::WriteAllLines(
                (Join-Path $env:FAKE_PROJECT_DIR ".env"),
                $concurrentEnv,
                [System.Text.UTF8Encoding]::new($false)
            )
            Remove-Item -LiteralPath (Join-Path $env:FAKE_PROJECT_DIR ".cargo-platform-install-pending") `
                -Force -ErrorAction SilentlyContinue
        }
        Write-Output "pulled"
        return
    }

    if ($dockerArgs[0] -eq "ps" -and $dockerArgs -contains "-a") {
        $hasInstalledContainers = $env:FAKE_CONTAINER_COUNT -eq "4" -or
            (Test-Path "$env:FAKE_STATE_DIR/concurrent-install-complete")
        if ($hasInstalledContainers -and $service) {
            Write-Output "container-$service"
        }
        return
    }
    if ($dockerArgs[0] -eq "volume" -and $dockerArgs[1] -eq "inspect") {
        $volumeExists = $env:FAKE_VOLUME_EXISTS -eq "1" -or
            (Test-Path "$env:FAKE_STATE_DIR/volume-created") -or
            (Test-Path "$env:FAKE_STATE_DIR/concurrent-install-complete")
        if (-not $volumeExists) {
            $global:LASTEXITCODE = 1
            return
        }
        Write-Output '[{"Name":"cargo-platform-data"}]'
        return
    }
    if ($dockerArgs[0] -eq "volume" -and $dockerArgs[1] -eq "create") {
        New-Item -ItemType File -Path "$env:FAKE_STATE_DIR/volume-created" -Force | Out-Null
        Write-Output "cargo-platform-data"
        return
    }

    if ($dockerArgs[0] -eq "compose") {
        if ($dockerArgs -contains "config") {
            if ($dockerArgs -contains "--format") {
                $version = $env:FAKE_TARGET_VERSION
                $config = [ordered]@{
                    services = [ordered]@{
                        "waybill-parser" = @{ image = "ghcr.io/ndlg/cargo-platform-waybill-parser:$version" }
                        backend = @{
                            image = "ghcr.io/ndlg/cargo-platform-backend:$version"
                            environment = @{ APP_VERSION = $version }
                        }
                        "tenant-ui" = @{ image = "ghcr.io/ndlg/cargo-platform-tenant-ui:$version" }
                        "platform-admin-ui" = @{ image = "ghcr.io/ndlg/cargo-platform-admin-ui:$version" }
                    }
                }
                Write-Output ($config | ConvertTo-Json -Compress -Depth 8)
            }
            return
        }
        if ($dockerArgs -contains "rm") {
            New-Item -ItemType File -Path "$env:FAKE_STATE_DIR/cleaned" -Force | Out-Null
            return
        }
        if ($dockerArgs -contains "pull") {
            New-Item -ItemType File -Path "$env:FAKE_STATE_DIR/pulled" -Force | Out-Null
            return
        }
        if ($dockerArgs -contains "stop") {
            New-Item -ItemType File -Path "$env:FAKE_STATE_DIR/stopped" -Force | Out-Null
            return
        }
        if ($dockerArgs -contains "start") {
            New-Item -ItemType File -Path "$env:FAKE_STATE_DIR/restarted" -Force | Out-Null
            return
        }
        if ($dockerArgs -contains "up") {
            if ($joined.Contains("docker-compose.rollback-")) {
                New-Item -ItemType File -Path "$env:FAKE_STATE_DIR/rollback" -Force | Out-Null
            }
            else {
                New-Item -ItemType File -Path "$env:FAKE_STATE_DIR/target" -Force | Out-Null
                if ($env:FAKE_DELETE_ENV_BACKUP_ON_FAILURE -eq "1") {
                    Get-ChildItem -LiteralPath $env:FAKE_BACKUP_DIR -Filter ".env-*" |
                        Remove-Item -Force
                }
                if ($env:FAKE_BREAK_ENV_DESTINATION_ON_FAILURE -eq "1") {
                    $targetEnv = Join-Path $env:FAKE_PROJECT_DIR ".env"
                    Remove-Item -LiteralPath $targetEnv -Force
                    New-Item -ItemType Directory -Path $targetEnv | Out-Null
                }
                if ($env:FAKE_TARGET_UP_FAIL -eq "1") {
                    $global:LASTEXITCODE = 42
                }
            }
            return
        }
        if ($dockerArgs -contains "ps") {
            if ($dockerArgs -contains "-q" -and $service) {
                $hasContainer = $env:FAKE_CONTAINER_COUNT -eq "4" -or
                    (Test-Path "$env:FAKE_STATE_DIR/concurrent-install-complete")
                if (Test-Path "$env:FAKE_STATE_DIR/cleaned") { $hasContainer = $false }
                if (Test-Path "$env:FAKE_STATE_DIR/target") { $hasContainer = $true }
                if (Test-Path "$env:FAKE_STATE_DIR/rollback") { $hasContainer = $true }
                if ($hasContainer) { Write-Output "container-$service" }
            }
            return
        }
    }

    if ($dockerArgs[0] -eq "image" -and $dockerArgs[1] -eq "inspect") {
        if ($service) { Write-Output "sha256:new-$service" }
        return
    }
    if ($dockerArgs[0] -eq "inspect") {
        if ($joined.Contains(".State.Running")) {
            Write-Output "true"
            return
        }
        if ($joined.Contains(".Config.Image")) {
            Write-Output "ghcr.io/ndlg/cargo-platform-$service`:1.0.0"
            return
        }
        if ($joined.Contains(".Config.Env")) {
            Write-Output "APP_VERSION=1.0.0"
            return
        }
        if ($joined.Contains(".Image")) {
            if (Test-Path "$env:FAKE_STATE_DIR/rollback") {
                Write-Output "sha256:old-$service"
            }
            elseif (Test-Path "$env:FAKE_STATE_DIR/target") {
                Write-Output "sha256:new-$service"
            }
            else {
                Write-Output "sha256:old-$service"
            }
            return
        }
    }

    if ($dockerArgs[0] -eq "logs") {
        if ($env:FAKE_LOCK_LOG_DELAY -eq "1" -and -not (Test-Path "$env:FAKE_STATE_DIR/lock-log-read")) {
            New-Item -ItemType File -Path "$env:FAKE_STATE_DIR/lock-log-read" -Force | Out-Null
            return
        }
        Write-Output "LOCKED:$($env:FAKE_LOCK_STATUS)"
        return
    }
    if ($dockerArgs[0] -eq "rm" -and $dockerArgs -contains "-f") {
        return
    }
    if ($dockerArgs[0] -eq "run") {
        if ($joined.Contains("--name cargo-platform-deploy-mutex")) {
            if ($env:FAKE_MUTEX_EXISTS -eq "1") {
                $global:LASTEXITCODE = 1
                return
            }
            Write-Output "mutex-container-id"
            return
        }
        if ($joined.Contains("--name cargo-platform-deploy-db-lock")) {
            Write-Output "lock-container-id"
            return
        }
        if ($joined.Contains("PRAGMA integrity_check")) {
            Write-Output "ok"
            return
        }
        if ($joined.Contains("sqlite_snapshot.py") -or $joined.Contains(" backup ")) {
            $snapshot = Join-Path $env:FAKE_BACKUP_DIR "cargo-platform-data-fake.db"
            [System.IO.File]::WriteAllText($snapshot, "snapshot")
            Write-Output '{"action":"backup","integrity_check":"ok","path":"/backup/cargo-platform-data-fake.db","source":"","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size":8}'
            return
        }
        if (-not [string]::IsNullOrWhiteSpace($env:FAKE_ACTIVE_STATUS)) {
            Write-Output $env:FAKE_ACTIVE_STATUS
        }
        else {
            Write-Output ""
        }
        return
    }
}

Set-Alias -Name docker -Value Invoke-FakeDocker -Scope Global

function Invoke-RestMethod {
    param([string]$Uri, [int]$TimeoutSec)
    if ($Uri.Contains("8010")) {
        return [pscustomobject]@{ status = "ok"; version = $env:FAKE_TARGET_VERSION }
    }
    return [pscustomobject]@{ status = "ready"; version = $env:FAKE_TARGET_VERSION }
}

function Invoke-WebRequest {
    param([string]$Uri, [int]$TimeoutSec, [switch]$UseBasicParsing)
    return [pscustomobject]@{ StatusCode = 200 }
}

$parameters = @{ BackupDirectory = $env:FAKE_BACKUP_DIR }
if (-not [string]::IsNullOrWhiteSpace($env:FAKE_TARGET_VERSION)) {
    $parameters.Version = $env:FAKE_TARGET_VERSION
}
try {
    & $env:FAKE_DEPLOY_SCRIPT @parameters
    exit 0
}
catch {
    [Console]::Error.WriteLine("$($_.Exception.Message)`n$($_.ScriptStackTrace)")
    exit 1
}
'''


def _write_windows_env(project: Path, *, version: str, blank_secrets: bool = False) -> None:
    secret = "" if blank_secrets else "keep"
    (project / ".env").write_text(
        "\n".join(
            (
                f"CARGO_PLATFORM_VERSION={version}",
                f"SECRET_KEY={secret}",
                f"COLLECTOR_TOKEN_HASH_KEY={secret}",
                "COLLECTOR_TOKEN_PREVIOUS_HASH_KEY=keep-previous",
                f"INITIAL_SETUP_TOKEN={secret}",
                "CORS_ORIGINS=http://127.0.0.1:5173",
                "",
            )
        ),
        encoding="utf-8",
    )


def _windows_env_values(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if line)


def _run_windows_deploy(
    tmp_path: Path,
    *,
    target_version: str = "1.0.2",
    volume_exists: bool = True,
    container_count: int = 4,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, list[str]]:
    project = tmp_path / "windows release"
    scripts = project / "scripts"
    backup = tmp_path / "backups"
    state = tmp_path / "fake-state"
    for directory in (scripts, backup, state):
        directory.mkdir(parents=True, exist_ok=True)
    for name in ("docker-compose.release.yml", "deploy.env.example"):
        shutil.copy2(ROOT / name, project / name)
    for name in ("deploy_business_containers.ps1", "sqlite_volume_snapshot.ps1", "sqlite_snapshot.py"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)

    runner = tmp_path / "run-deploy.ps1"
    runner.write_text(WINDOWS_DEPLOY_RUNNER, encoding="utf-8")
    call_log = tmp_path / "docker-calls.jsonl"
    env = os.environ.copy()
    env.update(
        {
            "FAKE_DEPLOY_SCRIPT": str(scripts / "deploy_business_containers.ps1"),
            "FAKE_PROJECT_DIR": str(project),
            "FAKE_BACKUP_DIR": str(backup),
            "FAKE_CALL_LOG": str(call_log),
            "FAKE_STATE_DIR": str(state),
            "FAKE_TARGET_VERSION": target_version,
            "FAKE_VOLUME_EXISTS": "1" if volume_exists else "0",
            "FAKE_CONTAINER_COUNT": str(container_count),
            "FAKE_MUTEX_EXISTS": "0",
            "FAKE_LOCK_STATUS": "",
            "FAKE_LOCK_LOG_DELAY": "0",
            "FAKE_ACTIVE_STATUS": "",
            "FAKE_TARGET_UP_FAIL": "0",
            "FAKE_DELETE_ENV_BACKUP_ON_FAILURE": "0",
            "FAKE_BREAK_ENV_DESTINATION_ON_FAILURE": "0",
            "FAKE_COMPLETE_INSTALL_DURING_BACKEND_PULL": "0",
        }
    )
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["pwsh.exe", "-NoProfile", "-File", str(runner)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = []
    if call_log.exists():
        calls = [" ".join(json.loads(line)) for line in call_log.read_text(encoding="utf-8").splitlines()]
    return result, project, backup, calls


def test_windows_pending_marker_with_four_containers_resumes_first_install(tmp_path: Path) -> None:
    project = tmp_path / "windows release"
    project.mkdir(parents=True)
    _write_windows_env(project, version="1.0.0", blank_secrets=True)
    (project / ".cargo-platform-install-pending").write_text("", encoding="utf-8")

    result, project, backup, calls = _run_windows_deploy(tmp_path, target_version="1.0.0")

    assert result.returncode == 0, result.stdout + result.stderr
    values = _windows_env_values(project / ".env")
    assert values["CARGO_PLATFORM_VERSION"] == "1.0.0"
    assert values["SECRET_KEY"]
    assert values["COLLECTOR_TOKEN_HASH_KEY"]
    assert values["INITIAL_SETUP_TOKEN"]
    assert any(" compose " in f" {call} " and " rm " in f" {call} " for call in calls)
    assert not list(backup.glob("docker-compose.rollback-*.yml"))
    assert not (project / ".cargo-platform-install-pending").exists()


def test_windows_fresh_install_handles_no_existing_container_output(tmp_path: Path) -> None:
    result, project, _, calls = _run_windows_deploy(
        tmp_path,
        volume_exists=False,
        container_count=0,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / ".env").is_file()
    assert any(call.startswith("volume create cargo-platform-data") for call in calls)
    assert not (project / ".cargo-platform-install-pending").exists()


def test_windows_pending_install_refuses_cross_version_resume(tmp_path: Path) -> None:
    project = tmp_path / "windows release"
    project.mkdir(parents=True)
    _write_windows_env(project, version="1.0.0", blank_secrets=True)
    (project / ".cargo-platform-install-pending").write_text("", encoding="utf-8")

    result, project, backup, calls = _run_windows_deploy(tmp_path, target_version="1.0.2")

    assert result.returncode != 0
    assert "original version" in result.stderr.casefold()
    assert _windows_env_values(project / ".env")["CARGO_PLATFORM_VERSION"] == "1.0.0"
    assert not list(backup.glob("docker-compose.rollback-*.yml"))
    assert not any(" compose " in f" {call} " and " up " in f" {call} " for call in calls)


def test_windows_fixed_mutex_refuses_concurrent_deployment_before_stop(tmp_path: Path) -> None:
    project = tmp_path / "windows release"
    project.mkdir(parents=True)
    _write_windows_env(project, version="1.0.0")

    result, _, _, calls = _run_windows_deploy(
        tmp_path,
        extra_env={"FAKE_MUTEX_EXISTS": "1"},
    )

    assert result.returncode != 0
    assert "another deployment" in result.stderr
    assert not any(" compose " in f" {call} " and " stop " in f" {call} " for call in calls)


def test_windows_mutex_conflict_does_not_mutate_pending_install(tmp_path: Path) -> None:
    project = tmp_path / "windows release"
    project.mkdir(parents=True)
    _write_windows_env(project, version="1.0.0", blank_secrets=True)
    env_before = (project / ".env").read_bytes()
    marker = project / ".cargo-platform-install-pending"
    marker.write_text("pending", encoding="utf-8")

    result, project, _, calls = _run_windows_deploy(
        tmp_path,
        target_version="1.0.0",
        extra_env={"FAKE_MUTEX_EXISTS": "1"},
    )

    assert result.returncode != 0
    assert (project / ".env").read_bytes() == env_before
    assert marker.read_text(encoding="utf-8") == "pending"
    assert not any(" compose " in f" {call} " and " rm " in f" {call} " for call in calls)
    assert not any(" compose " in f" {call} " and " stop " in f" {call} " for call in calls)
    assert not any(call.startswith("volume create ") for call in calls)


def test_windows_exclusive_fence_refuses_capture_before_stopping_services(tmp_path: Path) -> None:
    project = tmp_path / "windows release"
    project.mkdir(parents=True)
    _write_windows_env(project, version="1.0.0")

    result, _, _, calls = _run_windows_deploy(
        tmp_path,
        extra_env={"FAKE_LOCK_STATUS": "collecting"},
    )

    assert result.returncode != 0
    assert any("cargo-platform-deploy-db-lock" in call and "BEGIN EXCLUSIVE" in call for call in calls)
    assert not any(" compose " in f" {call} " and " stop " in f" {call} " for call in calls)


def test_windows_exclusive_fence_is_held_until_old_services_stop(tmp_path: Path) -> None:
    project = tmp_path / "windows release"
    project.mkdir(parents=True)
    _write_windows_env(project, version="1.0.0")

    result, _, _, calls = _run_windows_deploy(
        tmp_path,
        extra_env={"FAKE_LOCK_LOG_DELAY": "1"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert any("--name cargo-platform-deploy-mutex" in call for call in calls)
    assert any("--name cargo-platform-deploy-db-lock" in call for call in calls)
    mutex = next(i for i, call in enumerate(calls) if "--name cargo-platform-deploy-mutex" in call)
    fence = next(i for i, call in enumerate(calls) if "--name cargo-platform-deploy-db-lock" in call)
    stop_ui = next(i for i, call in enumerate(calls) if " stop " in f" {call} " and "tenant-ui" in call)
    stop_backend = next(i for i, call in enumerate(calls) if " stop " in f" {call} " and "backend" in call)
    release_fence = next(i for i, call in enumerate(calls) if call.startswith("rm -f cargo-platform-deploy-db-lock"))
    snapshot = next(i for i, call in enumerate(calls) if "sqlite_snapshot.py backup" in call)
    target_up = next(i for i, call in enumerate(calls) if " compose " in f" {call} " and " up " in f" {call} ")
    assert mutex < fence < stop_ui < stop_backend < release_fence < snapshot < target_up
    assert "threading.Event().wait()" in calls[mutex]
    assert " --rm " not in f" {calls[mutex]} "


def _assert_windows_env_restore_failure_still_attempts_image_rollback(
    tmp_path: Path,
    failure_env: dict[str, str],
) -> None:
    project = tmp_path / "windows release"
    project.mkdir(parents=True)
    _write_windows_env(project, version="1.0.0")

    result, _, _, calls = _run_windows_deploy(
        tmp_path,
        extra_env={"FAKE_TARGET_UP_FAIL": "1", **failure_env},
    )

    assert result.returncode != 0
    assert any(
        "docker-compose.rollback-" in call
        and " compose " in f" {call} "
        and " up " in f" {call} "
        for call in calls
    )
    combined = (result.stdout + result.stderr).casefold()
    assert "previous release restored" not in combined
    assert not any(call.startswith("rm -f cargo-platform-deploy-mutex") for call in calls)


def test_windows_missing_env_backup_does_not_skip_image_rollback(tmp_path: Path) -> None:
    _assert_windows_env_restore_failure_still_attempts_image_rollback(
        tmp_path,
        {"FAKE_DELETE_ENV_BACKUP_ON_FAILURE": "1"},
    )


def test_windows_unwritable_env_destination_does_not_skip_image_rollback(tmp_path: Path) -> None:
    _assert_windows_env_restore_failure_still_attempts_image_rollback(
        tmp_path,
        {"FAKE_BREAK_ENV_DESTINATION_ON_FAILURE": "1"},
    )


def test_windows_reclassifies_install_completed_during_mutex_image_pull(tmp_path: Path) -> None:
    result, project, backup, calls = _run_windows_deploy(
        tmp_path,
        volume_exists=False,
        container_count=0,
        extra_env={"FAKE_COMPLETE_INSTALL_DURING_BACKEND_PULL": "1"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    values = _windows_env_values(project / ".env")
    assert values["CARGO_PLATFORM_VERSION"] == "1.0.2"
    assert values["SECRET_KEY"] == "concurrent-secret"
    assert values["COLLECTOR_TOKEN_HASH_KEY"] == "concurrent-token-key"
    assert values["COLLECTOR_TOKEN_PREVIOUS_HASH_KEY"] == "concurrent-previous-key"
    assert values["INITIAL_SETUP_TOKEN"] == "concurrent-setup-token"
    assert values["CORS_ORIGINS"] == "http://concurrent.example"
    assert not (project / ".cargo-platform-install-pending").exists()

    backend_pull = next(
        i
        for i, call in enumerate(calls)
        if call.startswith("pull ghcr.io/ndlg/cargo-platform-backend:1.0.2")
    )
    backend_inspect = next(
        i
        for i, call in enumerate(calls)
        if call.startswith(
            "image inspect --format {{.Id}} ghcr.io/ndlg/cargo-platform-backend:1.0.2"
        )
    )
    mutex = next(i for i, call in enumerate(calls) if "--name cargo-platform-deploy-mutex" in call)
    first_state_read = min(
        i
        for i, call in enumerate(calls)
        if call.startswith("volume inspect ") or call.startswith("ps -a ")
    )
    snapshot = next(i for i, call in enumerate(calls) if "sqlite_snapshot.py backup" in call)
    target_up = next(
        i
        for i, call in enumerate(calls)
        if " compose " in f" {call} " and " up " in f" {call} "
    )

    assert backend_pull < backend_inspect < mutex < first_state_read < snapshot < target_up
    assert list(backup.glob(".env-*"))
    assert list(backup.glob("docker-compose.rollback-*.yml"))
    assert not any(" compose " in f" {call} " and " rm " in f" {call} " for call in calls)
    assert not any(call.startswith("volume create ") for call in calls)
