from __future__ import annotations

import argparse
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
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


CLIENT_VERSION = "single-exe-token-collector-20260614"
DEFAULT_BASE_URL = "http://127.0.0.1:5173/api/v1"
DEFAULT_WEB_PORT = 5173
DEFAULT_COLLECTOR_NAME = ""
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


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()


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
        return cls(
            base_url=normalize_base_url(str(payload.get("base_url") or DEFAULT_BASE_URL)),
            token=str(payload.get("token") or ""),
            workspace_id=int(payload["workspace_id"]) if payload.get("workspace_id") else None,
            collector_id=str(payload.get("collector_id") or default_collector_id()),
            collector_name=normalize_collector_name(payload.get("collector_name")),
            interval=max(1, int(payload.get("interval") or 3)),
            batch_size=max(1, int(payload.get("batch_size") or 50)),
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
            batch_size=args.batch_size if args.batch_size is not None else self.batch_size,
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
        write_json(path, self.to_dict())


@dataclass(frozen=True)
class PrintTaskRow:
    rowid: int
    task_id: str | None
    msg: str
    task_time: str | None


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

    def read_since(self, rowid: int, limit: int) -> list[PrintTaskRow]:
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
    ) -> None:
        self.idle_watermarks = idle_watermarks or {}
        self.capture_watermarks = capture_watermarks or {}

    @staticmethod
    def capture_key(task_id: int, source_component: str) -> str:
        return f"{task_id}:{source_component}"

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
        return cls(idle_watermarks=idle_watermarks, capture_watermarks=capture_watermarks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "updated_at": utc_now(),
            "idle_watermarks": self.idle_watermarks,
            "capture_watermarks": self.capture_watermarks,
        }

    def save(self, path: Path) -> None:
        write_json(path, self.to_dict())

    def idle_watermark(self, adapter: PrintDbAdapter) -> int | None:
        return self.idle_watermarks.get(adapter.source_component)

    def update_idle_watermark(self, adapter: PrintDbAdapter) -> None:
        self.idle_watermarks[adapter.source_component] = adapter.max_rowid()

    def start_capture_watermark(self, task_id: int, adapter: PrintDbAdapter) -> int:
        key = self.capture_key(task_id, adapter.source_component)
        if key not in self.capture_watermarks:
            baseline = self.idle_watermark(adapter)
            if baseline is None:
                baseline = adapter.max_rowid()
            self.capture_watermarks[key] = baseline
        return self.capture_watermarks[key]

    def advance_capture_watermark(self, task_id: int, adapter: PrintDbAdapter, rowid: int) -> None:
        self.capture_watermarks[self.capture_key(task_id, adapter.source_component)] = rowid

    def active_task_ids(self) -> set[int]:
        task_ids: set[int] = set()
        for key in self.capture_watermarks:
            task_id, _separator, _component = key.partition(":")
            if task_id.isdigit():
                task_ids.add(int(task_id))
        return task_ids

    def finish_task(self, task_id: int) -> None:
        prefix = f"{task_id}:"
        keys = [key for key in self.capture_watermarks if key.startswith(prefix)]
        for key in keys:
            del self.capture_watermarks[key]


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


def save_state_safely(state: CollectorState, state_path: Path, notice: ReconnectNotice) -> None:
    try:
        state.save(state_path)
    except Exception as exc:  # noqa: BLE001 - state persistence must not kill the listener.
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


def build_raw_record(adapter: PrintDbAdapter, row: PrintTaskRow) -> dict[str, Any]:
    payload_hash = sha256_text(row.msg)
    component_task_id = row.task_id or f"row-{row.rowid}"
    return {
        "document_id": extract_document_id(row.msg, component_task_id),
        "source_machine": machine_name(),
        "source_component": adapter.source_component,
        "source_index": str(row.rowid),
        "dedupe_key": f"{adapter.source_component}:{component_task_id}:{payload_hash}",
        "payload_format": "json",
        "raw_payload": row.msg,
        "source_columns": {
            "rowid": row.rowid,
            "component_task_id": component_task_id,
            "task_time": row.task_time,
            "db_path": str(adapter.db_path),
        },
        "captured_at": row.task_time or utc_now(),
    }


def heartbeat(
    base_url: str,
    token: str,
    adapters: list[PrintDbAdapter],
    collector_id: str | None = None,
    collector_name: str | None = None,
    last_error: str | None = None,
    runtime_status: str = "checking",
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
            "queue_size": 0,
            "last_error": last_error,
        },
    )


def upload_records(base_url: str, token: str, task_id: int, records: list[dict[str, Any]]) -> dict[str, Any]:
    return post_json(
        base_url,
        "/collector-runtime/raw-records",
        token,
        {
            "task_id": task_id,
            "records": records,
        },
    )


def upload_rows_for_task(
    base_url: str,
    token: str,
    state: CollectorState,
    task_id: int,
    adapter: PrintDbAdapter,
    batch_size: int,
) -> int:
    watermark = state.start_capture_watermark(task_id, adapter)
    rows = adapter.read_since(watermark, batch_size)
    if not rows:
        return 0

    records = [build_raw_record(adapter, row) for row in rows]
    result = upload_records(base_url, token, task_id, records)
    max_rowid = max(row.rowid for row in rows)
    state.advance_capture_watermark(task_id, adapter, max_rowid)
    LOGGER.info(
        "uploaded task=%s component=%s rows=%s inserted=%s skipped=%s",
        task_id,
        adapter.source_component,
        len(rows),
        result.get("inserted"),
        result.get("skipped"),
    )
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
    heartbeat_state = heartbeat(
        base_url,
        token,
        adapters,
        collector_id=collector_id,
        collector_name=collector_name,
        runtime_status="listening",
    )
    active_tasks = heartbeat_state.get("tasks", [])
    active_task_ids = {int(task["id"]) for task in active_tasks}
    LOGGER.info("heartbeat ok, active tasks: %s", len(active_task_ids))

    if active_task_ids:
        for task_id in sorted(active_task_ids):
            for adapter in adapters:
                upload_rows_for_task(base_url, token, state, task_id, adapter, batch_size)
        return

    closing_task_ids = state.active_task_ids()
    for task_id in sorted(closing_task_ids):
        uploaded = 0
        for adapter in adapters:
            uploaded += upload_rows_for_task(base_url, token, state, task_id, adapter, batch_size)
        if uploaded == 0:
            state.finish_task(task_id)

    for adapter in adapters:
        state.update_idle_watermark(adapter)


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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config_path = Path(args.config)
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

    state = CollectorState.load(state_path)
    sequence = 1
    reconnect_notice = ReconnectNotice()
    while True:
        try:
            config = poll_collector_once(config, state, adapters, sequence, config_path)
            reconnect_notice.reset()
        except urllib.error.HTTPError as exc:
            config = recover_from_http_error(exc, config, config_path, reconnect_notice)
        except NETWORK_RETRY_EXCEPTIONS as exc:
            reconnect_notice.warning(
                "network",
                "collector network was interrupted; staying in background and retrying: %s",
                exc,
            )
        except sqlite3.Error as exc:
            LOGGER.error("collector sqlite error: %s", exc)
        finally:
            save_state_safely(state, state_path, reconnect_notice)

        if not args.loop:
            break
        sequence += 1
        time.sleep(config.interval)

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
