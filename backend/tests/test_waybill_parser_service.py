import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_MAIN = REPO_ROOT / "services" / "waybill-parser" / "service_app" / "main.py"
SERVICE_ROOT = REPO_ROOT / "services" / "waybill-parser"


def load_parser_service_app():
    service_root = str(SERVICE_ROOT)
    if service_root not in sys.path:
        sys.path.insert(0, service_root)
    spec = importlib.util.spec_from_file_location("waybill_parser_service_main", SERVICE_MAIN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.app


def valid_rule_pack_payload() -> dict:
    return {
        "contract_version": "recognition_rule_pack_v1",
        "pack": {"code": "test-shoes", "name": "测试鞋类规则包", "version": "1.0.0"},
        "parser_policy": {"requires_active_rule_pack": True, "order_row_parser": "shoe_waybill_v1"},
    }


def rule_pack_without_parser_payload() -> dict:
    return {
        "contract_version": "recognition_rule_pack_v1",
        "pack": {"code": "metadata-only", "name": "只有元信息的规则包", "version": "1.0.0"},
        "parser_policy": {"requires_active_rule_pack": True},
    }


def test_waybill_parser_service_validates_rule_pack() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/rule-packs/validate", json={"rule_pack": valid_rule_pack_payload()})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "valid"
    assert body["contract_version"] == "recognition_rule_pack_v1"
    assert body["pack"]["code"] == "test-shoes"
    assert body["errors"] == []


def test_waybill_parser_service_requires_explicit_order_row_parser() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/rule-packs/validate",
            json={"rule_pack": rule_pack_without_parser_payload()},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "invalid"
    assert "parser_policy.order_row_parser" in body["errors"]


def test_waybill_parser_service_rejects_invalid_rule_pack() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/rule-packs/validate", json={"rule_pack": {"pack": {"code": ""}}})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "invalid"
    assert "contract_version" in body["errors"]
    assert "pack.code" in body["errors"]
    assert "pack.name" in body["errors"]
    assert "pack.version" in body["errors"]


def test_waybill_parser_service_explains_rule_pack_without_business_db() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/rule-packs/explain", json={"rule_pack": valid_rule_pack_payload()})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "valid"
    assert body["pack"]["name"] == "测试鞋类规则包"
    assert "requires active rule pack" in " ".join(body["capabilities"])
    assert "shoe waybill order-row parser" in " ".join(body["capabilities"])
    assert body["business_db_access"] is False


def test_waybill_parser_service_refuses_hidden_default_parser_when_pack_has_no_parser() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/preview",
            json={
                "task_id": 19,
                "rule_pack": rule_pack_without_parser_payload(),
                "waybill_samples": [
                    {
                        "raw_record_id": 7132,
                        "task_id": 19,
                        "parent_sequence": 1,
                        "source_component": "cainiao-cnprint",
                        "source_index": "7132",
                        "sample_text": "范33 带木one帆布kw，木村-3M反光，42.5*1",
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rule_pack_invalid"
    assert "parser_policy.order_row_parser" in body["errors"]
    assert body["summary"]["draft_count"] == 0
    assert body["rows"] == []


def test_waybill_parser_service_preview_is_read_only_and_returns_rows() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/preview",
            json={
                "task_id": 19,
                "rule_pack": valid_rule_pack_payload(),
                "waybill_samples": [
                    {
                        "raw_record_id": 7132,
                        "task_id": 19,
                        "parent_sequence": 1,
                        "source_component": "cainiao-cnprint",
                        "source_index": "7132",
                        "sample_text": "范33 带木one帆布kw，木村-3M反光，42.5*1",
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["preview"] is True
    assert body["mutates_platform_data"] is False
    assert body["summary"]["draft_count"] == 1
    assert body["rows"][0]["product"] == "范33 带木one帆布kw"
    assert body["rows"][0]["sales_attr1"] == "木村-3M反光"
    assert body["rows"][0]["sales_attr2"] == "42.5"


def test_waybill_parser_service_preview_requires_rule_pack() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/preview",
            json={
                "task_id": 19,
                "waybill_samples": [
                    {
                        "raw_record_id": 7132,
                        "task_id": 19,
                        "parent_sequence": 1,
                        "sample_text": "范33 带木one帆布kw，木村-3M反光，42.5*1",
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rule_pack_missing"
    assert body["rule_pack_required"] is True
    assert body["rows"] == []


def test_waybill_parser_service_batch_parse_contract() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 18,
                "rule_pack": {
                    "contract_version": "recognition_rule_pack_v1",
                    "pack": {"code": "test-shoes", "name": "测试鞋类规则包", "version": "1.0.0"},
                    "parser_policy": {"requires_active_rule_pack": True, "order_row_parser": "shoe_waybill_v1"},
                },
                "standard_details": [
                    {
                        "standard_detail_id": 734,
                        "parent_sequence": 48,
                        "field_values": {
                            "capture_task_id": 18,
                            "raw_record_id": 148,
                            "source_component": "cainiao-cnprint",
                            "source_index": "7118",
                            "product_short_text": "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代 4.0黑白灰;42 【1件】",
                        },
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "order_row_drafts_v1"
    assert body["task_id"] == 18
    assert body["summary"] == {
        "parent_waybill_count": 1,
        "child_waybill_count": 1,
        "draft_count": 1,
        "needs_review_count": 0,
        "special_count": 0,
    }
    assert body["rows"][0]["product"] == "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代"
    assert body["rows"][0]["sales_attr1"] == "4.0黑白灰"
    assert body["rows"][0]["sales_attr2"] == "42"
    assert body["rows"][0]["quantity"] == 1


def test_waybill_parser_service_splits_repeated_quantity_items_without_brackets() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 18,
                "rule_pack": {
                    "contract_version": "recognition_rule_pack_v1",
                    "pack": {"code": "test-shoes", "name": "测试鞋类规则包", "version": "1.0.0"},
                    "parser_policy": {"requires_active_rule_pack": True, "order_row_parser": "shoe_waybill_v1"},
                },
                "standard_details": [
                    {
                        "standard_detail_id": 780,
                        "parent_sequence": 26,
                        "field_values": {
                            "capture_task_id": 18,
                            "raw_record_id": 180,
                            "source_component": "cloud-print-client",
                            "source_index": "7180",
                            "product_short_text": (
                                "5.0范51，5.0二代全黑，44.5*1 "
                                "5.0范51，5.0二代白紫，44.5*1 "
                                "5.0范51，5.0二代黑，44.5*1"
                            ),
                        },
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["parent_waybill_count"] == 1
    assert body["summary"]["child_waybill_count"] == 3
    assert [row["product"] for row in body["rows"]] == ["5.0范51", "5.0范51", "5.0范51"]
    assert [row["sales_attr1"] for row in body["rows"]] == ["5.0二代全黑", "5.0二代白紫", "5.0二代黑"]
    assert [row["sales_attr2"] for row in body["rows"]] == ["44.5", "44.5", "44.5"]
    assert [row["quantity"] for row in body["rows"]] == [1, 1, 1]


def test_waybill_parser_service_accepts_expanded_waybill_samples() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 19,
                "rule_pack": {
                    "contract_version": "recognition_rule_pack_v1",
                    "pack": {"code": "test-shoes", "name": "测试鞋类规则包", "version": "1.0.0"},
                    "parser_policy": {"requires_active_rule_pack": True, "order_row_parser": "shoe_waybill_v1"},
                },
                "waybill_samples": [
                    {
                        "raw_record_id": 7118,
                        "task_id": 19,
                        "parent_sequence": 1,
                        "document_id": "DOC-1",
                        "document_sequence": 1,
                        "source_component": "cainiao-cnprint",
                        "source_index": "7134",
                        "sample_text": "5.0范48，,5.0二代白黑红，39*1",
                    },
                    {
                        "raw_record_id": 7118,
                        "task_id": 19,
                        "parent_sequence": 2,
                        "document_id": "DOC-2",
                        "document_sequence": 2,
                        "source_component": "cainiao-cnprint",
                        "source_index": "7134",
                        "sample_text": "范33 带木one帆布kw，木村-3M反光，42.5*1",
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "parent_waybill_count": 2,
        "child_waybill_count": 2,
        "draft_count": 2,
        "needs_review_count": 0,
        "special_count": 0,
    }
    assert [row["child_label"] for row in body["rows"]] == [
        "第1批-第1单-子1",
        "第1批-第2单-子1",
    ]
    assert [row["source_index"] for row in body["rows"]] == ["7134", "7134"]
    assert [row["product"] for row in body["rows"]] == ["5.0范48", "范33 带木one帆布kw"]
    assert [row["sales_attr1"] for row in body["rows"]] == ["5.0二代白黑红", "木村-3M反光"]
    assert [row["sales_attr2"] for row in body["rows"]] == ["39", "42.5"]
    assert [row["quantity"] for row in body["rows"]] == [1, 1]


def test_waybill_parser_service_raw_records_use_batch_sequence_labels() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 19,
                "rule_pack": {
                    "contract_version": "recognition_rule_pack_v1",
                    "pack": {"code": "test-shoes", "name": "测试鞋类规则包", "version": "1.0.0"},
                    "parser_policy": {"requires_active_rule_pack": True, "order_row_parser": "shoe_waybill_v1"},
                },
                "raw_records": [
                    {
                        "raw_record_id": 7132,
                        "task_id": 19,
                        "source_component": "cainiao-cnprint",
                        "source_index": "7132",
                        "parent_sequence": 8,
                        "payload": {
                            "task": {
                                "documents": [
                                    {
                                        "documentID": "DOC-7132",
                                        "contents": [
                                            {
                                                "data": {
                                                    "productCount": "1件",
                                                    "productInfo": "2025新款网面女鞋男鞋情侣透气跑步鞋 5.0二代灰色 38.5 1件",
                                                    "productShortInfo": "2025新款网面女鞋男鞋情侣透气跑步鞋 5.0二代灰色 38.5 1件",
                                                }
                                            }
                                        ],
                                    }
                                ]
                            }
                        },
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["rows"][0]["child_label"] == "第1批-第8单-子1"


def test_waybill_parser_service_refuses_to_parse_without_rule_pack() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 18,
                "standard_details": [
                    {
                        "standard_detail_id": 734,
                        "parent_sequence": 48,
                        "field_values": {"product_short_text": "【鞋款】黑色 42 1件"},
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rule_pack_missing"
    assert body["rule_pack_required"] is True
    assert body["rows"] == []
