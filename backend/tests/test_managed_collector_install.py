from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


COLLECTOR_CLIENT_PATH = Path(__file__).resolve().parents[2] / "collector-client"
if str(COLLECTOR_CLIENT_PATH) not in sys.path:
    sys.path.insert(0, str(COLLECTOR_CLIENT_PATH))

import client as collector_client  # noqa: E402
import windows_host  # noqa: E402


def connection_code(base_url: str = "http://10.0.0.5:5173", token: str = "one-time") -> str:
    payload = json.dumps(
        {"v": 1, "base_url": base_url, "token": token},
        separators=(",", ":"),
    ).encode()
    return "CP1." + base64.urlsafe_b64encode(payload).decode().rstrip("=")


def paths_for(tmp_path: Path) -> windows_host.WindowsCollectorPaths:
    install_dir = tmp_path / "local" / "CargoPlatformCollector"
    data_dir = install_dir
    return windows_host.WindowsCollectorPaths(
        install_dir=install_dir,
        exe_path=install_dir / "collector.exe",
        data_dir=data_dir,
        config_path=data_dir / "collector-config.json",
        state_path=data_dir / "collector-state.json",
        log_path=data_dir / "collector.log",
        legacy_home=tmp_path / "legacy",
    )


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_connection_code_decoder_returns_server_and_one_time_token() -> None:
    assert windows_host.decode_connection_code(connection_code()) == (
        "http://10.0.0.5:5173",
        "one-time",
    )


@pytest.mark.parametrize(
    "code",
    [
        "TOKEN abc",
        "CP1.not-base64!",
        connection_code(token=""),
        connection_code(base_url="http:///missing-host"),
        connection_code(base_url="https://user@collector.example.com"),
        connection_code(base_url="https://collector.example.com/api"),
        connection_code(base_url="https://collector.example.com?next=evil"),
        connection_code(base_url="https://collector.example.com:65536"),
        "CP1."
        + base64.urlsafe_b64encode(b'{"v":2,"base_url":"http://host","token":"t"}')
        .decode()
        .rstrip("="),
    ],
)
def test_connection_code_decoder_rejects_malformed_codes(code: str) -> None:
    with pytest.raises(ValueError, match="连接码"):
        windows_host.decode_connection_code(code)


def test_dpapi_current_user_round_trip_or_rejects_non_windows() -> None:
    if os.name != "nt":
        with pytest.raises(OSError, match="Windows"):
            windows_host.protect_secret("collector-secret")
        return

    protected = windows_host.protect_secret("collector-secret")

    assert protected.startswith("dpapi:")
    assert "collector-secret" not in protected
    assert windows_host.unprotect_secret(protected) == "collector-secret"


def test_managed_paths_keep_exe_config_and_state_in_local_app_data(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    paths = windows_host.machine_paths()

    assert paths.install_dir == tmp_path / "local" / "CargoPlatformCollector"
    assert paths.exe_path == paths.install_dir / "collector.exe"
    assert paths.data_dir == paths.install_dir
    assert paths.legacy_home == tmp_path / "local" / "CargoPlatformCollector"


def test_legacy_state_migration_copies_missing_files_without_deleting_source(tmp_path) -> None:
    paths = paths_for(tmp_path)
    write_json(paths.legacy_home / "collector-config.json", {"token": "legacy-token"})
    write_json(paths.legacy_home / "collector-state.json", {"idle_watermarks": {"printer": 41}})

    result = windows_host.migrate_legacy_home(paths)

    assert json.loads(paths.state_path.read_text(encoding="utf-8"))["idle_watermarks"]["printer"] == 41
    assert (paths.legacy_home / "collector-state.json").exists()
    assert result.backup_path.exists()
    assert set(result.copied) == {"collector-config.json", "collector-state.json"}


def test_legacy_state_migration_never_overwrites_machine_cursor(tmp_path) -> None:
    paths = paths_for(tmp_path)
    write_json(paths.legacy_home / "collector-state.json", {"idle_watermarks": {"printer": 41}})
    write_json(paths.state_path, {"idle_watermarks": {"printer": 55}})

    result = windows_host.migrate_legacy_home(paths)

    assert json.loads(paths.state_path.read_text(encoding="utf-8"))["idle_watermarks"]["printer"] == 55
    assert json.loads((result.backup_path / "collector-state.json").read_text(encoding="utf-8"))[
        "idle_watermarks"
    ]["printer"] == 41


def test_same_directory_migration_only_backs_up_existing_state(tmp_path) -> None:
    paths = paths_for(tmp_path)
    paths = windows_host.WindowsCollectorPaths(
        install_dir=paths.install_dir,
        exe_path=paths.exe_path,
        data_dir=paths.data_dir,
        config_path=paths.config_path,
        state_path=paths.state_path,
        log_path=paths.log_path,
        legacy_home=paths.data_dir,
    )
    write_json(paths.config_path, {"token": "legacy-token"})
    write_json(paths.state_path, {"idle_watermarks": {"printer": 55}})

    result = windows_host.migrate_legacy_home(paths)

    assert result.copied == ()
    assert set(result.skipped) == {"collector-config.json", "collector-state.json"}
    assert json.loads(paths.state_path.read_text(encoding="utf-8"))["idle_watermarks"]["printer"] == 55
    assert (result.backup_path / "collector-config.json").exists()
    assert (result.backup_path / "collector-state.json").exists()


def test_install_existing_adopts_trusted_legacy_cursor_without_replaying_history(
    monkeypatch,
    tmp_path,
) -> None:
    paths = paths_for(tmp_path)
    source_exe = tmp_path / "release.exe"
    source_exe.write_bytes(b"MZcollector")
    db_path = tmp_path / "cloud-print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (rowid, taskID, msg, time) values (?, ?, ?, ?)",
            [
                (1, "first", '{"task":"first"}', "2026-08-01 10:00:00"),
                (2885, "uploaded", '{"task":"uploaded"}', "2026-08-05 10:00:00"),
                (2886, "new", '{"task":"new"}', "2026-08-05 10:01:00"),
            ],
        )
    write_json(paths.config_path, {"base_url": "http://server", "token": "device-token"})
    write_json(
        paths.state_path,
        {
            "idle_watermarks": {
                "cainiao-cnprint": 7580,
                "cloud-print-client": 2885,
            }
        },
    )
    monkeypatch.setattr(windows_host, "machine_paths", lambda: paths)
    monkeypatch.setattr(windows_host, "protect_secret", lambda value: f"dpapi:{value}")

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = windows_host.install_collector(source_exe, migrate_existing=True, runner=runner)
    adapter = collector_client.PrintDbAdapter(
        "cloud-print-client",
        "CloudPrintClient",
        db_path,
    )
    state = collector_client.CollectorState.load(paths.state_path)
    state.sync_db_generation(adapter, audit_prefix=True)

    assert result.success is True
    assert state.source_cursor(adapter) == 2885
    assert state.pending_count([adapter]) == 1
    assert state.source_epochs[adapter.source_component]
    assert state.cursor_fingerprints[adapter.source_component] == adapter.row_fingerprint(2885)
    assert state.last_reconnect_reason != "source_history_ambiguous"


def test_trusted_upgrade_does_not_mark_source_with_existing_epoch(tmp_path) -> None:
    paths = paths_for(tmp_path)
    write_json(
        paths.state_path,
        {
            "idle_watermarks": {"cloud-print-client": 2885},
            "source_epochs": {"cloud-print-client": "established-epoch"},
        },
    )

    windows_host.mark_trusted_legacy_state(paths.state_path)

    payload = json.loads(paths.state_path.read_text(encoding="utf-8"))
    assert payload["source_epochs"] == {"cloud-print-client": "established-epoch"}


def test_trusted_upgrade_adopts_capture_only_legacy_cursor(tmp_path) -> None:
    paths = paths_for(tmp_path)
    db_path = tmp_path / "cloud-print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (rowid, taskID, msg, time) values (?, ?, ?, ?)",
            [
                (1, "first", '{"task":"first"}', "2026-08-01 10:00:00"),
                (2885, "uploaded", '{"task":"uploaded"}', "2026-08-05 10:00:00"),
                (2886, "new", '{"task":"new"}', "2026-08-05 10:01:00"),
            ],
        )
    write_json(
        paths.state_path,
        {"capture_watermarks": {"58:cloud-print-client": 2885}},
    )
    windows_host.mark_trusted_legacy_state(paths.state_path)
    adapter = collector_client.PrintDbAdapter("cloud-print-client", "CloudPrintClient", db_path)
    state = collector_client.CollectorState.load(paths.state_path)

    state.sync_db_generation(adapter, audit_prefix=True)

    assert state.source_cursor(adapter) == 2885
    assert state.pending_count([adapter]) == 1
    assert state.source_epochs[adapter.source_component] != windows_host.TRUSTED_UPGRADE_EPOCH
    assert state.last_reconnect_reason != "source_history_ambiguous"


def test_trusted_upgrade_replays_conservatively_when_cursor_row_is_missing(tmp_path) -> None:
    paths = paths_for(tmp_path)
    db_path = tmp_path / "cloud-print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (rowid, taskID, msg, time) values (?, ?, ?, ?)",
            [
                (1, "first", '{"task":"first"}', "2026-08-05 10:00:00"),
                (3, "third", '{"task":"third"}', "2026-08-05 10:02:00"),
            ],
        )
    write_json(paths.state_path, {"idle_watermarks": {"cloud-print-client": 2}})
    windows_host.mark_trusted_legacy_state(paths.state_path)
    adapter = collector_client.PrintDbAdapter("cloud-print-client", "CloudPrintClient", db_path)
    state = collector_client.CollectorState.load(paths.state_path)

    state.sync_db_generation(adapter, audit_prefix=True)

    assert state.source_cursor(adapter) == 0
    assert state.last_reconnect_reason == "source_history_ambiguous"
    assert state.source_epochs[adapter.source_component] != windows_host.TRUSTED_UPGRADE_EPOCH


def test_trusted_upgrade_replays_conservatively_when_saved_fingerprint_does_not_match(
    tmp_path,
) -> None:
    paths = paths_for(tmp_path)
    db_path = tmp_path / "cloud-print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("first", '{"task":"first"}', "2026-08-05 10:00:00"),
                ("changed", '{"task":"changed"}', "2026-08-05 10:01:00"),
            ],
        )
    write_json(
        paths.state_path,
        {
            "idle_watermarks": {"cloud-print-client": 2},
            "cursor_fingerprints": {"cloud-print-client": "saved-before-upgrade"},
        },
    )
    windows_host.mark_trusted_legacy_state(paths.state_path)
    adapter = collector_client.PrintDbAdapter("cloud-print-client", "CloudPrintClient", db_path)
    state = collector_client.CollectorState.load(paths.state_path)

    state.sync_db_generation(adapter, audit_prefix=True)

    assert state.source_cursor(adapter) == 0
    assert state.last_reconnect_reason == "source_history_ambiguous"
    assert state.source_epochs[adapter.source_component] != windows_host.TRUSTED_UPGRADE_EPOCH


def test_trusted_upgrade_marker_is_consumed_when_snapshot_is_unavailable(tmp_path) -> None:
    paths = paths_for(tmp_path)
    write_json(paths.state_path, {"idle_watermarks": {"cloud-print-client": 2885}})
    windows_host.mark_trusted_legacy_state(paths.state_path)
    adapter = collector_client.PrintDbAdapter(
        "cloud-print-client",
        "CloudPrintClient",
        tmp_path / "missing.db",
    )
    state = collector_client.CollectorState.load(paths.state_path)

    state.sync_db_generation(adapter, audit_prefix=True)

    assert adapter.source_component not in state.source_epochs


def test_trusted_upgrade_marker_is_consumed_when_snapshot_raises(
    monkeypatch,
    tmp_path,
) -> None:
    paths = paths_for(tmp_path)
    write_json(paths.state_path, {"idle_watermarks": {"cloud-print-client": 2885}})
    windows_host.mark_trusted_legacy_state(paths.state_path)
    adapter = collector_client.PrintDbAdapter(
        "cloud-print-client",
        "CloudPrintClient",
        tmp_path / "cloud-print.db",
    )
    state = collector_client.CollectorState.load(paths.state_path)

    def fail_snapshot(*_args, **_kwargs):
        raise collector_client.sqlite3.Error("database is locked")

    monkeypatch.setattr(collector_client.PrintDbAdapter, "snapshot", fail_snapshot)

    with pytest.raises(collector_client.sqlite3.Error, match="database is locked"):
        state.sync_db_generation(adapter, audit_prefix=True)
    state.save(paths.state_path)

    reloaded = collector_client.CollectorState.load(paths.state_path)
    assert adapter.source_component not in reloaded.source_epochs


def test_first_run_consumes_trusted_upgrade_marker_for_unconfigured_source(
    monkeypatch,
    tmp_path,
) -> None:
    paths = paths_for(tmp_path)
    write_json(paths.state_path, {"idle_watermarks": {"cloud-print-client": 2885}})
    windows_host.mark_trusted_legacy_state(paths.state_path)
    state = collector_client.CollectorState.load(paths.state_path)
    monkeypatch.setattr(
        collector_client,
        "heartbeat",
        lambda *_args, **_kwargs: {
            "assignment_protocol_version": 2,
            "task_windows": [],
            "window_coverage_complete": True,
        },
    )

    collector_client.run_sqlite_once("http://collector.test", "token", state, [], 100)

    reloaded = collector_client.CollectorState.load(paths.state_path)
    assert "cloud-print-client" not in reloaded.source_epochs


def test_machine_config_writes_dpapi_token_and_loads_it(monkeypatch, tmp_path) -> None:
    paths = paths_for(tmp_path)
    monkeypatch.setattr(windows_host, "machine_paths", lambda: paths)
    monkeypatch.setattr(windows_host, "protect_secret", lambda value: f"dpapi:protected-{value}")
    monkeypatch.setattr(
        windows_host,
        "unprotect_secret",
        lambda value: value.removeprefix("dpapi:protected-"),
    )

    collector_client.CollectorConfig(base_url="http://server:5173", token="device-token").save(
        paths.config_path
    )

    raw = json.loads(paths.config_path.read_text(encoding="utf-8"))
    assert raw["token"] == "dpapi:protected-device-token"
    assert collector_client.CollectorConfig.load(paths.config_path).token == "device-token"


def test_managed_task_runs_in_current_interactive_user_session(monkeypatch) -> None:
    monkeypatch.setenv("USERDOMAIN", "WAREHOUSE")
    monkeypatch.setenv("USERNAME", "operator")
    xml = windows_host.managed_task_xml(
        r"C:\Users\operator\AppData\Local\CargoPlatformCollector\collector.exe"
    )

    assert "<LogonTrigger>" in xml
    assert "WAREHOUSE\\operator" in xml
    assert "<LogonType>InteractiveToken</LogonType>" in xml
    assert "<RestartOnFailure>" in xml
    assert "<Interval>PT1M</Interval>" in xml
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml
    assert "S-1-5-18" not in xml
    assert "--managed-run" in xml


def test_failed_install_restores_previous_exe_config_and_state(monkeypatch, tmp_path) -> None:
    paths = paths_for(tmp_path)
    source_exe = tmp_path / "release.exe"
    source_exe.write_bytes(b"MZnew collector")
    paths.exe_path.parent.mkdir(parents=True)
    paths.exe_path.write_bytes(b"MZold collector")
    write_json(paths.config_path, {"base_url": "http://old", "token": "old-token"})
    write_json(paths.state_path, {"idle_watermarks": {"printer": 88}})
    original_state = paths.state_path.read_bytes()
    old_hash = hashlib.sha256(paths.exe_path.read_bytes()).hexdigest()
    monkeypatch.setattr(windows_host, "machine_paths", lambda: paths)
    monkeypatch.setattr(windows_host, "protect_secret", lambda value: f"dpapi:{value}")

    def failing_health_runner(command, **_kwargs):
        if str(command[0]).lower().endswith(".exe") and "--check" in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = windows_host.install_collector(
        source_exe,
        migrate_existing=True,
        runner=failing_health_runner,
    )

    assert result.success is False
    assert result.rolled_back is True
    assert hashlib.sha256(paths.exe_path.read_bytes()).hexdigest() == old_hash
    assert json.loads(paths.config_path.read_text(encoding="utf-8"))["token"] == "old-token"
    assert paths.state_path.read_bytes() == original_state


def test_failed_install_preserves_rotated_connection_credential(monkeypatch, tmp_path) -> None:
    paths = paths_for(tmp_path)
    source_exe = tmp_path / "release.exe"
    source_exe.write_bytes(b"MZnew collector")
    paths.exe_path.parent.mkdir(parents=True)
    paths.exe_path.write_bytes(b"MZold collector")
    write_json(paths.config_path, {"base_url": "http://old/api/v1", "token": "old-token"})
    write_json(paths.state_path, {"idle_watermarks": {"printer": 88}})
    monkeypatch.setattr(windows_host, "machine_paths", lambda: paths)
    monkeypatch.setattr(windows_host, "protect_secret", lambda value: f"dpapi:cipher-{value}")
    monkeypatch.setattr(
        windows_host,
        "exchange_connection_code",
        lambda _code: ("http://new/api/v1", "new-token"),
    )

    def failing_health_runner(command, **_kwargs):
        if str(command[0]).lower().endswith(".exe") and "--check" in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = windows_host.install_collector(
        source_exe,
        connection_code="CP1.code",
        runner=failing_health_runner,
    )

    config = json.loads(paths.config_path.read_text(encoding="utf-8"))
    assert result.success is False
    assert result.rolled_back is True
    assert config["base_url"] == "http://new/api/v1"
    assert config["token"] == "dpapi:cipher-new-token"
    assert json.loads(paths.state_path.read_text(encoding="utf-8"))["idle_watermarks"]["printer"] == 88


def test_failed_install_restores_and_restarts_previous_task(monkeypatch, tmp_path) -> None:
    paths = paths_for(tmp_path)
    source_exe = tmp_path / "release.exe"
    source_exe.write_bytes(b"MZnew collector")
    paths.exe_path.parent.mkdir(parents=True)
    paths.exe_path.write_bytes(b"MZold collector")
    write_json(paths.config_path, {"base_url": "http://old", "token": "old-token"})
    monkeypatch.setattr(windows_host, "machine_paths", lambda: paths)
    monkeypatch.setattr(windows_host, "protect_secret", lambda _value: "dpapi:cipher")
    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        command = [str(part) for part in command]
        commands.append(command)
        if command[:3] == ["schtasks.exe", "/Query", "/TN"]:
            return subprocess.CompletedProcess(command, 0, stdout="<Task />", stderr="")
        if command[0].lower().endswith(".exe") and "--check" in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = windows_host.install_collector(source_exe, runner=runner)

    assert result.rolled_back is True
    assert commands[-1] == ["schtasks.exe", "/Run", "/TN", "CargoPlatformCollector"]


def test_failed_install_reports_incomplete_rollback_when_previous_task_will_not_restart(
    monkeypatch,
    tmp_path,
) -> None:
    paths = paths_for(tmp_path)
    source_exe = tmp_path / "release.exe"
    source_exe.write_bytes(b"MZnew collector")
    write_json(paths.config_path, {"base_url": "http://old", "token": "old-token"})
    monkeypatch.setattr(windows_host, "machine_paths", lambda: paths)
    monkeypatch.setattr(windows_host, "protect_secret", lambda _value: "dpapi:cipher")

    def runner(command, **_kwargs):
        command = [str(part) for part in command]
        if command[:3] == ["schtasks.exe", "/Query", "/TN"]:
            return subprocess.CompletedProcess(command, 0, stdout="<Task />", stderr="")
        if command[0].lower().endswith(".exe") and "--check" in command:
            raise subprocess.CalledProcessError(1, command)
        if command[:2] == ["schtasks.exe", "/Run"]:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = windows_host.install_collector(source_exe, runner=runner)

    assert result.success is False
    assert result.rolled_back is False
    assert "回滚失败" in result.message


def test_install_stops_task_before_snapshotting_state(monkeypatch, tmp_path) -> None:
    paths = paths_for(tmp_path)
    source_exe = tmp_path / "release.exe"
    source_exe.write_bytes(b"MZcollector")
    write_json(paths.config_path, {"base_url": "http://server", "token": "device-token"})
    monkeypatch.setattr(windows_host, "machine_paths", lambda: paths)
    monkeypatch.setattr(windows_host, "protect_secret", lambda _value: "dpapi:cipher")
    events: list[str] = []
    real_snapshot = windows_host._snapshot_files

    def snapshot(*args, **kwargs):
        events.append("snapshot")
        return real_snapshot(*args, **kwargs)

    def runner(command, **_kwargs):
        command = [str(part) for part in command]
        if command[:2] == ["schtasks.exe", "/End"]:
            events.append("end")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(windows_host, "_snapshot_files", snapshot)

    result = windows_host.install_collector(source_exe, runner=runner)

    assert result.success is True
    assert events.index("end") < events.index("snapshot")


def test_successful_install_encrypts_config_in_every_backup(monkeypatch, tmp_path) -> None:
    paths = paths_for(tmp_path)
    source_exe = tmp_path / "release.exe"
    source_exe.write_bytes(b"MZcollector")
    write_json(paths.config_path, {"base_url": "http://server", "token": "plain-secret"})
    monkeypatch.setattr(windows_host, "machine_paths", lambda: paths)
    monkeypatch.setattr(windows_host, "protect_secret", lambda _value: "dpapi:ciphertext")

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = windows_host.install_collector(source_exe, migrate_existing=True, runner=runner)

    assert result.success is True
    configs = list((paths.data_dir / "backups").rglob("collector-config.json"))
    assert configs
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["token"] == "dpapi:ciphertext"
        for path in configs
    )


def test_install_registers_and_starts_only_named_managed_task(monkeypatch, tmp_path) -> None:
    paths = paths_for(tmp_path)
    source_exe = tmp_path / "release.exe"
    source_exe.write_bytes(b"MZcollector")
    write_json(paths.config_path, {"base_url": "http://server", "token": "device-token"})
    monkeypatch.setattr(windows_host, "machine_paths", lambda: paths)
    monkeypatch.setattr(windows_host, "protect_secret", lambda value: f"dpapi:{value}")
    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        commands.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = windows_host.install_collector(source_exe, runner=runner)

    assert result.success is True
    task_commands = [command for command in commands if command[0].lower() == "schtasks.exe"]
    assert any("/Create" in command and "CargoPlatformCollector" in command for command in task_commands)
    assert any("/Run" in command and "CargoPlatformCollector" in command for command in task_commands)
    assert all("taskkill" not in command[0].lower() for command in commands)


def test_client_parser_exposes_managed_lifecycle_flags() -> None:
    args = collector_client.build_parser().parse_args(
        [
            "--install-code-file",
            "connection.txt",
            "--install-existing",
            "--managed-run",
            "--uninstall",
            "--quiet",
        ]
    )

    assert args.install_code_file == "connection.txt"
    assert args.install_existing is True
    assert args.managed_run is True
    assert args.uninstall is True
    assert args.quiet is True


def test_double_click_opens_setup_without_elevation(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["collector.exe"])
    monkeypatch.setattr(collector_client, "show_setup_ui", lambda: 7)

    assert collector_client.main() == 7


def test_quiet_install_succeeds_for_standard_user(monkeypatch) -> None:
    installed: list[bool] = []
    monkeypatch.setattr(sys, "argv", ["collector.exe", "--install-existing", "--quiet"])
    monkeypatch.setattr(
        windows_host,
        "install_collector",
        lambda *_args, **_kwargs: installed.append(True)
        or windows_host.InstallResult(True, False, Path("backup"), "ok"),
    )

    assert collector_client.main() == 0
    assert installed == [True]
