from __future__ import annotations

import http.client
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import sys
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
os.environ["SECRET_KEY"] = "test-secret"
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
from app.models import Collector, RecognitionRulePack  # noqa: E402
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


def test_collector_client_default_name_uses_windows_machine_name(monkeypatch) -> None:
    monkeypatch.setenv("COMPUTERNAME", "WAREHOUSE-PC-08")

    assert collector_client.default_collector_name() == "WAREHOUSE-PC-08"
    assert collector_client.normalize_collector_name("") == "WAREHOUSE-PC-08"
    assert collector_client.normalize_collector_name("Cargo Platform 采集器") == "WAREHOUSE-PC-08"
    assert collector_client.normalize_collector_name("采集器") == "WAREHOUSE-PC-08"


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
                "queue_size": 0,
            },
        )

    assert heartbeat.status_code == 200
    assert heartbeat.json()["collector"]["collector_name"] == "WAREHOUSE-PC-09"
    assert heartbeat.json()["collector"]["source_machine"] == "WAREHOUSE-PC-09"


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


def test_collector_token_rebind_keeps_three_heartbeats_accepted() -> None:
    with TestClient(app) as client:
        headers = login_headers(client)
        identities = ["runtime-machine-a", "runtime-machine-b", "runtime-machine-c"]
        for identity in identities:
            register_collector(client, headers, identity)

        for index, identity in enumerate(identities, start=1):
            heartbeat = client.post(
                "/api/v1/collector-runtime/heartbeat",
                headers={"X-Collector-Token": f"rebound-token-{index}"},
                json={
                    "collector_id": identity,
                    "source_machine": identity,
                    "runtime_status": "listening",
                    "adapter_status": {"simulator": {"status": "ready"}},
                    "queue_size": 0,
                },
            )
            assert heartbeat.status_code == 200
            assert heartbeat.json()["collector"]["online_status"] == "online"
            assert heartbeat.json()["collector"]["collector_id"] == identity


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

        exe_path = source_dir / collector_runtime_route.COLLECTOR_CLIENT_RELEASE_EXE
        exe_path.parent.mkdir(parents=True, exist_ok=True)
        exe_path.write_bytes(b"collector exe stub")

        ready_status = client.get("/api/v1/collector-control/status", headers=headers)
        assert ready_status.status_code == 200
        ready_package = ready_status.json()["collector_client"]
        assert ready_package["release_available"] is True
        assert ready_package["status"] == "ready"
        assert ready_package["package_version"] == collector_runtime_route.COLLECTOR_CLIENT_PACKAGE_VERSION
        assert ready_package["release_exe"] == "dist/Cargo Platform 采集器.exe"


def test_collector_client_download_contains_single_exe_package(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "collector-client"
    exe_path = source_dir / collector_runtime_route.COLLECTOR_CLIENT_RELEASE_EXE
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    exe_path.write_bytes(b"collector exe stub")
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
            assert not any(name.endswith((".bat", ".vbs", ".json", ".py")) for name in names)
            assert archive.read("Cargo Platform 采集器/Cargo Platform 采集器.exe") == b"collector exe stub"

            version_text = archive.read("Cargo Platform 采集器/VERSION.txt").decode("utf-8")
            assert collector_runtime_route.COLLECTOR_CLIENT_PACKAGE_VERSION in version_text
            assert "single-exe" in version_text
            assert "token-only" in version_text

            guide_text = archive.read("Cargo Platform 采集器/参数说明.txt").decode("utf-8")
            assert '"Cargo Platform 采集器.exe" --base-url' in guide_text
            assert '--collector-name "%COMPUTERNAME%"' in guide_text
            assert "业务机不再输入系统账号密码" in guide_text
            assert "不要填写 8000 端口" in guide_text


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
