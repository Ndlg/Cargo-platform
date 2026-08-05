from __future__ import annotations

import argparse
from contextlib import closing
import http.client
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import sys
import time
from typing import Any, Callable, Iterable
import urllib.error
import urllib.parse
import urllib.request

import windows_host

try:
    from collector_build_info import CLIENT_VERSION
except ModuleNotFoundError:
    CLIENT_VERSION = "development"

DEFAULT_BASE_URL = "http://127.0.0.1:5173/api/v1"
DEFAULT_WEB_PORT = 5173
DEFAULT_COLLECTOR_NAME = ""
ASSIGNMENT_PROTOCOL_VERSION = 2
MAX_BATCH_SIZE = 100
LOGGER = logging.getLogger("cargo_platform_collector")
LEGACY_DEFAULT_COLLECTOR_NAMES = {"", "Cargo Platform 采集器", "业务机采集器", "本机采集器", "采集器"}
NETWORK_RETRY_EXCEPTIONS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    http.client.HTTPException,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_db_time_from_iso(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:19].replace("T", " ")
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def comparable_local_db_time(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()


def source_generation_key(value: str) -> str:
    return sha256_text(value)[:16]


def default_home() -> Path:
    configured = os.environ.get("CARGO_PLATFORM_COLLECTOR_HOME")
    if configured:
        return Path(configured)

    local_app_data = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local_app_data:
        return Path(local_app_data) / "CargoPlatformCollector"

    return Path.home() / ".cargo-platform-collector"


def default_config_path() -> Path:
    return default_home() / "collector-config.json"


def default_state_path(config_path: Path) -> Path:
    return config_path.parent / "collector-state.json"


def default_log_path(config_path: Path) -> Path:
    return config_path.parent / "collector.log"


def machine_name() -> str:
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown-machine").strip()


def default_collector_id() -> str:
    return machine_name()


def default_collector_name() -> str:
    return machine_name()


def normalize_collector_name(value: Any) -> str:
    name = str(value or "").strip()
    return default_collector_name() if name in LEGACY_DEFAULT_COLLECTOR_NAMES else name


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def normalize_base_url(value: str) -> str:
    raw_value = value.strip().rstrip("/")
    if not raw_value:
        return raw_value

    has_scheme = raw_value.startswith(("http://", "https://"))
    normalized = raw_value if has_scheme else f"http://{raw_value}"
    parsed = urllib.parse.urlparse(normalized)
    path = parsed.path.rstrip("/")

    if not has_scheme and not parsed.port and path in {"", "/"}:
        netloc = parsed.hostname or raw_value
        normalized = urllib.parse.urlunparse(
            (
                parsed.scheme,
                f"{netloc}:{DEFAULT_WEB_PORT}",
                "",
                "",
                "",
                "",
            )
        )

    normalized = normalized.rstrip("/")
    if normalized.endswith("/api/v1"):
        return normalized
    if normalized.endswith("/api"):
        return f"{normalized}/v1"
    return f"{normalized}/api/v1"


def display_base_url(value: str) -> str:
    normalized = normalize_base_url(value)
    try:
        parsed = urllib.parse.urlparse(normalized)
    except ValueError:
        return value
    if (
        parsed.scheme == "http"
        and parsed.port == DEFAULT_WEB_PORT
        and parsed.path.rstrip("/") == "/api/v1"
        and parsed.hostname
    ):
        return parsed.hostname
    return value


def validate_public_base_url(value: str) -> None:
    try:
        parsed = urllib.parse.urlparse(value)
        port = parsed.port
    except ValueError:
        port = None
    if port == 8000:
        raise ValueError(
            "不要填写后端 8000 端口。业务机只需要填写服务器 IP，采集器会自动补齐。"
        )


def default_adapter_configs() -> list[dict[str, str]]:
    return [
        {
            "source_component": "cainiao-cnprint",
            "display_name": "Cainiao CNPrintClient",
            "db_path": r"C:\Program Files (x86)\CNPrintTool\resources\print.db",
        },
        {
            "source_component": "cloud-print-client",
            "display_name": "CloudPrintClient",
            "db_path": r"C:\Program Files (x86)\CloudPrintClient\resources\print.db",
        },
    ]


@dataclass
class CollectorConfig:
    base_url: str = DEFAULT_BASE_URL
    token: str = ""
    workspace_id: int | None = None
    collector_id: str = field(default_factory=default_collector_id)
    collector_name: str = field(default_factory=default_collector_name)
    interval: int = 3
    batch_size: int = 50
    simulate: bool = False
    adapters: list[dict[str, str]] = field(default_factory=default_adapter_configs)

    @classmethod
    def load(cls, path: Path) -> "CollectorConfig":
        payload = read_json(path)
        if not payload:
            return cls()
        token = str(payload.get("token") or "")
        if token.startswith("dpapi:"):
            token = windows_host.unprotect_secret(token)
        return cls(
            base_url=normalize_base_url(str(payload.get("base_url") or DEFAULT_BASE_URL)),
            token=token,
            workspace_id=int(payload["workspace_id"]) if payload.get("workspace_id") else None,
            collector_id=str(payload.get("collector_id") or default_collector_id()),
            collector_name=normalize_collector_name(payload.get("collector_name")),
            interval=max(1, int(payload.get("interval") or 3)),
            batch_size=min(MAX_BATCH_SIZE, max(1, int(payload.get("batch_size") or 50))),
            simulate=bool(payload.get("simulate") or False),
            adapters=list(payload.get("adapters") or default_adapter_configs()),
        )

    def apply_args(self, args: argparse.Namespace) -> "CollectorConfig":
        return CollectorConfig(
            base_url=normalize_base_url(args.base_url or self.base_url),
            token=args.token or self.token,
            workspace_id=args.workspace_id if args.workspace_id is not None else self.workspace_id,
            collector_id=args.collector_id or self.collector_id or default_collector_id(),
            collector_name=normalize_collector_name(args.collector_name or self.collector_name),
            interval=args.interval if args.interval is not None else self.interval,
            batch_size=min(
                MAX_BATCH_SIZE,
                max(1, args.batch_size if args.batch_size is not None else self.batch_size),
            ),
            simulate=bool(args.simulate or self.simulate),
            adapters=self.adapters,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": normalize_base_url(self.base_url),
            "token": self.token,
            "workspace_id": self.workspace_id,
            "collector_id": self.collector_id,
            "collector_name": self.collector_name,
            "interval": self.interval,
            "batch_size": self.batch_size,
            "simulate": self.simulate,
            "adapters": self.adapters,
        }

    def save(self, path: Path) -> None:
        payload = self.to_dict()
        if self.token and windows_host.is_machine_config_path(path):
            payload["token"] = windows_host.protect_secret(self.token)
        write_json(path, payload)


@dataclass(frozen=True)
class PrintTaskRow:
    rowid: int
    task_id: str | None
    msg: str
    task_time: str | None


EMPTY_PREFIX_FINGERPRINT = bytes(32).hex()


def extend_prefix_fingerprint(seed: str, rows: Iterable[PrintTaskRow]) -> str:
    digest = bytes.fromhex(seed)
    for row in rows:
        row_digest = PrintDbSnapshot.row_fingerprint(row)
        digest = hashlib.sha256(
            digest + str(row.rowid).encode("ascii") + b"\0" + row_digest.encode("ascii")
        ).digest()
    return digest.hex()


@dataclass(frozen=True)
class PrintDbSnapshot:
    generation: str | None
    change_token: str
    max_rowid: int
    cursor_rowid: int
    cursor_fingerprint: str | None
    origin_fingerprint: str | None
    tail_fingerprint: str | None
    batch_last_rowid: int
    rows: tuple[PrintTaskRow, ...]

    @staticmethod
    def row_fingerprint(row: PrintTaskRow) -> str:
        return sha256_text(f"{row.task_id}\0{row.task_time}\0{row.msg}")

    def fingerprint_for(self, rowid: int) -> str | None:
        if rowid <= 0:
            return None
        if rowid == self.cursor_rowid:
            return self.cursor_fingerprint
        if rowid == 1:
            return self.origin_fingerprint
        if rowid == self.max_rowid:
            return self.tail_fingerprint
        row = next((item for item in self.rows if item.rowid == rowid), None)
        return self.row_fingerprint(row) if row is not None else None

    def logical_epoch(self) -> str:
        signature = "|".join(
            str(value or "")
            for value in (
                self.generation,
                self.max_rowid,
                self.origin_fingerprint,
                self.cursor_fingerprint,
                self.tail_fingerprint,
            )
        )
        return source_generation_key(signature)


@dataclass(frozen=True)
class PrintDbAdapter:
    source_component: str
    display_name: str
    db_path: Path

    def connect(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    def get_status(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return {
                "status": "missing",
                "db_path": str(self.db_path),
            }

        try:
            with self.connect() as connection:
                task_table = connection.execute(
                    "select name from sqlite_master where type = 'table' and name = 'task'"
                ).fetchone()
                if task_table is None:
                    return {
                        "status": "unsupported",
                        "db_path": str(self.db_path),
                        "error": "task table not found",
                    }

                row = connection.execute(
                    "select count(*) as task_count, coalesce(max(rowid), 0) as max_rowid from task"
                ).fetchone()
                return {
                    "status": "ready",
                    "db_path": str(self.db_path),
                    "task_count": int(row["task_count"] or 0),
                    "max_rowid": int(row["max_rowid"] or 0),
                }
        except sqlite3.Error as exc:
            return {
                "status": "error",
                "db_path": str(self.db_path),
                "error": str(exc),
            }

    def max_rowid(self) -> int:
        status = self.get_status()
        if status.get("status") != "ready":
            return 0
        return int(status.get("max_rowid") or 0)

    def generation(self) -> str | None:
        try:
            stat = self.db_path.stat()
        except OSError:
            return None
        return f"{stat.st_dev}:{stat.st_ino}"

    def change_token(self) -> str:
        parts = []
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-journal"),
        ):
            try:
                stat = path.stat()
            except OSError:
                continue
            parts.append(f"{path.name}:{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}")
        return sha256_text("|".join(parts))

    def snapshot(self, cursor: int, limit: int) -> PrintDbSnapshot | None:
        if not self.db_path.exists():
            return None
        with closing(self.connect()) as connection:
            connection.execute("begin")
            maximum = int(
                connection.execute("select coalesce(max(rowid), 0) from task").fetchone()[0]
            )

            def task_row(rowid: int) -> PrintTaskRow | None:
                if rowid <= 0:
                    return None
                row = connection.execute(
                    "select rowid, taskID, msg, time from task where rowid = ?",
                    (rowid,),
                ).fetchone()
                if row is None:
                    return None
                return PrintTaskRow(
                    rowid=int(row["rowid"]),
                    task_id=row["taskID"],
                    msg=row["msg"] or "",
                    task_time=row["time"],
                )

            rows = tuple(
                PrintTaskRow(
                    rowid=int(row["rowid"]),
                    task_id=row["taskID"],
                    msg=row["msg"] or "",
                    task_time=row["time"],
                )
                for row in connection.execute(
                    """
                    select rowid, taskID, msg, time
                    from task
                    where rowid > ?
                    order by rowid asc
                    limit ?
                    """,
                    (cursor, limit),
                ).fetchall()
            )
            origin = task_row(1)
            cursor_row = task_row(cursor)
            tail = task_row(maximum)
            batch_last_rowid = rows[-1].rowid if rows else cursor
        return PrintDbSnapshot(
            generation=self.generation(),
            change_token=self.change_token(),
            max_rowid=maximum,
            cursor_rowid=cursor,
            cursor_fingerprint=(
                PrintDbSnapshot.row_fingerprint(cursor_row) if cursor_row is not None else None
            ),
            origin_fingerprint=(
                PrintDbSnapshot.row_fingerprint(origin) if origin is not None else None
            ),
            tail_fingerprint=(
                PrintDbSnapshot.row_fingerprint(tail) if tail is not None else None
            ),
            batch_last_rowid=batch_last_rowid,
            rows=rows,
        )

    def prefix_fingerprint(self, start_rowid: int, end_rowid: int) -> str:
        # ponytail: one streaming catch-up audit; add chunk digests only if a round outgrows it.
        if end_rowid <= start_rowid:
            return EMPTY_PREFIX_FINGERPRINT
        with closing(self.connect()) as connection:
            connection.execute("begin")
            rows = connection.execute(
                """
                select rowid, taskID, msg, time
                from task
                where rowid > ? and rowid <= ?
                order by rowid asc
                """,
                (start_rowid, end_rowid),
            )
            return extend_prefix_fingerprint(
                EMPTY_PREFIX_FINGERPRINT,
                (
                    PrintTaskRow(
                        rowid=int(row["rowid"]),
                        task_id=row["taskID"],
                        msg=row["msg"] or "",
                        task_time=row["time"],
                    )
                    for row in rows
                ),
            )

    def row_fingerprint(self, rowid: int) -> str | None:
        rows = self.read_since(rowid - 1, 1) if rowid > 0 else []
        if not rows or rows[0].rowid != rowid:
            return None
        row = rows[0]
        return sha256_text(f"{row.task_id}\0{row.task_time}\0{row.msg}")

    def logical_epoch(self, cursor: int | None) -> str:
        snapshot = self.snapshot(cursor or 0, 0)
        return snapshot.logical_epoch() if snapshot is not None else source_generation_key("")

    def initial_cursor_for_active_task(self, capture_started_at: str | None) -> int:
        cutoff = comparable_local_db_time(capture_started_at)
        if not cutoff or not self.db_path.exists():
            return 0

        try:
            with self.connect() as connection:
                rows = connection.execute(
                    "select rowid, time from task order by rowid asc"
                ).fetchall()
        except sqlite3.Error:
            return 0

        baseline = 0
        for row in rows:
            row_time = comparable_local_db_time(row["time"])
            if row_time is None:
                return baseline
            if row_time >= cutoff:
                return baseline
            baseline = int(row["rowid"])
        return baseline

    def read_since(
        self,
        rowid: int,
        limit: int,
    ) -> list[PrintTaskRow]:
        if not self.db_path.exists():
            return []

        with self.connect() as connection:
            rows = connection.execute(
                """
                select rowid, taskID, msg, time
                from task
                where rowid > ?
                order by rowid asc
                limit ?
                """,
                (rowid, limit),
            ).fetchall()

        return [
            PrintTaskRow(
                rowid=int(row["rowid"]),
                task_id=row["taskID"],
                msg=row["msg"] or "",
                task_time=row["time"],
            )
            for row in rows
        ]


class CollectorState:
    def __init__(
        self,
        idle_watermarks: dict[str, int] | None = None,
        capture_watermarks: dict[str, int] | None = None,
        db_generations: dict[str, str] | None = None,
        db_change_tokens: dict[str, str] | None = None,
        source_epochs: dict[str, str] | None = None,
        cursor_fingerprints: dict[str, str] | None = None,
        prefix_fingerprints: dict[str, str] | None = None,
        prefix_start_rowids: dict[str, int] | None = None,
        origin_fingerprints: dict[str, str] | None = None,
        ambiguous_replay_until: dict[str, int] | None = None,
        last_upload_at: str | None = None,
        last_reconnect_reason: str | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.idle_watermarks = idle_watermarks or {}
        self.capture_watermarks = capture_watermarks or {}
        self.db_generations = db_generations or {}
        self.db_change_tokens = db_change_tokens or {}
        self.source_epochs = source_epochs or {}
        self.cursor_fingerprints = cursor_fingerprints or {}
        self.prefix_fingerprints = prefix_fingerprints or {}
        self.prefix_start_rowids = prefix_start_rowids or {}
        self.origin_fingerprints = origin_fingerprints or {}
        self.ambiguous_replay_until = ambiguous_replay_until or {}
        self.last_upload_at = last_upload_at
        self.last_reconnect_reason = last_reconnect_reason
        self.state_path = state_path
        self.audited_sources: set[str] = set()

    @classmethod
    def load(cls, path: Path) -> "CollectorState":
        payload = read_json(path)
        idle_watermarks = {
            str(key): int(value)
            for key, value in dict(payload.get("idle_watermarks") or {}).items()
        }
        capture_watermarks = {
            str(key): int(value)
            for key, value in dict(payload.get("capture_watermarks") or {}).items()
        }
        db_generations = {
            str(key): str(value)
            for key, value in dict(payload.get("db_generations") or {}).items()
        }
        db_change_tokens = {
            str(key): str(value)
            for key, value in dict(payload.get("db_change_tokens") or {}).items()
        }
        source_epochs = {
            str(key): str(value)
            for key, value in dict(payload.get("source_epochs") or {}).items()
        }
        cursor_fingerprints = {
            str(key): str(value)
            for key, value in dict(payload.get("cursor_fingerprints") or {}).items()
        }
        prefix_fingerprints = {
            str(key): str(value)
            for key, value in dict(payload.get("prefix_fingerprints") or {}).items()
        }
        prefix_start_rowids = {
            str(key): int(value)
            for key, value in dict(payload.get("prefix_start_rowids") or {}).items()
        }
        origin_fingerprints = {
            str(key): str(value)
            for key, value in dict(payload.get("origin_fingerprints") or {}).items()
        }
        ambiguous_replay_until = {
            str(key): int(value)
            for key, value in dict(payload.get("ambiguous_replay_until") or {}).items()
        }
        return cls(
            idle_watermarks=idle_watermarks,
            capture_watermarks=capture_watermarks,
            db_generations=db_generations,
            db_change_tokens=db_change_tokens,
            source_epochs=source_epochs,
            cursor_fingerprints=cursor_fingerprints,
            prefix_fingerprints=prefix_fingerprints,
            prefix_start_rowids=prefix_start_rowids,
            origin_fingerprints=origin_fingerprints,
            ambiguous_replay_until=ambiguous_replay_until,
            last_upload_at=str(payload.get("last_upload_at") or "").strip() or None,
            last_reconnect_reason=str(payload.get("last_reconnect_reason") or "").strip() or None,
            state_path=path,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "updated_at": utc_now(),
            "idle_watermarks": self.idle_watermarks,
            "capture_watermarks": self.capture_watermarks,
            "db_generations": self.db_generations,
            "db_change_tokens": self.db_change_tokens,
            "source_epochs": self.source_epochs,
            "cursor_fingerprints": self.cursor_fingerprints,
            "prefix_fingerprints": self.prefix_fingerprints,
            "prefix_start_rowids": self.prefix_start_rowids,
            "origin_fingerprints": self.origin_fingerprints,
            "ambiguous_replay_until": self.ambiguous_replay_until,
            "last_upload_at": self.last_upload_at,
            "last_reconnect_reason": self.last_reconnect_reason,
        }

    def save(self, path: Path) -> None:
        write_json(path, self.to_dict())

    def idle_watermark(self, adapter: PrintDbAdapter) -> int | None:
        return self.idle_watermarks.get(adapter.source_component)

    def update_idle_watermark(self, adapter: PrintDbAdapter, rowid: int | None = None) -> None:
        self.idle_watermarks[adapter.source_component] = adapter.max_rowid() if rowid is None else rowid

    def source_cursor(self, adapter: PrintDbAdapter) -> int | None:
        if adapter.source_component in self.source_epochs:
            return self.idle_watermark(adapter)
        suffix = f":{adapter.source_component}"
        legacy = [value for key, value in self.capture_watermarks.items() if key.endswith(suffix)]
        return min(legacy) if legacy else self.idle_watermark(adapter)

    def set_rollback_watermark(
        self,
        adapter: PrintDbAdapter,
        rowid: int,
        task_id: int | None = None,
    ) -> None:
        suffix = f":{adapter.source_component}"
        compatible_task_ids = [
            int(key.partition(":")[0])
            for key in self.capture_watermarks
            if key.endswith(suffix) and key.partition(":")[0].isdigit()
        ]
        for key in [key for key in self.capture_watermarks if key.endswith(suffix)]:
            del self.capture_watermarks[key]
        compatible_task_id = task_id or max(compatible_task_ids, default=None)
        if compatible_task_id is not None:
            self.capture_watermarks[f"{compatible_task_id}:{adapter.source_component}"] = rowid

    def advance_source_cursor(
        self,
        adapter: PrintDbAdapter,
        rowid: int,
        rollback_task_id: int | None = None,
        snapshot: PrintDbSnapshot | None = None,
    ) -> None:
        component = adapter.source_component
        previous_cursor = self.source_cursor(adapter)
        self.update_idle_watermark(adapter, rowid)
        fingerprint = (
            snapshot.fingerprint_for(rowid)
            if snapshot is not None
            else adapter.row_fingerprint(rowid)
        )
        if fingerprint:
            self.cursor_fingerprints[component] = fingerprint
        else:
            self.cursor_fingerprints.pop(component, None)
        if rowid == 0:
            self.prefix_start_rowids[component] = 0
            self.prefix_fingerprints[component] = EMPTY_PREFIX_FINGERPRINT
        elif snapshot is not None:
            base_cursor = previous_cursor if previous_cursor is not None else snapshot.cursor_rowid
            if component not in self.prefix_fingerprints:
                self.prefix_start_rowids[component] = base_cursor
                self.prefix_fingerprints[component] = EMPTY_PREFIX_FINGERPRINT
            if snapshot.cursor_rowid == base_cursor:
                self.prefix_fingerprints[component] = extend_prefix_fingerprint(
                    self.prefix_fingerprints[component],
                    (item for item in snapshot.rows if item.rowid <= rowid),
                )
            else:
                self.prefix_start_rowids[component] = rowid
                self.prefix_fingerprints[component] = EMPTY_PREFIX_FINGERPRINT
        else:
            prefix_start = self.prefix_start_rowids.get(component, 0)
            self.prefix_start_rowids[component] = prefix_start
            self.prefix_fingerprints[component] = adapter.prefix_fingerprint(prefix_start, rowid)
        origin_fingerprint = (
            snapshot.origin_fingerprint
            if snapshot is not None
            else adapter.row_fingerprint(1)
        )
        if origin_fingerprint:
            self.origin_fingerprints[component] = origin_fingerprint
        else:
            self.origin_fingerprints.pop(component, None)
        self.set_rollback_watermark(adapter, rowid, rollback_task_id)
        replay_until = self.ambiguous_replay_until.get(component)
        if replay_until is not None and rowid >= replay_until:
            del self.ambiguous_replay_until[component]

    def baseline_source_cursor(self, adapter: PrintDbAdapter, rowid: int) -> None:
        component = adapter.source_component
        snapshot = adapter.snapshot(rowid, 0)
        self.update_idle_watermark(adapter, rowid)
        fingerprint = snapshot.cursor_fingerprint if snapshot is not None else None
        origin_fingerprint = snapshot.origin_fingerprint if snapshot is not None else None
        if fingerprint:
            self.cursor_fingerprints[component] = fingerprint
        else:
            self.cursor_fingerprints.pop(component, None)
        if origin_fingerprint:
            self.origin_fingerprints[component] = origin_fingerprint
        else:
            self.origin_fingerprints.pop(component, None)
        self.prefix_start_rowids[component] = rowid
        self.prefix_fingerprints[component] = EMPTY_PREFIX_FINGERPRINT
        self.set_rollback_watermark(adapter, rowid)

    def rotate_source_epoch(self, component: str, snapshot: PrintDbSnapshot) -> None:
        previous = self.source_epochs.get(component, "")
        candidate = source_generation_key(
            f"{previous}\0{snapshot.logical_epoch()}\0{snapshot.change_token}"
        )
        if candidate == previous:
            candidate = f"{candidate[:-1]}{'0' if candidate[-1:] != '0' else '1'}"
        self.source_epochs[component] = candidate

    def sync_db_generation(
        self,
        adapter: PrintDbAdapter,
        snapshot: PrintDbSnapshot | None = None,
        audit_prefix: bool = False,
    ) -> None:
        component = adapter.source_component
        cursor = self.source_cursor(adapter)
        snapshot = snapshot or adapter.snapshot(cursor or 0, 0)
        if snapshot is None or snapshot.generation is None:
            return
        generation = snapshot.generation
        max_rowid = snapshot.max_rowid
        previous_change_token = self.db_change_tokens.get(component)
        change_token_changed = bool(
            previous_change_token and previous_change_token != snapshot.change_token
        )
        expected_fingerprint = self.cursor_fingerprints.get(component)
        current_fingerprint = snapshot.cursor_fingerprint
        expected_prefix = self.prefix_fingerprints.get(component)
        expected_origin = self.origin_fingerprints.get(component)
        current_origin = snapshot.origin_fingerprint
        if component not in self.source_epochs:
            has_legacy_cursor = component in self.idle_watermarks or any(
                key.endswith(f":{component}") for key in self.capture_watermarks
            )
            legacy_cursors = [
                self.idle_watermarks.get(component, 0),
                *[
                    value
                    for key, value in self.capture_watermarks.items()
                    if key.endswith(f":{component}")
                ],
            ]
            replay_until = max(legacy_cursors, default=0) if has_legacy_cursor else 0
            replay_legacy_state = bool(replay_until and self.state_path is not None)
            self.source_epochs[component] = (
                snapshot.logical_epoch() if replay_legacy_state or not has_legacy_cursor else ""
            )
            if replay_legacy_state:
                self.advance_source_cursor(adapter, 0, snapshot=snapshot)
                self.ambiguous_replay_until[component] = replay_until
                self.last_reconnect_reason = "source_history_ambiguous"
                cursor = 0
        if component not in self.prefix_fingerprints:
            self.prefix_start_rowids[component] = cursor or snapshot.cursor_rowid
            self.prefix_fingerprints[component] = EMPTY_PREFIX_FINGERPRINT
            expected_prefix = EMPTY_PREFIX_FINGERPRINT
        cursor_changed = bool(
            cursor and expected_fingerprint and current_fingerprint != expected_fingerprint
        )
        origin_changed = bool(expected_origin and current_origin != expected_origin)
        prefix_changed = False
        audit_prefix = audit_prefix or bool(
            change_token_changed and cursor is not None and max_rowid <= cursor
        )
        if audit_prefix and cursor is not None and expected_prefix:
            prefix_changed = adapter.prefix_fingerprint(
                self.prefix_start_rowids.get(component, cursor),
                cursor,
            ) != expected_prefix
        generation_changed = bool(
            self.db_generations.get(component)
            and self.db_generations[component] != generation
        )
        history_changed = cursor_changed or origin_changed or prefix_changed
        reset_detected = (cursor is not None and max_rowid < cursor) or (
            generation_changed and history_changed
        )
        if reset_detected:
            self.rotate_source_epoch(component, snapshot)
            self.advance_source_cursor(adapter, 0, snapshot=snapshot)
            self.ambiguous_replay_until.pop(component, None)
            self.last_reconnect_reason = "db_reset"
        elif history_changed:
            replay_until = max(
                cursor or 0,
                self.ambiguous_replay_until.get(component, 0),
            )
            self.rotate_source_epoch(component, snapshot)
            self.advance_source_cursor(adapter, 0, snapshot=snapshot)
            self.ambiguous_replay_until[component] = replay_until
            self.last_reconnect_reason = "source_history_ambiguous"
        elif cursor and current_fingerprint and not expected_fingerprint:
            self.cursor_fingerprints[component] = current_fingerprint
        if current_origin:
            self.origin_fingerprints[component] = current_origin
        else:
            self.origin_fingerprints.pop(component, None)
        self.db_generations[component] = generation
        self.db_change_tokens[component] = snapshot.change_token
        if audit_prefix:
            self.audited_sources.add(component)

    def pending_count(self, adapters: list[PrintDbAdapter]) -> int | None:
        adapters_by_component = {adapter.source_component: adapter for adapter in adapters}
        total = 0
        for component, adapter in adapters_by_component.items():
            watermark = self.source_cursor(adapter)
            if watermark is None:
                continue
            status = adapter.get_status()
            if status.get("status") != "ready":
                if status.get("status") == "error":
                    self.last_reconnect_reason = "sqlite"
                return None
            total += max(0, int(status.get("max_rowid") or 0) - watermark)
        return total


def adapters_from_config(config: CollectorConfig) -> list[PrintDbAdapter]:
    adapters: list[PrintDbAdapter] = []
    for item in config.adapters:
        source_component = str(item.get("source_component") or "").strip()
        db_path = str(item.get("db_path") or "").strip()
        if not source_component or not db_path:
            continue
        adapters.append(
            PrintDbAdapter(
                source_component=source_component,
                display_name=str(item.get("display_name") or source_component),
                db_path=Path(db_path),
            )
        )
    return adapters


def setup_logging(log_path: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def post_json(base_url: str, path: str, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    validate_public_base_url(base_url)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Collector-Token"] = token
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def http_error_body(exc: urllib.error.HTTPError) -> str:
    return exc.read().decode("utf-8", "replace")


def is_auth_http_error(exc: urllib.error.HTTPError) -> bool:
    return exc.code in {401, 403}


def reconnect_reason(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return "auth" if is_auth_http_error(exc) else "http"
    if isinstance(exc, sqlite3.Error):
        return "sqlite"
    return "network"


class ReconnectNotice:
    def __init__(self, min_interval_seconds: int = 60) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.last_key = ""
        self.last_at = 0.0

    def warning(self, key: str, message: str, *args: object) -> None:
        now = time.monotonic()
        if key == self.last_key and now - self.last_at < self.min_interval_seconds:
            return
        self.last_key = key
        self.last_at = now
        LOGGER.warning(message, *args)

    def reset(self) -> None:
        self.last_key = ""
        self.last_at = 0.0


class CollectorInstanceLock:
    def __init__(self, state_path: Path) -> None:
        self.path = state_path.with_suffix(f"{state_path.suffix}.lock")
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def poll_collector_once(
    config: CollectorConfig,
    state: CollectorState,
    adapters: list[PrintDbAdapter],
    sequence: int,
    config_path: Path | None = None,
) -> CollectorConfig:
    if not config.token:
        raise RuntimeError("collector token is required")
    if config.simulate:
        run_simulator_once(config.base_url, config.token, sequence, config.collector_id, config.collector_name)
    else:
        run_sqlite_once(
            config.base_url,
            config.token,
            state,
            adapters,
            config.batch_size,
            config.collector_id,
            config.collector_name,
        )
    return config


def recover_from_http_error(
    exc: urllib.error.HTTPError,
    config: CollectorConfig,
    config_path: Path | None,
    notice: ReconnectNotice,
) -> CollectorConfig:
    body = http_error_body(exc)
    if is_auth_http_error(exc):
        notice.warning(
            "auth-no-password",
            "collector token is invalid or expired; generate a new token in the web console and restart with --token.",
        )
        return config

    notice.warning(
        f"http-{exc.code}",
        "collector server request failed; waiting for server recovery: HTTP %s %s",
        exc.code,
        body,
    )
    return config


def poll_collector_safely(
    config: CollectorConfig,
    state: CollectorState,
    adapters: list[PrintDbAdapter],
    sequence: int,
    config_path: Path | None,
    notice: ReconnectNotice,
    *,
    poll_once: Callable[..., CollectorConfig] = poll_collector_once,
) -> CollectorConfig:
    try:
        config = poll_once(config, state, adapters, sequence, config_path)
        notice.reset()
    except urllib.error.HTTPError as exc:
        state.last_reconnect_reason = reconnect_reason(exc)
        config = recover_from_http_error(exc, config, config_path, notice)
    except NETWORK_RETRY_EXCEPTIONS as exc:
        state.last_reconnect_reason = reconnect_reason(exc)
        notice.warning(
            "network",
            "collector network was interrupted; staying in background and retrying: %s",
            exc,
        )
    except sqlite3.Error as exc:
        state.last_reconnect_reason = reconnect_reason(exc)
        LOGGER.error("collector sqlite error: %s", exc)
    except Exception as exc:  # noqa: BLE001 - one malformed poll must not kill collection.
        state.last_reconnect_reason = "unexpected"
        notice.warning(
            "unexpected",
            "collector unexpected error; staying in background and retrying: %s",
            exc,
        )
    return config


def save_state_safely(state: CollectorState, state_path: Path, notice: ReconnectNotice) -> None:
    try:
        state.save(state_path)
    except Exception as exc:  # noqa: BLE001 - state persistence must not kill the listener.
        state.last_reconnect_reason = "state_save"
        notice.warning(
            "state-save",
            "collector state save failed; continuing in background: %s",
            exc,
        )


def extract_document_id(raw_payload: str, fallback: str | None) -> str | None:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return fallback

    task = payload.get("task") if isinstance(payload, dict) else None
    documents = task.get("documents") if isinstance(task, dict) else None
    if isinstance(documents, list) and documents:
        first_document = documents[0]
        if isinstance(first_document, dict) and first_document.get("documentID"):
            return str(first_document["documentID"])
    if isinstance(task, dict) and task.get("taskID"):
        return str(task["taskID"])
    return fallback


def build_raw_record(
    adapter: PrintDbAdapter,
    row: PrintTaskRow,
    source_epoch: str | None = None,
) -> dict[str, Any]:
    payload_hash = sha256_text(row.msg)
    component_task_id = row.task_id or f"row-{row.rowid}"
    source_index = str(row.rowid)
    if source_epoch:
        source_index = f"{source_epoch}:{row.rowid}"
    source_columns = {
        "rowid": row.rowid,
        "component_task_id": component_task_id,
        "task_time": row.task_time,
        "db_path": str(adapter.db_path),
    }
    if source_epoch:
        source_columns["source_epoch"] = source_epoch
    return {
        "document_id": extract_document_id(row.msg, component_task_id),
        "source_machine": machine_name(),
        "source_component": adapter.source_component,
        "source_index": source_index,
        "dedupe_key": f"{adapter.source_component}:{component_task_id}:{payload_hash}",
        "payload_format": "json",
        "raw_payload": row.msg,
        "source_columns": source_columns,
        "captured_at": row.task_time,
    }


def heartbeat(
    base_url: str,
    token: str,
    adapters: list[PrintDbAdapter],
    collector_id: str | None = None,
    collector_name: str | None = None,
    last_error: str | None = None,
    runtime_status: str = "checking",
    state: CollectorState | None = None,
    pending_captured_at: str | None = None,
    pending_captured_until: str | None = None,
    pending_row_count: int = 0,
) -> dict[str, Any]:
    adapter_status = {
        adapter.source_component: {
            "display_name": adapter.display_name,
            **adapter.get_status(),
        }
        for adapter in adapters
    }
    return post_json(
        base_url,
        "/collector-runtime/heartbeat",
        token,
        {
            "source_machine": machine_name(),
            "collector_id": collector_id or default_collector_id(),
            "collector_name": normalize_collector_name(collector_name),
            "client_version": CLIENT_VERSION,
            "runtime_status": runtime_status,
            "adapter_status": adapter_status,
            "queue_size": state.pending_count(adapters) if state is not None else 0,
            "last_error": last_error,
            "last_upload_at": state.last_upload_at if state is not None else None,
            "last_reconnect_reason": state.last_reconnect_reason if state is not None else None,
            "assignment_protocol_version": ASSIGNMENT_PROTOCOL_VERSION,
            "pending_captured_at": pending_captured_at,
            "pending_captured_until": pending_captured_until,
            "pending_row_count": pending_row_count,
        },
    )


def upload_records(base_url: str, token: str, task_id: int, records: list[dict[str, Any]]) -> dict[str, Any]:
    return post_json(
        base_url,
        "/collector-runtime/raw-records",
        token,
        {
            "task_id": task_id,
            "assignment_protocol_version": ASSIGNMENT_PROTOCOL_VERSION,
            "records": records,
        },
    )


def pending_batch_profile(
    state: CollectorState,
    adapters: list[PrintDbAdapter],
    batch_size: int,
) -> tuple[int, str | None, str | None]:
    times: list[str] = []
    pending_row_count = 0
    for adapter in adapters:
        cursor = state.source_cursor(adapter)
        if cursor is None:
            continue
        rows = adapter.read_since(cursor, batch_size)
        pending_row_count += len(rows)
        times.extend(
            captured_at
            for row in rows
            if (captured_at := comparable_local_db_time(row.task_time)) is not None
        )
    return (
        pending_row_count,
        min(times) if times else None,
        max(times) if times else None,
    )


def task_window_for_row(
    row: PrintTaskRow,
    tasks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    ordered = sorted(tasks, key=lambda task: int(task.get("id") or 0), reverse=True)
    captured_at = comparable_local_db_time(row.task_time)
    if not captured_at:
        return next(
            (task for task in ordered if str(task.get("status") or "") == "collecting"),
            ordered[0] if ordered else None,
        )
    for task in ordered:
        started_at = local_db_time_from_iso(task.get("started_at"))
        ended_at = local_db_time_from_iso(task.get("ended_at"))
        if started_at and captured_at < started_at:
            continue
        if ended_at and captured_at > ended_at:
            continue
        return task
    return None


def upload_adapter_rows(
    base_url: str,
    token: str,
    state: CollectorState,
    adapter: PrintDbAdapter,
    tasks: list[dict[str, Any]],
    batch_size: int,
    window_coverage_complete: bool = False,
) -> int:
    cursor = state.source_cursor(adapter)
    if cursor is None:
        starts = [
            str(task.get("started_at") or "")
            for task in tasks
            if task.get("started_at") and str(task.get("status") or "") == "collecting"
        ]
        cursor = adapter.initial_cursor_for_active_task(min(starts)) if starts else adapter.max_rowid()
    snapshot: PrintDbSnapshot | None = None
    for _attempt in range(3):
        snapshot = adapter.snapshot(cursor, batch_size)
        if snapshot is None:
            raise RuntimeError(f"print database unavailable: {adapter.db_path}")
        state.sync_db_generation(adapter, snapshot)
        synced_cursor = state.source_cursor(adapter)
        if synced_cursor is not None and synced_cursor != cursor:
            cursor = synced_cursor
            if state.state_path is not None:
                state.save(state.state_path)
            continue
        break
    else:
        raise RuntimeError("print database changed repeatedly while preparing an upload batch")
    if state.state_path is not None:
        state.save(state.state_path)
    rows = list(snapshot.rows)
    if not rows:
        state.advance_source_cursor(adapter, cursor, snapshot=snapshot)
        return 0

    grouped: dict[int, list[dict[str, Any]]] = {}
    source_epoch = state.source_epochs.get(adapter.source_component)
    for row in rows:
        task = task_window_for_row(row, tasks)
        if task is None:
            if comparable_local_db_time(row.task_time) is not None and window_coverage_complete:
                continue
            raise RuntimeError(f"no capture task window covers local row {row.rowid}")
        record = build_raw_record(adapter, row, source_epoch)
        replay_until = state.ambiguous_replay_until.get(adapter.source_component, 0)
        if row.rowid <= replay_until:
            record["source_columns"]["capture_assignment"] = "source_history_ambiguous"
        elif comparable_local_db_time(row.task_time) is None:
            record["source_columns"]["capture_assignment"] = "timestamp_invalid_fallback"
        grouped.setdefault(int(task["id"]), []).append(record)

    for task_id, records in sorted(grouped.items()):
        state.set_rollback_watermark(adapter, cursor, task_id)
        if state.state_path is not None:
            state.save(state.state_path)
        result = upload_records(base_url, token, task_id, records)
        window_rejected = int(result.get("window_rejected") or 0)
        duplicates = int(result.get("duplicates", result.get("skipped")) or 0)
        acknowledged = int(result.get("inserted") or 0) + duplicates
        if window_rejected:
            raise RuntimeError(f"server rejected {window_rejected} rows outside task {task_id}")
        if acknowledged != len(records):
            raise RuntimeError(
                f"server acknowledged {acknowledged} of {len(records)} rows for task {task_id}"
            )
        LOGGER.info(
            "uploaded task=%s component=%s rows=%s inserted=%s skipped=%s",
            task_id,
            adapter.source_component,
            len(records),
            result.get("inserted"),
            result.get("skipped"),
        )

    state.advance_source_cursor(
        adapter,
        rows[-1].rowid,
        rollback_task_id=max(grouped, default=None),
        snapshot=snapshot,
    )
    if rows[-1].rowid >= snapshot.max_rowid:
        state.sync_db_generation(adapter, audit_prefix=True)
    if grouped:
        state.last_upload_at = utc_now()
    return len(rows)


def run_sqlite_once(
    base_url: str,
    token: str,
    state: CollectorState,
    adapters: list[PrintDbAdapter],
    batch_size: int,
    collector_id: str | None = None,
    collector_name: str | None = None,
) -> None:
    for adapter in adapters:
        state.sync_db_generation(
            adapter,
            audit_prefix=adapter.source_component not in state.audited_sources,
        )
    if state.state_path is not None:
        state.save(state.state_path)
    idle_snapshots = {
        adapter.source_component: adapter.max_rowid()
        for adapter in adapters
    }
    pending_row_count, pending_from, pending_until = pending_batch_profile(
        state,
        adapters,
        batch_size,
    )
    heartbeat_state = heartbeat(
        base_url,
        token,
        adapters,
        collector_id=collector_id,
        collector_name=collector_name,
        runtime_status="listening",
        state=state,
        pending_captured_at=pending_from,
        pending_captured_until=pending_until,
        pending_row_count=pending_row_count,
    )
    protocol_version = int(heartbeat_state.get("assignment_protocol_version") or 1)
    if protocol_version < ASSIGNMENT_PROTOCOL_VERSION:
        try:
            heartbeat(
                base_url,
                token,
                adapters,
                collector_id=collector_id,
                collector_name=collector_name,
                runtime_status="error",
                last_error="collector assignment protocol v2 is required",
                state=state,
            )
        except Exception:  # noqa: BLE001 - preserve the protocol error as the primary failure.
            pass
        raise RuntimeError("server does not support collector assignment protocol v2")
    tasks = list(heartbeat_state.get("task_windows") or [])
    window_coverage_complete = bool(heartbeat_state.get("window_coverage_complete"))
    LOGGER.info("heartbeat ok, task windows: %s", len(tasks))

    for adapter in adapters:
        if tasks or pending_row_count:
            upload_adapter_rows(
                base_url,
                token,
                state,
                adapter,
                tasks,
                batch_size,
                window_coverage_complete,
            )
        else:
            state.baseline_source_cursor(adapter, idle_snapshots[adapter.source_component])


def upload_sample(base_url: str, token: str, task_id: int, sequence: int) -> dict[str, Any]:
    document_id = f"SIM-{task_id}-{sequence}"
    return upload_records(
        base_url,
        token,
        task_id,
        [
            {
                "document_id": document_id,
                "source_machine": machine_name(),
                "source_component": "mvp-simulator",
                "source_index": str(sequence),
                "dedupe_key": f"mvp-simulator:{document_id}",
                "payload_format": "json",
                "raw_payload": json.dumps(
                    {
                        "document_id": document_id,
                        "sample": "local collector simulator raw payload",
                        "captured_at": utc_now(),
                    },
                    ensure_ascii=False,
                ),
                "captured_at": utc_now(),
            }
        ],
    )


def run_simulator_once(
    base_url: str,
    token: str,
    sequence: int,
    collector_id: str | None = None,
    collector_name: str | None = None,
) -> None:
    state = post_json(
        base_url,
        "/collector-runtime/heartbeat",
        token,
        {
            "source_machine": machine_name(),
            "collector_id": collector_id or default_collector_id(),
            "collector_name": normalize_collector_name(collector_name),
            "client_version": f"{CLIENT_VERSION}-simulator",
            "runtime_status": "listening",
            "adapter_status": {"simulator": {"status": "ready"}},
            "queue_size": 0,
            "assignment_protocol_version": ASSIGNMENT_PROTOCOL_VERSION,
        },
    )
    tasks = state.get("tasks", [])
    LOGGER.info("heartbeat ok, active tasks: %s", len(tasks))
    for task in tasks:
        task_id = int(task["id"])
        result = upload_sample(base_url, token, task_id, sequence)
        LOGGER.info("uploaded simulator record task=%s result=%s", task_id, result)


def run_check(config: CollectorConfig, config_path: Path, state_path: Path, adapters: list[PrintDbAdapter]) -> int:
    print(f"collector version: {CLIENT_VERSION}")
    print(f"config path: {config_path}")
    print(f"state path: {state_path}")
    print(f"base url: {config.base_url}")
    print(f"collector id: {config.collector_id or '-'}")
    print(f"collector name: {config.collector_name or '-'}")
    print(f"workspace id: {config.workspace_id or '-'}")
    print(f"token configured: {'yes' if config.token else 'no'}")
    print("")
    print("local adapters:")
    for adapter in adapters:
        status = adapter.get_status()
        print(f"- {adapter.source_component} [{adapter.display_name}]")
        print(f"  db_path: {adapter.db_path}")
        print(f"  status: {status.get('status')}")
        if "task_count" in status:
            print(f"  task_count: {status.get('task_count')}")
        if "max_rowid" in status:
            print(f"  max_rowid: {status.get('max_rowid')}")
        if "error" in status:
            print(f"  error: {status.get('error')}")

    if not config.token:
        print("")
        print("server heartbeat skipped: token is not configured")
        return 0

    print("")
    try:
        heartbeat_state = heartbeat(config.base_url, config.token, adapters, runtime_status="checking")
    except Exception as exc:  # noqa: BLE001 - command-line diagnostics should show exact failure.
        print(f"server heartbeat failed: {exc}")
        return 1

    tasks = heartbeat_state.get("tasks", [])
    collector = heartbeat_state.get("collector", {})
    protocol_version = int(heartbeat_state.get("assignment_protocol_version") or 1)
    if protocol_version < ASSIGNMENT_PROTOCOL_VERSION:
        print("server heartbeat failed: collector assignment protocol v2 is required")
        return 1
    LOGGER.info(
        "server heartbeat ok collector_id=%s online_status=%s active_tasks=%s",
        collector.get("id"),
        collector.get("online_status"),
        len(tasks),
    )
    print("server heartbeat: ok")
    print(f"collector id: {collector.get('id')}")
    print(f"online status: {collector.get('online_status')}")
    print(f"active tasks: {len(tasks)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cargo Platform 采集器")
    parser.add_argument("--config", default=str(default_config_path()), help="Collector config JSON path.")
    parser.add_argument("--state", default="", help="Collector state JSON path.")
    parser.add_argument("--base-url", default="", help=f"Backend API base URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--token", default="", help="Collector runtime token generated in the web console.")
    parser.add_argument("--workspace-id", type=int, default=None, help="Workspace id when the account has multiple workspaces.")
    parser.add_argument("--collector-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--collector-name", default="", help="Display name for this collector.")
    parser.add_argument("--interval", type=int, default=None, help="Polling interval in seconds.")
    parser.add_argument("--batch-size", type=int, default=None, help="Max local task rows per upload batch.")
    parser.add_argument("--loop", action="store_true", help="Keep polling while the collector is running.")
    parser.add_argument("--simulate", action="store_true", help="Upload a simulator record instead of reading print DBs.")
    parser.add_argument("--save-config", action="store_true", help="Save current settings to the config file and exit.")
    parser.add_argument("--check", action="store_true", help="Check local adapters and server heartbeat, then exit.")
    parser.add_argument("--log-file", default="", help="Log file path. Default: config directory collector.log.")
    parser.add_argument("--no-log-file", action="store_true", help="Only log to the console.")
    parser.add_argument("--install-code-file", default="", help="Install using a CP1 connection-code file.")
    parser.add_argument("--install-existing", action="store_true", help="Install and copy existing per-user state.")
    parser.add_argument("--managed-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--uninstall", action="store_true", help="Uninstall the managed collector.")
    parser.add_argument("--quiet", action="store_true", help="Suppress setup dialogs.")
    return parser


def show_setup_ui() -> int:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("Cargo Platform 采集器安装")
    root.resizable(False, False)
    tk.Label(root, text="粘贴连接码：").pack(anchor="w", padx=16, pady=(16, 4))
    code = tk.Entry(root, width=64)
    code.pack(fill="x", padx=16)
    status = tk.StringVar(value="等待安装")
    tk.Label(root, textvariable=status).pack(anchor="w", padx=16, pady=8)

    def install() -> None:
        value = code.get().strip()
        if not value:
            messagebox.showerror("安装失败", "请输入连接码")
            return
        status.set("正在安装…")
        root.update_idletasks()
        result = windows_host.install_collector(
            windows_host.current_executable(),
            connection_code=value,
            migrate_existing=True,
        )
        status.set(result.message)
        (messagebox.showinfo if result.success else messagebox.showerror)("采集器安装", result.message)

    tk.Button(root, text="安装", command=install, width=16).pack(pady=(0, 16))
    root.mainloop()
    return 0


def main() -> int:
    if len(sys.argv) == 1:
        return show_setup_ui()
    parser = build_parser()
    args = parser.parse_args()

    if args.uninstall:
        result = windows_host.uninstall_collector()
        if not args.quiet:
            print(result.message)
        return 0 if result.success else 1

    if args.install_code_file or args.install_existing:
        connection_code = None
        if args.install_code_file:
            connection_code = Path(args.install_code_file).read_text(encoding="utf-8-sig").strip()
        result = windows_host.install_collector(
            windows_host.current_executable(),
            connection_code=connection_code,
            migrate_existing=args.install_existing,
        )
        if not args.quiet:
            print(result.message)
        return 0 if result.success else 1

    managed_paths = windows_host.machine_paths() if args.managed_run else None
    config_path = managed_paths.config_path if managed_paths else Path(args.config)
    state_path = Path(args.state) if args.state else default_state_path(config_path)
    existing_config = CollectorConfig.load(config_path)
    config = existing_config.apply_args(args)
    adapters = adapters_from_config(config)

    log_path = None
    if not args.no_log_file:
        log_path = Path(args.log_file) if args.log_file else default_log_path(config_path)
    setup_logging(log_path)

    if args.save_config:
        if not config.token:
            parser.error("--save-config requires --token or an existing token in the config file.")
        config.save(config_path)
        LOGGER.info("collector config saved: %s", config_path)
        if not args.check:
            return 0

    if args.check:
        return run_check(config, config_path, state_path, adapters)

    if not config.token:
        parser.error(
            "Missing collector token. Generate a token in the web console, then start with --token <token> --loop."
        )

    instance_lock = CollectorInstanceLock(state_path)
    if not instance_lock.acquire():
        LOGGER.error("another collector process is already using this state file: %s", state_path)
        return 1
    try:
        state = CollectorState.load(state_path)
        sequence = 1
        reconnect_notice = ReconnectNotice()
        while True:
            try:
                config = poll_collector_safely(
                    config,
                    state,
                    adapters,
                    sequence,
                    config_path,
                    reconnect_notice,
                )
            finally:
                save_state_safely(state, state_path, reconnect_notice)

            if not (args.loop or args.managed_run):
                break
            sequence += 1
            time.sleep(config.interval)
    finally:
        instance_lock.release()

    return 0


def safe_main() -> int:
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 - avoid PyInstaller unhandled-exception popups.
        try:
            if not logging.getLogger().handlers and not LOGGER.handlers:
                setup_logging(default_log_path(default_config_path()))
            LOGGER.exception("collector fatal error: %s", exc)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(safe_main())
