from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
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


def test_recognize_sanitizes_pii_and_reuses_identical_session(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel()
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        console_base_url="http://127.0.0.1:6183",
    )

    with TestClient(app) as client:
        first = client.post("/api/v1/recognize", json=raw_request())
        second = client.post("/api/v1/recognize", json=raw_request())

    assert first.status_code == 200
    assert first.json()["status"] == "ai_rule_pending"
    assert first.json()["session_id"] == second.json()["session_id"]
    assert first.json()["console_url"].startswith("http://127.0.0.1:6183/console?session=")
    assert len(model.calls) == 1
    sent = json.dumps(model.calls[0]["payload"], ensure_ascii=False)
    assert "张三" not in sent
    assert "13800138000" not in sent
    assert "福建省泉州市" not in sent
    assert "SF123456789012" not in sent
    assert "范74" in sent


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

    assert response.status_code == 200
    assert response.json()["status"] == "ai_parse_failed"
    assert response.json()["error"]
    assert response.json()["candidate"] is None


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

    assert response.json()["status"] == "ai_rule_pending"
    rule = response.json()["candidate"]["candidate_rule"]
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
        response = client.post(
            f"/api/v1/sessions/{session_id}/feedback",
            json={"message": "销售属性1应该只保留5代白金"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ai_rule_pending"
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
    candidate_rule_schema = request["format"]["properties"]["candidate_rule"]
    assert candidate_rule_schema["properties"]["strategy"]["enum"] == [
        "structured_items_v1",
        "text_pipeline_v1",
    ]
    assert candidate_rule_schema["additionalProperties"] is False
    sent_schema = json.dumps(request["format"], sort_keys=True)
    assert "maxItems" not in sent_schema
    assert "minItems" not in sent_schema
    assert "maxLength" not in sent_schema
    assert "minLength" not in sent_schema


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
    assert sessions.json() == []
