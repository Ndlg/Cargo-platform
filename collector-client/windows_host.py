from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
import html
import getpass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from typing import Callable
from urllib.parse import urlsplit
import urllib.request


TASK_NAME = "CargoPlatformCollector"
APP_DIR_NAME = "CargoPlatformCollector"
TRUSTED_UPGRADE_EPOCH = "__trusted_upgrade_pending__"


@dataclass(frozen=True)
class WindowsCollectorPaths:
    install_dir: Path
    exe_path: Path
    data_dir: Path
    config_path: Path
    state_path: Path
    log_path: Path
    legacy_home: Path


@dataclass(frozen=True)
class MigrationResult:
    backup_path: Path
    copied: tuple[str, ...]
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class InstallResult:
    success: bool
    rolled_back: bool
    backup_path: Path
    message: str


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def decode_connection_code(code: str) -> tuple[str, str]:
    text = str(code or "").strip()
    if not text.startswith("CP1."):
        raise ValueError("连接码格式无效")
    try:
        encoded = text[4:]
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        base_url = str(payload.get("base_url") or "").strip().rstrip("/")
        token = str(payload.get("token") or "").strip()
        parsed = urlsplit(base_url)
        parsed.port
        if (
            payload.get("v") != 1
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or not token
        ):
            raise ValueError
        base_url = f"{parsed.scheme}://{parsed.netloc}"
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("连接码格式无效") from exc
    return base_url, token


def _dpapi(value: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise OSError("DPAPI machine protection requires Windows")
    source_buffer = ctypes.create_string_buffer(value)
    source = _DataBlob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def protect_secret(value: str) -> str:
    return "dpapi:" + base64.b64encode(_dpapi(value.encode("utf-8"), protect=True)).decode("ascii")


def unprotect_secret(value: str) -> str:
    text = str(value or "")
    if not text.startswith("dpapi:"):
        return text
    try:
        protected = base64.b64decode(text[6:], validate=True)
        return _dpapi(protected, protect=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("受保护的采集器凭据无效") from exc


def machine_paths() -> WindowsCollectorPaths:
    local_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    install_dir = local_data / APP_DIR_NAME
    data_dir = install_dir
    return WindowsCollectorPaths(
        install_dir=install_dir,
        exe_path=install_dir / "collector.exe",
        data_dir=data_dir,
        config_path=data_dir / "collector-config.json",
        state_path=data_dir / "collector-state.json",
        log_path=data_dir / "collector.log",
        legacy_home=local_data / APP_DIR_NAME,
    )


def is_machine_config_path(path: Path) -> bool:
    return os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(machine_paths().config_path))


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _protect_config_copy(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    token = str(payload.get("token") or "")
    if token and not token.startswith("dpapi:"):
        payload["token"] = protect_secret(token)
        _atomic_write_json(path, payload)


def _backup_directory(data_dir: Path, prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    path = data_dir / "backups" / f"{prefix}-{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def migrate_legacy_home(paths: WindowsCollectorPaths) -> MigrationResult:
    backup_path = _backup_directory(paths.data_dir, "legacy")
    destinations = {
        "collector-config.json": paths.config_path,
        "collector-state.json": paths.state_path,
        "collector.log": paths.log_path,
    }
    copied: list[str] = []
    skipped: list[str] = []
    for name, destination in destinations.items():
        source = paths.legacy_home / name
        if not source.is_file():
            continue
        _atomic_copy(source, backup_path / name)
        if name == "collector-config.json":
            _protect_config_copy(backup_path / name)
        if destination.exists():
            skipped.append(name)
            continue
        _atomic_copy(source, destination)
        copied.append(name)
    return MigrationResult(backup_path=backup_path, copied=tuple(copied), skipped=tuple(skipped))


def mark_trusted_legacy_state(path: Path) -> None:
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    source_epochs = dict(payload.get("source_epochs") or {})
    components = set(dict(payload.get("idle_watermarks") or {}))
    components.update(
        component
        for key in dict(payload.get("capture_watermarks") or {})
        if (component := str(key).partition(":")[2])
    )
    pending = [component for component in components if component not in source_epochs]
    if pending:
        source_epochs.update({component: TRUSTED_UPGRADE_EPOCH for component in pending})
        payload["source_epochs"] = source_epochs
        _atomic_write_json(path, payload)


def managed_task_xml(exe_path: str | Path) -> str:
    command = html.escape(str(exe_path))
    domain = str(os.environ.get("USERDOMAIN") or "").strip()
    username = str(os.environ.get("USERNAME") or getpass.getuser()).strip()
    user_id = html.escape(f"{domain}\\{username}" if domain else username)
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{user_id}</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{user_id}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author"><Exec><Command>{command}</Command><Arguments>--managed-run --quiet</Arguments></Exec></Actions>
</Task>
'''


def _api_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    return normalized if normalized.endswith("/api/v1") else f"{normalized}/api/v1"


def exchange_connection_code(code: str) -> tuple[str, str]:
    base_url, one_time_token = decode_connection_code(code)
    identity = (os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown-machine").strip()
    try:
        from collector_build_info import CLIENT_VERSION
    except ModuleNotFoundError:
        CLIENT_VERSION = "development"
    payload = json.dumps(
        {
            "token": one_time_token,
            "collector_id": identity,
            "collector_name": identity,
            "source_machine": identity,
            "client_version": CLIENT_VERSION,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{_api_base_url(base_url)}/collector-runtime/enroll",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    token = str(result.get("collector_token") or "").strip()
    if not token:
        raise RuntimeError("服务器未返回采集器凭据")
    return _api_base_url(base_url), token


def _run(runner: Callable[..., subprocess.CompletedProcess], command: list[str], *, check: bool = True):
    return runner(command, check=check, capture_output=True, text=True)


def _snapshot_files(paths: WindowsCollectorPaths, backup_path: Path) -> dict[Path, Path | None]:
    snapshots: dict[Path, Path | None] = {}
    for path in (paths.exe_path, paths.config_path, paths.state_path, paths.log_path):
        if path.exists():
            backup = backup_path / path.name
            _atomic_copy(path, backup)
            if path == paths.config_path:
                _protect_config_copy(backup)
            snapshots[path] = backup
        else:
            snapshots[path] = None
    return snapshots


def _restore_files(snapshots: dict[Path, Path | None]) -> None:
    for destination, backup in snapshots.items():
        if backup is None:
            destination.unlink(missing_ok=True)
        else:
            _atomic_copy(backup, destination)


def install_collector(
    exe_path: str | Path,
    connection_code: str | None = None,
    migrate_existing: bool = False,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> InstallResult:
    source_exe = Path(exe_path).resolve()
    if not source_exe.is_file():
        raise ValueError("采集器安装包不是有效的 Windows EXE")
    with source_exe.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise ValueError("采集器安装包不是有效的 Windows EXE")
    paths = machine_paths()
    backup_path = _backup_directory(paths.data_dir, "install")
    task_backup = backup_path / "scheduled-task.xml"
    queried = _run(runner, ["schtasks.exe", "/Query", "/TN", TASK_NAME, "/XML"], check=False)
    task_existed = (
        getattr(queried, "returncode", 1) == 0
        and bool(str(getattr(queried, "stdout", "")).strip())
    )
    if task_existed:
        task_backup.write_text(str(queried.stdout), encoding="utf-16")

    snapshots: dict[Path, Path | None] = {}
    original_config: bytes | None = None
    rotated_config: dict[str, object] | None = None
    try:
        _run(runner, ["schtasks.exe", "/End", "/TN", TASK_NAME], check=False)
        original_config = paths.config_path.read_bytes() if paths.config_path.exists() else None
        snapshots = _snapshot_files(paths, backup_path)
        if migrate_existing:
            migrate_legacy_home(paths)
            mark_trusted_legacy_state(paths.state_path)
        if source_exe != paths.exe_path.resolve():
            _atomic_copy(source_exe, paths.exe_path)

        config: dict[str, object] = {}
        if paths.config_path.exists():
            config = json.loads(paths.config_path.read_text(encoding="utf-8-sig"))
        if connection_code:
            config["base_url"], config["token"] = exchange_connection_code(connection_code)
            rotated_config = dict(config)
        token = str(config.get("token") or "")
        if token.startswith("dpapi:"):
            unprotect_secret(token)
        elif token:
            config["token"] = protect_secret(token)
        else:
            raise ValueError("未找到可用的采集器连接凭据")
        _atomic_write_json(paths.config_path, config)

        task_xml = backup_path / "managed-task.xml"
        task_xml.write_text(managed_task_xml(paths.exe_path), encoding="utf-16")
        _run(runner, ["schtasks.exe", "/Create", "/TN", TASK_NAME, "/XML", str(task_xml), "/F"])
        _run(runner, [str(paths.exe_path), "--managed-run", "--check", "--no-log-file"])
        _run(runner, ["schtasks.exe", "/Run", "/TN", TASK_NAME])
        return InstallResult(True, False, backup_path, "采集器安装成功")
    except Exception as exc:
        try:
            _run(runner, ["schtasks.exe", "/End", "/TN", TASK_NAME], check=False)
            if snapshots:
                _restore_files(snapshots)
            if rotated_config is not None:
                token = str(rotated_config.get("token") or "")
                if token and not token.startswith("dpapi:"):
                    rotated_config["token"] = protect_secret(token)
                _atomic_write_json(paths.config_path, rotated_config)
            elif original_config is not None:
                _atomic_write_bytes(paths.config_path, original_config)
            if task_existed:
                _run(runner, ["schtasks.exe", "/Create", "/TN", TASK_NAME, "/XML", str(task_backup), "/F"])
                _run(runner, ["schtasks.exe", "/Run", "/TN", TASK_NAME])
            else:
                _run(runner, ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
        except Exception as rollback_exc:
            return InstallResult(False, False, backup_path, f"安装失败且回滚失败：{exc}; {rollback_exc}")
        suffix = "；新连接凭据已保留，可重试安装" if rotated_config is not None else ""
        return InstallResult(False, True, backup_path, f"安装失败，已回滚{suffix}：{exc}")


def uninstall_collector(
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> InstallResult:
    paths = machine_paths()
    backup_path = _backup_directory(paths.data_dir, "uninstall")
    try:
        _run(runner, ["schtasks.exe", "/End", "/TN", TASK_NAME], check=False)
        _run(runner, ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
        if paths.exe_path.exists():
            _atomic_copy(paths.exe_path, backup_path / paths.exe_path.name)
            paths.exe_path.unlink()
        return InstallResult(True, False, backup_path, "采集器已卸载，采集状态和配置已保留")
    except Exception as exc:
        return InstallResult(False, False, backup_path, f"卸载失败：{exc}")


def current_executable() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
