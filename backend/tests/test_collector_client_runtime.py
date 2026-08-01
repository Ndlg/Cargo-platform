from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
from urllib.parse import unquote
import urllib.error
import zipfile


TEST_DB = Path(__file__).resolve().parent / "collector_runtime_test.db"
TEST_STORAGE = Path(__file__).resolve().parent / "collector_runtime_storage"
if TEST_DB.exists():
    TEST_DB.unlink()
if TEST_STORAGE.exists():
    shutil.rmtree(TEST_STORAGE)

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["SECRET_KEY"] = "collector-runtime-test-secret-at-least-32-bytes"
os.environ["STORAGE_ROOT"] = TEST_STORAGE.as_posix()

COLLECTOR_CLIENT_PATH = Path(__file__).resolve().parents[2] / "collector-client"
if str(COLLECTOR_CLIENT_PATH) not in sys.path:
    sys.path.insert(0, str(COLLECTOR_CLIENT_PATH))

import client as collector_client  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from app.api.routes import collector_runtime as collector_runtime_route  # noqa: E402
from app.main import app  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models import CaptureTask, Collector, RawCaptureRecord, RecognitionRulePack  # noqa: E402
from app.api.routes.collector_runtime import (  # noqa: E402
    RAW_CAPTURE_BATCH_MAX_RECORDS,
    RAW_CAPTURE_PAYLOAD_MAX_CHARS,
    RAW_CAPTURE_SOURCE_COLUMNS_MAX_CHARS,
)
from app.services.collection_contract import (  # noqa: E402
    COLLECTION_MODULE_OUTPUT_CONTRACT,
    COLLECTION_MODULE_RULE_POLICY,
    COLLECTION_MODULE_SIMILARITY_POLICY,
    RAW_CAPTURE_RECORD_CONTRACT_FIELDS,
    RAW_CAPTURE_RECORD_SOURCE_METADATA_FIELDS,
)


def test_remote_disconnected_is_retryable_network_error() -> None:
    assert isinstance(
        http.client.RemoteDisconnected("server closed connection"),
        collector_client.NETWORK_RETRY_EXCEPTIONS,
    )


def test_http_error_is_handled_before_retryable_url_error() -> None:
    assert issubclass(urllib.error.HTTPError, collector_client.NETWORK_RETRY_EXCEPTIONS)
    assert collector_client.is_auth_http_error(
        urllib.error.HTTPError("http://server", 401, "unauthorized", {}, None)
    )


def login_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert login.status_code == 200
    return {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Workspace-Id": "1",
    }


def register_collector(client: TestClient, headers: dict[str, str], identity: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/collector-control/register",
        headers=headers,
        json={
            "collector_id": identity,
            "collector_name": f"Collector {identity}",
            "source_machine": identity,
            "client_version": "test-client",
        },
    )
    assert response.status_code == 201
    return response.json()


def write_test_collector_release(
    source_dir: Path,
    *,
    exe_content: bytes = b"MZcollector exe stub",
    sha256: str | None = None,
) -> dict[str, object]:
    exe_path = source_dir / collector_runtime_route.COLLECTOR_CLIENT_RELEASE_EXE
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    exe_path.write_bytes(exe_content)
    manifest = {
        "schema_version": 1,
        "artifact": "Cargo Platform 采集器.exe",
        "release_version": "1.0.0-rc.1",
        "client_version": "1.0.0-rc.1+aaaaaaaaaaaa",
        "git_sha": "a" * 40,
        "python_version": "3.12.13",
        "pyinstaller_version": "6.21.0",
        "size": len(exe_content),
        "sha256": sha256 or hashlib.sha256(exe_content).hexdigest(),
    }
    manifest_path = source_dir / collector_runtime_route.COLLECTOR_CLIENT_RELEASE_MANIFEST
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_collector_client_default_name_uses_windows_machine_name(monkeypatch) -> None:
    monkeypatch.setenv("COMPUTERNAME", "WAREHOUSE-PC-08")

    assert collector_client.default_collector_name() == "WAREHOUSE-PC-08"
    assert collector_client.normalize_collector_name("") == "WAREHOUSE-PC-08"
    assert collector_client.normalize_collector_name("Cargo Platform 采集器") == "WAREHOUSE-PC-08"
    assert collector_client.normalize_collector_name("采集器") == "WAREHOUSE-PC-08"


def test_collector_client_clamps_batch_size_to_server_contract(tmp_path) -> None:
    config_path = tmp_path / "collector-config.json"
    collector_client.write_json(config_path, {"batch_size": 1000})

    assert collector_client.CollectorConfig.load(config_path).batch_size == 100


def test_register_collector_replaces_generic_name_with_source_machine() -> None:
    with TestClient(app) as client:
        headers = login_headers(client)
        response = client.post(
            "/api/v1/collector-control/register",
            headers=headers,
            json={
                "collector_id": "collector-default-name-machine",
                "collector_name": "Cargo Platform 采集器",
                "source_machine": "WAREHOUSE-PC-08",
                "client_version": "test-client",
            },
        )

    assert response.status_code == 201
    assert response.json()["collector"]["collector_name"] == "WAREHOUSE-PC-08"
    assert response.json()["collector"]["source_machine"] == "WAREHOUSE-PC-08"


def test_heartbeat_replaces_existing_generic_name_with_source_machine() -> None:
    with TestClient(app) as client:
        headers = login_headers(client)
        registration = client.post(
            "/api/v1/collector-control/register",
            headers=headers,
            json={
                "collector_id": "legacy-generic-collector",
                "collector_name": "Cargo Platform 采集器",
                "client_version": "test-client",
            },
        )
        assert registration.status_code == 201
        token = registration.json()["collector_token"]
        collector_db_id = int(registration.json()["collector"]["id"])
        with SessionLocal() as db:
            collector = db.get(Collector, collector_db_id)
            assert collector is not None
            collector.collector_name = "Cargo Platform 采集器"
            db.commit()

        heartbeat = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": token},
            json={
                "collector_id": "WAREHOUSE-PC-09",
                "source_machine": "WAREHOUSE-PC-09",
                "runtime_status": "listening",
                "adapter_status": {"simulator": {"status": "ready"}},
                "queue_size": 3,
                "last_upload_at": "2026-07-27T10:00:00+00:00",
                "last_reconnect_reason": "network",
            },
        )

    assert heartbeat.status_code == 200
    assert heartbeat.json()["collector"]["collector_name"] == "WAREHOUSE-PC-09"
    assert heartbeat.json()["collector"]["source_machine"] == "WAREHOUSE-PC-09"
    status_payload = heartbeat.json()["collector"]["status_payload"]
    assert status_payload["queue_size"] == 3
    assert status_payload["last_upload_at"] == "2026-07-27T10:00:00+00:00"
    assert status_payload["last_reconnect_reason"] == "network"


def test_heartbeat_negotiates_task_windows_without_changing_legacy_tasks() -> None:
    with TestClient(app) as client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "tracked-completed-machine")
        token = str(registration["collector_token"])
        collector_id = int(registration["collector"]["id"])
        started = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"collector_id": collector_id},
        )
        assert started.status_code == 201
        task_id = int(started.json()["id"])
        stopped = client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": task_id},
        )
        assert stopped.status_code == 200

        heartbeat = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": token},
            json={
                "assignment_protocol_version": 2,
                "pending_captured_at": stopped.json()["started_at"],
                "pending_captured_until": stopped.json()["ended_at"],
                "pending_row_count": 1,
            },
        )
        legacy = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": token},
            json={"tracked_task_ids": [task_id]},
        )

    assert heartbeat.status_code == 200
    assert heartbeat.json()["tasks"] == []
    assert [(task["id"], task["status"]) for task in heartbeat.json()["task_windows"]] == [
        (task_id, "completed")
    ]
    assert [(task["id"], task["status"]) for task in legacy.json()["tasks"]] == [
        (task_id, "completed")
    ]
    assert legacy.json()["task_windows"] == []


def test_v2_heartbeat_keeps_pending_archived_window_retryable() -> None:
    with TestClient(app) as client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "archived-window-machine")
        token = str(registration["collector_token"])
        collector_id = int(registration["collector"]["id"])
        started = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"collector_id": collector_id},
        )
        assert started.status_code == 201
        task_id = int(started.json()["id"])
        stopped = client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": task_id},
        )
        assert stopped.status_code == 200
        pending_at = stopped.json()["started_at"]

        with SessionLocal() as db:
            task = db.get(CaptureTask, task_id)
            assert task is not None
            task.archived_at = datetime.now(timezone.utc).isoformat()
            db.commit()

        heartbeat = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": token},
            json={
                "assignment_protocol_version": 2,
                "pending_captured_at": pending_at,
                "pending_captured_until": stopped.json()["ended_at"],
                "pending_row_count": 1,
            },
        )
        upload = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": token},
            json={
                "task_id": task_id,
                "assignment_protocol_version": 2,
                "records": [
                    {
                        "source_component": "cainiao-cnprint",
                        "source_index": "generation-a:1",
                        "captured_at": pending_at,
                        "raw_payload": "{}",
                    }
                ],
            },
        )

    assert heartbeat.status_code == 200
    assert [task["id"] for task in heartbeat.json()["task_windows"]] == [task_id]
    assert upload.status_code == 201
    assert upload.json()["inserted"] == 1


def test_v1_heartbeat_cannot_break_v2_archived_window_lease() -> None:
    with TestClient(app) as client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "protocol-takeover-machine")
        token = str(registration["collector_token"])
        collector_id = int(registration["collector"]["id"])
        started = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"collector_id": collector_id},
        ).json()
        stopped = client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": int(started["id"])},
        ).json()
        with SessionLocal() as db:
            task = db.get(CaptureTask, int(started["id"]))
            assert task is not None
            task.archived_at = datetime.now(timezone.utc).isoformat()
            db.commit()

        v2_heartbeat = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": token},
            json={
                "assignment_protocol_version": 2,
                "pending_captured_at": stopped["started_at"],
                "pending_captured_until": stopped["ended_at"],
                "pending_row_count": 1,
            },
        )
        legacy_heartbeat = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": token},
            json={"tracked_task_ids": [int(started["id"])]},
        )
        legacy_upload = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": token},
            json={
                "task_id": int(started["id"]),
                "records": [{"source_component": "cainiao-cnprint", "source_index": "1", "raw_payload": "{}"}],
            },
        )
        v2_upload = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": token},
            json={
                "task_id": int(started["id"]),
                "assignment_protocol_version": 2,
                "records": [
                    {
                        "source_component": "cainiao-cnprint",
                        "source_index": "generation-a:1",
                        "captured_at": stopped["started_at"],
                        "raw_payload": "{}",
                    }
                ],
            },
        )

    assert v2_heartbeat.status_code == 200
    assert "task_window_lease" in legacy_heartbeat.json()["collector"]["status_payload"]
    assert legacy_upload.status_code == 409
    assert v2_upload.status_code == 201
    assert v2_upload.json()["inserted"] == 1


def test_concurrent_legacy_heartbeat_cannot_erase_v2_takeover(monkeypatch) -> None:
    with TestClient(app) as client, TestClient(app) as legacy_client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "heartbeat-takeover-race-machine")
        token = str(registration["collector_token"])

        original_get_collector = collector_runtime_route.get_collector_from_token
        legacy_authenticated = threading.Event()
        release_legacy = threading.Event()
        call_lock = threading.Lock()
        call_count = 0

        def delay_first_authenticated_request(db, collector_token):
            nonlocal call_count
            collector = original_get_collector(db, collector_token)
            with call_lock:
                call_count += 1
                is_first = call_count == 1
            if is_first:
                legacy_authenticated.set()
                assert release_legacy.wait(timeout=5)
            return collector

        monkeypatch.setattr(
            collector_runtime_route,
            "get_collector_from_token",
            delay_first_authenticated_request,
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            legacy_future = pool.submit(
                legacy_client.post,
                "/api/v1/collector-runtime/heartbeat",
                headers={"X-Collector-Token": token},
                json={},
            )
            assert legacy_authenticated.wait(timeout=5)
            v2_heartbeat = client.post(
                "/api/v1/collector-runtime/heartbeat",
                headers={"X-Collector-Token": token},
                json={"assignment_protocol_version": 2},
            )
            release_legacy.set()
            legacy_heartbeat = legacy_future.result(timeout=5)

        assert v2_heartbeat.status_code == 200
        assert legacy_heartbeat.status_code == 200
        assert (
            legacy_heartbeat.json()["collector"]["status_payload"][
                "assignment_protocol_lease"
            ]["version"]
            == 2
        )


def test_v2_takeover_blocks_legacy_upload_authenticated_before_takeover(monkeypatch) -> None:
    with TestClient(app) as client, TestClient(app) as legacy_client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "upload-takeover-race-machine")
        token = str(registration["collector_token"])
        collector_id = int(registration["collector"]["id"])
        task = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"collector_id": collector_id},
        ).json()

        original_get_collector = collector_runtime_route.get_collector_from_token
        legacy_authenticated = threading.Event()
        release_legacy = threading.Event()
        call_lock = threading.Lock()
        call_count = 0

        def delay_first_authenticated_request(db, collector_token):
            nonlocal call_count
            collector = original_get_collector(db, collector_token)
            with call_lock:
                call_count += 1
                is_first = call_count == 1
            if is_first:
                legacy_authenticated.set()
                assert release_legacy.wait(timeout=5)
            return collector

        monkeypatch.setattr(
            collector_runtime_route,
            "get_collector_from_token",
            delay_first_authenticated_request,
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            legacy_future = pool.submit(
                legacy_client.post,
                "/api/v1/collector-runtime/raw-records",
                headers={"X-Collector-Token": token},
                json={
                    "task_id": int(task["id"]),
                    "records": [
                        {
                            "source_component": "cainiao-cnprint",
                            "source_index": "42",
                            "captured_at": task["started_at"],
                            "raw_payload": '{"task":"takeover race"}',
                        }
                    ],
                },
            )
            assert legacy_authenticated.wait(timeout=5)
            v2_heartbeat = client.post(
                "/api/v1/collector-runtime/heartbeat",
                headers={"X-Collector-Token": token},
                json={"assignment_protocol_version": 2},
            )
            release_legacy.set()
            legacy_upload = legacy_future.result(timeout=5)

        assert v2_heartbeat.status_code == 200
        assert legacy_upload.status_code == 409
        stop = client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": int(task["id"])},
        )
        assert stop.status_code == 200
        with SessionLocal() as db:
            assert (
                db.query(RawCaptureRecord)
                .filter(
                    RawCaptureRecord.collector_id == collector_id,
                    RawCaptureRecord.source_index == "42",
                )
                .count()
                == 0
            )


def test_protocol_bridge_deduplicates_exact_v1_v2_source_event_during_upgrade() -> None:
    raw_payload = '{"task":"same physical print"}'
    captured_at = "2026-07-27T10:00:00+00:00"
    with TestClient(app) as client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "protocol-bridge-machine")
        token = str(registration["collector_token"])
        collector_id = int(registration["collector"]["id"])
        task = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"collector_id": collector_id},
        ).json()
        task_id = int(task["id"])
        captured_at = str(task["started_at"])

        legacy_first = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": token},
            json={
                "task_id": task_id,
                "records": [
                    {
                        "source_component": "cainiao-cnprint",
                        "source_index": "42",
                        "captured_at": captured_at,
                        "raw_payload": raw_payload,
                    }
                ],
            },
        )
        client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": token},
            json={"assignment_protocol_version": 2},
        )
        v2_replay = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": token},
            json={
                "task_id": task_id,
                "assignment_protocol_version": 2,
                "records": [
                    {
                        "source_component": "cainiao-cnprint",
                        "source_index": "generation-a:42",
                        "captured_at": captured_at,
                        "raw_payload": raw_payload,
                    }
                ],
            },
        )
        v2_new = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": token},
            json={
                "task_id": task_id,
                "assignment_protocol_version": 2,
                "records": [
                    {
                        "source_component": "cainiao-cnprint",
                        "source_index": "generation-a:43",
                        "captured_at": captured_at,
                        "raw_payload": raw_payload,
                    }
                ],
            },
        )
        with SessionLocal() as db:
            collector = db.get(Collector, collector_id)
            assert collector is not None
            collector.assignment_protocol_lease_expires_at = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            db.commit()
        rollback_heartbeat = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": token},
            json={"tracked_task_ids": [task_id]},
        )
        legacy_rollback = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": token},
            json={
                "task_id": task_id,
                "records": [
                    {
                        "source_component": "cainiao-cnprint",
                        "source_index": "43",
                        "captured_at": captured_at,
                        "raw_payload": raw_payload,
                    }
                ],
            },
        )
        client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": task_id},
        )

    assert legacy_first.json()["inserted"] == 1
    assert v2_replay.json() == {
        "inserted": 0,
        "skipped": 1,
        "duplicates": 1,
        "window_rejected": 0,
    }
    assert v2_new.json()["inserted"] == 1
    assert rollback_heartbeat.status_code == 200
    assert legacy_rollback.status_code == 201
    assert legacy_rollback.json() == {"inserted": 0, "skipped": 1}


def test_v2_source_row_is_duplicate_even_if_task_window_changes() -> None:
    record = {
        "source_component": "cainiao-cnprint",
        "source_index": "generation-a:42",
        "raw_payload": '{"task":"same local print"}',
    }
    with TestClient(app) as client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "cross-window-dedupe-machine")
        token = str(registration["collector_token"])
        collector_id = int(registration["collector"]["id"])

        first = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"collector_id": collector_id},
        )
        first_task_id = int(first.json()["id"])
        client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": token},
            json={
                "task_id": first_task_id,
                "assignment_protocol_version": 2,
                "records": [record],
            },
        )
        client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": first_task_id},
        )

        second = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"collector_id": collector_id},
        )
        second_task_id = int(second.json()["id"])
        duplicate = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": token},
            json={
                "task_id": second_task_id,
                "assignment_protocol_version": 2,
                "records": [record],
            },
        )
        client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": second_task_id},
        )

    assert duplicate.status_code == 201
    assert duplicate.json() == {
        "inserted": 0,
        "skipped": 1,
        "duplicates": 1,
        "window_rejected": 0,
    }
    with SessionLocal() as db:
        stored = db.query(RawCaptureRecord).filter(
            RawCaptureRecord.collector_id == collector_id,
            RawCaptureRecord.source_component == record["source_component"],
            RawCaptureRecord.source_index == record["source_index"],
        ).one()
        assert stored.capture_event_key


def test_v2_unique_event_race_is_counted_as_duplicate() -> None:
    source_component = "cainiao-cnprint"
    source_index = "generation-race:42"
    with TestClient(app) as client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "event-race-machine")
        token = str(registration["collector_token"])
        collector_id = int(registration["collector"]["id"])
        task = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"collector_id": collector_id},
        ).json()
        event_key = hashlib.sha256(
            f"1\0{collector_id}\0{source_component}\0{source_index}".encode("utf-8")
        ).hexdigest()
        with SessionLocal() as db:
            db.add(
                RawCaptureRecord(
                    tenant_id=1,
                    workspace_id=1,
                    task_id=int(task["id"]),
                    collector_id=collector_id,
                    source_component="concurrent-uncommitted-view",
                    source_index="different-query-key",
                    capture_event_key=event_key,
                    payload_format="json",
                    raw_payload="{}",
                    status="pending",
                )
            )
            db.commit()

        duplicate = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": token},
            json={
                "task_id": int(task["id"]),
                "assignment_protocol_version": 2,
                "records": [
                    {
                        "source_component": source_component,
                        "source_index": source_index,
                        "raw_payload": "{}",
                    }
                ],
            },
        )
        client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": int(task["id"])},
        )

    assert duplicate.status_code == 201
    assert duplicate.json() == {
        "inserted": 0,
        "skipped": 1,
        "duplicates": 1,
        "window_rejected": 0,
    }


def deactivate_recognition_rule_packs() -> None:
    with SessionLocal() as db:
        db.query(RecognitionRulePack).filter(RecognitionRulePack.workspace_id == 1).update(
            {"is_enabled": False, "status": "inactive"}
        )
        db.commit()


def activate_recognition_rule_pack() -> None:
    payload = {
        "contract_version": "recognition_rule_pack_v1",
        "pack": {"code": "collector-test-pack", "name": "采集测试规则包", "version": "1.0.0"},
        "parser_policy": {"requires_active_rule_pack": True, "order_row_parser": "shoe_waybill_v1"},
    }
    with SessionLocal() as db:
        db.query(RecognitionRulePack).filter(RecognitionRulePack.workspace_id == 1).update(
            {"is_enabled": False, "status": "inactive"}
        )
        pack = db.query(RecognitionRulePack).filter(
            RecognitionRulePack.workspace_id == 1,
            RecognitionRulePack.code == "collector-test-pack",
            RecognitionRulePack.is_deleted.is_(False),
        ).first()
        if pack is None:
            pack = RecognitionRulePack(
                tenant_id=1,
                workspace_id=1,
                code="collector-test-pack",
                name="采集测试规则包",
                version="1.0.0",
                payload=payload,
                is_enabled=True,
                status="active",
            )
            db.add(pack)
        else:
            pack.payload = payload
            pack.is_enabled = True
            pack.status = "active"
        db.commit()


def test_parse_records_requires_active_recognition_rule_pack() -> None:
    with TestClient(app) as client:
        deactivate_recognition_rule_packs()
        headers = login_headers(client)
        response = client.post("/api/v1/collector-control/parse-records", headers=headers, json={})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rule_pack_missing"
    assert body["rule_pack_required"] is True
    assert "导入" in body["message"]


def test_parse_records_legacy_entry_is_disabled_when_rule_pack_active() -> None:
    with TestClient(app) as client:
        activate_recognition_rule_pack()
        headers = login_headers(client)
        response = client.post("/api/v1/collector-control/parse-records", headers=headers, json={})

    assert response.status_code == 410
    assert "旧面单解析入口已停用" in response.json()["detail"]


def test_collector_token_rotation_requires_admin_authorization() -> None:
    with TestClient(app) as client:
        headers = login_headers(client)
        identity = "runtime-machine-a"
        registration = register_collector(client, headers, identity)
        original_token = str(registration["collector_token"])
        heartbeat_payload = {
            "collector_id": identity,
            "source_machine": identity,
            "runtime_status": "listening",
            "adapter_status": {"simulator": {"status": "ready"}},
            "queue_size": 0,
        }

        takeover = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": "attacker-selected-token"},
            json=heartbeat_payload,
        )
        assert takeover.status_code == 401

        original_heartbeat = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": original_token},
            json=heartbeat_payload,
        )
        assert original_heartbeat.status_code == 200

        rotated = register_collector(client, headers, identity)
        rotated_token = str(rotated["collector_token"])
        assert rotated_token != original_token

        old_token_heartbeat = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": original_token},
            json=heartbeat_payload,
        )
        assert old_token_heartbeat.status_code == 401

        rotated_heartbeat = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": rotated_token},
            json=heartbeat_payload,
        )
        assert rotated_heartbeat.status_code == 200


def test_collector_token_hash_migrates_from_previous_key(monkeypatch) -> None:
    previous_key = "previous-collector-token-key-at-least-32-bytes"
    with TestClient(app) as client:
        headers = login_headers(client)
        identity = "runtime-machine-key-migration"
        registration = register_collector(client, headers, identity)
        token = str(registration["collector_token"])
        collector_id = int(registration["collector"]["id"])

        with SessionLocal() as db:
            collector = db.get(Collector, collector_id)
            assert collector is not None
            collector.token_hash = collector_runtime_route.hash_collector_token(token, previous_key)
            db.commit()

        settings = collector_runtime_route.get_settings().model_copy(
            update={"collector_token_previous_hash_key": previous_key}
        )
        monkeypatch.setattr(collector_runtime_route, "get_settings", lambda: settings)

        heartbeat = client.post(
            "/api/v1/collector-runtime/heartbeat",
            headers={"X-Collector-Token": token},
            json={"collector_id": identity, "source_machine": identity},
        )
        assert heartbeat.status_code == 200

        with SessionLocal() as db:
            collector = db.get(Collector, collector_id)
            assert collector is not None
            assert collector.token_hash == collector_runtime_route.hash_collector_token(token)


def test_collector_status_reports_client_package_availability(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "collector-client"
    source_dir.mkdir()
    monkeypatch.setattr(collector_runtime_route, "collector_client_source_dir", lambda: source_dir)

    with TestClient(app) as client:
        headers = login_headers(client)
        missing_status = client.get("/api/v1/collector-control/status", headers=headers)
        assert missing_status.status_code == 200
        missing_package = missing_status.json()["collector_client"]
        assert missing_package["release_available"] is False
        assert missing_package["status"] == "missing"
        assert "collector-client/dist" in missing_package["message"]

        manifest = write_test_collector_release(source_dir)

        ready_status = client.get("/api/v1/collector-control/status", headers=headers)
        assert ready_status.status_code == 200
        ready_package = ready_status.json()["collector_client"]
        assert ready_package["release_available"] is True
        assert ready_package["status"] == "ready"
        assert ready_package["package_version"] == manifest["client_version"]
        assert ready_package["sha256"] == manifest["sha256"]
        assert ready_package["release_exe"] == "dist/Cargo Platform 采集器.exe"


def test_collector_status_rejects_invalid_release_manifest(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "collector-client"
    write_test_collector_release(source_dir, sha256="0" * 64)
    monkeypatch.setattr(collector_runtime_route, "collector_client_source_dir", lambda: source_dir)

    status_payload = collector_runtime_route.collector_client_release_status()

    assert status_payload["release_available"] is False
    assert status_payload["status"] == "invalid"
    assert "SHA-256" in status_payload["message"]


def test_collector_status_rejects_mixed_platform_version(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "collector-client"
    write_test_collector_release(source_dir)
    manifest_path = source_dir / collector_runtime_route.COLLECTOR_CLIENT_RELEASE_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_version"] = "9.9.9"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(collector_runtime_route, "collector_client_source_dir", lambda: source_dir)

    status_payload = collector_runtime_route.collector_client_release_status()

    assert status_payload["release_available"] is False
    assert status_payload["status"] == "invalid"
    assert "平台版本不一致" in status_payload["message"]


def test_collector_status_requires_release_manifest(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "collector-client"
    exe_path = source_dir / collector_runtime_route.COLLECTOR_CLIENT_RELEASE_EXE
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    exe_path.write_bytes(b"MZcollector exe stub")
    monkeypatch.setattr(collector_runtime_route, "collector_client_source_dir", lambda: source_dir)

    status_payload = collector_runtime_route.collector_client_release_status()

    assert status_payload["release_available"] is False
    assert status_payload["status"] == "missing"


def test_collector_client_download_contains_single_exe_package(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "collector-client"
    exe_content = b"MZcollector exe stub"
    manifest = write_test_collector_release(source_dir, exe_content=exe_content)
    monkeypatch.setattr(collector_runtime_route, "collector_client_source_dir", lambda: source_dir)

    with TestClient(app) as client:
        headers = login_headers(client)
        response = client.get("/api/v1/collector-client/download", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["content-length"] == str(len(response.content))

        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            names = set(archive.namelist())
            assert "Cargo Platform 采集器/Cargo Platform 采集器.exe" in names
            assert "Cargo Platform 采集器/VERSION.txt" in names
            assert "Cargo Platform 采集器/参数说明.txt" in names
            assert "Cargo Platform 采集器/collector-manifest.json" in names
            assert not any(name.endswith((".bat", ".vbs", ".py")) for name in names)
            assert archive.read("Cargo Platform 采集器/Cargo Platform 采集器.exe") == exe_content

            archived_manifest = json.loads(
                archive.read("Cargo Platform 采集器/collector-manifest.json").decode("utf-8")
            )
            assert archived_manifest == manifest

            version_text = archive.read("Cargo Platform 采集器/VERSION.txt").decode("utf-8")
            assert str(manifest["client_version"]) in version_text
            assert str(manifest["sha256"]) in version_text
            assert "single-exe" in version_text
            assert "token-only" in version_text

            guide_text = archive.read("Cargo Platform 采集器/参数说明.txt").decode("utf-8")
            assert '"Cargo Platform 采集器.exe" --base-url' in guide_text
            assert '--collector-name "%COMPUTERNAME%"' in guide_text
            assert "业务机不再输入系统账号密码" in guide_text
            assert "不要填写 8000 端口" in guide_text


def test_collector_client_download_rejects_hash_mismatch(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "collector-client"
    write_test_collector_release(source_dir, sha256="0" * 64)
    monkeypatch.setattr(collector_runtime_route, "collector_client_source_dir", lambda: source_dir)

    with TestClient(app) as client:
        headers = login_headers(client)
        response = client.get("/api/v1/collector-client/download", headers=headers)

    assert response.status_code == 503
    assert "SHA-256" in response.json()["detail"]


def test_collector_client_builds_raw_record_without_parser_fields(tmp_path) -> None:
    adapter = collector_client.PrintDbAdapter(
        source_component="cloud-print-client",
        display_name="Cloud Print",
        db_path=tmp_path / "print.db",
    )
    row = collector_client.PrintTaskRow(
        rowid=42,
        task_id="LOCAL-TASK-42",
        msg='{"task":{"taskID":"REMOTE-TASK-42","documents":[{"documentID":"DOC-42"}]}}',
        task_time="2026-06-18 10:11:12",
    )

    record = collector_client.build_raw_record(adapter, row)

    assert record["document_id"] == "DOC-42"
    assert record["source_component"] == "cloud-print-client"
    assert record["source_index"] == "42"
    assert record["dedupe_key"].startswith("cloud-print-client:LOCAL-TASK-42:")
    assert record["payload_format"] == "json"
    assert record["raw_payload"] == row.msg
    assert record["source_columns"] == {
        "rowid": 42,
        "component_task_id": "LOCAL-TASK-42",
        "task_time": "2026-06-18 10:11:12",
        "db_path": str(adapter.db_path),
    }
    assert "waybill_mode" not in record
    assert "parsed_payload" not in record
    assert "field_mapping" not in record
    assert "product_match" not in record


def test_collector_task_watermark_ignores_rows_before_capture_start(tmp_path) -> None:
    db_path = tmp_path / "print.db"
    start_utc = datetime.fromisoformat("2026-07-23T06:51:39.241233+00:00")
    start_local = start_utc.astimezone().replace(tzinfo=None)
    before_start = (start_local - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    after_start = (start_local + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("old-local-task", '{"task":{"taskID":"OLD"}}', before_start),
        )
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("new-local-task", '{"task":{"taskID":"NEW"}}', after_start),
        )

    adapter = collector_client.PrintDbAdapter(
        source_component="cainiao-cnprint",
        display_name="Cainiao",
        db_path=db_path,
    )
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 0})
    uploads: list[str] = []
    task = {
        "id": 58,
        "status": "collecting",
        "started_at": "2026-07-23T06:51:39.241233+00:00",
    }

    original_upload = collector_client.upload_records
    try:
        collector_client.upload_records = lambda _url, _token, _task_id, records: (
            uploads.extend(record["source_index"] for record in records)
            or {"inserted": len(records), "skipped": 0}
        )
        collector_client.upload_adapter_rows(
            "http://collector.test", "token", state, adapter, [task], 10, True
        )
    finally:
        collector_client.upload_records = original_upload

    assert uploads == ["2"]
    assert state.source_cursor(adapter) == 2


def test_fresh_collector_does_not_assign_old_invalid_time_row_to_active_round(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("old-valid", "{}", "2026-07-27 09:00:00"),
                ("old-invalid", "{}", "not-a-time"),
                ("current", "{}", "2026-07-27 10:01:00"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState()
    uploaded_rowids: list[int] = []

    def capture_upload(_base_url, _token, _task_id, records):
        uploaded_rowids.extend(int(record["source_columns"]["rowid"]) for record in records)
        return {"inserted": len(records), "duplicates": 0, "window_rejected": 0}

    monkeypatch.setattr(collector_client, "upload_records", capture_upload)
    collector_client.upload_adapter_rows(
        "http://collector.test",
        "token",
        state,
        adapter,
        [
            {
                "id": 58,
                "status": "collecting",
                "started_at": "2026-07-27T02:00:00+00:00",
            }
        ],
        100,
        True,
    )

    assert uploaded_rowids == [2, 3]
    assert state.source_cursor(adapter) == 3


def test_fresh_collector_quarantines_invalid_time_row_before_current_round_row(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("old", "{}", "2026-07-27 09:00:00"),
                ("current-invalid", "{}", "not-a-time"),
                ("current-valid", "{}", "2026-07-27 10:01:00"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState()
    uploads: list[dict[str, object]] = []

    def capture_upload(_base_url, _token, _task_id, records):
        uploads.extend(records)
        return {"inserted": len(records), "duplicates": 0, "window_rejected": 0}

    monkeypatch.setattr(collector_client, "upload_records", capture_upload)
    collector_client.upload_adapter_rows(
        "http://collector.test",
        "token",
        state,
        adapter,
        [
            {
                "id": 58,
                "status": "collecting",
                "started_at": "2026-07-27T02:00:00+00:00",
            }
        ],
        100,
        True,
    )

    assert [record["source_columns"]["rowid"] for record in uploads] == [2, 3]
    assert uploads[0]["source_columns"]["capture_assignment"] == "timestamp_invalid_fallback"
    assert state.source_cursor(adapter) == 3


def test_collector_keeps_print_created_after_idle_heartbeat_before_task_is_observed(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    capture_started_at = "2026-07-23T06:51:39.241233+00:00"
    start_local = datetime.strptime(
        collector_client.local_db_time_from_iso(capture_started_at),
        "%Y-%m-%d %H:%M:%S",
    )
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            (
                "old-local-task",
                '{"task":{"taskID":"OLD"}}',
                (start_local - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 1})
    heartbeat_count = 0
    uploaded_source_indexes: list[str] = []

    def heartbeat_with_task_start_race(*_args, **_kwargs):
        nonlocal heartbeat_count
        heartbeat_count += 1
        if heartbeat_count == 1:
            with collector_client.sqlite3.connect(db_path) as connection:
                connection.execute(
                    "insert into task (taskID, msg, time) values (?, ?, ?)",
                    (
                        "new-local-task",
                        '{"task":{"taskID":"NEW"}}',
                        (start_local + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
            return {
                "assignment_protocol_version": 2,
                "task_windows": [],
                "window_coverage_complete": True,
            }
        return {
            "assignment_protocol_version": 2,
            "task_windows": [{"id": 58, "status": "collecting", "started_at": capture_started_at}],
            "window_coverage_complete": True,
        }

    def capture_upload(_base_url, _token, _task_id, records):
        uploaded_source_indexes.extend(str(record["source_columns"]["rowid"]) for record in records)
        return {"inserted": len(records), "skipped": 0}

    monkeypatch.setattr(collector_client, "heartbeat", heartbeat_with_task_start_race)
    monkeypatch.setattr(collector_client, "upload_records", capture_upload)

    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)
    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)

    assert uploaded_source_indexes == ["2"]


def test_collector_keeps_task_open_for_print_committed_after_first_empty_closing_poll(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("already-uploaded", '{"task":{"taskID":"OLD"}}', "2026-07-27 10:00:01"),
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(
        idle_watermarks={"cainiao-cnprint": 1},
        capture_watermarks={"58:cainiao-cnprint": 1},
    )
    uploaded_source_indexes: list[str] = []

    completed_task = {
        "id": 58,
        "status": "completed",
        "started_at": "2026-07-27T01:59:00+00:00",
        "ended_at": "2026-07-27T02:00:05+00:00",
    }
    monkeypatch.setattr(
        collector_client,
        "heartbeat",
        lambda *_args, **_kwargs: {
            "assignment_protocol_version": 2,
            "task_windows": [completed_task],
            "window_coverage_complete": True,
        },
    )

    def capture_upload(_base_url, _token, _task_id, records):
        uploaded_source_indexes.extend(str(record["source_columns"]["rowid"]) for record in records)
        return {"inserted": len(records), "skipped": 0}

    monkeypatch.setattr(collector_client, "upload_records", capture_upload)

    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("late-commit", '{"task":{"taskID":"LATE"}}', "2026-07-27 10:00:02"),
        )
    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)

    assert uploaded_source_indexes == ["2"]


def test_collector_drains_completed_task_without_collecting_post_stop_prints(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("already-uploaded", '{"task":{"taskID":"OLD"}}', "2026-07-27 10:00:01"),
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(
        idle_watermarks={"cainiao-cnprint": 1},
        capture_watermarks={"58:cainiao-cnprint": 1},
    )
    uploaded_source_indexes: list[str] = []
    completed_task = {
        "id": 58,
        "status": "completed",
        "started_at": "2026-07-27T01:59:00+00:00",
        "ended_at": "2026-07-27T02:00:05+00:00",
    }
    monkeypatch.setattr(
        collector_client,
        "heartbeat",
        lambda *_args, **_kwargs: {
            "assignment_protocol_version": 2,
            "task_windows": [completed_task],
            "window_coverage_complete": True,
        },
    )

    def capture_upload(_base_url, _token, _task_id, records):
        uploaded_source_indexes.extend(str(record["source_columns"]["rowid"]) for record in records)
        return {"inserted": len(records), "skipped": 0}

    monkeypatch.setattr(collector_client, "upload_records", capture_upload)

    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("late-before-stop", '{"task":{"taskID":"LATE"}}', "2026-07-27 10:00:04"),
                ("printed-after-stop", '{"task":{"taskID":"IDLE"}}', "2026-07-27 10:00:06"),
            ],
        )
    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)

    assert uploaded_source_indexes == ["2"]


def test_collector_keeps_previous_round_until_late_print_can_no_longer_enter_new_round(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("already-uploaded", '{"task":{"taskID":"OLD"}}', "2026-07-27 10:00:01"),
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(
        idle_watermarks={"cainiao-cnprint": 1},
        capture_watermarks={"58:cainiao-cnprint": 1},
    )
    completed_task = {
        "id": 58,
        "status": "completed",
        "started_at": "2026-07-27T01:59:00+00:00",
        "ended_at": "2026-07-27T02:00:05+00:00",
    }
    collecting_task = {
        "id": 59,
        "status": "collecting",
        "started_at": "2026-07-27T02:01:00+00:00",
    }

    uploads: list[tuple[int, str]] = []

    def capture_upload(_base_url, _token, task_id, records):
        uploads.extend((task_id, str(record["source_columns"]["rowid"])) for record in records)
        return {"inserted": len(records), "skipped": 0}

    monkeypatch.setattr(
        collector_client,
        "heartbeat",
        lambda *_args, **_kwargs: {
            "assignment_protocol_version": 2,
            "task_windows": [completed_task, collecting_task],
            "window_coverage_complete": True,
        },
    )
    monkeypatch.setattr(collector_client, "upload_records", capture_upload)

    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)
    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("late-old-round", '{"task":{"taskID":"LATE"}}', "2026-07-27 10:00:04"),
        )
    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)

    assert uploads == [(58, "2")]


def test_collector_requests_windows_for_oldest_time_in_the_whole_pending_batch(tmp_path) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("committed-first", "{}", "2026-07-27 10:05:00"),
                ("late-old-time", "{}", "2026-07-27 10:01:00"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 0})

    assert collector_client.pending_batch_profile(state, [adapter], 100) == (
        2,
        "2026-07-27 10:01:00",
        "2026-07-27 10:05:00",
    )


def test_collector_keeps_invalid_timestamp_row_with_an_audit_reason(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("invalid-time", "{}", "not-a-time"),
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 0})
    uploaded: list[dict[str, object]] = []

    def capture_upload(_base_url, _token, _task_id, records):
        uploaded.extend(records)
        return {"inserted": len(records), "duplicates": 0, "window_rejected": 0}

    monkeypatch.setattr(collector_client, "upload_records", capture_upload)
    collector_client.upload_adapter_rows(
        "http://collector.test",
        "token",
        state,
        adapter,
        [
            {
                "id": 58,
                "status": "completed",
                "started_at": "2026-07-27T01:59:00+00:00",
                "ended_at": "2026-07-27T02:00:05+00:00",
            }
        ],
        100,
    )

    assert uploaded[0]["captured_at"] == "not-a-time"
    assert uploaded[0]["source_columns"]["capture_assignment"] == "timestamp_invalid_fallback"
    assert state.source_cursor(adapter) == 1


def test_fresh_collector_does_not_baseline_past_an_invalid_timestamp_row(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("old", '{"task":"old"}', "2026-07-27 09:00:00"),
                ("invalid", '{"task":"invalid"}', "not-a-time"),
                ("late-old", '{"task":"late-old"}', "2026-07-27 09:30:00"),
                ("current", '{"task":"current"}', "2026-07-27 10:01:00"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState()
    uploaded: list[dict[str, object]] = []

    def capture_upload(_base_url, _token, _task_id, records):
        uploaded.extend(records)
        return {"inserted": len(records), "duplicates": 0, "window_rejected": 0}

    monkeypatch.setattr(collector_client, "upload_records", capture_upload)
    collector_client.upload_adapter_rows(
        "http://collector.test",
        "token",
        state,
        adapter,
        [
            {
                "id": 58,
                "status": "collecting",
                "started_at": "2026-07-27T02:00:00+00:00",
            }
        ],
        100,
        window_coverage_complete=True,
    )

    assert [record["source_columns"]["rowid"] for record in uploaded] == [2, 4]
    assert uploaded[0]["source_columns"]["capture_assignment"] == "timestamp_invalid_fallback"
    assert state.source_cursor(adapter) == 4


def test_collector_assigns_same_second_boundary_row_to_one_round_only(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("boundary", '{"task":{"taskID":"BOUNDARY"}}', "2026-07-27 10:00:05"),
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 0})
    monkeypatch.setattr(
        collector_client,
        "heartbeat",
        lambda *_args, **_kwargs: {
            "assignment_protocol_version": 2,
            "task_windows": [
                {
                    "id": 58,
                    "status": "completed",
                    "started_at": "2026-07-27T01:59:00+00:00",
                    "ended_at": "2026-07-27T02:00:05.900000+00:00",
                },
                {
                    "id": 59,
                    "status": "collecting",
                    "started_at": "2026-07-27T02:00:05.100000+00:00",
                },
            ],
            "window_coverage_complete": True,
        },
    )
    uploads: list[tuple[int, str]] = []

    def capture_upload(_base_url, _token, task_id, records):
        uploads.extend((task_id, str(record["source_columns"]["rowid"])) for record in records)
        return {"inserted": len(records), "skipped": 0}

    monkeypatch.setattr(collector_client, "upload_records", capture_upload)

    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)

    assert uploads == [(59, "1")]


def test_collector_keeps_cursor_when_print_db_is_replaced_with_same_history(tmp_path) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("one", '{"task":"one"}', "2026-07-27 10:00:01"),
                ("two", '{"task":"two"}', "2026-07-27 10:00:02"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(
        idle_watermarks={"cainiao-cnprint": 2},
        db_generations={"cainiao-cnprint": str(adapter.generation())},
    )
    state.advance_source_cursor(adapter, 2)
    state.sync_db_generation(adapter)
    original_epoch = state.source_epochs["cainiao-cnprint"]

    replacement = tmp_path / "replacement.db"
    shutil.copyfile(db_path, replacement)
    replacement_adapter = collector_client.PrintDbAdapter(
        "cainiao-cnprint",
        "Cainiao",
        replacement,
    )
    assert str(replacement_adapter.generation()) != state.db_generations["cainiao-cnprint"]

    state.sync_db_generation(replacement_adapter)

    assert state.source_cursor(replacement_adapter) == 2
    assert state.source_epochs["cainiao-cnprint"] == original_epoch


def test_collector_resets_cursor_and_changes_source_identity_for_new_print_db(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("old", '{"task":"old"}', "2026-07-27 10:00:01"),
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(
        idle_watermarks={"cainiao-cnprint": 1},
        db_generations={"cainiao-cnprint": str(adapter.generation())},
    )
    state.advance_source_cursor(adapter, 1)
    old_generation = state.db_generations["cainiao-cnprint"]

    replacement = tmp_path / "replacement.db"
    with collector_client.sqlite3.connect(replacement) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("new", '{"task":"new"}', "2026-07-27 10:00:03"),
        )
    replacement_adapter = collector_client.PrintDbAdapter(
        "cainiao-cnprint",
        "Cainiao",
        replacement,
    )
    state.sync_db_generation(replacement_adapter)
    assert state.source_cursor(replacement_adapter) == 0
    assert state.db_generations["cainiao-cnprint"] != old_generation

    uploaded_source_indexes: list[str] = []

    def capture_upload(_base_url, _token, _task_id, records):
        uploaded_source_indexes.extend(record["source_index"] for record in records)
        return {"inserted": len(records), "duplicates": 0, "window_rejected": 0}

    monkeypatch.setattr(collector_client, "upload_records", capture_upload)
    collector_client.upload_adapter_rows(
        "http://collector.test",
        "token",
        state,
        replacement_adapter,
        [
            {
                "id": 58,
                "status": "collecting",
                "started_at": "2026-07-27T01:59:00+00:00",
            }
        ],
        100,
    )

    assert uploaded_source_indexes == [
        f"{state.source_epochs['cainiao-cnprint']}:1"
    ]


def test_collector_rotates_logical_epoch_when_same_db_file_is_rebuilt(tmp_path) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("old-1", '{"task":"old-1"}', "2026-07-27 10:00:01"),
                ("old-2", '{"task":"old-2"}', "2026-07-27 10:00:02"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 2})
    state.advance_source_cursor(adapter, 2)
    state.sync_db_generation(adapter)
    original_generation = state.db_generations["cainiao-cnprint"]
    assert state.source_epochs["cainiao-cnprint"] == ""

    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("delete from task")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("new-1", '{"task":"new-1"}', "2026-07-27 11:00:01"),
                ("new-2", '{"task":"new-2"}', "2026-07-27 11:00:02"),
                ("new-3", '{"task":"new-3"}', "2026-07-27 11:00:03"),
            ],
        )

    state.sync_db_generation(adapter)

    assert state.db_generations["cainiao-cnprint"] == original_generation
    assert state.source_cursor(adapter) == 0
    assert state.source_epochs["cainiao-cnprint"]


def test_collector_quarantines_history_when_uploaded_row_is_updated(tmp_path) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("one", '{"task":"one"}', "2026-07-27 10:00:01"),
                ("two", '{"task":"two"}', "2026-07-27 10:00:02"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 2})
    state.advance_source_cursor(adapter, 2)
    state.sync_db_generation(adapter)
    original_epoch = state.source_epochs["cainiao-cnprint"]

    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("update task set msg = ? where rowid = 2", ('{"task":"two-updated"}',))

    state.sync_db_generation(adapter)

    assert state.source_cursor(adapter) == 0
    assert state.source_epochs["cainiao-cnprint"] != original_epoch
    assert state.ambiguous_replay_until["cainiao-cnprint"] == 2
    assert state.last_reconnect_reason == "source_history_ambiguous"


def test_collector_quarantines_reused_rows_when_same_db_is_rebuilt_with_same_origin(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("same", '{"task":"same"}', "2026-07-27 10:00:01"),
                ("old", '{"task":"old"}', "2026-07-27 10:00:02"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 2})
    state.advance_source_cursor(adapter, 2)
    state.sync_db_generation(adapter)

    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("delete from task")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("same", '{"task":"same"}', "2026-07-27 10:00:01"),
                ("new-two", '{"task":"new-two"}', "2026-07-27 10:00:02"),
                ("new-three", '{"task":"new-three"}', "2026-07-27 10:00:03"),
            ],
        )

    state.sync_db_generation(adapter)
    uploads: list[dict[str, object]] = []

    def capture_upload(_base_url, _token, _task_id, records):
        uploads.extend(records)
        return {"inserted": len(records), "duplicates": 0, "window_rejected": 0}

    monkeypatch.setattr(collector_client, "upload_records", capture_upload)
    collector_client.upload_adapter_rows(
        "http://collector.test",
        "token",
        state,
        adapter,
        [
            {
                "id": 58,
                "status": "collecting",
                "started_at": "2026-07-27T01:59:00+00:00",
            }
        ],
        100,
    )

    assert [record["source_columns"]["rowid"] for record in uploads] == [1, 2, 3]
    assert [
        record["source_columns"].get("capture_assignment") for record in uploads
    ] == ["source_history_ambiguous", "source_history_ambiguous", None]
    assert state.source_cursor(adapter) == 3
    assert "cainiao-cnprint" not in state.ambiguous_replay_until


def test_collector_persists_rotated_epoch_before_upload_for_ambiguous_and_reset_recovery(
    tmp_path,
    monkeypatch,
) -> None:
    attempts: dict[str, list[list[str]]] = {"ambiguous": [], "reset": []}
    active_case = ""
    seen: dict[str, set[str]] = {"ambiguous": set(), "reset": set()}

    monkeypatch.setattr(
        collector_client,
        "heartbeat",
        lambda *_args, **_kwargs: {
            "assignment_protocol_version": 2,
            "task_windows": [
                {
                    "id": 58,
                    "status": "collecting",
                    "started_at": "2026-07-27T01:59:00+00:00",
                }
            ],
            "window_coverage_complete": True,
        },
    )

    def capture_upload(_base_url, _token, _task_id, records):
        indexes = [str(record["source_index"]) for record in records]
        attempts[active_case].append(indexes)
        inserted = sum(index not in seen[active_case] for index in indexes)
        seen[active_case].update(indexes)
        return {
            "inserted": inserted,
            "duplicates": len(indexes) - inserted,
            "window_rejected": 0,
        }

    monkeypatch.setattr(collector_client, "upload_records", capture_upload)

    for case in ("ambiguous", "reset"):
        active_case = case
        case_dir = tmp_path / case
        case_dir.mkdir()
        db_path = case_dir / "print.db"
        state_path = case_dir / "collector-state.json"
        with collector_client.sqlite3.connect(db_path) as connection:
            connection.execute("create table task (taskID text, msg text, time text)")
            connection.executemany(
                "insert into task (taskID, msg, time) values (?, ?, ?)",
                [
                    ("old-1", '{"task":"old-1"}', "2026-07-27 10:00:01"),
                    ("old-2", '{"task":"old-2"}', "2026-07-27 10:00:02"),
                ],
            )
        connection.close()

        adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
        initial_state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 2})
        initial_state.advance_source_cursor(adapter, 2)
        initial_state.sync_db_generation(adapter)
        initial_state.save(state_path)

        if case == "ambiguous":
            with collector_client.sqlite3.connect(db_path) as connection:
                connection.execute("delete from task")
                connection.executemany(
                    "insert into task (taskID, msg, time) values (?, ?, ?)",
                    [
                        ("old-1", '{"task":"old-1"}', "2026-07-27 10:00:01"),
                        ("new-2", '{"task":"new-2"}', "2026-07-27 10:00:02"),
                        ("new-3", '{"task":"new-3"}', "2026-07-27 10:00:03"),
                    ],
                )
            connection.close()
        else:
            with collector_client.sqlite3.connect(db_path) as connection:
                connection.execute("delete from task")
                connection.execute(
                    "insert into task (taskID, msg, time) values (?, ?, ?)",
                    ("new-1", '{"task":"new-1"}', "2026-07-27 10:00:01"),
                )
            connection.close()

        crashed_state = collector_client.CollectorState.load(state_path)
        collector_client.run_sqlite_once(
            "http://collector.test",
            "token",
            crashed_state,
            [adapter],
            100,
        )
        persisted_before_upload = collector_client.CollectorState.load(state_path)
        assert persisted_before_upload.source_cursor(adapter) == 0

        with collector_client.sqlite3.connect(db_path) as connection:
            next_row = connection.execute("select coalesce(max(rowid), 0) + 1 from task").fetchone()[0]
            connection.execute(
                "insert into task (taskID, msg, time) values (?, ?, ?)",
                (
                    f"new-{next_row}",
                    f'{{"task":"new-{next_row}"}}',
                    f"2026-07-27 10:00:0{next_row}",
                ),
            )
        connection.close()

        restarted_state = collector_client.CollectorState.load(state_path)
        collector_client.run_sqlite_once(
            "http://collector.test",
            "token",
            restarted_state,
            [adapter],
            100,
        )

        first_attempt, second_attempt = attempts[case]
        assert second_attempt[: len(first_attempt)] == first_attempt
        assert len(second_attempt) == len(first_attempt) + 1
        assert len(seen[case]) == len(second_attempt)


def test_collector_rechecks_database_snapshot_after_heartbeat_before_advancing_cursor(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    state_path = tmp_path / "collector-state.json"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("old-1", '{"task":"old-1"}', "2026-07-27 10:00:01"),
                ("old-2", '{"task":"old-2"}', "2026-07-27 10:00:02"),
            ],
        )
    connection.close()

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 2})
    state.advance_source_cursor(adapter, 2)
    state.sync_db_generation(adapter)
    state.state_path = state_path
    state.save(state_path)

    heartbeat_count = 0

    def rebuild_during_heartbeat(*_args, **_kwargs):
        nonlocal heartbeat_count
        heartbeat_count += 1
        if heartbeat_count == 1:
            with collector_client.sqlite3.connect(db_path) as connection:
                connection.execute("delete from task")
                connection.executemany(
                    "insert into task (taskID, msg, time) values (?, ?, ?)",
                    [
                        ("old-1", '{"task":"old-1"}', "2026-07-27 10:00:01"),
                        ("new-2", '{"task":"new-2"}', "2026-07-27 10:00:02"),
                        ("new-3", '{"task":"new-3"}', "2026-07-27 10:00:03"),
                    ],
                )
            connection.close()
        return {
            "assignment_protocol_version": 2,
            "task_windows": [
                {
                    "id": 58,
                    "status": "collecting",
                    "started_at": "2026-07-27T01:59:00+00:00",
                }
            ],
            "window_coverage_complete": True,
        }

    uploads: list[list[dict[str, object]]] = []

    def capture_upload(_base_url, _token, _task_id, records):
        uploads.append(records)
        return {"inserted": len(records), "duplicates": 0, "window_rejected": 0}

    monkeypatch.setattr(collector_client, "heartbeat", rebuild_during_heartbeat)
    monkeypatch.setattr(collector_client, "upload_records", capture_upload)

    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)
    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)

    assert len(uploads) == 1
    assert [record["source_columns"]["rowid"] for record in uploads[0]] == [1, 2, 3]
    assert [
        record["source_columns"].get("capture_assignment") for record in uploads[0]
    ] == ["source_history_ambiguous", "source_history_ambiguous", None]
    assert state.source_cursor(adapter) == 3


def test_collector_detects_middle_history_change_committed_during_upload(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("same-1", '{"task":"same-1"}', "2026-07-27 10:00:01"),
                ("old-2", '{"task":"old-2"}', "2026-07-27 10:00:02"),
                ("same-3", '{"task":"same-3"}', "2026-07-27 10:00:03"),
            ],
        )
    connection.close()

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 2})
    state.advance_source_cursor(adapter, 2)
    state.sync_db_generation(adapter)
    monkeypatch.setattr(
        collector_client,
        "heartbeat",
        lambda *_args, **_kwargs: {
            "assignment_protocol_version": 2,
            "task_windows": [
                {
                    "id": 58,
                    "status": "collecting",
                    "started_at": "2026-07-27T01:59:00+00:00",
                }
            ],
            "window_coverage_complete": True,
        },
    )

    uploads: list[list[dict[str, object]]] = []

    def rebuild_on_first_upload(_base_url, _token, _task_id, records):
        uploads.append(records)
        if len(uploads) == 1:
            with collector_client.sqlite3.connect(db_path) as connection:
                connection.execute("delete from task")
                connection.executemany(
                    "insert into task (taskID, msg, time) values (?, ?, ?)",
                    [
                        ("same-1", '{"task":"same-1"}', "2026-07-27 10:00:01"),
                        ("new-2", '{"task":"new-2"}', "2026-07-27 10:00:02"),
                        ("same-3", '{"task":"same-3"}', "2026-07-27 10:00:03"),
                    ],
                )
            connection.close()
        return {"inserted": len(records), "duplicates": 0, "window_rejected": 0}

    monkeypatch.setattr(collector_client, "upload_records", rebuild_on_first_upload)

    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)
    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)

    assert [record["source_columns"]["rowid"] for record in uploads[0]] == [3]
    assert [record["source_columns"]["rowid"] for record in uploads[1]] == [1, 2, 3]
    assert uploads[1][1]["source_columns"]["capture_assignment"] == "source_history_ambiguous"
    assert sum(
        record["source_columns"]["rowid"] == 3
        and not record["source_columns"].get("capture_assignment")
        for batch in uploads
        for record in batch
    ) == 1


def test_collector_audits_prefix_only_at_startup_and_when_backlog_is_drained(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                (str(index), f'{{"task":"{index}"}}', f"2026-07-27 10:{index // 60:02d}:{index % 60:02d}")
                for index in range(1, 251)
            ],
        )
    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(
        idle_watermarks={"cainiao-cnprint": 0},
        source_epochs={"cainiao-cnprint": "epoch-a"},
        prefix_fingerprints={
            "cainiao-cnprint": collector_client.EMPTY_PREFIX_FINGERPRINT,
        },
        prefix_start_rowids={"cainiao-cnprint": 0},
    )
    monkeypatch.setattr(
        collector_client,
        "heartbeat",
        lambda *_args, **_kwargs: {
            "assignment_protocol_version": 2,
            "task_windows": [
                {
                    "id": 58,
                    "status": "collecting",
                    "started_at": "2026-07-27T01:59:00+00:00",
                }
            ],
            "window_coverage_complete": True,
        },
    )
    monkeypatch.setattr(
        collector_client,
        "upload_records",
        lambda _base_url, _token, _task_id, records: {
            "inserted": len(records),
            "duplicates": 0,
            "window_rejected": 0,
        },
    )
    audits: list[tuple[int, int]] = []
    original_prefix_fingerprint = collector_client.PrintDbAdapter.prefix_fingerprint

    def counted_prefix_fingerprint(self, start_rowid, end_rowid):
        audits.append((start_rowid, end_rowid))
        return original_prefix_fingerprint(self, start_rowid, end_rowid)

    monkeypatch.setattr(
        collector_client.PrintDbAdapter,
        "prefix_fingerprint",
        counted_prefix_fingerprint,
    )

    for _ in range(3):
        collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)

    assert state.source_cursor(adapter) == 250
    assert audits == [(0, 0), (0, 250)]


def test_collector_detects_caught_up_middle_row_rewrite_without_new_rowid(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("same-1", '{"task":"same-1"}', "2026-07-27 10:00:01"),
                ("old-2", '{"task":"old-2"}', "2026-07-27 10:00:02"),
                ("same-3", '{"task":"same-3"}', "2026-07-27 10:00:03"),
            ],
        )
    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(
        idle_watermarks={"cainiao-cnprint": 0},
        source_epochs={"cainiao-cnprint": "epoch-a"},
        prefix_fingerprints={
            "cainiao-cnprint": collector_client.EMPTY_PREFIX_FINGERPRINT,
        },
        prefix_start_rowids={"cainiao-cnprint": 0},
    )
    monkeypatch.setattr(
        collector_client,
        "heartbeat",
        lambda *_args, **_kwargs: {
            "assignment_protocol_version": 2,
            "task_windows": [
                {
                    "id": 58,
                    "status": "collecting",
                    "started_at": "2026-07-27T01:59:00+00:00",
                }
            ],
            "window_coverage_complete": True,
        },
    )
    uploads: list[list[dict[str, object]]] = []
    monkeypatch.setattr(
        collector_client,
        "upload_records",
        lambda _base_url, _token, _task_id, records: (
            uploads.append(records)
            or {"inserted": len(records), "duplicates": 0, "window_rejected": 0}
        ),
    )

    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)
    previous_change_token = adapter.change_token()
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute(
            "update task set taskID = ?, msg = ? where rowid = 2",
            ("new-2", '{"task":"new-2"}'),
        )
    assert adapter.change_token() != previous_change_token

    collector_client.run_sqlite_once("http://collector.test", "token", state, [adapter], 100)

    assert [record["source_columns"]["rowid"] for record in uploads[0]] == [1, 2, 3]
    assert [record["source_columns"]["rowid"] for record in uploads[1]] == [1, 2, 3]
    assert uploads[1][1]["raw_payload"] == '{"task":"new-2"}'
    assert all(
        record["source_columns"]["capture_assignment"] == "source_history_ambiguous"
        for record in uploads[1]
    )


def test_consecutive_same_max_middle_rewrites_rotate_source_epoch_each_time(tmp_path) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("same-1", '{"task":"same-1"}', "2026-07-27 10:00:01"),
                ("old-2", '{"task":"old-2"}', "2026-07-27 10:00:02"),
                ("same-3", '{"task":"same-3"}', "2026-07-27 10:00:03"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState()
    initial_snapshot = adapter.snapshot(0, 100)
    assert initial_snapshot is not None
    state.sync_db_generation(adapter, initial_snapshot)
    state.advance_source_cursor(adapter, 3, snapshot=initial_snapshot)

    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute(
            "update task set taskID = ?, msg = ? where rowid = 2",
            ("new-2", '{"task":"new-2"}'),
        )
    state.sync_db_generation(adapter, adapter.snapshot(3, 100), audit_prefix=True)
    first_replay_epoch = state.source_epochs[adapter.source_component]
    first_replay_snapshot = adapter.snapshot(0, 100)
    assert first_replay_snapshot is not None
    state.advance_source_cursor(adapter, 3, snapshot=first_replay_snapshot)

    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute(
            "update task set taskID = ?, msg = ? where rowid = 2",
            ("newer-2", '{"task":"newer-2"}'),
        )
    state.sync_db_generation(adapter, adapter.snapshot(3, 100), audit_prefix=True)

    assert state.source_epochs[adapter.source_component] != first_replay_epoch
    assert state.source_cursor(adapter) == 0
    assert state.ambiguous_replay_until[adapter.source_component] == 3


def test_collector_state_replays_old_file_conservatively_and_keeps_rollback_watermark(tmp_path) -> None:
    state_path = tmp_path / "collector-state.json"
    collector_client.write_json(
        state_path,
        {
            "idle_watermarks": {"cainiao-cnprint": 0},
            "capture_watermarks": {
                "57:cainiao-cnprint": 1,
                "58:cainiao-cnprint": 2,
            },
        },
    )
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("1", "{}", "2026-07-27 10:00:01"),
                ("2", "{}", "2026-07-27 10:00:02"),
                ("3", "{}", "2026-07-27 10:00:03"),
            ],
        )
    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)

    state = collector_client.CollectorState.load(state_path)
    state.sync_db_generation(adapter)

    assert state.last_upload_at is None
    assert state.last_reconnect_reason == "source_history_ambiguous"
    assert state.source_cursor(adapter) == 0
    assert state.pending_count([adapter]) == 3
    assert state.source_epochs["cainiao-cnprint"]
    assert state.ambiguous_replay_until["cainiao-cnprint"] == 2
    assert collector_client.build_raw_record(
        adapter,
        adapter.read_since(0, 1)[0],
        state.source_epochs["cainiao-cnprint"],
    )["source_index"].endswith(":1")

    state.advance_source_cursor(adapter, 1)
    state.last_upload_at = "2026-07-27T10:00:00+00:00"
    state.last_reconnect_reason = "network"
    state.save(state_path)
    reloaded = collector_client.CollectorState.load(state_path)
    assert reloaded.last_upload_at == "2026-07-27T10:00:00+00:00"
    assert reloaded.last_reconnect_reason == "network"
    assert reloaded.capture_watermarks == {"58:cainiao-cnprint": 1}
    assert reloaded.source_cursor(adapter) == 1


def test_collector_pending_count_is_unknown_when_print_db_is_unavailable(tmp_path) -> None:
    adapter = collector_client.PrintDbAdapter(
        "cainiao-cnprint",
        "Cainiao",
        tmp_path / "missing.db",
    )
    state = collector_client.CollectorState(
        capture_watermarks={"58:cainiao-cnprint": 0},
    )

    assert state.pending_count([adapter]) is None


def test_collector_retries_unacknowledged_rows_without_advancing_watermark(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("local-1", '{"task":"one"}', "2026-07-27 10:00:01"),
                ("local-2", '{"task":"two"}', "2026-07-27 10:00:02"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 0})
    attempts: list[list[str]] = []

    def upload_with_lost_first_response(_base_url, _token, _task_id, records):
        attempts.append([record["source_index"] for record in records])
        if len(attempts) == 1:
            raise ConnectionError("response lost")
        return {"inserted": 0, "skipped": len(records)}

    monkeypatch.setattr(collector_client, "upload_records", upload_with_lost_first_response)
    monkeypatch.setattr(
        collector_client,
        "utc_now",
        lambda: "2026-07-27T10:00:00+00:00",
    )
    tasks = [
        {
            "id": 58,
            "status": "collecting",
            "started_at": "2026-07-27T01:59:00+00:00",
        }
    ]

    try:
        collector_client.upload_adapter_rows(
            "http://collector.test", "token", state, adapter, tasks, 100
        )
    except ConnectionError:
        pass
    else:
        raise AssertionError("first upload must simulate a lost response")

    assert state.source_cursor(adapter) == 0
    assert state.last_upload_at is None
    assert collector_client.upload_adapter_rows(
        "http://collector.test",
        "token",
        state,
        adapter,
        tasks,
        100,
    ) == 2
    assert attempts == [["1", "2"], ["1", "2"]]
    assert state.source_cursor(adapter) == 2
    assert state.last_upload_at == "2026-07-27T10:00:00+00:00"


def test_collector_does_not_advance_cursor_when_server_rejects_a_task_window(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.execute(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            ("local-1", '{"task":"one"}', "2026-07-27 10:00:01"),
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 0})
    state.sync_db_generation(adapter)
    monkeypatch.setattr(
        collector_client,
        "upload_records",
        lambda *_args, **_kwargs: {
            "inserted": 0,
            "duplicates": 0,
            "window_rejected": 1,
        },
    )

    try:
        collector_client.upload_adapter_rows(
            "http://collector.test",
            "token",
            state,
            adapter,
            [
                {
                    "id": 58,
                    "status": "collecting",
                    "started_at": "2026-07-27T01:59:00+00:00",
                }
            ],
            100,
        )
    except RuntimeError as exc:
        assert "outside task 58" in str(exc)
    else:
        raise AssertionError("window rejection must keep the local row retryable")

    assert state.source_cursor(adapter) == 0


def test_collector_reconnect_reason_uses_stable_categories() -> None:
    auth_error = urllib.error.HTTPError("http://test", 401, "unauthorized", {}, None)
    http_error = urllib.error.HTTPError("http://test", 503, "unavailable", {}, None)

    assert collector_client.reconnect_reason(auth_error) == "auth"
    assert collector_client.reconnect_reason(http_error) == "http"
    assert collector_client.reconnect_reason(collector_client.sqlite3.OperationalError("locked")) == "sqlite"
    assert collector_client.reconnect_reason(ConnectionError("offline")) == "network"


def test_collector_heartbeat_reports_upload_status(monkeypatch) -> None:
    sent_payloads: list[dict[str, object]] = []

    def capture_post(_base_url, _path, _token, payload):
        sent_payloads.append(payload)
        return {"tasks": []}

    monkeypatch.setattr(collector_client, "post_json", capture_post)
    state = collector_client.CollectorState(
        capture_watermarks={"58:cainiao-cnprint": 1},
        last_upload_at="2026-07-27T10:00:00+00:00",
        last_reconnect_reason="network",
    )

    collector_client.heartbeat("http://collector.test", "token", [], state=state)

    assert len(sent_payloads) == 1
    assert sent_payloads[0]["queue_size"] == 0
    assert sent_payloads[0]["last_upload_at"] == "2026-07-27T10:00:00+00:00"
    assert sent_payloads[0]["last_reconnect_reason"] == "network"
    assert "tracked_task_ids" not in sent_payloads[0]


def test_collector_check_rejects_server_without_assignment_protocol_v2(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        collector_client,
        "heartbeat",
        lambda *_args, **_kwargs: {
            "assignment_protocol_version": 1,
            "tasks": [],
            "collector": {"id": 1, "online_status": "online"},
        },
    )

    assert collector_client.run_check(
        collector_client.CollectorConfig(token="token"),
        tmp_path / "collector-config.json",
        tmp_path / "collector-state.json",
        [],
    ) == 1


def test_collector_state_file_allows_only_one_running_instance(tmp_path) -> None:
    state_path = tmp_path / "collector-state.json"
    first = collector_client.CollectorInstanceLock(state_path)
    second = collector_client.CollectorInstanceLock(state_path)

    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()


def test_collector_state_save_failure_records_reason(tmp_path, monkeypatch) -> None:
    state = collector_client.CollectorState()

    def fail_save(_path) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(state, "save", fail_save)

    collector_client.save_state_safely(
        state,
        tmp_path / "collector-state.json",
        collector_client.ReconnectNotice(),
    )

    assert state.last_reconnect_reason == "state_save"


def test_collector_unexpected_poll_error_stays_retryable(monkeypatch) -> None:
    config = collector_client.CollectorConfig(token="token")
    state = collector_client.CollectorState()
    notice = collector_client.ReconnectNotice()
    warnings: list[tuple[object, ...]] = []

    def fail_poll(*_args, **_kwargs):
        raise ValueError("bad payload")

    monkeypatch.setattr(notice, "warning", lambda *args: warnings.append(args))

    returned = collector_client.poll_collector_safely(
        config,
        state,
        [],
        1,
        None,
        notice,
        poll_once=fail_poll,
    )

    assert returned is config
    assert state.last_reconnect_reason == "unexpected"
    assert warnings[0][0] == "unexpected"


def test_raw_record_upload_keeps_records_inside_the_capture_task_window() -> None:
    def local_db_time(value: str, offset: timedelta = timedelta()) -> str:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")) + offset
        return moment.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(sep=" ")

    def record(source_index: str, captured_at: str) -> dict[str, str]:
        return {
            "source_component": "cainiao-cnprint",
            "source_index": source_index,
            "payload_format": "json",
            "raw_payload": f'{{"task":"{source_index}"}}',
            "captured_at": captured_at,
        }

    with TestClient(app) as client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "task-window-machine")
        collector_headers = {"X-Collector-Token": str(registration["collector_token"])}

        task = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"name": "Capture window"},
        ).json()
        upload = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers=collector_headers,
            json={
                "task_id": task["id"],
                "records": [
                    record("old-window-replay", local_db_time(task["started_at"], -timedelta(minutes=1))),
                    record("current-window", local_db_time(task["started_at"])),
                ],
            },
        )
        assert upload.json() == {"inserted": 1, "skipped": 1}

        stopped_task = client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": task["id"]},
        ).json()
        after_stop = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers=collector_headers,
            json={
                "task_id": task["id"],
                "records": [
                    record("after-stop", local_db_time(stopped_task["ended_at"], timedelta(seconds=1)))
                ],
            },
        )
        assert after_stop.json() == {"inserted": 0, "skipped": 1}


def test_raw_record_upload_contract_stops_at_raw_capture_record() -> None:
    assert COLLECTION_MODULE_OUTPUT_CONTRACT == "raw_capture_record"
    assert COLLECTION_MODULE_SIMILARITY_POLICY == "no_similarity_or_fingerprint_decisions"
    assert COLLECTION_MODULE_RULE_POLICY == "no_field_product_or_similarity_rules"
    assert "raw_payload" in RAW_CAPTURE_RECORD_CONTRACT_FIELDS
    assert "status" in RAW_CAPTURE_RECORD_CONTRACT_FIELDS
    assert "waybill_mode" not in RAW_CAPTURE_RECORD_CONTRACT_FIELDS
    assert "parsed_payload" not in RAW_CAPTURE_RECORD_CONTRACT_FIELDS
    assert "source_columns" in RAW_CAPTURE_RECORD_SOURCE_METADATA_FIELDS
    assert "collector_id" in RAW_CAPTURE_RECORD_SOURCE_METADATA_FIELDS

    with TestClient(app) as client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "raw-contract-machine")
        collector_token = str(registration["collector_token"])
        task = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"name": "Raw contract capture"},
        )
        assert task.status_code == 201

        upload = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers={"X-Collector-Token": collector_token},
            json={
                "task_id": task.json()["id"],
                "records": [
                    {
                        "document_id": "RAW-CONTRACT-1",
                        "source_machine": "raw-contract-machine",
                        "source_component": "simulator",
                        "source_index": "1",
                        "dedupe_key": "raw-contract-1",
                        "waybill_mode": "client-guessed-mode",
                        "payload_format": "json",
                        "raw_payload": "{\"order\":\"A001\"}",
                        "source_columns": {
                            "rowid": 17,
                            "component_task_id": "LOCAL-TASK-17",
                            "task_time": "2026-06-17 10:11:12",
                            "db_path": "C:\\PrintClient\\print.db",
                        },
                        "parsed_payload": {"client_guess": True},
                    }
                ],
            },
        )
        assert upload.status_code == 201
        assert upload.json() == {"inserted": 1, "skipped": 0}

        raw_records = client.get("/api/v1/raw-capture-records?limit=2000", headers=headers)
        assert raw_records.status_code == 200
        stored_record = next(
            record for record in raw_records.json() if record["document_id"] == "RAW-CONTRACT-1"
        )
        assert stored_record["status"] == "pending"
        assert stored_record["waybill_mode"] is None
        assert stored_record["raw_payload"] == "{\"order\":\"A001\"}"
        assert stored_record["parsed_payload"] is None
        assert stored_record["standard_detail_id"] is None
        assert stored_record["source_machine"] == "raw-contract-machine"
        assert stored_record["source_component"] == "simulator"
        assert stored_record["source_index"] == "1"
        assert stored_record["dedupe_key"] == "raw-contract-1"
        assert stored_record["source_columns"]["rowid"] == 17
        assert stored_record["source_columns"]["component_task_id"] == "LOCAL-TASK-17"

        raw_document = client.get(
            f"/api/v1/collector-control/tasks/{task.json()['id']}/raw-document",
            headers=headers,
        )
        assert raw_document.status_code == 200
        disposition = unquote(raw_document.headers["content-disposition"])
        assert "capture-task" not in disposition
        assert f"-{task.json()['id']}-" not in disposition
        assert re.search(r"采集原文_\d{8}_\d{6}\.xlsx", disposition)
        workbook = load_workbook(BytesIO(raw_document.content))
        sheet = workbook.active
        headers_row = [cell.value for cell in sheet[1]]
        assert headers_row == [
            "ID",
            "采集器",
            "电脑名",
            "来源组件",
            "来源序号",
            "去重键",
            "采集时间",
            "原文格式",
            "本地来源信息",
            "状态",
            "采集原文",
        ]
        values_row = [cell.value for cell in sheet[2]]
        assert values_row[4] == "1"
        assert values_row[5] == "raw-contract-1"
        assert values_row[7] == "json"
        assert '"component_task_id": "LOCAL-TASK-17"' in values_row[8]
        assert values_row[9] == "pending"
        assert values_row[10] == "{\"order\":\"A001\"}"

        raw_records_after_upload = client.get("/api/v1/raw-capture-records?limit=2000", headers=headers)
        assert raw_records_after_upload.status_code == 200
        uploaded_record = next(
            record for record in raw_records_after_upload.json() if record["document_id"] == "RAW-CONTRACT-1"
        )
        assert uploaded_record["standard_detail_id"] is None

        stop = client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": task.json()["id"]},
        )
        assert stop.status_code == 200
        assert stop.json()["status"] == "completed"


def test_invalid_capture_time_is_explained_as_a_visible_exception() -> None:
    row = collector_runtime_route.pending_unmapped_waybill_product_sku_linking_row(
        {
            "raw_record_id": 7133,
            "capture_assignment": "timestamp_invalid_fallback",
            "sample_text": "商品原文",
        },
        detail_number=1,
    )

    assert row["coverage_only"] is True
    assert row["status"] == "pending"
    assert row["reason"] == "这条打印记录的采集时间无效，已保留并隔离，请检查采集源时间。"
    assert row["exception_code"] == "timestamp_invalid_fallback"

    history_row = collector_runtime_route.pending_unmapped_waybill_product_sku_linking_row(
        {"capture_assignment": "source_history_ambiguous"},
        detail_number=2,
    )
    assert history_row["reason"] == "打印数据库历史发生变化，这条记录已保留并隔离，请检查采集源。"
    assert history_row["exception_code"] == "source_history_ambiguous"


def test_raw_record_upload_preserves_identical_prints_with_different_source_indexes() -> None:
    with TestClient(app) as client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "repeated-print-machine")
        collector_headers = {"X-Collector-Token": str(registration["collector_token"])}
        task = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"name": "Repeated print capture"},
        )
        assert task.status_code == 201
        task_id = task.json()["id"]
        repeated_payload = '{"order":"SAME-WAYBILL"}'

        upload = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers=collector_headers,
            json={
                "task_id": task_id,
                "records": [
                    {
                        "document_id": "REPEATED-PRINT-1",
                        "source_component": "cainiao-cnprint",
                        "source_index": "101",
                        "dedupe_key": "same-content-key",
                        "payload_format": "json",
                        "raw_payload": repeated_payload,
                    },
                    {
                        "document_id": "REPEATED-PRINT-2",
                        "source_component": "cainiao-cnprint",
                        "source_index": "102",
                        "dedupe_key": "same-content-key",
                        "payload_format": "json",
                        "raw_payload": repeated_payload,
                    },
                ],
            },
        )

        retry = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers=collector_headers,
            json={
                "task_id": task_id,
                "records": [
                    {
                        "document_id": "REPEATED-PRINT-1",
                        "source_component": "cainiao-cnprint",
                        "source_index": "101",
                        "dedupe_key": "same-content-key",
                        "payload_format": "json",
                        "raw_payload": repeated_payload,
                    }
                ],
            },
        )

        stop = client.post(
            "/api/v1/collector-control/stop",
            headers=headers,
            json={"task_id": task_id},
        )

        assert upload.status_code == 201
        assert upload.json() == {"inserted": 2, "skipped": 0}
        assert retry.status_code == 201
        assert retry.json() == {"inserted": 0, "skipped": 1}
        assert stop.status_code == 200


def test_raw_record_upload_rejects_unbounded_batches_and_payloads() -> None:
    with TestClient(app) as client:
        headers = login_headers(client)
        registration = register_collector(client, headers, "raw-validation-machine")
        collector_token = str(registration["collector_token"])
        task = client.post(
            "/api/v1/collector-control/start",
            headers=headers,
            json={"name": "Raw validation capture"},
        )
        assert task.status_code == 201
        task_id = task.json()["id"]
        collector_headers = {"X-Collector-Token": collector_token}

        empty_batch = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers=collector_headers,
            json={"task_id": task_id, "records": []},
        )
        assert empty_batch.status_code == 422

        missing_v2_identity = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers=collector_headers,
            json={
                "task_id": task_id,
                "assignment_protocol_version": 2,
                "records": [{"raw_payload": "{}"}],
            },
        )
        assert missing_v2_identity.status_code == 422

        too_many_records = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers=collector_headers,
            json={
                "task_id": task_id,
                "records": [
                    {
                        "document_id": f"TOO-MANY-{index}",
                        "payload_format": "json",
                        "raw_payload": "{}",
                    }
                    for index in range(RAW_CAPTURE_BATCH_MAX_RECORDS + 1)
                ],
            },
        )
        assert too_many_records.status_code == 422

        oversized_payload = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers=collector_headers,
            json={
                "task_id": task_id,
                "records": [
                    {
                        "document_id": "OVERSIZED-PAYLOAD",
                        "payload_format": "json",
                        "raw_payload": "x" * (RAW_CAPTURE_PAYLOAD_MAX_CHARS + 1),
                    }
                ],
            },
        )
        assert oversized_payload.status_code == 422

        oversized_source_columns = client.post(
            "/api/v1/collector-runtime/raw-records",
            headers=collector_headers,
            json={
                "task_id": task_id,
                "records": [
                    {
                        "document_id": "OVERSIZED-SOURCE-COLUMNS",
                        "payload_format": "json",
                        "raw_payload": "{}",
                        "source_columns": {
                            "audit_text": "x" * (RAW_CAPTURE_SOURCE_COLUMNS_MAX_CHARS + 1),
                        },
                    }
                ],
            },
        )
        assert oversized_source_columns.status_code == 422
