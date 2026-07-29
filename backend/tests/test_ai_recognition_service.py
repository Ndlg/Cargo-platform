from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
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
                        "image_match_text": f"{product} 5代白金 45",
                        "source_trace": {
                            "product": "task.documents[0].contents[0].data.productName",
                            "sales_attr1": "task.documents[0].contents[0].data.sku",
                            "sales_attr2": "task.documents[0].contents[0].data.sku",
                            "quantity": "task.documents[0].contents[0].data.quantity",
                        },
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
    assert approvals[0][0]["candidate_rule"]["strategy"] == "structured_items_v1"
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert len(approvals) == 1


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
    assert request["format"]["required"] == ["parents", "candidate_rule"]
    assert request["format"]["properties"]["parents"]["maxItems"] == 1
    row_schema = request["format"]["properties"]["parents"]["items"]["properties"]["rows"]["items"]
    assert row_schema["additionalProperties"] is False
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
    assert "格式指纹" not in console.text
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
