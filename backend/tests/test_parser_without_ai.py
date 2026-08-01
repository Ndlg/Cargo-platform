from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = REPO_ROOT / "services" / "waybill-parser"
SERVICE_MAIN = SERVICE_ROOT / "service_app" / "main.py"


def load_parser_module():
    service_root = str(SERVICE_ROOT)
    if service_root not in sys.path:
        sys.path.insert(0, service_root)
    spec = importlib.util.spec_from_file_location("waybill_parser_without_ai", SERVICE_MAIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def empty_declarative_pack() -> dict:
    return {
        "contract_version": "recognition_rule_pack_v1",
        "pack": {
            "code": "adaptive-recognition-main",
            "name": "自适应识别规则包",
            "version": "1.0.0",
        },
        "parser_policy": {
            "requires_active_rule_pack": True,
            "order_row_parser": "declarative_v1",
            "fingerprint_strategy": "business_shape_v2",
            "format_profiles": [
                {
                    "fingerprint": f"v2:CN-PACKAGE-ITEMS:sha256:{'0' * 64}",
                    "strategy": "structured_items_v1",
                    "items_path": "task.documents[].contents[].data.packageItemDetail[]",
                    "fields": {
                        "product": "itemName",
                        "quantity": "itemNum",
                    },
                }
            ],
        },
    }


def test_parser_exposes_fingerprint_catalog_without_an_ai_service() -> None:
    module = load_parser_module()
    response = TestClient(module.app).get("/api/v1/fingerprints")

    assert response.status_code == 200
    assert [item["code"] for item in response.json()["fingerprints"]] == [
        "CN-ITEM-INFO",
        "CN-PRINT-XML",
        "CN-CUSTOM-CONTENT",
        "CN-PACKAGE-ITEMS",
        "CLOUD-PRODUCT-INFO",
    ]

    inspection = TestClient(module.app).post(
        "/api/v1/fingerprints/inspect",
        json={
            "raw_payload": {"contents": [{"data": {"ITEM_INFO": "商品甲 黑色 42 1件"}}]},
            "source_component": "cainiao-cnprint",
        },
    )
    assert inspection.status_code == 200
    assert inspection.json()["fingerprint"]["fingerprint_code"] == "CN-ITEM-INFO"


def test_parser_batch_contract_has_no_ai_switch_or_session_output() -> None:
    module = load_parser_module()
    assert "allow_ai" not in module.BatchParseRequest.model_fields
    assert "ai_field_selections" not in module.RawRecordParseInput.model_fields

    response = TestClient(module.app).post(
        "/api/v1/parse/batch",
        json={
            "task_id": 1,
            "rule_pack": empty_declarative_pack(),
            "raw_records": [
                {
                    "raw_record_id": 10,
                    "task_id": 1,
                    "source_component": "cainiao-cnprint",
                    "source_index": "10",
                    "payload": {
                        "task": {
                            "documents": [
                                {
                                    "contents": [
                                        {"data": {"ITEM_INFO": "陌生商品 黑色 42 1件"}}
                                    ]
                                }
                            ]
                        }
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "format_profile_missing"
    assert "ai_sessions" not in payload
    assert payload["diagnostics"] == [
        {
            "raw_record_id": 10,
            "parent_label": "第1批-第1单",
            "fingerprint": payload["diagnostics"][0]["fingerprint"],
            "reason": "format_profile_missing",
            "document_sequence": 1,
            "parent_sequence": 1,
        }
    ]
