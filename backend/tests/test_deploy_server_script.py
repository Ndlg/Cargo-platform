from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy_server.sh"
SERVICES = ("waybill-parser", "backend", "tenant-ui", "platform-admin-ui")
GENERATED_SECRET_KEYS = (
    "SECRET_KEY",
    "COLLECTOR_TOKEN_HASH_KEY",
    "INITIAL_SETUP_TOKEN",
)
PRESERVED_ENV_KEYS = (*GENERATED_SECRET_KEYS, "COLLECTOR_TOKEN_PREVIOUS_HASH_KEY", "CUSTOM_SETTING")
OLD_IMAGES = {
    "waybill-parser": "ghcr.io/ndlg/cargo-platform-waybill-parser:1.0.0",
    "backend": "ghcr.io/ndlg/cargo-platform-backend:1.0.0",
    "tenant-ui": "ghcr.io/ndlg/cargo-platform-tenant-ui:1.0.0",
    "platform-admin-ui": "ghcr.io/ndlg/cargo-platform-admin-ui:1.0.0",
}

EXISTING_ENV = """\
CARGO_PLATFORM_VERSION=1.0.0
SECRET_KEY=keep-secret
COLLECTOR_TOKEN_HASH_KEY=keep-hash
COLLECTOR_TOKEN_PREVIOUS_HASH_KEY=keep-previous-hash
INITIAL_SETUP_TOKEN=keep-setup-token
CUSTOM_SETTING=keep-custom-setting
"""

FAKE_DOCKER = r"""#!/usr/bin/env bash
set -eu

args="$*"
printf 'docker %s\n' "$args" >>"$FAKE_CALL_LOG"
mkdir -p "$FAKE_STATE_DIR"

service_from_args() {
    for service in waybill-parser backend tenant-ui platform-admin-ui; do
        case "$args" in
            *"$service"*) printf '%s\n' "$service"; return ;;
        esac
    done
}

image_for_service() {
    case "$1" in
        waybill-parser) printf 'ghcr.io/ndlg/cargo-platform-waybill-parser:1.0.0\n' ;;
        backend) printf 'ghcr.io/ndlg/cargo-platform-backend:1.0.0\n' ;;
        tenant-ui) printf 'ghcr.io/ndlg/cargo-platform-tenant-ui:1.0.0\n' ;;
        platform-admin-ui) printf 'ghcr.io/ndlg/cargo-platform-admin-ui:1.0.0\n' ;;
    esac
}

volume_exists="${FAKE_VOLUME_EXISTS:-1}"
if [ -f "$FAKE_STATE_DIR/volume-created" ] || [ -f "$FAKE_STATE_DIR/concurrent-install-complete" ]; then
    volume_exists=1
fi

case "$args" in
    *"pull ghcr.io/ndlg/cargo-platform-backend:"*)
        if [ "${FAKE_COMPLETE_INSTALL_DURING_MUTEX_PULL:-0}" = 1 ] && [ ! -f "$FAKE_STATE_DIR/concurrent-install-complete" ]; then
            : >"$FAKE_STATE_DIR/concurrent-install-complete"
            cat >"$FAKE_PROJECT_DIR/.env" <<EOF
CARGO_PLATFORM_VERSION=${FAKE_CONCURRENT_VERSION:-1.0.0}
SECRET_KEY=concurrent-secret
COLLECTOR_TOKEN_HASH_KEY=concurrent-hash
COLLECTOR_TOKEN_PREVIOUS_HASH_KEY=
INITIAL_SETUP_TOKEN=concurrent-setup-token
EOF
            rm -f "$FAKE_PROJECT_DIR/.cargo-platform-install-pending"
        fi
        exit 0
        ;;
    *"run"*"--name cargo-platform-deploy-mutex"*)
        [ "${FAKE_MUTEX_EXISTS:-0}" != 1 ] || exit 1
        : >"$FAKE_STATE_DIR/deployment-mutex"
        printf 'mutex-container-id\n'
        exit 0
        ;;
    *"run"*"--name cargo-platform-deploy-db-lock"*)
        : >"$FAKE_STATE_DIR/deployment-lock"
        printf 'lock-container-id\n'
        exit 0
        ;;
    *"logs"*"cargo-platform-deploy-db-lock"*)
        printf 'LOCKED:%s\n' "${FAKE_ACTIVE_STATUS_AFTER_LOCK:-}"
        exit 0
        ;;
    *"rm -f"*"cargo-platform-deploy-db-lock"*)
        [ "${FAKE_DB_LOCK_RM_FAIL:-0}" != 1 ] || exit 1
        rm -f "$FAKE_STATE_DIR/deployment-lock"
        exit 0
        ;;
    *"rm -f"*"cargo-platform-deploy-mutex"*)
        rm -f "$FAKE_STATE_DIR/deployment-mutex"
        exit 0
        ;;
    *"volume inspect"*"cargo-platform-data"*)
        [ "$volume_exists" = 1 ] || exit 1
        printf '[{"Name":"cargo-platform-data"}]\n'
        exit 0
        ;;
    *"volume create"*"cargo-platform-data"*)
        : >"$FAKE_STATE_DIR/volume-created"
        printf 'cargo-platform-data\n'
        exit 0
        ;;
    *"ps -q --no-trunc"*"volume=cargo-platform-data"*)
        [ ! -f "$FAKE_STATE_DIR/deployment-lock" ] || printf 'consumer-lock\n'
        [ -f "$FAKE_STATE_DIR/stopped" ] || printf 'consumer-backend\n'
        if [ "${FAKE_EXTRA_VOLUME_CONSUMER:-0}" = 1 ] || {
            [ "${FAKE_EXTRA_VOLUME_CONSUMER_AFTER_STOP:-0}" = 1 ] && [ -f "$FAKE_STATE_DIR/stopped" ]
        }; then
            printf 'consumer-extra\n'
        fi
        exit 0
        ;;
    *"ps -a -q"*"name=^/cargo-platform-"*)
        service="$(service_from_args || true)"
        if [ -f "$FAKE_STATE_DIR/concurrent-install-complete" ]; then
            :
        elif [ -n "${FAKE_INSTALLED_ONLY:-}" ]; then
            case ",${FAKE_INSTALLED_ONLY}," in
                *",${service},"*) ;;
                *) exit 0 ;;
            esac
        else
            [ "${FAKE_EXISTING_INSTALL:-1}" = 1 ] || exit 0
        fi
        [ -n "$service" ] && printf 'container-%s\n' "$service"
        exit 0
        ;;
esac

case "$args" in
    *"compose"*" config"*)
        case "$args" in
            *"--format json"*)
                cat <<EOF
{"services":{"waybill-parser":{"image":"ghcr.io/ndlg/cargo-platform-waybill-parser:${FAKE_TARGET_VERSION}"},"backend":{"image":"ghcr.io/ndlg/cargo-platform-backend:${FAKE_TARGET_VERSION}","environment":{"APP_VERSION":"${FAKE_TARGET_VERSION}"}},"tenant-ui":{"image":"ghcr.io/ndlg/cargo-platform-tenant-ui:${FAKE_TARGET_VERSION}"},"platform-admin-ui":{"image":"ghcr.io/ndlg/cargo-platform-admin-ui:${FAKE_TARGET_VERSION}"}}}
EOF
                ;;
        esac
        exit 0
        ;;
    *"compose"*" ps"*" -q"*)
        if [ "${FAKE_EXISTING_INSTALL:-1}" != 1 ] && [ ! -f "$FAKE_STATE_DIR/concurrent-install-complete" ] && [ ! -f "$FAKE_STATE_DIR/target-up" ] && [ ! -f "$FAKE_STATE_DIR/rollback-restored" ]; then
            exit 0
        fi
        service="$(service_from_args || true)"
        [ -n "$service" ] && printf 'container-%s\n' "$service"
        exit 0
        ;;
    *"compose"*" images"*" -q"*)
        service="$(service_from_args || true)"
        [ -n "$service" ] && printf 'sha256:old-%s\n' "$service"
        exit 0
        ;;
    *"compose"*" pull"*)
        : >"$FAKE_STATE_DIR/pulled"
        exit 0
        ;;
    *"compose"*" stop"*)
        : >"$FAKE_STATE_DIR/stopped"
        exit 0
        ;;
    *"compose"*" rm"*)
        [ "${FAKE_CLEANUP_FAIL:-0}" != 1 ] || exit 1
        exit 0
        ;;
    *"compose"*" up"*)
        case "$args" in
            *"docker-compose.rollback-"*)
                [ "${FAKE_ROLLBACK_UP_FAIL:-0}" != 1 ] || exit 1
                : >"$FAKE_STATE_DIR/rollback-restored"
                exit 0
                ;;
        esac
        if [ "${FAKE_REQUIRE_SNAPSHOT:-${FAKE_EXISTING_INSTALL:-1}}" = 1 ]; then
            find "$FAKE_BACKUP_DIR" -maxdepth 1 -type f -name '*.db' | grep -q . || {
                printf 'target up happened before SQLite snapshot\n' >&2
                exit 91
            }
            find "$FAKE_BACKUP_DIR" -maxdepth 1 -type f -name 'docker-compose.rollback-*.yml' | grep -q . || {
                printf 'target up happened before rollback compose\n' >&2
                exit 92
            }
        fi
        if [ "${FAKE_UP_FAIL_ONCE:-0}" = 1 ] && [ ! -f "$FAKE_STATE_DIR/up-failed" ]; then
            if [ "${FAKE_REMOVE_ENV_BACKUP_BEFORE_ROLLBACK:-0}" = 1 ]; then
                rm -f "$FAKE_BACKUP_DIR"/.env-*
            fi
            : >"$FAKE_STATE_DIR/up-failed"
            exit 42
        fi
        : >"$FAKE_STATE_DIR/target-up"
        exit 0
        ;;
esac

case "$args" in
    *"inspect"*".Name"*)
        case "$args" in
            *consumer-lock*) printf '/cargo-platform-deploy-db-lock\n' ;;
            *consumer-backend*) printf '/cargo-platform-backend\n' ;;
            *consumer-extra*) printf '/unexpected-db-user\n' ;;
        esac
        exit 0
        ;;
    *"inspect"*".Config.Image"*)
        service="$(service_from_args || true)"
        [ -n "$service" ] && image_for_service "$service"
        exit 0
        ;;
    *"inspect"*".Image"*)
        service="$(service_from_args || true)"
        if [ -n "$service" ]; then
            if [ -f "$FAKE_STATE_DIR/rollback-restored" ] && [ "${FAKE_ROLLBACK_WRONG_IMAGE:-0}" = 1 ]; then
                printf 'sha256:wrong-%s\n' "$service"
            elif [ -f "$FAKE_STATE_DIR/target-up" ] && [ ! -f "$FAKE_STATE_DIR/rollback-restored" ] && [ "${FAKE_TARGET_WRONG_IMAGE:-}" != "$service" ]; then
                printf 'sha256:new-%s\n' "$service"
            else
                printf 'sha256:old-%s\n' "$service"
            fi
        fi
        exit 0
        ;;
    *"inspect"*".Config.Env"*)
        printf 'APP_VERSION=1.0.0\n'
        exit 0
        ;;
    *"image inspect"*)
        service="$(service_from_args || true)"
        [ -n "$service" ] && printf 'sha256:new-%s\n' "$service"
        exit 0
        ;;
esac

case "$args" in
    *" exec "*)
        printf '%s\n' "${FAKE_ACTIVE_STATUS:-}"
        exit 0
        ;;
    *"run"*)
        case "$args" in
            *"PRAGMA integrity_check"*)
                if [ "${FAKE_DB_INTEGRITY_ALWAYS_FAIL:-0}" = 1 ] || {
                    [ "${FAKE_DB_INTEGRITY_FAIL_TARGET:-0}" = 1 ] &&
                    [ -f "$FAKE_STATE_DIR/target-up" ] &&
                    [ ! -f "$FAKE_STATE_DIR/rollback-restored" ]
                }; then
                    printf 'corrupt\n'
                    exit 1
                fi
                printf 'ok\n'
                ;;
            *backup*|*sqlite_snapshot*|*"/backup/"*)
                [ "${FAKE_SNAPSHOT_FAIL:-0}" != 1 ] || exit 1
                snapshot_name="${args##* /backup/}"
                snapshot="$FAKE_BACKUP_DIR/$snapshot_name"
                printf 'snapshot' >"$snapshot"
                printf '{"action":"backup","integrity_check":"ok","path":"/backup/cargo-platform-data-test.db","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size":8}\n'
                ;;
            *)
                status="${FAKE_ACTIVE_STATUS:-}"
                if [ -f "$FAKE_STATE_DIR/stopped" ] && [ -n "${FAKE_ACTIVE_STATUS_AFTER_STOP:-}" ]; then
                    status="$FAKE_ACTIVE_STATUS_AFTER_STOP"
                elif [ -f "$FAKE_STATE_DIR/pulled" ] && [ -n "${FAKE_ACTIVE_STATUS_AFTER_PULL:-}" ]; then
                    status="$FAKE_ACTIVE_STATUS_AFTER_PULL"
                fi
                printf '%s\n' "$status"
                ;;
        esac
        exit 0
        ;;
esac

exit 0
"""

FAKE_CURL = r"""#!/usr/bin/env bash
set -eu

args="$*"
printf 'curl %s\n' "$args" >>"$FAKE_CALL_LOG"
active="${FAKE_ACTIVE_STATUS:-}"
if [ "$active" = collecting ]; then
    active_task='{"status":"collecting"}'
    collecting=1
    stopping=0
elif [ "$active" = stopping ]; then
    active_task='{"status":"stopping"}'
    collecting=0
    stopping=1
else
    active_task=null
    collecting=0
    stopping=0
fi

case "$args" in
    *8010*) printf '{"status":"ok","version":"%s"}\n' "$FAKE_TARGET_VERSION" ;;
    *) printf '{"status":"ready","version":"%s","active_task":%s,"collecting_tasks":%s,"stopping_tasks":%s}\n' "$FAKE_TARGET_VERSION" "$active_task" "$collecting" "$stopping" ;;
esac
"""


def _shell_path(path: Path) -> str:
    resolved = path.resolve().as_posix()
    if os.name == "nt":
        return f"/{resolved[0].lower()}{resolved[2:]}"
    return resolved


def _shell() -> str:
    candidates = [shutil.which("sh")]
    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidates.extend(
            (
                str(program_files / "Git" / "usr" / "bin" / "sh.exe"),
                str(program_files / "Git" / "bin" / "sh.exe"),
            )
        )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise AssertionError("A POSIX sh is required to exercise deploy_server.sh")


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@pytest.fixture
def deploy_sandbox(tmp_path: Path) -> dict[str, object]:
    project = tmp_path / "server release"
    scripts = project / "scripts"
    fake_bin = tmp_path / "fake bin"
    backup = tmp_path / "deployment backups"
    state = tmp_path / "fake state"
    for directory in (scripts, fake_bin, backup, state):
        directory.mkdir(parents=True)

    for name in ("docker-compose.yml", "docker-compose.release.yml", ".env.example", "deploy.env.example"):
        shutil.copy2(ROOT / name, project / name)
    shutil.copy2(ROOT / "scripts" / "sqlite_snapshot.py", scripts / "sqlite_snapshot.py")
    _write_executable(fake_bin / "docker", FAKE_DOCKER)
    _write_executable(fake_bin / "curl", FAKE_CURL)

    call_log = tmp_path / "calls.log"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "HOME": _shell_path(tmp_path / "home"),
            "FAKE_CALL_LOG": _shell_path(call_log),
            "FAKE_STATE_DIR": _shell_path(state),
            "FAKE_BACKUP_DIR": _shell_path(backup),
            "FAKE_PROJECT_DIR": _shell_path(project),
            "FAKE_TARGET_VERSION": "1.0.1",
            "FAKE_VOLUME_EXISTS": "1",
            "FAKE_EXISTING_INSTALL": "1",
        }
    )
    return {
        "project": project,
        "backup": backup,
        "state": state,
        "call_log": call_log,
        "env": env,
    }


def _run_deploy(
    sandbox: dict[str, object],
    *,
    version: str | None = "1.0.1",
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    assert SCRIPT.is_file(), f"missing implementation: {SCRIPT}"
    project = sandbox["project"]
    assert isinstance(project, Path)
    deployed_script = project / "scripts" / SCRIPT.name
    shutil.copy2(SCRIPT, deployed_script)
    deployed_script.chmod(0o755)

    env = dict(sandbox["env"])
    env.update(overrides)
    backup = sandbox["backup"]
    assert isinstance(backup, Path)
    command = [_shell(), _shell_path(deployed_script)]
    if version is not None:
        command.extend(("--version", version))
    command.extend(("--backup-dir", _shell_path(backup)))
    return subprocess.run(
        command,
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _write_existing_env(project: Path, *, version: str = "1.0.0") -> dict[str, str]:
    env_file = project / ".env"
    env_file.write_text(
        EXISTING_ENV.replace("CARGO_PLATFORM_VERSION=1.0.0", f"CARGO_PLATFORM_VERSION={version}"),
        encoding="utf-8",
        newline="\n",
    )
    env_file.chmod(0o600)
    return _env_values(env_file)


def _call_log(sandbox: dict[str, object]) -> str:
    path = sandbox["call_log"]
    assert isinstance(path, Path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_first_install_creates_volume_and_mode_0600_env(deploy_sandbox: dict[str, object]) -> None:
    result = _run_deploy(
        deploy_sandbox,
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = _call_log(deploy_sandbox)
    assert re.search(r"^docker .*volume create .*cargo-platform-data", calls, re.MULTILINE)
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    env_file = project / ".env"
    values = _env_values(env_file)
    assert values["CARGO_PLATFORM_VERSION"] == "1.0.1"
    for key in GENERATED_SECRET_KEYS:
        assert values[key]
    assert "COLLECTOR_TOKEN_PREVIOUS_HASH_KEY" in values
    if os.name != "nt":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_first_install_resumes_after_env_creation_was_interrupted(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    before = _write_existing_env(project)
    (project / ".cargo-platform-install-pending").write_text("", encoding="utf-8")

    result = _run_deploy(
        deploy_sandbox,
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    after = _env_values(project / ".env")
    assert after["CARGO_PLATFORM_VERSION"] == "1.0.1"
    for key in PRESERVED_ENV_KEYS:
        assert after[key] == before[key]


def test_first_install_marker_with_residual_containers_is_not_an_upgrade(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project, version="1.0.1")
    (project / ".cargo-platform-install-pending").write_text("", encoding="utf-8")

    result = _run_deploy(deploy_sandbox, FAKE_REQUIRE_SNAPSHOT="0")

    assert result.returncode != 0
    assert "refusing automatic teardown" in result.stderr
    calls = _call_log(deploy_sandbox)
    assert not re.search(r"^docker .*compose .*\b(?:rm|stop|up)\b", calls, re.MULTILINE)
    assert not any("sqlite_snapshot" in call or " backup " in call for call in calls.splitlines())
    assert (project / ".cargo-platform-install-pending").exists()

    retry = _run_deploy(
        deploy_sandbox,
        FAKE_EXISTING_INSTALL="0",
        FAKE_REQUIRE_SNAPSHOT="0",
    )
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert not (project / ".cargo-platform-install-pending").exists()


def test_first_install_marker_with_one_residual_container_is_not_torn_down(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project, version="1.0.1")
    (project / ".cargo-platform-install-pending").write_text("", encoding="utf-8")

    result = _run_deploy(deploy_sandbox, FAKE_INSTALLED_ONLY="backend")

    assert result.returncode != 0
    calls = _call_log(deploy_sandbox)
    assert not re.search(r"^docker .*compose .*\b(?:rm|stop|up)\b", calls, re.MULTILINE)


def test_first_install_has_signal_cleanup_handler() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "first_install_interrupted" in source
    assert "trap first_install_interrupted HUP INT TERM" in source
    assert source.count("trap '' HUP INT TERM") >= 2


def test_first_install_finalizes_marker_before_unlocking() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    marker = source.rindex('rm -f "$install_marker"')
    unlock = source.rindex('release_deployment_mutex || die')
    assert marker < unlock


def test_missing_volume_with_unmarked_env_refuses_empty_replacement(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project)

    result = _run_deploy(
        deploy_sandbox,
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
    )

    assert result.returncode != 0
    calls = _call_log(deploy_sandbox)
    assert not re.search(r"^docker .*volume create .*cargo-platform-data", calls, re.MULTILINE)


def test_release_compose_is_a_standalone_deployment_input(deploy_sandbox: dict[str, object]) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    (project / "docker-compose.yml").unlink()

    result = _run_deploy(
        deploy_sandbox,
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = _call_log(deploy_sandbox)
    assert "docker-compose.release.yml" in calls
    assert "docker-compose.yml" not in calls.replace("docker-compose.release.yml", "")


def test_version_defaults_from_deploy_env_template(deploy_sandbox: dict[str, object]) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    template = project / "deploy.env.example"
    template.write_text(
        template.read_text(encoding="utf-8").replace(
            "CARGO_PLATFORM_VERSION=1.0.2",
            "CARGO_PLATFORM_VERSION=1.0.3",
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = _run_deploy(
        deploy_sandbox,
        version=None,
        FAKE_TARGET_VERSION="1.0.3",
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _env_values(project / ".env")["CARGO_PLATFORM_VERSION"] == "1.0.3"


def test_failed_first_install_stops_containers_without_deleting_volume(
    deploy_sandbox: dict[str, object],
) -> None:
    result = _run_deploy(
        deploy_sandbox,
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
        FAKE_UP_FAIL_ONCE="1",
    )

    assert result.returncode != 0
    calls = _call_log(deploy_sandbox)
    assert re.search(r"^docker .*compose .*\brm -f -s\b", calls, re.MULTILINE)
    assert not re.search(r"^docker .*compose .*\bdown\b", calls, re.MULTILINE)
    assert not re.search(r"^docker .*volume (?:rm|remove)\b", calls, re.MULTILINE)


def test_failed_first_install_can_resume_with_preserved_volume(
    deploy_sandbox: dict[str, object],
) -> None:
    first = _run_deploy(
        deploy_sandbox,
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
        FAKE_UP_FAIL_ONCE="1",
    )
    assert first.returncode != 0

    second = _run_deploy(
        deploy_sandbox,
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
        FAKE_UP_FAIL_ONCE="1",
    )

    assert second.returncode == 0, second.stdout + second.stderr


def test_pending_first_install_with_data_refuses_a_new_version(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project, version="1.0.1")
    (project / ".cargo-platform-install-pending").write_text("", encoding="utf-8")

    result = _run_deploy(
        deploy_sandbox,
        version="1.0.2",
        FAKE_TARGET_VERSION="1.0.2",
        FAKE_EXISTING_INSTALL="0",
    )

    assert result.returncode != 0
    assert "retry version 1.0.1" in result.stderr
    assert _env_values(project / ".env")["CARGO_PLATFORM_VERSION"] == "1.0.1"
    assert (project / ".cargo-platform-install-pending").exists()
    calls = _call_log(deploy_sandbox)
    assert not re.search(r"^docker .*compose .*\b(?:pull|up|rm)\b", calls, re.MULTILINE)


def test_upgrade_preserves_existing_env_values_and_changes_version(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    before = _write_existing_env(project)

    result = _run_deploy(deploy_sandbox)

    assert result.returncode == 0, result.stdout + result.stderr
    after = _env_values(project / ".env")
    assert after["CARGO_PLATFORM_VERSION"] == "1.0.1"
    for key in PRESERVED_ENV_KEYS:
        assert after[key] == before[key]
    calls = _call_log(deploy_sandbox).splitlines()
    lock_run = next(line for line in calls if "--name cargo-platform-deploy-db-lock" in line)
    stop = next(i for i, line in enumerate(calls) if re.search(r"\bcompose\b.*\bstop\b", line))
    lock_release = next(i for i, line in enumerate(calls) if "rm -f cargo-platform-deploy-db-lock" in line)
    consumer_checks = [i for i, line in enumerate(calls) if "ps -q --no-trunc --filter volume=cargo-platform-data" in line]
    assert "BEGIN EXCLUSIVE" in lock_run
    assert "type=volume,src=cargo-platform-data,dst=/data" in lock_run
    assert "dst=/data,readonly" not in lock_run
    assert len(consumer_checks) == 2
    assert calls.index(lock_run) < consumer_checks[0] < stop < consumer_checks[1] < lock_release


@pytest.mark.parametrize("active_status", ["collecting", "stopping"])
def test_active_capture_refuses_before_service_changes(
    deploy_sandbox: dict[str, object],
    active_status: str,
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project)

    result = _run_deploy(deploy_sandbox, FAKE_ACTIVE_STATUS=active_status)

    assert result.returncode != 0
    calls = _call_log(deploy_sandbox)
    direct_pulls = [line for line in calls.splitlines() if re.match(r"^docker pull\b", line)]
    assert len(direct_pulls) == 1
    assert "cargo-platform-backend:1.0.1" in direct_pulls[0]
    assert not re.search(r"^docker .*compose .*\bpull\b", calls, re.MULTILINE)
    assert not re.search(r"^docker .*compose .*\b(stop|up)\b", calls, re.MULTILINE)
    assert not any("sqlite_snapshot" in call or " backup " in call for call in calls.splitlines())


def test_capture_started_during_pull_refuses_before_snapshot_or_up(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project)

    result = _run_deploy(
        deploy_sandbox,
        FAKE_ACTIVE_STATUS_AFTER_PULL="collecting",
    )

    assert result.returncode != 0
    calls = _call_log(deploy_sandbox)
    assert re.search(r"^docker .*compose .*\bpull\b", calls, re.MULTILINE)
    assert not any("sqlite_snapshot" in call or " backup " in call for call in calls.splitlines())
    assert not re.search(r"^docker .*compose .*\bup\b", calls, re.MULTILINE)


def test_deployment_lock_catches_capture_started_immediately_before_switch(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project)

    result = _run_deploy(
        deploy_sandbox,
        FAKE_ACTIVE_STATUS_AFTER_LOCK="collecting",
    )

    assert result.returncode != 0
    calls = _call_log(deploy_sandbox)
    assert "cargo-platform-deploy-db-lock" in calls
    assert not re.search(r"^docker .*compose .*\b(stop|start)\b", calls, re.MULTILINE)
    assert not any("sqlite_snapshot" in call or " backup " in call for call in calls.splitlines())
    assert not re.search(r"^docker .*compose .*\bup\b", calls, re.MULTILINE)


def test_unexpected_volume_consumer_refuses_before_service_changes(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project)

    result = _run_deploy(deploy_sandbox, FAKE_EXTRA_VOLUME_CONSUMER="1")

    assert result.returncode != 0
    assert "unexpected-db-user" in result.stderr
    calls = _call_log(deploy_sandbox)
    assert not re.search(r"^docker .*compose .*\b(stop|up)\b", calls, re.MULTILINE)
    assert not any("sqlite_snapshot" in call or " backup " in call for call in calls.splitlines())


def test_volume_consumer_started_while_stopping_preserves_both_locks(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    state = deploy_sandbox["state"]
    assert isinstance(project, Path)
    assert isinstance(state, Path)
    _write_existing_env(project)

    result = _run_deploy(deploy_sandbox, FAKE_EXTRA_VOLUME_CONSUMER_AFTER_STOP="1")

    assert result.returncode != 0
    assert "unexpected-db-user" in result.stderr
    calls = _call_log(deploy_sandbox)
    assert re.search(r"^docker .*compose .*\bstop\b", calls, re.MULTILINE)
    assert not re.search(r"^docker .*compose .*\bup\b", calls, re.MULTILINE)
    assert not any("sqlite_snapshot" in call or " backup " in call for call in calls.splitlines())
    assert (state / "deployment-lock").exists()
    assert (state / "deployment-mutex").exists()
    assert "rm -f cargo-platform-deploy-db-lock" not in calls
    assert "rm -f cargo-platform-deploy-mutex" not in calls
    assert "docker rm -f cargo-platform-deploy-db-lock" in result.stderr
    assert "docker rm -f cargo-platform-deploy-mutex" in result.stderr


def test_failed_database_lock_release_preserves_both_locks_and_recovery_steps(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    state = deploy_sandbox["state"]
    assert isinstance(project, Path)
    assert isinstance(state, Path)
    _write_existing_env(project)

    result = _run_deploy(deploy_sandbox, FAKE_DB_LOCK_RM_FAIL="1")

    assert result.returncode != 0
    assert "docker rm -f cargo-platform-deploy-db-lock" in result.stderr
    assert "docker rm -f cargo-platform-deploy-mutex" in result.stderr
    assert (state / "deployment-lock").exists()
    assert (state / "deployment-mutex").exists()
    calls = _call_log(deploy_sandbox)
    assert not re.search(r"^docker .*compose .*\bup\b", calls, re.MULTILINE)
    assert not any("sqlite_snapshot" in call or " backup " in call for call in calls.splitlines())


def test_concurrent_deployment_is_refused_before_service_changes(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    backup = deploy_sandbox["backup"]
    assert isinstance(project, Path)
    assert isinstance(backup, Path)
    before = _write_existing_env(project)

    result = _run_deploy(deploy_sandbox, FAKE_MUTEX_EXISTS="1")

    assert result.returncode != 0
    assert "another deployment" in result.stderr
    assert _env_values(project / ".env") == before
    assert not list(backup.iterdir())
    calls = _call_log(deploy_sandbox)
    assert not re.search(r"^docker .*compose .*\b(pull|rm|stop|up)\b", calls, re.MULTILINE)


def test_concurrent_first_install_does_not_create_local_state(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)

    result = _run_deploy(
        deploy_sandbox,
        FAKE_MUTEX_EXISTS="1",
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
    )

    assert result.returncode != 0
    assert not (project / ".env").exists()
    assert not (project / ".cargo-platform-install-pending").exists()
    calls = _call_log(deploy_sandbox)
    assert not re.search(r"^docker .*volume create\b", calls, re.MULTILINE)
    assert not re.search(r"^docker .*compose .*\b(pull|rm|stop|up)\b", calls, re.MULTILINE)


def test_state_is_reclassified_after_mutex_image_pull(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)

    result = _run_deploy(
        deploy_sandbox,
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
        FAKE_COMPLETE_INSTALL_DURING_MUTEX_PULL="1",
        FAKE_REQUIRE_SNAPSHOT="1",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    values = _env_values(project / ".env")
    assert values["CARGO_PLATFORM_VERSION"] == "1.0.1"
    assert values["SECRET_KEY"] == "concurrent-secret"
    calls = _call_log(deploy_sandbox)
    assert any("sqlite_snapshot" in call or " backup " in call for call in calls.splitlines())
    assert not re.search(r"^docker .*volume create\b", calls, re.MULTILINE)
    assert not re.search(r"^docker .*compose .*\brm\b", calls, re.MULTILINE)


def test_deployment_mutex_has_no_time_limit() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "threading.Event().wait()" in source
    assert "time.sleep(900)" not in source


def test_upgrade_creates_snapshot_and_rollback_before_up(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    backup = deploy_sandbox["backup"]
    assert isinstance(project, Path)
    assert isinstance(backup, Path)
    _write_existing_env(project)

    result = _run_deploy(deploy_sandbox)

    assert result.returncode == 0, result.stdout + result.stderr
    snapshots = list(backup.glob("*.db"))
    snapshot_records = list(backup.glob("snapshot-*.json"))
    rollbacks = list(backup.glob("docker-compose.rollback-*.yml"))
    assert snapshots and snapshots[0].stat().st_size > 0
    assert len(snapshot_records) == 1
    assert "a" * 64 in snapshot_records[0].read_text(encoding="utf-8")
    assert len(rollbacks) == 1
    assert "PRAGMA integrity_check" in _call_log(deploy_sandbox)
    calls = _call_log(deploy_sandbox).splitlines()
    snapshot_index = next(i for i, call in enumerate(calls) if "backup" in call or "sqlite_snapshot" in call)
    stop_index = next(i for i, call in enumerate(calls) if re.search(r"docker .*compose .*\bstop\b", call))
    up_index = next(i for i, call in enumerate(calls) if re.search(r"docker .*compose .*\bup\b", call))
    assert stop_index < snapshot_index < up_index


def test_failed_up_restores_four_old_images_and_old_env_version(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    backup = deploy_sandbox["backup"]
    assert isinstance(project, Path)
    assert isinstance(backup, Path)
    before = _write_existing_env(project)

    result = _run_deploy(deploy_sandbox, FAKE_UP_FAIL_ONCE="1")

    assert result.returncode != 0
    assert _env_values(project / ".env") == before
    rollbacks = list(backup.glob("docker-compose.rollback-*.yml"))
    assert len(rollbacks) == 1
    rollback = rollbacks[0].read_text(encoding="utf-8")
    for service in SERVICES:
        assert service in rollback
        assert OLD_IMAGES[service] in rollback or f"sha256:old-{service}" in rollback
    rollback_calls = [
        call
        for call in _call_log(deploy_sandbox).splitlines()
        if "rollback" in call and re.search(r"docker .*compose .*\bup\b", call)
    ]
    assert len(rollback_calls) == 1


def test_target_database_integrity_failure_rolls_back(deploy_sandbox: dict[str, object]) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    before = _write_existing_env(project)

    result = _run_deploy(deploy_sandbox, FAKE_DB_INTEGRITY_FAIL_TARGET="1")

    assert result.returncode != 0
    assert "previous release restored" in result.stderr
    assert _env_values(project / ".env") == before
    assert _call_log(deploy_sandbox).count("PRAGMA integrity_check") == 2


def test_target_image_id_mismatch_rolls_back(deploy_sandbox: dict[str, object]) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    before = _write_existing_env(project)

    result = _run_deploy(deploy_sandbox, FAKE_TARGET_WRONG_IMAGE="backend")

    assert result.returncode != 0
    assert "previous release restored" in result.stderr
    assert _env_values(project / ".env") == before


def test_failed_rollback_integrity_points_to_snapshot_record(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project)

    result = _run_deploy(deploy_sandbox, FAKE_DB_INTEGRITY_ALWAYS_FAIL="1")

    assert result.returncode != 0
    assert "snapshot record:" in result.stderr


def test_rollback_fails_if_any_restored_image_does_not_match(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project)

    result = _run_deploy(
        deploy_sandbox,
        FAKE_UP_FAIL_ONCE="1",
        FAKE_ROLLBACK_WRONG_IMAGE="1",
    )

    assert result.returncode != 0
    assert "rollback" in (result.stdout + result.stderr).casefold()
    assert "previous release restored" not in (result.stdout + result.stderr).casefold()


def test_rollback_does_not_claim_success_when_env_backup_is_missing(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    assert isinstance(project, Path)
    _write_existing_env(project)

    result = _run_deploy(
        deploy_sandbox,
        FAKE_UP_FAIL_ONCE="1",
        FAKE_REMOVE_ENV_BACKUP_BEFORE_ROLLBACK="1",
    )

    assert result.returncode != 0
    assert "rollback" in (result.stdout + result.stderr).casefold()
    assert "previous release restored" not in (result.stdout + result.stderr).casefold()
    calls = _call_log(deploy_sandbox)
    assert re.search(r"^docker .*compose .*docker-compose\.rollback-.*\bup\b", calls, re.MULTILINE)
    state = deploy_sandbox["state"]
    assert isinstance(state, Path)
    assert (state / "deployment-mutex").exists()
    assert not re.search(r"^docker rm -f cargo-platform-deploy-mutex", calls, re.MULTILINE)


def test_failed_verified_restore_preserves_deployment_mutex(
    deploy_sandbox: dict[str, object],
) -> None:
    project = deploy_sandbox["project"]
    state = deploy_sandbox["state"]
    assert isinstance(project, Path)
    assert isinstance(state, Path)
    _write_existing_env(project)

    result = _run_deploy(
        deploy_sandbox,
        FAKE_SNAPSHOT_FAIL="1",
        FAKE_ROLLBACK_UP_FAIL="1",
    )

    assert result.returncode != 0
    assert (state / "deployment-mutex").exists()
    calls = _call_log(deploy_sandbox)
    assert not re.search(r"^docker rm -f cargo-platform-deploy-mutex", calls, re.MULTILINE)


def test_failed_first_install_cleanup_preserves_deployment_mutex(
    deploy_sandbox: dict[str, object],
) -> None:
    state = deploy_sandbox["state"]
    assert isinstance(state, Path)

    result = _run_deploy(
        deploy_sandbox,
        FAKE_VOLUME_EXISTS="0",
        FAKE_EXISTING_INSTALL="0",
        FAKE_UP_FAIL_ONCE="1",
        FAKE_CLEANUP_FAIL="1",
    )

    assert result.returncode != 0
    assert (state / "deployment-mutex").exists()
    calls = _call_log(deploy_sandbox)
    assert not re.search(r"^docker rm -f cargo-platform-deploy-mutex", calls, re.MULTILINE)
