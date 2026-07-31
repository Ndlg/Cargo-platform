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
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "services" / "ai-recognition" / "service_app"
AI_TOKEN = "test-internal-token"
AI_HEADERS = {"X-AI-Recognition-Token": AI_TOKEN}


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
    assert package_spec and package_spec.loader
    package = importlib.util.module_from_spec(package_spec)
    sys.modules[package_name] = package
    package_spec.loader.exec_module(package)

    main_spec = importlib.util.spec_from_file_location(
        f"{package_name}.main",
        PACKAGE_DIR / "main.py",
    )
    assert main_spec and main_spec.loader
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


def evidence_bundle(*, rows: int = 1) -> dict[str, Any]:
    spans: list[dict[str, Any]] = []
    structured_groups: list[list[str]] = []
    for index in range(1, rows + 1):
        group: list[str] = []
        for name, value, token_class, start in (
            ("product", f"范{73 + index}", "text", 0),
            ("attr1", "5代白金", "delimiter_segment", 4),
            ("attr2", str(44 + index), "shoe_size_like_numeric_segment", 9),
            ("quantity", "1", "positive_integer_quantity", 13),
        ):
            span_id = f"{name}-{index}"
            group.append(span_id)
            spans.append(
                {
                    "span_id": span_id,
                    "source_path": f"task.documents[0].contents[0].data.items[{index - 1}].{name}",
                    "original_text": value,
                    "normalized_text": value,
                    "start": start,
                    "end": start + len(value),
                    "token_class": token_class,
                }
            )
        structured_groups.append(group)
    return {
        "contract_version": "waybill_evidence_v1",
        "source_component": "cainiao-cnprint",
        "fingerprint_code": "CN-PACKAGE-ITEMS",
        "selected_fields": ["item_name", "item_quantity"],
        "evidence_sha256": "e" * 64,
        "structural_fingerprint": "v2:CN-PACKAGE-ITEMS:sha256:" + "a" * 64,
        "grammar_signature": "grammar:test",
        "spans": spans,
        "candidate_groups": {
            "structured_list_item": structured_groups,
            "line": [[f"product-{index}"] for index in range(1, rows + 1)],
            "delimiter_separated_segment": [[f"attr1-{index}"] for index in range(1, rows + 1)],
            "positive_integer_quantity": [[f"quantity-{index}"] for index in range(1, rows + 1)],
            "shoe_size_like_numeric_segment": [[f"attr2-{index}"] for index in range(1, rows + 1)],
            "repeated_line_or_array_group": [],
        },
        "excluded_field_counts": {"non_business": 8, "unselected_business": 1},
    }


def span_selection(*, rows: int = 1) -> dict[str, Any]:
    return {
        "rows": [
            {
                "product_span_ids": [f"product-{index}"],
                "sales_attr1_span_ids": [f"attr1-{index}"],
                "sales_attr2_span_ids": [f"attr2-{index}"],
                "quantity_span_id": f"quantity-{index}",
                "remark_span_ids": [],
            }
            for index in range(1, rows + 1)
        ]
    }


def recognize_request(*, rows: int = 1) -> dict[str, Any]:
    return {
        "workspace_id": 1,
        "task_id": 61,
        "raw_record_id": 901,
        "document_sequence": 1,
        "source_component": "cainiao-cnprint",
        "deterministic_failure_reason": "format_profile_missing",
        "evidence": evidence_bundle(rows=rows),
    }


class FakeModel:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or span_selection()
        self.calls: list[dict[str, Any]] = []

    def recognize(self, evidence: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(evidence)
        return self.result


def wait_for_status(
    client: TestClient,
    session_id: str,
    status: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 2
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = client.get(
            f"/api/v1/sessions/{session_id}",
            headers=AI_HEADERS,
        ).json()
        if payload["status"] == status:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"session {session_id} did not reach {status}; last={payload}")


def test_model_schema_exposes_only_span_selection(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    encoded = json.dumps(module.ollama_json_schema(evidence_bundle()))

    assert "candidate_rule" not in encoded
    assert "steps" not in encoded
    assert "source_path" not in encoded
    assert "product_span_ids" in encoded
    assert "quantity_span_id" in encoded


def test_model_schema_limits_rows_to_evidence_instances(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")

    schema = module.ollama_json_schema(
        module.sanitize_evidence(evidence_bundle(rows=3))
    )

    assert schema["properties"]["rows"]["maxItems"] == 3


def test_evidence_given_to_model_has_only_ids_labels_short_values_and_groups(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    sanitized = module.sanitize_evidence(evidence_bundle())
    encoded = json.dumps(sanitized, ensure_ascii=False)

    assert set(sanitized) == {
        "fingerprint_code",
        "selected_fields",
        "evidence_sha256",
        "spans",
        "candidate_groups",
    }
    assert sanitized["fingerprint_code"] == "CN-PACKAGE-ITEMS"
    assert sanitized["selected_fields"] == ["item_name", "item_quantity"]
    assert sanitized["evidence_sha256"] == "e" * 64
    assert set(sanitized["spans"][0]) == {"span_id", "label", "value"}
    assert "source_path" not in encoded
    assert "original_text" not in encoded
    assert "excluded_field_counts" not in encoded


@pytest.mark.parametrize("selected_fields", [[], ["item_name", "item_name"], ["receiver.address"]])
def test_evidence_rejects_invalid_selected_field_snapshot(
    tmp_path: Path,
    selected_fields: list[str],
) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = evidence_bundle()
    evidence["selected_fields"] = selected_fields

    with pytest.raises(ValueError, match="selected fields"):
        module.sanitize_evidence(evidence)


def test_obvious_pii_span_is_removed_before_model_input(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = evidence_bundle()
    evidence["spans"].append(
        {
            "span_id": "memo-phone",
            "source_path": "task.documents[0].contents[0].data.BUYER_MEMO",
            "original_text": "联系电话 13800138000",
            "normalized_text": "联系电话 13800138000",
            "start": 0,
            "end": 18,
            "token_class": "text",
        }
    )
    evidence["candidate_groups"]["line"].append(["memo-phone"])

    sanitized = module.sanitize_evidence(evidence)
    encoded = json.dumps(sanitized, ensure_ascii=False)

    assert "13800138000" not in encoded
    assert "memo-phone" not in encoded


def test_sensitive_paths_and_unlabelled_address_are_removed_without_dropping_product_text(
    tmp_path: Path,
) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = evidence_bundle()
    evidence["spans"].extend(
        [
            {
                "span_id": "receiver-name",
                "source_path": "task.documents[0].receiverName",
                "original_text": "张三",
                "normalized_text": "张三",
                "start": 0,
                "end": 2,
                "token_class": "text",
            },
            {
                "span_id": "unlabelled-address",
                "source_path": "task.documents[0].contents[0].data.note",
                "original_text": "北京市朝阳区建国路88号",
                "normalized_text": "北京市朝阳区建国路88号",
                "start": 0,
                "end": 13,
                "token_class": "text",
            },
            {
                "span_id": "unlabelled-name",
                "source_path": "task.documents[0].contents[0].data.note",
                "original_text": "张三",
                "normalized_text": "张三",
                "start": 0,
                "end": 2,
                "token_class": "text",
            },
            {
                "span_id": "normal-product",
                "source_path": "task.documents[0].contents[0].data.itemName",
                "original_text": "李宁飞电4.0 路跑鞋",
                "normalized_text": "李宁飞电4.0 路跑鞋",
                "start": 0,
                "end": 12,
                "token_class": "text",
            },
            {
                "span_id": "short-product",
                "source_path": "task.documents[0].contents[0].data.itemName",
                "original_text": "李宁",
                "normalized_text": "李宁",
                "start": 0,
                "end": 2,
                "token_class": "text",
            },
        ]
    )
    evidence["candidate_groups"]["line"].extend(
        [
            ["receiver-name"],
            ["unlabelled-address"],
            ["unlabelled-name"],
            ["normal-product"],
            ["short-product"],
        ]
    )

    sanitized = module.sanitize_evidence(evidence)
    span_ids = {span["span_id"] for span in sanitized["spans"]}

    assert "receiver-name" not in span_ids
    assert "unlabelled-address" not in span_ids
    assert "unlabelled-name" not in span_ids
    assert "normal-product" in span_ids
    assert "short-product" in span_ids


def test_unknown_span_id_is_rejected(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = module.sanitize_evidence(evidence_bundle())
    selection = span_selection()
    selection["rows"][0]["product_span_ids"] = ["not-present"]

    assert module.validate_selection(selection, evidence)["status"] == "candidate_invalid"


def test_same_span_cannot_populate_two_fields(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = module.sanitize_evidence(evidence_bundle())
    selection = span_selection()
    selection["rows"][0]["sales_attr1_span_ids"] = ["product-1"]

    result = module.validate_selection(selection, evidence)

    assert result["status"] == "candidate_invalid"
    assert "conflicting" in result["error"]


def test_model_cannot_smuggle_values_or_rules_beside_span_ids(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = module.sanitize_evidence(evidence_bundle())
    selection = span_selection()
    selection["candidate_rule"] = {"strategy": "text_pipeline_v1"}
    selection["rows"][0]["product"] = "模型直接填写的商品"

    result = module.validate_selection(selection, evidence)

    assert result["status"] == "candidate_invalid"


def test_selection_resolves_in_source_order_and_preserves_duplicate_rows(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = module.sanitize_evidence(evidence_bundle(rows=2))
    selection = span_selection(rows=2)
    selection["rows"][0]["product_span_ids"] = ["attr1-1", "product-1"]
    selection["rows"][0]["sales_attr1_span_ids"] = []

    result = module.validate_selection(selection, evidence)

    assert result["status"] == "candidate_valid"
    assert result["rows"][0]["product"] == "范74 5代白金"
    assert [row["quantity"] for row in result["rows"]] == [1, 1]
    assert len(result["rows"]) == 2


def test_selection_rejects_nonpositive_quantity_and_excess_rows(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = module.sanitize_evidence(evidence_bundle())
    evidence["spans"][-1]["value"] = "0"

    assert module.validate_selection(span_selection(), evidence)["status"] == "candidate_invalid"
    assert module.validate_selection(span_selection(rows=2), evidence)["status"] == "candidate_invalid"


def test_selection_rejects_cross_row_span_reuse(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = module.sanitize_evidence(evidence_bundle(rows=2))
    selection = span_selection(rows=2)
    selection["rows"][1] = dict(selection["rows"][0])

    result = module.validate_selection(selection, evidence)

    assert result["status"] == "candidate_invalid"
    assert "across rows" in result["error"]


def test_row_limit_counts_candidate_instances_not_spans_inside_groups(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = evidence_bundle(rows=3)
    evidence["candidate_groups"]["structured_list_item"] = evidence["candidate_groups"][
        "structured_list_item"
    ][:2]
    evidence["candidate_groups"]["repeated_line_or_array_group"] = [
        [span["span_id"] for span in evidence["spans"]]
    ]
    sanitized = module.sanitize_evidence(evidence)

    result = module.validate_selection(span_selection(rows=3), sanitized)

    assert result["status"] == "candidate_invalid"
    assert "exceeds evidence repeat groups" in result["error"]


def test_same_value_rows_with_distinct_spans_are_preserved(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    evidence = evidence_bundle(rows=2)
    values = {span["span_id"]: span["normalized_text"] for span in evidence["spans"]}
    for span in evidence["spans"]:
        if span["span_id"].endswith("-2"):
            span["normalized_text"] = values[span["span_id"].replace("-2", "-1")]
    sanitized = module.sanitize_evidence(evidence)

    result = module.validate_selection(span_selection(rows=2), sanitized)

    assert result["status"] == "candidate_valid"
    assert result["rows"][0] == result["rows"][1]
    assert len(result["rows"]) == 2


def test_recognize_returns_running_before_slow_model_finishes(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")

    class SlowModel(FakeModel):
        def __init__(self) -> None:
            super().__init__()
            self.started = Event()
            self.release = Event()

        def recognize(self, evidence: dict[str, Any]) -> dict[str, Any]:
            self.started.set()
            self.release.wait(2)
            return super().recognize(evidence)

    model = SlowModel()
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
    )
    result: dict[str, Any] = {}
    with TestClient(app) as client:
        thread = Thread(
            target=lambda: result.update(
                response=client.post(
                    "/api/v1/recognize",
                    json=recognize_request(),
                    headers=AI_HEADERS,
                )
            ),
            daemon=True,
        )
        thread.start()
        assert model.started.wait(1)
        try:
            thread.join(0.2)
            assert not thread.is_alive()
            assert result["response"].json()["status"] == "model_running"
        finally:
            model.release.set()
            thread.join(2)
        wait_for_status(client, result["response"].json()["session_id"], "ai_rule_pending")


def test_recognize_reuses_identical_safe_evidence_session(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel()
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
    )

    with TestClient(app) as client:
        first = client.post("/api/v1/recognize", json=recognize_request(), headers=AI_HEADERS)
        second = client.post("/api/v1/recognize", json=recognize_request(), headers=AI_HEADERS)
        stored = wait_for_status(client, first.json()["session_id"], "ai_rule_pending")

    assert first.json()["session_id"] == second.json()["session_id"]
    assert stored["candidate"]["contract_version"] == "ai_span_selection_candidate_v1"
    assert stored["candidate"]["parents"][0]["rows"][0] == {
        "product": "范74",
        "sales_attr1": "5代白金",
        "sales_attr2": "45",
        "quantity": 1,
        "remark": "",
    }
    assert stored["model_input"] == {"sanitized_payload": model.calls[0]}
    assert len(model.calls) == 1


def test_recognize_does_not_reuse_session_after_selected_fields_change(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    app = module.create_app(
        model_client=FakeModel(),
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
    )
    changed = recognize_request()
    changed["evidence"]["selected_fields"] = ["spec_name"]

    with TestClient(app) as client:
        first = client.post("/api/v1/recognize", json=recognize_request(), headers=AI_HEADERS)
        second = client.post("/api/v1/recognize", json=changed, headers=AI_HEADERS)

    assert first.json()["session_id"] != second.json()["session_id"]


def test_recognize_rejects_raw_payload_and_mismatched_evidence_identity(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    app = module.create_app(
        model_client=FakeModel(),
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
    )
    raw = recognize_request()
    raw["payload"] = {"receiverName": "张三"}
    raw.pop("evidence")
    mismatch = recognize_request()
    mismatch["source_component"] = "other"

    with TestClient(app) as client:
        raw_response = client.post("/api/v1/recognize", json=raw, headers=AI_HEADERS)
        mismatch_response = client.post("/api/v1/recognize", json=mismatch, headers=AI_HEADERS)

    assert raw_response.status_code == 422
    assert mismatch_response.status_code == 422


def test_invalid_selection_is_editable_business_result(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel({"rows": [{**span_selection()["rows"][0], "product_span_ids": ["missing"]}]})
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/recognize", json=recognize_request(), headers=AI_HEADERS)
        stored = wait_for_status(client, response.json()["session_id"], "candidate_invalid")

    assert stored["candidate"] is None
    assert stored["model_candidate"]["parents"] == []
    assert "unknown span" in stored["error"]


def test_model_failure_is_ai_unavailable(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")

    class BrokenModel:
        def recognize(self, _evidence: dict[str, Any]) -> dict[str, Any]:
            raise httpx.ConnectError("model offline")

    app = module.create_app(
        model_client=BrokenModel(),
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/recognize", json=recognize_request(), headers=AI_HEADERS)
        stored = wait_for_status(client, response.json()["session_id"], "ai_unavailable")

    assert stored["candidate"] is None
    assert "model offline" in stored["error"]


def test_model_failure_can_use_admin_rows_without_fake_model_candidate(
    tmp_path: Path,
) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    approvals: list[dict[str, Any]] = []

    class BrokenModel:
        calls = 0

        def recognize(self, _evidence: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            raise httpx.ConnectError("model offline")

    model = BrokenModel()

    def sender(payload: dict[str, Any], _token: str) -> dict[str, Any]:
        approvals.append(payload)
        return {
            "status": "approved",
            "compiler_result": {"status": "compiled"},
        }

    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
        approval_sender=sender,
    )
    administrator_rows = [
        {
            "product": "管理员确认商品",
            "sales_attr1": "灰蓝",
            "sales_attr2": "39",
            "quantity": 1,
            "remark": "",
        }
    ]
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/recognize",
            json=recognize_request(),
            headers=AI_HEADERS,
        ).json()
        failed = wait_for_status(client, created["session_id"], "ai_unavailable")
        feedback = client.post(
            f"/api/v1/sessions/{created['session_id']}/feedback",
            json={"corrected_rows": administrator_rows},
            headers=AI_HEADERS,
        )
        pending = wait_for_status(client, created["session_id"], "ai_rule_pending")
        approved = client.post(
            f"/api/v1/sessions/{created['session_id']}/approve",
            json={"approval_claim": "model-failure-approval-claim"},
            headers=AI_HEADERS,
        )

    assert failed["model_candidate"] is None
    assert feedback.status_code == 200
    assert pending["model_candidate"] is None
    assert pending["administrator_rows"] == administrator_rows
    assert pending["candidate"]["parents"][0]["rows"] == administrator_rows
    assert model.calls == 1
    assert approved.status_code == 200
    assert approvals[0]["model_candidate"] is None
    assert approvals[0]["administrator_rows"] == administrator_rows


def test_invalid_candidate_admin_feedback_preserves_model_provenance_without_retry(
    tmp_path: Path,
) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    model = FakeModel(
        {
            "rows": [
                {
                    **span_selection()["rows"][0],
                    "product_span_ids": ["missing"],
                }
            ]
        }
    )
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/recognize",
            json=recognize_request(),
            headers=AI_HEADERS,
        ).json()
        original = wait_for_status(client, created["session_id"], "candidate_invalid")
        response = client.post(
            f"/api/v1/sessions/{created['session_id']}/feedback",
            json={
                "corrected_rows": [
                    {
                        "product": "管理员确认商品",
                        "sales_attr1": "灰蓝",
                        "sales_attr2": "39",
                        "quantity": 2,
                        "remark": "",
                    }
                ]
            },
            headers=AI_HEADERS,
        )
        stored = wait_for_status(client, created["session_id"], "ai_rule_pending")

    assert response.status_code == 200
    assert len(model.calls) == 1
    assert stored["model_calls"] == 1
    assert stored["model_candidate"] == original["model_candidate"]
    assert stored["model_candidate"]["parents"] == []
    assert stored["administrator_rows"] == [
        {
            "product": "管理员确认商品",
            "sales_attr1": "灰蓝",
            "sales_attr2": "39",
            "quantity": 2,
            "remark": "",
        }
    ]
    assert stored["candidate"]["parents"][0]["rows"][0]["product"] == "管理员确认商品"
    assert "span_selection" not in stored["candidate"]


def test_approval_sends_only_resolved_candidate_not_model_rule(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    approvals: list[tuple[dict[str, Any], str]] = []

    def sender(payload: dict[str, Any], token: str) -> dict[str, Any]:
        approvals.append((payload, token))
        return {"status": "approved"}

    app = module.create_app(
        model_client=FakeModel(),
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
        approval_sender=sender,
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/recognize",
            json=recognize_request(),
            headers=AI_HEADERS,
        ).json()
        assert created["fingerprint_code"] == "CN-PACKAGE-ITEMS"
        wait_for_status(client, created["session_id"], "ai_rule_pending")
        approved = client.post(
            f"/api/v1/sessions/{created['session_id']}/approve",
            json={"approval_claim": "opaque-approval-claim-1"},
            headers=AI_HEADERS,
        )

    assert approved.status_code == 200
    payload, token = approvals[0]
    assert token == AI_TOKEN
    assert "candidate_rule" not in payload
    assert "rule_evidence" not in payload
    assert payload["fingerprint_code"] == "CN-PACKAGE-ITEMS"
    assert payload["candidate_output"]["parents"][0]["rows"][0]["product"] == "范74"
    assert payload["model_candidate"]["parents"] == payload["candidate_output"]["parents"]
    assert "span_selection" in payload["model_candidate"]
    assert payload["administrator_rows"] == payload["candidate_output"]["parents"][0]["rows"]
    assert payload["approval_claim"] == "opaque-approval-claim-1"
    assert "actor" not in payload
    assert "model_candidate_sha256" not in payload
    assert "administrator_rows_sha256" not in payload
    assert approved.json()["compiler_result"] == {"status": "approved"}


def test_rerun_warning_does_not_move_ai_session_back_to_pending(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")

    def sender(_payload: dict[str, Any], _token: str) -> dict[str, Any]:
        return {
            "status": "approved",
            "reruns": [
                {
                    "task_id": 61,
                    "status": "failed",
                    "error": "parser offline",
                }
            ],
            "warnings": ["采集轮次 61 重算失败：parser offline"],
        }

    app = module.create_app(
        model_client=FakeModel(),
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
        approval_sender=sender,
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/recognize",
            json=recognize_request(),
            headers=AI_HEADERS,
        ).json()
        wait_for_status(client, created["session_id"], "ai_rule_pending")
        approved = client.post(
            f"/api/v1/sessions/{created['session_id']}/approve",
            json={"approval_claim": "opaque-approval-claim-2"},
            headers=AI_HEADERS,
        )
        stored = client.get(
            f"/api/v1/sessions/{created['session_id']}",
            headers=AI_HEADERS,
        ).json()

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert stored["status"] == "approved"
    assert stored["compiler_result"] == stored["platform_response"]
    assert stored["platform_response"]["warnings"] == [
        "采集轮次 61 重算失败：parser offline"
    ]


def test_default_approval_sender_allows_parser_synthesis_time(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"status": "activated"}

    def post(url, *, json, headers, timeout):
        captured.update(
            url=url,
            payload=json,
            headers=headers,
            timeout=timeout,
        )
        return Response()

    monkeypatch.setattr(module.httpx, "post", post)
    sender = module.default_approval_sender("http://backend:8000")
    result = sender({"session_id": "session-1"}, "shared")

    assert result == {"status": "activated"}
    assert captured["timeout"] == 90


def test_ollama_request_contains_only_bounded_evidence_and_span_schema(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        captured["timeout"] = request.extensions["timeout"]
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(span_selection(), ensure_ascii=False)}},
        )

    evidence = module.sanitize_evidence(evidence_bundle())
    client = module.OllamaModelClient(
        base_url="http://ollama",
        model="qwen-test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.recognize(evidence) == span_selection()
    user_payload = json.loads(captured["messages"][1]["content"])
    encoded = json.dumps(captured, ensure_ascii=False)
    assert user_payload == evidence
    assert "candidate_rule" not in encoded
    assert "source_path" not in encoded
    assert "original_text" not in encoded
    assert captured["think"] is False
    assert captured["stream"] is False
    assert captured["options"]["num_predict"] == 1024
    assert captured["timeout"]["read"] == 30.0


def test_ollama_timeout_becomes_editable_ai_failure(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    captured: dict[str, Any] = {}

    class TimeoutClient:
        def post(self, _url: str, *, json: dict[str, Any], timeout: float) -> None:
            captured.update(payload=json, timeout=timeout)
            raise httpx.ReadTimeout("model request timed out")

    model = module.OllamaModelClient(
        base_url="http://ollama",
        model="qwen-test",
        request_timeout_seconds=7,
        max_output_tokens=256,
        http_client=TimeoutClient(),
    )
    app = module.create_app(
        model_client=model,
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/recognize",
            json=recognize_request(),
            headers=AI_HEADERS,
        ).json()
        failed = wait_for_status(client, created["session_id"], "ai_unavailable")

    assert captured["timeout"] == 7
    assert captured["payload"]["options"]["num_predict"] == 256
    assert failed["candidate"] is None
    assert failed["status"] == "ai_unavailable"
    assert failed["error"] == "model request timed out"


def test_create_app_reads_ollama_request_limits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    captured: dict[str, Any] = {}

    class ConfiguredModel:
        model = "qwen-test"

        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(module, "OllamaModelClient", ConfiguredModel)
    monkeypatch.setenv("OLLAMA_REQUEST_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("OLLAMA_MAX_OUTPUT_TOKENS", "384")
    app = module.create_app(
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
    )

    with TestClient(app):
        pass

    assert captured["request_timeout_seconds"] == 9
    assert captured["max_output_tokens"] == 384


def test_existing_session_database_adds_document_sequence_column(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as db:
        db.execute(
            """
            CREATE TABLE recognition_sessions (
                session_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                workspace_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
                raw_record_id INTEGER NOT NULL, source_component TEXT NOT NULL,
                fingerprint TEXT NOT NULL, deterministic_failure_reason TEXT NOT NULL,
                sanitized_payload TEXT NOT NULL, candidate TEXT, feedback TEXT NOT NULL DEFAULT '[]',
                platform_response TEXT, status TEXT NOT NULL, error TEXT,
                model_calls INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
    store = module.SessionStore(database)
    session, _created = store.reserve(
        request_key="new",
        workspace_id=1,
        task_id=61,
        raw_record_id=901,
        document_sequence=3,
        source_component="test",
        fingerprint="v2:test",
        deterministic_failure_reason="missing",
        sanitized_payload=module.sanitize_evidence(evidence_bundle()),
    )

    assert session["document_sequence"] == 3
    assert session["generation"] == 1
    assert session["model_candidate"] is None
    assert session["administrator_rows"] is None
    assert session["compiler_result"] is None


def test_legacy_corrected_session_does_not_invent_model_or_compiler_provenance(
    tmp_path: Path,
) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    database = tmp_path / "legacy-three-stage.db"
    candidate = {
        "parents": [
            {
                "rows": [
                    {
                        "product": "旧候选",
                        "sales_attr1": "",
                        "sales_attr2": "",
                        "quantity": 1,
                        "remark": "",
                    }
                ]
            }
        ]
    }
    corrected_rows = [{**candidate["parents"][0]["rows"][0], "product": "管理员结果"}]
    platform_response = {"status": "approved", "warnings": []}
    with sqlite3.connect(database) as db:
        db.execute(
            """
            CREATE TABLE recognition_sessions (
                session_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                workspace_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
                raw_record_id INTEGER NOT NULL, source_component TEXT NOT NULL,
                fingerprint TEXT NOT NULL, deterministic_failure_reason TEXT NOT NULL,
                sanitized_payload TEXT NOT NULL, candidate TEXT, feedback TEXT NOT NULL DEFAULT '[]',
                platform_response TEXT, status TEXT NOT NULL, error TEXT,
                model_calls INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO recognition_sessions VALUES (
                'legacy', 'legacy-key', 1, 61, 901, 'test', 'v2:test', 'missing',
                '{}', ?, ?, ?, 'approved', NULL, 1, 'old', 'old'
            )
            """,
            (
                json.dumps(candidate),
                json.dumps([json.dumps({"corrected_rows": corrected_rows})]),
                json.dumps(platform_response),
            ),
        )

    session = module.SessionStore(database).get("legacy")

    assert session is not None
    assert session["model_candidate"] is None
    assert session["administrator_rows"] == corrected_rows
    assert session["compiler_result"] is None


def test_legacy_session_migrates_only_provable_model_and_nested_compiler_values(
    tmp_path: Path,
) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    database = tmp_path / "legacy-provable-stages.db"
    candidate = {
        "parents": [
            {
                "rows": [
                    {
                        "product": "旧模型候选",
                        "sales_attr1": "",
                        "sales_attr2": "",
                        "quantity": 1,
                        "remark": "",
                    }
                ]
            }
        ]
    }
    compiler_result = {
        "status": "compiled",
        "fingerprint": "v2:test",
        "grammar_signature": "grammar-a",
        "replay_report": [],
    }
    with sqlite3.connect(database) as db:
        db.execute(
            """
            CREATE TABLE recognition_sessions (
                session_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                workspace_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
                raw_record_id INTEGER NOT NULL, source_component TEXT NOT NULL,
                fingerprint TEXT NOT NULL, deterministic_failure_reason TEXT NOT NULL,
                sanitized_payload TEXT NOT NULL, candidate TEXT, feedback TEXT NOT NULL DEFAULT '[]',
                platform_response TEXT, status TEXT NOT NULL, error TEXT,
                model_calls INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO recognition_sessions VALUES (
                'legacy', 'legacy-key', 1, 61, 901, 'test', 'v2:test', 'missing',
                '{}', ?, '[]', ?, 'approved', NULL, 1, 'old', 'old'
            )
            """,
            (
                json.dumps(candidate),
                json.dumps(
                    {
                        "status": "approved",
                        "compiler_result": compiler_result,
                        "warnings": [],
                    }
                ),
            ),
        )

    session = module.SessionStore(database).get("legacy")

    assert session is not None
    assert session["model_candidate"] == candidate
    assert session["administrator_rows"] is None
    assert session["compiler_result"] == compiler_result


def test_health_is_public_but_all_ai_api_endpoints_require_internal_token(
    tmp_path: Path,
) -> None:
    module = load_ai_service(tmp_path / "import-default.db")
    app = module.create_app(
        model_client=FakeModel(),
        db_path=tmp_path / "sessions.db",
        internal_token=AI_TOKEN,
    )
    with TestClient(app) as client:
        health = client.get("/health")
        protected_requests = [
            ("GET", "/api/v1/fingerprints", None),
            (
                "POST",
                "/api/v1/fingerprints/inspect",
                {"source_component": "test", "payload": {}},
            ),
            ("POST", "/api/v1/recognize", recognize_request()),
            ("GET", "/api/v1/sessions", None),
            ("GET", "/api/v1/sessions/not-found", None),
            (
                "POST",
                "/api/v1/sessions/not-found/feedback",
                {"note": "test"},
            ),
            (
                "POST",
                "/api/v1/sessions/not-found/approve",
                {"approval_claim": "opaque-approval-claim-3"},
            ),
            ("POST", "/api/v1/sessions/not-found/reject", None),
        ]
        denied = [
            client.request(method, path, json=body, headers=headers)
            for method, path, body in protected_requests
            for headers in (
                {},
                {"X-AI-Recognition-Token": "wrong"},
            )
        ]
        catalog = client.get("/api/v1/fingerprints", headers=AI_HEADERS)
        inspection = client.post(
            "/api/v1/fingerprints/inspect",
            json={
                "source_component": "cainiao-cnprint",
                "payload": {"contents": [{"data": {"ITEM_INFO": "范74 灰蓝 39【1件】"}}]},
            },
            headers=AI_HEADERS,
        )
        recognize = client.post(
            "/api/v1/recognize",
            json=recognize_request(),
            headers=AI_HEADERS,
        )
        session = client.get(
            f"/api/v1/sessions/{recognize.json()['session_id']}",
            headers=AI_HEADERS,
        )
        console = client.get("/console")

    assert health.json()["status"] == "ok"
    assert {response.status_code for response in denied} == {401}
    assert len(catalog.json()["fingerprints"]) == 5
    assert inspection.json()["fingerprint_code"] == "CN-ITEM-INFO"
    assert recognize.status_code == 200
    assert session.status_code == 200
    assert console.status_code == 404


def test_ai_service_has_no_rule_compiler_or_model_rule_contract(tmp_path: Path) -> None:
    module = load_ai_service(tmp_path / "import-default.db")

    assert not hasattr(module, "compile_corrected_text_rule")
    assert not hasattr(module, "compile_corrected_structured_rule")
    assert not hasattr(module, "normalize_candidate_rule")
    assert not hasattr(module, "AiCandidate")


def test_ai_image_copies_only_shared_fingerprint_contract() -> None:
    dockerfile = (REPO_ROOT / "services" / "ai-recognition" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "COPY services/shared /app/services/shared" in dockerfile
    assert "waybill-parser/service_app" not in dockerfile
