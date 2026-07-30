from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from threading import Event, Thread
import time
from typing import Any

from fastapi.testclient import TestClient
import httpx
from services.shared.waybill_fingerprint import business_shape_fingerprint, fingerprint_for_payload


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "services" / "ai-recognition" / "service_app"


def load_ai_service(default_db: Path):
    package_name = "cargo_platform_ai_recognition_test"
    for module_name in list(sys.modules):
        if module_name == package_name or module_name.startswith(f"{package_name}."):
            del sys.modules[module_name]

    package_spec = importlib.util.spec_from_file_location(
        package_name,
        PACKAGE_DIR / "__init__.py",
        submodule_search_locations=[str(PACKAGE_DIR)],
    )
    assert package_spec is not None and package_spec.loader is not None
    package = importlib.util.module_from_spec(package_spec)
    sys.modules[package_name] = package
    package_spec.loader.exec_module(package)

    main_spec = importlib.util.spec_from_file_location(
        f"{package_name}.main",
        PACKAGE_DIR / "main.py",
    )
    assert main_spec is not None and main_spec.loader is not None
    module = importlib.util.module_from_spec(main_spec)
    sys.modules[main_spec.name] = module
    previous = os.environ.get("AI_RECOGNITION_DB")
    os.environ["AI_RECOGNITION_DB"] = str(default_db)
    try:
        main_spec.loader.exec_module(module)
    finally:
        if previous is None:
            os.environ.pop("AI_RECOGNITION_DB", None)
        else:
            os.environ["AI_RECOGNITION_DB"] = previous
    return module


def raw_request(product: str = "范74") -> dict[str, Any]:
    return {
        "workspace_id": 1,
        "task_id": 61,
        "raw_record_id": 901,
        "document_sequence": 1,
        "source_component": "cainiao-cnprint",
        "deterministic_failure_reason": "format_profile_missing",
        "payload": {
            "receiverName": "张三",
            "phone": "13800138000",
            "address": "福建省泉州市测试路 1 号",
            "waybillCode": "SF123456789012",
            "task": {
                "documents": [
                    {
                        "contents": [
                            {
                                "data": {
                                    "productName": product,
                                    "sku": "5代白金 / 45",
                                    "quantity": 1,
                                }
                            }
                        ]
                    }
                ]
            },
        },
    }


def package_request() -> dict[str, Any]:
    request = raw_request()
    request["payload"] = {
        "receiverName": "张三",
        "task": {
            "documents": [
                {
                    "contents": [
                        {
                            "data": {
                                "packageItemDetail": [
                                    {
                                        "itemName": "范74",
                                        "skuFullName": "5代白金 45",
                                        "itemNum": 1,
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        },
    }
    return request


def candidate(product: str = "范74") -> dict[str, Any]:
    return {
        "contract_version": "ai_waybill_candidate_v1",
        "fingerprint": "model-value-is-overwritten",
        "parents": [
            {
                "source": {"raw_record_id": 901, "document_index": 0},
                "rows": [
                    {
                        "product": product,
                        "sales_attr1": "5代白金",
                        "sales_attr2": "45",
                        "quantity": 1,
                        "remark": "",
                    }
                ],
            }
        ],
        "rule_evidence": ["商品名称和规格位于固定结构化路径"],
        "candidate_rule": {
            "strategy": "structured_items_v1",
            "items_path": "task.documents[].contents[].data",
            "fields": {
                "product": "productName",
                "sales_attr1": "sku",
                "quantity": "quantity",
            },
        },
        "warnings": [],
    }


class FakeModel:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or candidate()
        self.calls: list[dict[str, Any]] = []

    def recognize(
        self,
        payload: dict[str, Any],
        fingerprint: str,
        feedback: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "payload": payload,
                "fingerprint": fingerprint,
                "feedback": feedback or [],
            }
        )
        return self.result


def wait_for_session(client: TestClient, session_id: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + 2
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v1/sessions/{session_id}").json()
        if payload["status"] == status:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"session {session_id} did not reach {status}; last={payload}")


def test_recognize_returns_running_session_before_slow_model_finishes(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")

    class SlowModel(FakeModel):
        def __init__(self) -> None:
            super().__init__()
            self.started = Event()
            self.release = Event()

        def recognize(
            self,
            payload: dict[str, Any],
            fingerprint: str,
            feedback: list[str] | None = None,
        ) -> dict[str, Any]:
            self.started.set()
            self.release.wait(2)
            return super().recognize(payload, fingerprint, feedback)

    model = SlowModel()
    app = module.create_app(model_client=model, db_path=tmp_path / "sessions.db")
    result: dict[str, Any] = {}
    with TestClient(app) as client:
        request_thread = Thread(
            target=lambda: result.update(response=client.post("/api/v1/recognize", json=raw_request())),
            daemon=True,
        )
        request_thread.start()
        assert model.started.wait(1)
        try:
            request_thread.join(0.2)
            assert not request_thread.is_alive()
            response = result["response"]
            assert response.status_code == 200
            assert response.json()["status"] == "model_running"
        finally:
            model.release.set()
            request_thread.join(2)

        wait_for_session(client, result["response"].json()["session_id"], "ai_rule_pending")


def test_existing_session_database_adds_document_sequence_column(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    database = tmp_path / "legacy-sessions.db"
    with sqlite3.connect(database) as db:
        db.execute(
            """
            CREATE TABLE recognition_sessions (
                session_id TEXT PRIMARY KEY,
                request_key TEXT NOT NULL UNIQUE,
                workspace_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                raw_record_id INTEGER NOT NULL,
                source_component TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                deterministic_failure_reason TEXT NOT NULL,
                sanitized_payload TEXT NOT NULL,
                candidate TEXT,
                feedback TEXT NOT NULL DEFAULT '[]',
                platform_response TEXT,
                status TEXT NOT NULL,
                error TEXT,
                model_calls INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO recognition_sessions(
                session_id, request_key, workspace_id, task_id, raw_record_id,
                source_component, fingerprint, deterministic_failure_reason,
                sanitized_payload, candidate, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-session",
                "legacy-request",
                1,
                61,
                901,
                "test",
                "sha256:test",
                "format_profile_missing",
                json.dumps({"product": "shoe"}),
                json.dumps(candidate()),
                "ai_rule_pending",
                "2026-07-29T00:00:00+00:00",
                "2026-07-29T00:00:00+00:00",
            ),
        )

    store = module.SessionStore(database)
    session, created = store.reserve(
        request_key="new-request",
        workspace_id=1,
        task_id=61,
        raw_record_id=901,
        document_sequence=3,
        source_component="test",
        fingerprint="sha256:test",
        deterministic_failure_reason="format_profile_missing",
        sanitized_payload={"product": "shoe"},
    )

    assert created is True
    assert session["document_sequence"] == 3
    assert session["generation"] == 1
    legacy = store.get("legacy-session")
    assert legacy is not None
    assert legacy["document_sequence"] == 0
    assert legacy["generation"] == 0

    app = module.create_app(model_client=FakeModel(), db_path=database)
    with TestClient(app) as client:
        approval = client.post("/api/v1/sessions/legacy-session/approve")
    assert approval.status_code == 409
    assert approval.json()["detail"] == "旧会话无法确定所选面单，请重新创建识别会话。"


def test_recognize_sanitizes_pii_and_reuses_identical_session(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel()
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        console_base_url="http://127.0.0.1:6183",
    )

    request = raw_request()
    request["payload"]["requestID"] = "technical-request-id"
    request["payload"]["task"]["taskID"] = "technical-task-id"
    request["payload"]["task"]["printer"] = r"\\machine\printer"
    content = request["payload"]["task"]["documents"][0]["contents"][0]
    content["encryptedData"] = "AES:opaque-secret"
    content["templateURL"] = "https://example.invalid/template"
    content["addData"] = {"sender": {"name": "某店铺"}}
    content["printXML"] = (
        "<layout><text><![CDATA[范33 带木one帆布kw，木村-3M反光，40*1]]></text></layout>"
    )
    content["data"]["SHOP_NAME"] = "某店铺"
    content["data"]["ITEM_INFO"] = "范74 5代白金 45 【1件】"

    with TestClient(app) as client:
        first = client.post("/api/v1/recognize", json=request)
        second = client.post("/api/v1/recognize", json=request)
        stored = wait_for_session(client, first.json()["session_id"], "ai_rule_pending")

    assert first.status_code == 200
    assert first.json()["status"] == "model_running"
    assert first.json()["session_id"] == second.json()["session_id"]
    assert first.json()["console_url"].startswith("http://127.0.0.1:6183/console?session=")
    assert stored["candidate"]
    assert stored["document_sequence"] == 1
    assert stored["model_input"] == {
        "fingerprint": stored["fingerprint"],
        "sanitized_payload": model.calls[0]["payload"],
        "administrator_feedback": [],
    }
    assert len(model.calls) == 1
    sent = json.dumps(model.calls[0]["payload"], ensure_ascii=False)
    assert "张三" not in sent
    assert "13800138000" not in sent
    assert "福建省泉州市" not in sent
    assert "SF123456789012" not in sent
    assert "technical-request-id" not in sent
    assert "technical-task-id" not in sent
    assert "machine" not in sent
    assert "AES:opaque-secret" not in sent
    assert "example.invalid" not in sent
    assert "某店铺" not in sent
    assert "范74" in sent
    assert "ITEM_INFO" in sent
    assert "范33 带木one帆布kw，木村-3M反光，40*1" in sent
    assert "<layout>" not in sent


def test_recognize_sends_only_tenant_selected_fingerprint_fields_to_model(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel()
    app = module.create_app(model_client=model, db_path=tmp_path / "sessions.db")
    request = raw_request()
    request["payload"] = {
        "contents": [
            {
                "data": {
                    "productInfo": "范74 5代白金 45 1件",
                    "productShortInfo": "范74",
                    "sPInfo": "5代白金 45",
                    "remark": "不要传给模型",
                    "buyerRemark": "也不要传给模型",
                    "productCount": "1件",
                }
            }
        ]
    }
    request["field_selections"] = {
        "CLOUD-PRODUCT-INFO": ["product_info", "product_count"],
    }

    with TestClient(app) as client:
        response = client.post("/api/v1/recognize", json=request)
        wait_for_session(client, response.json()["session_id"], "ai_rule_pending")

    assert model.calls[0]["payload"] == {
        "contents": [
            {
                "data": {
                    "productInfo": "范74 5代白金 45 1件",
                    "productCount": "1件",
                }
            }
        ]
    }


def test_package_session_uses_shared_business_shape_v2_fingerprint(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    app = module.create_app(model_client=FakeModel(), db_path=tmp_path / "sessions.db")
    request = package_request()

    with TestClient(app) as client:
        response = client.post("/api/v1/recognize", json=request)

    fingerprint = response.json()["fingerprint"]
    assert fingerprint.startswith("v2:CN-PACKAGE-ITEMS:sha256:")
    assert fingerprint == business_shape_fingerprint(request["payload"], request["source_component"])
    assert fingerprint == module.business_shape_fingerprint(request["payload"], request["source_component"])
    assert fingerprint == fingerprint_for_payload(
        request["payload"], request["source_component"], "business_shape_v2"
    )


def test_tenant_selection_changes_model_input_without_changing_raw_v2_fingerprint(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel()
    app = module.create_app(model_client=model, db_path=tmp_path / "sessions.db")
    first = package_request()
    second = package_request()
    first["field_selections"] = {"CN-PACKAGE-ITEMS": ["item_name"]}
    second["field_selections"] = {"CN-PACKAGE-ITEMS": ["sku_full_name"]}
    second["raw_record_id"] = 902

    with TestClient(app) as client:
        first_response = client.post("/api/v1/recognize", json=first)
        second_response = client.post("/api/v1/recognize", json=second)
        wait_for_session(client, first_response.json()["session_id"], "ai_rule_pending")
        wait_for_session(client, second_response.json()["session_id"], "ai_rule_pending")

    assert first_response.json()["fingerprint"] == second_response.json()["fingerprint"]
    assert model.calls[0]["payload"] != model.calls[1]["payload"]
    assert "itemName" in json.dumps(model.calls[0]["payload"], ensure_ascii=False)
    assert "skuFullName" in json.dumps(model.calls[1]["payload"], ensure_ascii=False)


def test_field_selection_cannot_smuggle_receiver_name_past_catalog(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel()
    app = module.create_app(model_client=model, db_path=tmp_path / "sessions.db")
    request = package_request()
    request["field_selections"] = {"CN-PACKAGE-ITEMS": ["item_name", "receiverName"]}

    with TestClient(app) as client:
        inspected = client.post(
            "/api/v1/fingerprints/inspect",
            json={"source_component": request["source_component"], "payload": request["payload"]},
        )
        response = client.post("/api/v1/recognize", json=request)
        wait_for_session(client, response.json()["session_id"], "ai_rule_pending")

    assert inspected.json()["fingerprint_code"] == "CN-PACKAGE-ITEMS"
    sent = json.dumps(model.calls[0]["payload"], ensure_ascii=False)
    assert "范74" in sent
    assert "张三" not in sent


def test_rejected_identical_request_starts_a_new_session(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel()
    app = module.create_app(model_client=model, db_path=tmp_path / "sessions.db")

    with TestClient(app) as client:
        first = client.post("/api/v1/recognize", json=raw_request()).json()
        wait_for_session(client, first["session_id"], "ai_rule_pending")
        client.post(f"/api/v1/sessions/{first['session_id']}/reject")
        second = client.post("/api/v1/recognize", json=raw_request()).json()
        wait_for_session(client, second["session_id"], "ai_rule_pending")

    assert second["status"] == "model_running"
    assert second["session_id"] != first["session_id"]
    assert len(model.calls) == 2


def test_fingerprint_is_value_stable_but_changes_with_structure(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    app = module.create_app(model_client=FakeModel(), db_path=tmp_path / "sessions.db")

    with TestClient(app) as client:
        first = client.post("/api/v1/recognize", json=raw_request("范74")).json()
        second = client.post("/api/v1/recognize", json=raw_request("秒45")).json()
        punctuation_value = client.post("/api/v1/recognize", json=raw_request("范74/联名")).json()
        changed = raw_request()
        changed["payload"]["task"]["documents"][0]["contents"][0]["data"]["color"] = "白色"
        third = client.post("/api/v1/recognize", json=changed).json()

    assert first["fingerprint"] == second["fingerprint"]
    assert first["fingerprint"] == punctuation_value["fingerprint"]
    assert first["fingerprint"] != third["fingerprint"]


def test_invalid_model_candidate_becomes_business_failure(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel({"contract_version": "ai_waybill_candidate_v1", "parents": []})
    app = module.create_app(model_client=model, db_path=tmp_path / "sessions.db")

    with TestClient(app) as client:
        response = client.post("/api/v1/recognize", json=raw_request())
        stored = wait_for_session(client, response.json()["session_id"], "ai_parse_failed")

    assert response.status_code == 200
    assert response.json()["status"] == "model_running"
    assert stored["error"]
    assert stored["candidate"] is None


def test_invalid_ai_row_remains_editable_until_admin_corrects_it(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    invalid = candidate()
    invalid["parents"][0]["rows"][0]["product"] = ""
    invalid["parents"][0]["rows"][0]["quantity"] = 0
    app = module.create_app(
        model_client=FakeModel(invalid),
        db_path=tmp_path / "sessions.db",
    )
    corrected_rows = [
        {
            "product": "登山鞋",
            "sales_attr1": "紫色",
            "sales_attr2": "42.5",
            "quantity": 1,
            "remark": "",
        }
    ]

    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        failed = wait_for_session(client, session_id, "ai_result_invalid")
        approval = client.post(f"/api/v1/sessions/{session_id}/approve")
        client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"message": json.dumps({"corrected_rows": corrected_rows}, ensure_ascii=False)},
        )
        corrected = wait_for_session(client, session_id, "ai_rule_pending")

    assert failed["candidate"]["parents"][0]["rows"][0]["product"] == ""
    assert failed["candidate"]["parents"][0]["rows"][0]["quantity"] == 0
    assert failed["error"] == "AI 未完整识别商品或数量，请修改后重新生成规则。"
    assert approval.status_code == 409
    assert corrected["candidate"]["parents"][0]["rows"][0]["product"] == "登山鞋"
    assert corrected["candidate"]["parents"][0]["rows"][0]["quantity"] == 1


def test_field_labels_in_ai_values_are_rejected_as_editable_result(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    invalid = candidate()
    invalid["parents"][0]["rows"][0]["product"] = "商品是带木one帆布kw"
    invalid["parents"][0]["rows"][0]["sales_attr1"] = "销售属性1是木村-3M反光"
    app = module.create_app(
        model_client=FakeModel(invalid),
        db_path=tmp_path / "sessions.db",
    )

    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        stored = wait_for_session(client, session_id, "ai_result_invalid")

    assert stored["candidate"]["parents"][0]["rows"][0]["product"] == "商品是带木one帆布kw"
    assert stored["error"] == "AI 返回的字段值包含字段名称，请修改后重新生成规则。"


def test_structured_candidate_rule_paths_are_normalized_from_payload(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    result = candidate()
    result["candidate_rule"] = {
        "strategy": "structured_items_v1",
        "items_path": "[0]",
        "fields": {
            "product": ".productName",
            "sales_attr1": ".sku",
            "quantity": ".quantity",
        },
        "steps": [
            {
                "op": "rsplit",
                "source": "sales_attr1",
                "delimiter": " ",
                "targets": ["sales_attr1", "sales_attr2"],
            }
        ],
    }
    app = module.create_app(
        model_client=FakeModel(result),
        db_path=tmp_path / "sessions.db",
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/recognize", json=raw_request())
        stored = wait_for_session(client, response.json()["session_id"], "ai_rule_pending")

    assert response.json()["status"] == "model_running"
    rule = stored["candidate"]["candidate_rule"]
    assert rule["items_path"] == "task.documents[].contents[].data"
    assert rule["fields"] == {
        "product": "productName",
        "sales_attr1": "sku",
        "quantity": "quantity",
    }
    assert rule["steps"] == result["candidate_rule"]["steps"]


def test_feedback_reruns_model_and_persists_session(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    database = tmp_path / "sessions.db"
    model = FakeModel()
    app = module.create_app(model_client=model, db_path=database)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_pending")
        response = client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"message": "销售属性1应该只保留5代白金"},
        )
        stored = wait_for_session(client, session_id, "ai_rule_pending")

    assert response.status_code == 200
    assert response.json()["status"] == "model_running"
    assert stored["status"] == "ai_rule_pending"
    assert len(model.calls) == 2
    assert model.calls[-1]["payload"] == model.calls[0]["payload"]
    assert model.calls[-1]["feedback"] == ["销售属性1应该只保留5代白金"]

    restarted = module.create_app(model_client=FakeModel(), db_path=database)
    with TestClient(restarted) as client:
        stored = client.get(f"/api/v1/sessions/{session_id}")
    assert stored.status_code == 200
    assert stored.json()["feedback"] == ["销售属性1应该只保留5代白金"]


def test_admin_corrected_rows_are_preserved_when_rule_is_regenerated(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel()
    app = module.create_app(model_client=model, db_path=tmp_path / "sessions.db")
    corrected_rows = [
        {
            "product": "带木one帆布kw",
            "sales_attr1": "木村-3M反光",
            "sales_attr2": "40",
            "quantity": 1,
            "remark": "",
        }
    ]
    message = json.dumps({"corrected_rows": corrected_rows}, ensure_ascii=False)

    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_pending")
        response = client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"message": message},
        )
        stored = wait_for_session(client, session_id, "ai_rule_pending")

    assert response.status_code == 200
    row = stored["candidate"]["parents"][0]["rows"][0]
    assert {
        key: row[key]
        for key in ("product", "sales_attr1", "sales_attr2", "quantity", "remark")
    } == corrected_rows[0]
    assert model.calls[-1]["feedback"] == [message]


def test_structured_correction_accepts_many_rows_without_message_limit(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel()
    app = module.create_app(model_client=model, db_path=tmp_path / "sessions.db")
    corrected_rows = [
        {
            "product": f"商品{i}-" + "长名称" * 30,
            "sales_attr1": "颜色",
            "sales_attr2": str(35 + i),
            "quantity": 1,
            "remark": "",
        }
        for i in range(30)
    ]

    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_pending")
        response = client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"corrected_rows": corrected_rows, "note": "管理员核对"},
        )
        stored = wait_for_session(client, session_id, "ai_rule_pending")

    assert response.status_code == 200
    assert stored["candidate"]["parents"][0]["rows"] == corrected_rows
    assert json.loads(stored["feedback"][-1]) == {
        "corrected_rows": corrected_rows,
        "note": "管理员核对",
    }


def test_older_feedback_result_cannot_replace_newer_correction(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")

    class SequencedModel(FakeModel):
        def __init__(self) -> None:
            super().__init__()
            self.invocations = 0
            self.second_started = Event()
            self.release_second = Event()
            self.third_started = Event()
            self.release_third = Event()

        def recognize(
            self,
            payload: dict[str, Any],
            fingerprint: str,
            feedback: list[str] | None = None,
        ) -> dict[str, Any]:
            self.invocations += 1
            if self.invocations == 2:
                self.second_started.set()
                self.release_second.wait(2)
            elif self.invocations == 3:
                self.third_started.set()
                self.release_third.wait(2)
            return super().recognize(payload, fingerprint, feedback)

    model = SequencedModel()
    app = module.create_app(model_client=model, db_path=tmp_path / "sessions.db")
    correction_a = [{
        "product": "修正 A",
        "sales_attr1": "",
        "sales_attr2": "",
        "quantity": 1,
        "remark": "",
    }]
    correction_b = [{**correction_a[0], "product": "修正 B"}]

    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_pending")
        client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"corrected_rows": correction_a},
        )
        assert model.second_started.wait(1)
        client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"corrected_rows": correction_b},
        )
        model.release_second.set()
        assert model.third_started.wait(1)
        try:
            current = client.get(f"/api/v1/sessions/{session_id}").json()
            approval = client.post(f"/api/v1/sessions/{session_id}/approve")
            assert current["status"] == "model_running"
            assert approval.status_code == 409
        finally:
            model.release_third.set()
        stored = wait_for_session(client, session_id, "ai_rule_pending")

    assert stored["candidate"]["parents"][0]["rows"] == correction_b


def test_duplicate_feedback_is_idempotent_while_model_is_running(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")

    class BlockingFeedbackModel(FakeModel):
        def __init__(self) -> None:
            super().__init__()
            self.invocations = 0
            self.feedback_started = Event()
            self.release_feedback = Event()

        def recognize(
            self,
            payload: dict[str, Any],
            fingerprint: str,
            feedback: list[str] | None = None,
        ) -> dict[str, Any]:
            self.invocations += 1
            if self.invocations == 2:
                self.feedback_started.set()
                self.release_feedback.wait(2)
            return super().recognize(payload, fingerprint, feedback)

    model = BlockingFeedbackModel()
    app = module.create_app(model_client=model, db_path=tmp_path / "sessions.db")
    correction = [{
        "product": "修正商品",
        "sales_attr1": "黑色",
        "sales_attr2": "42",
        "quantity": 1,
        "remark": "",
    }]

    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_pending")
        first = client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"corrected_rows": correction},
        )
        assert model.feedback_started.wait(1)
        duplicate = client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"corrected_rows": correction},
        )
        current = client.get(f"/api/v1/sessions/{session_id}").json()
        model.release_feedback.set()
        stored = wait_for_session(client, session_id, "ai_rule_pending")

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["generation"] == first.json()["generation"]
    assert current["generation"] == first.json()["generation"]
    assert len(current["feedback"]) == 1
    assert len(stored["feedback"]) == 1
    assert model.invocations == 2


def test_feedback_is_rejected_while_rule_approval_is_running(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    approval_started = Event()
    release_approval = Event()

    def blocking_approval(payload: dict[str, Any], _token: str) -> dict[str, Any]:
        if payload.get("validate_only"):
            return {"status": "valid"}
        approval_started.set()
        release_approval.wait(2)
        return {"status": "activated"}

    app = module.create_app(
        model_client=FakeModel(),
        db_path=tmp_path / "sessions.db",
        internal_token="test-token",
        approval_sender=blocking_approval,
    )
    result: dict[str, Any] = {}
    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_pending")
        thread = Thread(
            target=lambda: result.update(
                approval=client.post(f"/api/v1/sessions/{session_id}/approve")
            ),
            daemon=True,
        )
        thread.start()
        assert approval_started.wait(1)
        feedback = client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"message": "不能在批准中修改"},
        )
        rejection = client.post(f"/api/v1/sessions/{session_id}/reject")
        release_approval.set()
        thread.join(2)

    assert feedback.status_code == 409
    assert rejection.status_code == 409
    assert result["approval"].status_code == 200
    assert result["approval"].json()["status"] == "approved"


def test_reject_cannot_overwrite_concurrently_claimed_approval(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    reject_update_started = Event()
    release_reject_update = Event()
    original_set_status = module.SessionStore.set_status

    def delayed_set_status(
        store,
        session_id: str,
        status: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if status == "rejected":
            reject_update_started.set()
            release_reject_update.wait(2)
        return original_set_status(store, session_id, status, **kwargs)

    module.SessionStore.set_status = delayed_set_status
    app = module.create_app(
        model_client=FakeModel(),
        db_path=tmp_path / "sessions.db",
        internal_token="test-token",
        approval_sender=lambda payload, _token: (
            {"status": "valid"} if payload.get("validate_only") else {"status": "activated"}
        ),
    )
    result: dict[str, Any] = {}
    try:
        with TestClient(app) as client:
            session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
            wait_for_session(client, session_id, "ai_rule_pending")
            thread = Thread(
                target=lambda: result.update(
                    rejection=client.post(f"/api/v1/sessions/{session_id}/reject")
                ),
                daemon=True,
            )
            thread.start()
            assert reject_update_started.wait(1)
            approval = client.post(f"/api/v1/sessions/{session_id}/approve")
            release_reject_update.set()
            thread.join(2)
            stored = client.get(f"/api/v1/sessions/{session_id}").json()
    finally:
        release_reject_update.set()
        module.SessionStore.set_status = original_set_status

    assert approval.status_code == 200
    assert result["rejection"].status_code == 409
    assert stored["status"] == "approved"


def test_candidate_must_pass_platform_replay_before_it_can_be_approved(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    validations: list[dict[str, Any]] = []

    def reject_invalid_rule(payload: dict[str, Any], _token: str) -> dict[str, Any]:
        validations.append(payload)
        raise ValueError("AI 生成的规则无法复现你确认的订单行，尚未保存。")

    app = module.create_app(
        model_client=FakeModel(),
        db_path=tmp_path / "sessions.db",
        internal_token="test-shared-token",
        approval_sender=reject_invalid_rule,
    )
    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        stored = wait_for_session(client, session_id, "ai_rule_invalid")
        approval = client.post(f"/api/v1/sessions/{session_id}/approve")

    assert validations[0]["validate_only"] is True
    assert stored["candidate"]["parents"][0]["rows"]
    assert stored["error"] == "AI 生成的规则无法复现你确认的订单行，尚未保存。"
    assert approval.status_code == 409


def test_invalid_rule_is_retried_once_after_admin_correction(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    corrected_rows = [{
        "product": "登山鞋",
        "sales_attr1": "紫色",
        "sales_attr2": "42.5",
        "quantity": 1,
        "remark": "",
    }]

    class RepairModel(FakeModel):
        def recognize(
            self,
            payload: dict[str, Any],
            fingerprint: str,
            feedback: list[str] | None = None,
        ) -> dict[str, Any]:
            result = json.loads(json.dumps(super().recognize(payload, fingerprint, feedback)))
            result["candidate_rule"]["defaults"] = {
                "quantity": 1 if len(self.calls) == 3 else 0,
            }
            return result

    model = RepairModel()
    validations: list[dict[str, Any]] = []

    def validate_rule(payload: dict[str, Any], _token: str) -> dict[str, Any]:
        validations.append(payload)
        if payload["candidate_rule"]["defaults"]["quantity"] != 1:
            raise module.RuleValidationRejected("AI candidate rule is invalid: defaults.quantity")
        return {"status": "valid"}

    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token="test-shared-token",
        approval_sender=validate_rule,
    )
    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_invalid")
        feedback = client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"corrected_rows": corrected_rows},
        )
        stored = wait_for_session(client, session_id, "ai_rule_pending")

    assert feedback.status_code == 200
    assert len(model.calls) == 3
    repair_context = json.loads(model.calls[-1]["feedback"][-1])
    assert repair_context == {
        "corrected_rows": corrected_rows,
        "rule_validation_error": "AI candidate rule is invalid: defaults.quantity",
    }
    assert stored["candidate"]["parents"][0]["rows"] == corrected_rows
    assert stored["candidate"]["candidate_rule"]["defaults"]["quantity"] == 1
    assert len(validations) == 3
    assert len(stored["feedback"]) == 1


def test_admin_correction_can_compile_a_value_independent_text_rule(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    request = raw_request()
    request["source_component"] = "cloud-print-client"
    request["payload"] = {
        "task": {
            "documents": [
                {
                    "contents": [
                        {
                            "data": {
                                "productInfo": (
                                    "【2026户外登山鞋男女徒步鞋防滑防水越野跑鞋】"
                                    "紫色 42.5 1 件"
                                )
                            }
                        }
                    ]
                }
            ]
        }
    }
    corrected_rows = [{
        "product": "【2026户外登山鞋男女徒步鞋防滑防水越野跑鞋】",
        "sales_attr1": "紫色",
        "sales_attr2": "42.5",
        "quantity": 1,
        "remark": "",
    }]
    validations: list[dict[str, Any]] = []

    def validate_rule(payload: dict[str, Any], _token: str) -> dict[str, Any]:
        validations.append(payload)
        first_step = payload["candidate_rule"].get("steps", [{}])[0]
        if first_step.get("include_delimiters") is True:
            return {"status": "valid"}
        raise module.RuleValidationRejected("AI 规则不能复现管理员确认行。")

    model = FakeModel()
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token="test-shared-token",
        approval_sender=validate_rule,
    )

    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=request).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_invalid")
        client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"corrected_rows": corrected_rows},
        )
        stored = wait_for_session(client, session_id, "ai_rule_pending")

    rule = stored["candidate"]["candidate_rule"]
    encoded_rule = json.dumps(rule, ensure_ascii=False)
    assert stored["candidate"]["parents"][0]["rows"] == corrected_rows
    assert rule == {
        "strategy": "text_pipeline_v1",
        "text_path": "task.documents[].contents[].data.productInfo",
        "steps": [
            {
                "op": "extract_between",
                "source": "text",
                "start": "【",
                "end": "】",
                "target": "product",
                "consume": True,
                "include_delimiters": True,
            },
            {"op": "strip_suffix", "target": "text", "literal": " 件"},
            {
                "op": "rsplit",
                "source": "text",
                "delimiter": " ",
                "targets": ["text", "quantity"],
            },
            {"op": "rsplit", "source": "text", "delimiter": " ", "targets": ["sales_attr1", "sales_attr2"]},
            {"op": "to_positive_int", "target": "quantity"},
        ],
        "defaults": {"remark": ""},
    }
    assert all(value not in encoded_rule for value in ("2026户外登山鞋", "紫色", "42.5"))
    assert len(model.calls) == 2
    assert len(validations) == 3


def test_admin_correction_can_compile_text_rule_when_product_follows_attributes(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "text-correction-source-order.db")
    request = raw_request()
    request["payload"] = {
        "task": {
            "documents": [
                {
                    "contents": [
                        {
                            "printXML": (
                                "<layout><text><![CDATA[5.0二代灰黑，38 "
                                "2026网面女鞋男鞋情侣透气跑步鞋休闲赤足时尚运动鞋健身一脚蹬*1"
                                "]]></text></layout>"
                            )
                        }
                    ]
                }
            ]
        }
    }
    request["field_selections"] = {"CN-PRINT-XML": ["print_text"]}
    corrected_rows = [
        {
            "product": "2026网面女鞋男鞋情侣透气跑步鞋休闲赤足时尚运动鞋健身一脚蹬",
            "sales_attr1": "5.0二代灰黑",
            "sales_attr2": "38",
            "quantity": 1,
            "remark": "",
        }
    ]
    expected_rule = {
        "strategy": "text_pipeline_v1",
        "text_path": "task.documents[].contents[].printXML",
        "steps": [
            {
                "op": "split",
                "source": "text",
                "delimiter": "，",
                "targets": ["sales_attr1", "text"],
            },
            {
                "op": "split",
                "source": "text",
                "delimiter": " ",
                "targets": ["sales_attr2", "text"],
            },
            {
                "op": "split",
                "source": "text",
                "delimiter": "*",
                "targets": ["product", "quantity"],
            },
            {"op": "to_positive_int", "target": "quantity"},
        ],
        "defaults": {"remark": ""},
    }
    validations: list[dict[str, Any]] = []

    def validate_rule(payload: dict[str, Any], _token: str) -> dict[str, Any]:
        validations.append(payload)
        if payload["candidate_rule"] == expected_rule:
            return {"status": "valid"}
        raise module.RuleValidationRejected("AI 规则不能复现管理员确认行。")

    model = FakeModel()
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token="test-shared-token",
        approval_sender=validate_rule,
    )

    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=request).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_invalid")
        client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"corrected_rows": corrected_rows},
        )
        stored = wait_for_session(client, session_id, "ai_rule_pending")

    rule = stored["candidate"]["candidate_rule"]
    encoded_rule = json.dumps(rule, ensure_ascii=False)
    assert stored["candidate"]["parents"][0]["rows"] == corrected_rows
    assert rule == expected_rule
    assert all(
        value not in encoded_rule
        for value in (
            "2026网面女鞋男鞋情侣透气跑步鞋休闲赤足时尚运动鞋健身一脚蹬",
            "5.0二代灰黑",
            '"38"',
        )
    )
    assert len(model.calls) == 2
    assert len(validations) == 3


def test_admin_correction_can_compile_a_structured_multi_item_rule(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "structured-correction.db")
    request = package_request()
    items = request["payload"]["task"]["documents"][0]["contents"][0]["data"]["packageItemDetail"]
    items.append(
        {
            "itemName": "秒67",
            "skuFullName": "冰川灰 40",
            "itemNum": 2,
        }
    )
    corrected_rows = [
        {
            "product": "范74",
            "sales_attr1": "5代白金",
            "sales_attr2": "45",
            "quantity": 1,
            "remark": "",
        },
        {
            "product": "秒67",
            "sales_attr1": "冰川灰",
            "sales_attr2": "40",
            "quantity": 2,
            "remark": "",
        },
    ]
    expected_rule = {
        "strategy": "structured_items_v1",
        "items_path": "task.documents[].contents[].data.packageItemDetail[]",
        "fields": {
            "product": "itemName",
            "sales_attr1": "skuFullName",
            "sales_attr2": "skuFullName",
            "quantity": "itemNum",
        },
        "steps": [
            {
                "op": "rsplit",
                "source": "sales_attr1",
                "delimiter": " ",
                "targets": ["sales_attr1", "sales_attr2"],
            }
        ],
        "defaults": {"remark": ""},
    }
    validations: list[dict[str, Any]] = []

    def validate_rule(payload: dict[str, Any], _token: str) -> dict[str, Any]:
        validations.append(payload)
        if payload["candidate_rule"] == expected_rule:
            return {"status": "valid"}
        raise module.RuleValidationRejected("AI 规则不能复现管理员确认行。")

    model = FakeModel()
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token="test-shared-token",
        approval_sender=validate_rule,
    )

    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=request).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_invalid")
        client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"corrected_rows": corrected_rows},
        )
        stored = wait_for_session(client, session_id, "ai_rule_pending")

    assert stored["candidate"]["parents"][0]["rows"] == corrected_rows
    assert stored["candidate"]["candidate_rule"] == expected_rule
    assert len(model.calls) == 2
    assert len(validations) == 3


def test_corrected_text_rule_does_not_capture_unconfirmed_dynamic_literals(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    rows = [{
        "product": "【登山鞋】",
        "sales_attr1": "紫色",
        "sales_attr2": "42.5",
        "quantity": 1,
        "remark": "",
    }]

    assert module.compile_corrected_text_rule(
        {"productInfo": "【登山鞋】紫色 SKU-20260730 42.5 1 件"},
        rows,
    ) is None
    assert module.compile_corrected_text_rule(
        {"productInfo": "【登山鞋】紫色 42.5 1 批次A"},
        rows,
    ) is None


def test_non_rule_sender_error_is_not_retried_after_admin_correction(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel()
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token="test-shared-token",
        approval_sender=lambda _payload, _token: (_ for _ in ()).throw(
            ValueError("platform approval response is not an object")
        ),
    )
    corrected_rows = [{
        "product": "登山鞋",
        "sales_attr1": "紫色",
        "sales_attr2": "42.5",
        "quantity": 1,
        "remark": "",
    }]

    with TestClient(app) as client:
        session_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        wait_for_session(client, session_id, "ai_rule_invalid")
        client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"corrected_rows": corrected_rows},
        )
        stored = wait_for_session(client, session_id, "ai_rule_invalid")

    assert len(model.calls) == 2
    assert stored["error"] == "platform approval response is not an object"


def test_approve_calls_platform_with_shared_token_and_reject_is_local(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    approvals: list[tuple[dict[str, Any], str]] = []

    def approve(payload: dict[str, Any], token: str) -> dict[str, Any]:
        approvals.append((payload, token))
        return {"status": "activated", "revision_code": "ai-cold-start-r0001"}

    app = module.create_app(
        model_client=FakeModel(),
        db_path=tmp_path / "sessions.db",
        internal_token="test-shared-token",
        approval_sender=approve,
    )
    with TestClient(app) as client:
        approved_id = client.post("/api/v1/recognize", json=raw_request()).json()["session_id"]
        wait_for_session(client, approved_id, "ai_rule_pending")
        approved = client.post(f"/api/v1/sessions/{approved_id}/approve")
        other = raw_request()
        other["raw_record_id"] = 902
        rejected_id = client.post("/api/v1/recognize", json=other).json()["session_id"]
        rejected = client.post(f"/api/v1/sessions/{rejected_id}/reject")

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approvals[0][1] == "test-shared-token"
    assert approvals[0][0]["session_id"] == approved_id
    assert approvals[0][0]["document_sequence"] == 1
    assert approvals[0][0]["candidate_rule"]["strategy"] == "structured_items_v1"
    assert approvals[0][0]["validate_only"] is True
    assert approvals[1][0]["validate_only"] is False
    assert approvals[2][0]["session_id"] == rejected_id
    assert approvals[2][0]["validate_only"] is True
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert len(approvals) == 3


def test_platform_validation_error_keeps_business_detail(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    response = httpx.Response(
        422,
        json={"detail": "AI 生成的规则无法复现你确认的订单行，尚未保存。"},
        request=httpx.Request("POST", "http://backend/api/v1/internal/ai-recognition/approve"),
    )
    original_post = module.httpx.post
    module.httpx.post = lambda *_args, **_kwargs: response
    try:
        module.default_approval_sender("http://backend")({}, "token")
    except module.RuleValidationRejected as exc:
        assert str(exc) == "AI 生成的规则无法复现你确认的订单行，尚未保存。"
    else:
        raise AssertionError("platform validation failure must keep its business detail")
    finally:
        module.httpx.post = original_post


def test_ollama_client_requests_schema_constrained_non_thinking_json(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.append(payload)
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(candidate(), ensure_ascii=False)}},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = module.OllamaModelClient(
        base_url="http://ollama:11434",
        model="qwen3.5:4b-q4_K_M",
        http_client=http_client,
    )

    result = client.recognize(package_request()["payload"], "sha256:test")

    assert result["parents"][0]["rows"][0]["product"] == "范74"
    request = captured[0]
    assert request["model"] == "qwen3.5:4b-q4_K_M"
    assert request["stream"] is False
    assert request["think"] is False
    assert request["options"]["temperature"] == 0
    assert request["options"]["num_ctx"] == 4096
    assert request["format"]["properties"]["parents"]
    assert "不得包含字段名称" in request["messages"][0]["content"]
    assert "corrected_rows" in request["messages"][0]["content"]
    assert "printXML 是文本字段，只能使用 text_pipeline_v1" in request["messages"][0]["content"]
    assert "编号 商品，,属性，尺码*数量" in request["messages"][0]["content"]
    assert "include_delimiters" in request["messages"][0]["content"]
    assert request["format"]["required"] == ["parents", "candidate_rule"]
    assert request["format"]["properties"]["parents"]["maxItems"] == 1
    row_schema = request["format"]["properties"]["parents"]["items"]["properties"]["rows"]["items"]
    assert row_schema["additionalProperties"] is False
    assert set(row_schema["required"]) == {
        "product",
        "sales_attr1",
        "sales_attr2",
        "quantity",
        "remark",
    }
    assert set(row_schema["properties"]) == {
        "product",
        "sales_attr1",
        "sales_attr2",
        "quantity",
        "remark",
    }
    assert row_schema["properties"]["quantity"]["minimum"] == 1
    assert row_schema["properties"]["quantity"]["maximum"] == 100_000
    assert "字段名称" in row_schema["properties"]["product"]["description"]
    candidate_rule_schema = request["format"]["properties"]["candidate_rule"]
    assert [schema["properties"]["strategy"]["const"] for schema in candidate_rule_schema["oneOf"]] == [
        "structured_items_v1",
        "text_pipeline_v1",
    ]
    assert all(schema["additionalProperties"] is False for schema in candidate_rule_schema["oneOf"])
    structured_steps = candidate_rule_schema["oneOf"][0]["properties"]["steps"]
    assert structured_steps["minItems"] == 1
    assert structured_steps["maxItems"] == 20
    structured_step_schemas = structured_steps["items"]["oneOf"]
    assert all(
        "text" not in schema["properties"].get("source", {}).get("enum", [])
        and "text" not in schema["properties"].get("target", {}).get("enum", [])
        and "text" not in schema["properties"].get("targets", {}).get("items", {}).get("enum", [])
        for schema in structured_step_schemas
    )
    split_schema = next(
        schema for schema in structured_step_schemas if "rsplit" in schema["properties"]["op"].get("enum", [])
    )
    assert split_schema["properties"]["delimiter"] == {"type": "string", "minLength": 1, "maxLength": 64}
    assert split_schema["properties"]["targets"]["uniqueItems"] is True
    extract_schema = next(
        schema for schema in structured_step_schemas if schema["properties"]["op"].get("const") == "extract_between"
    )
    assert extract_schema["properties"]["start"] == {"type": "string", "minLength": 1, "maxLength": 64}
    assert extract_schema["properties"]["end"] == {"type": "string", "minLength": 1, "maxLength": 64}
    trim_schema = next(schema for schema in structured_step_schemas if schema["properties"]["op"].get("const") == "trim")
    assert trim_schema["properties"]["chars"] == {"type": "string", "maxLength": 64}
    strip_schema = next(
        schema
        for schema in structured_step_schemas
        if "strip_suffix" in schema["properties"]["op"].get("enum", [])
    )
    assert strip_schema["properties"]["literal"] == {"type": "string", "minLength": 1, "maxLength": 64}
    text_step_schema = candidate_rule_schema["oneOf"][1]["properties"]["steps"]["items"]["oneOf"]
    extract_schema = next(
        schema for schema in text_step_schema if schema["properties"]["op"].get("const") == "extract_between"
    )
    assert extract_schema["properties"]["include_delimiters"] == {"type": "boolean"}
    assert all(
        schema["properties"]["defaults"]["properties"]["quantity"]["minimum"] == 1
        for schema in candidate_rule_schema["oneOf"]
    )
    rows_schema = request["format"]["properties"]["parents"]["items"]["properties"]["rows"]
    assert rows_schema["minItems"] == 1
    assert rows_schema["maxItems"] == 100


def test_ollama_schema_disallows_structured_rule_without_real_array_source(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "no-structured-source.db")
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(candidate(), ensure_ascii=False)}},
        )

    client = module.OllamaModelClient(
        base_url="http://ollama:11434",
        model="qwen3.5:4b-q4_K_M",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.recognize({"productInfo": "商品 属性 42 1 件"}, "v2:CLOUD-PRODUCT-INFO:sha256:test")

    schema = captured[0]["format"]
    candidates = schema["properties"]["candidate_rule"]["oneOf"]
    assert [candidate["properties"]["strategy"]["const"] for candidate in candidates] == [
        "text_pipeline_v1"
    ]


def test_ollama_structured_rule_schema_only_allows_real_source_paths(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "structured-paths.db")
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(candidate(), ensure_ascii=False)}},
        )

    client = module.OllamaModelClient(
        base_url="http://ollama:11434",
        model="qwen3.5:4b-q4_K_M",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.recognize(package_request()["payload"], "v2:CN-PACKAGE-ITEMS:sha256:test")

    request = captured[0]
    structured = request["format"]["properties"]["candidate_rule"]["oneOf"][0]
    assert structured["properties"]["items_path"] == {
        "const": "task.documents[].contents[].data.packageItemDetail[]"
    }
    field_schemas = structured["properties"]["fields"]["properties"]
    assert all(
        schema["enum"] == ["itemName", "itemNum", "skuFullName"]
        for schema in field_schemas.values()
    )
    assert all(
        "skuFullName.rsplit_first_part" not in schema["enum"]
        for schema in field_schemas.values()
    )
    assert "不得添加派生后缀" in request["messages"][0]["content"]


def test_health_console_and_session_list_are_available(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    app = module.create_app(model_client=FakeModel(), db_path=tmp_path / "sessions.db")

    with TestClient(app) as client:
        health = client.get("/health")
        console = client.get("/console")
        sessions = client.get("/api/v1/sessions")

    assert health.json()["status"] == "ok"
    assert console.status_code == 200
    assert "本地 AI 面单识别会话" in console.text
    assert all(label in console.text for label in ("商品", "销售属性1", "销售属性2", "数量", "备注"))
    assert 'data-field="${field}"' in console.text
    assert 'cell("product", item.product)' in console.text
    assert "添加商品行" in console.text
    assert "删除" in console.text
    assert "rows.length > 1" in console.text
    assert "confirmAndSync" in console.text
    assert "busySessions" in console.text
    assert "正在根据修改结果生成并校验规则" in console.text
    assert "button.disabled = true" in console.text
    assert "onclick=\"post('/api/v1/sessions/${row.session_id}/approve')\"" not in console.text
    assert "ai_rule_invalid" in console.text
    assert "ai_result_invalid" in console.text
    assert "本次传给 AI 的字段" in console.text
    assert "字段路径（原始英文名）" in console.text
    assert "modelInput(row)" in console.text
    assert "row.error" in console.text
    assert "window.alert(error?.detail" not in console.text
    assert "确定性规则失败原因" not in console.text
    assert "JSON.stringify(row.candidate" not in console.text
    assert sessions.json() == []


def test_fingerprint_catalog_exposes_five_named_code_assets(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    app = module.create_app(model_client=FakeModel(), db_path=tmp_path / "sessions.db")

    with TestClient(app) as client:
        response = client.get("/api/v1/fingerprints")

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract_version"] == "waybill_fingerprint_catalog_v1"
    assert [item["code"] for item in payload["fingerprints"]] == [
        "CN-ITEM-INFO",
        "CN-PRINT-XML",
        "CN-CUSTOM-CONTENT",
        "CN-PACKAGE-ITEMS",
        "CLOUD-PRODUCT-INFO",
    ]
    assert payload["fingerprints"][0]["fields"] == [
        {
            "key": "item_info",
            "label": "商品信息",
            "path": "contents[].data.ITEM_INFO",
            "default_selected": True,
        },
        {
            "key": "seller_memo",
            "label": "卖家备注",
            "path": "contents[].data.SELLER_MEMO",
            "default_selected": True,
        },
        {
            "key": "buyer_memo",
            "label": "买家备注",
            "path": "contents[].data.BUYER_MEMO",
            "default_selected": False,
        },
        {
            "key": "item_total_count",
            "label": "商品总数量",
            "path": "contents[].data.ITEM_TOTAL_COUNT",
            "default_selected": True,
        },
    ]


def test_ai_image_copies_shared_fingerprint_contract() -> None:
    dockerfile = (REPO_ROOT / "services" / "ai-recognition" / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY services/shared /app/services/shared" in dockerfile


def test_inspect_item_info_returns_configurable_fields_without_logistics_data(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    app = module.create_app(model_client=FakeModel(), db_path=tmp_path / "sessions.db")
    document = {
        "contents": [
            {"encryptedData": "AES:secret", "addData": {"receiver": {"name": "张三"}}},
            {
                "data": {
                    "ITEM_INFO": "范74 5代白金 45",
                    "SELLER_MEMO": "发白色",
                    "BUYER_MEMO": "尽快发货",
                    "ITEM_TOTAL_COUNT": "2",
                    "ORDER_ID": "123456789",
                }
            },
        ]
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/fingerprints/inspect",
            json={"source_component": "cainiao-cnprint", "payload": document},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fingerprint_code"] == "CN-ITEM-INFO"
    assert {item["key"]: item["value"] for item in payload["fields"]} == {
        "item_info": "范74 5代白金 45",
        "seller_memo": "发白色",
        "buyer_memo": "尽快发货",
        "item_total_count": "2",
    }
    assert "张三" not in json.dumps(payload, ensure_ascii=False)
    assert "123456789" not in json.dumps(payload, ensure_ascii=False)


def test_inspect_print_xml_returns_only_human_readable_text(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    app = module.create_app(model_client=FakeModel(), db_path=tmp_path / "sessions.db")
    document = {
        "contents": [
            {
                "printXML": (
                    '<?xml version="1.0"?><layout style="overflow:hidden">'
                    "<text><![CDATA[范33 带木one帆布kw，木村-3M反光，40*1]]></text>"
                    "<text><![CDATA[白色 42]]></text></layout>"
                )
            }
        ]
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/fingerprints/inspect",
            json={"source_component": "cainiao-cnprint", "payload": document},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fingerprint_code"] == "CN-PRINT-XML"
    assert payload["fields"] == [
        {
            "key": "print_text",
            "label": "打印文本",
            "path": "contents[].printXML//text",
            "default_selected": True,
            "value": "范33 带木one帆布kw，木村-3M反光，40*1\n白色 42",
        }
    ]
    assert "<?xml" not in json.dumps(payload, ensure_ascii=False)
    assert "<layout" not in json.dumps(payload, ensure_ascii=False)


def test_inspect_unknown_payload_returns_unsupported_fingerprint(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    app = module.create_app(model_client=FakeModel(), db_path=tmp_path / "sessions.db")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/fingerprints/inspect",
            json={"source_component": "unknown-printer", "payload": {"data": {"value": "x"}}},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "unsupported_fingerprint"
