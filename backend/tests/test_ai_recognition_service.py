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
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v1/sessions/{session_id}").json()
        if payload["status"] == status:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"session {session_id} did not reach {status}")


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

    store = module.SessionStore(database)
    session, created = store.reserve(
        request_key="legacy-request",
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
    except ValueError as exc:
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

    result = client.recognize({"task": {"documents": []}}, "sha256:test")

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
    assert "字段名称" in row_schema["properties"]["product"]["description"]
    candidate_rule_schema = request["format"]["properties"]["candidate_rule"]
    assert [schema["properties"]["strategy"]["const"] for schema in candidate_rule_schema["oneOf"]] == [
        "structured_items_v1",
        "text_pipeline_v1",
    ]
    assert all(schema["additionalProperties"] is False for schema in candidate_rule_schema["oneOf"])
    rows_schema = request["format"]["properties"]["parents"]["items"]["properties"]["rows"]
    assert rows_schema["minItems"] == 1
    assert rows_schema["maxItems"] == 100


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
    assert "confirmAndSync" in console.text
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
