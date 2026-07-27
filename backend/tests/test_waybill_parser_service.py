import json
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


def structured_rule_pack_payload() -> dict:
    payload = valid_rule_pack_payload()
    payload["parser_policy"]["structured_item_sources"] = [
        {
            "name": "package-item-detail",
            "items_path": "task.documents[].contents[].data.packageItemDetail[]",
            "product_fields": ["itemName", "simpleName"],
            "spec_fields": ["specName", "specSimpleName", "skuFullName"],
            "quantity_fields": ["itemNum"],
            "remark_fields": ["remark", "buyerRemark", "sellerRemark"],
        }
    ]
    return payload


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


def test_waybill_parser_service_validates_structured_item_source_contract() -> None:
    app = load_parser_service_app()
    invalid_pack = structured_rule_pack_payload()
    invalid_pack["parser_policy"]["structured_item_sources"][0]["product_fields"] = "itemName"

    with TestClient(app) as client:
        valid_response = client.post(
            "/api/v1/rule-packs/validate",
            json={"rule_pack": structured_rule_pack_payload()},
        )
        invalid_response = client.post(
            "/api/v1/rule-packs/validate",
            json={"rule_pack": invalid_pack},
        )

    assert valid_response.json()["status"] == "valid"
    assert invalid_response.json()["status"] == "invalid"
    assert (
        "parser_policy.structured_item_sources[0].product_fields"
        in invalid_response.json()["errors"]
    )


def test_waybill_parser_service_parses_rule_pack_structured_items_without_text_duplicates() -> None:
    app = load_parser_service_app()
    payload = {
        "task": {
            "documents": [
                {
                    "documentID": "YT7632498146506",
                    "contents": [
                        {"data": None, "encryptedData": "AES:carrier-data"},
                        {
                            "data": {
                                "packageItemDetail": [
                                    {
                                        "itemName": "秒21 vap2025",
                                        "simpleName": "秒21 vap2025",
                                        "specName": "二代全白 39 ",
                                        "specSimpleName": "二代全白 39 ",
                                        "skuFullName": "二代全白 39",
                                        "itemNum": 1,
                                    },
                                    {
                                        "itemName": "范33 带木one帆布kw",
                                        "specName": "木村-3M反光 42.5",
                                        "itemNum": 2,
                                    },
                                ],
                                "productShortInfo": "这段旧文本不能再次生成订单行",
                            }
                        },
                    ],
                }
            ]
        }
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 42,
                "rule_pack": structured_rule_pack_payload(),
                "raw_records": [
                    {
                        "raw_record_id": 1149,
                        "task_id": 42,
                        "parent_sequence": 1,
                        "source_component": "cainiao-cnprint",
                        "source_index": "2639",
                        "payload": payload,
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["parent_waybill_count"] == 1
    assert body["summary"]["child_waybill_count"] == 2
    assert [
        (row["product"], row["sales_attr1"], row["sales_attr2"], row["quantity"])
        for row in body["rows"]
    ] == [
        ("秒21 vap2025", "二代全白", "39", 1),
        ("范33 带木one帆布kw", "木村-3M反光", "42.5", 2),
    ]
    assert body["rows"][1]["source_trace"] == {
        "items_path": "task.documents[].contents[].data.packageItemDetail[]",
        "item_path": "task.documents[0].contents[1].data.packageItemDetail[1]",
        "item_index": 1,
    }


def test_waybill_parser_service_keeps_each_raw_document_as_one_parent_waybill() -> None:
    app = load_parser_service_app()
    documents = [
        {
            "documentID": "DOC-1",
            "contents": [
                {
                    "data": {
                        "packageItemDetail": [
                            {"itemName": "秒21 vap2025", "specName": "二代全白 39", "itemNum": 1}
                        ]
                    }
                }
            ],
        },
        {
            "documentID": "DOC-2",
            "contents": [
                {
                    "data": {
                        "packageItemDetail": [
                            {"itemName": "范33 带木one帆布kw", "specName": "木村-3M反光 42.5", "itemNum": 1}
                        ]
                    }
                }
            ],
        },
    ]

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 42,
                "rule_pack": structured_rule_pack_payload(),
                "raw_records": [
                    {
                        "raw_record_id": 1149,
                        "task_id": 42,
                        "parent_sequence": 1,
                        "source_component": "cainiao-cnprint",
                        "source_index": "2639",
                        "payload": {"task": {"documents": documents}},
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["parent_waybill_count"] == 2
    assert body["summary"]["child_waybill_count"] == 2
    assert [parent["parent_label"] for parent in body["parents"]] == ["第1批-第1单", "第1批-第2单"]
    assert [row["product"] for row in body["rows"]] == ["秒21 vap2025", "范33 带木one帆布kw"]


def test_current_shoe_rule_pack_declares_structured_item_source() -> None:
    rule_pack = json.loads((REPO_ROOT / "rule-packs" / "current-user-shoes.v1.json").read_text(encoding="utf-8"))

    assert rule_pack["pack"]["version"] == "1.1.0"
    source = rule_pack["parser_policy"]["structured_item_sources"][0]
    assert source["items_path"] == "task.documents[].contents[].data.packageItemDetail[]"


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


def test_waybill_parser_service_reports_policy_fields_that_are_not_applied() -> None:
    app = load_parser_service_app()
    rule_pack = structured_rule_pack_payload()
    rule_pack["parser_policy"]["quantity"] = {"default": 1}
    rule_pack["parser_policy"]["multi_item"] = {
        "split_parent_waybill": True,
        "preserve_parent_text": True,
        "pair_product_lines_with_labeled_attr_lines": True,
        "output_item_rows": True,
    }
    rule_pack["parser_policy"]["special_text_keywords"] = [
        {"keyword": "特殊单", "status": "special", "reason": "special_waybill"}
    ]

    with TestClient(app) as client:
        validation = client.post("/api/v1/rule-packs/validate", json={"rule_pack": rule_pack}).json()
        explanation = client.post("/api/v1/rule-packs/explain", json={"rule_pack": rule_pack}).json()

    assert validation["warnings"] == [
        {"code": "policy_field_not_applied", "field": "parser_policy.quantity"},
    ]
    assert explanation["policy_usage"] == {
        "selected": ["order_row_parser"],
        "applied": [
            "multi_item",
            "requires_active_rule_pack",
            "special_text_keywords",
            "structured_item_sources",
        ],
        "configured_but_not_applied": [
            "quantity",
        ],
    }
    assert explanation["warnings"] == validation["warnings"]
    assert "special waybill policy" not in explanation["capabilities"]
    assert "special waybill keyword rules" in explanation["capabilities"]
    assert "quantity normalization" not in explanation["capabilities"]


def test_waybill_parser_service_rejects_disabled_required_multi_item_contract() -> None:
    app = load_parser_service_app()
    rule_pack = structured_rule_pack_payload()
    rule_pack["parser_policy"]["requires_active_rule_pack"] = False
    rule_pack["parser_policy"]["multi_item"] = {
        "split_parent_waybill": True,
        "preserve_parent_text": True,
        "pair_product_lines_with_labeled_attr_lines": True,
        "output_item_rows": False,
    }

    with TestClient(app) as client:
        body = client.post("/api/v1/rule-packs/validate", json={"rule_pack": rule_pack}).json()

    assert body["status"] == "invalid"
    assert body["errors"] == [
        "parser_policy.requires_active_rule_pack",
        "parser_policy.multi_item.output_item_rows",
    ]


def test_waybill_parser_service_applies_and_validates_special_keyword_rules() -> None:
    app = load_parser_service_app()
    rule_pack = structured_rule_pack_payload()
    rule_pack["parser_policy"]["special_text_keywords"] = [
        {
            "keyword": "线下补单",
            "status": "special",
            "reason": "offline_special",
        }
    ]
    sample = {
        "raw_record_id": 1,
        "task_id": 1,
        "parent_sequence": 1,
        "sample_text": "线下补单 黑色 40",
    }
    invalid_pack = valid_rule_pack_payload()
    invalid_pack["parser_policy"]["special_text_keywords"] = [{"keyword": ""}]

    with TestClient(app) as client:
        parsed = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 1,
                "rule_pack": rule_pack,
                "standard_details": [
                    {
                        "standard_detail_id": 1,
                        "parent_sequence": 2,
                        "field_values": {"product_short_text": "线下补单 黑色 40"},
                    }
                ],
                "waybill_samples": [sample],
                "raw_records": [
                    {
                        "raw_record_id": 2,
                        "task_id": 1,
                        "parent_sequence": 3,
                        "payload": {
                            "task": {
                                "documents": [
                                    {
                                        "contents": [
                                            {
                                                "data": {
                                                    "packageItemDetail": [
                                                        {
                                                            "itemName": "线下补单",
                                                            "specName": "黑色 40",
                                                            "itemNum": 1,
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        },
                    }
                ],
            },
        ).json()
        without_rule = client.post(
            "/api/v1/parse/batch",
            json={"task_id": 1, "rule_pack": valid_rule_pack_payload(), "waybill_samples": [sample]},
        ).json()
        invalid = client.post(
            "/api/v1/rule-packs/validate",
            json={"rule_pack": invalid_pack},
        ).json()

    assert [(row["status"], row["review_reason"]) for row in parsed["rows"]] == [
        ("special", "offline_special"),
        ("special", "offline_special"),
        ("special", "offline_special"),
    ]
    assert without_rule["rows"][0]["status"] != "special"
    assert invalid["status"] == "invalid"
    assert "parser_policy.special_text_keywords[0].keyword" in invalid["errors"]


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


def test_waybill_parser_service_parses_space_separated_labeled_attrs() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 33,
                "rule_pack": valid_rule_pack_payload(),
                "standard_details": [
                    {
                        "standard_detail_id": 2001,
                        "parent_sequence": 20,
                        "field_values": {
                            "capture_task_id": 33,
                            "raw_record_id": 2001,
                            "source_component": "cainiao-cnprint",
                            "source_index": "2001",
                            "product_short_text": "颜色分类:4.0黑白灰 鞋码:42，,*1",
                        },
                    },
                    {
                        "standard_detail_id": 2002,
                        "parent_sequence": 21,
                        "field_values": {
                            "capture_task_id": 33,
                            "raw_record_id": 2002,
                            "source_component": "cainiao-cnprint",
                            "source_index": "2002",
                            "product_short_text": "颜色分类:5.0二代灰黑;鞋码:42.5，,*1",
                        },
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["child_waybill_count"] == 2
    rows = body["rows"]
    assert rows[0]["product"] == ""
    assert rows[0]["sales_attr1"] == "4.0黑白灰"
    assert rows[0]["sales_attr2"] == "42"
    assert rows[0]["quantity"] == 1
    assert rows[0]["image_match_text"] == "4.0黑白灰 42 1"
    assert rows[1]["product"] == ""
    assert rows[1]["sales_attr1"] == "5.0二代灰黑"
    assert rows[1]["sales_attr2"] == "42.5"
    assert rows[1]["quantity"] == 1


def test_waybill_parser_service_parses_bracket_title_product_with_attr_and_quantity_continuation() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 33,
                "rule_pack": valid_rule_pack_payload(),
                "standard_details": [
                    {
                        "standard_detail_id": 2023,
                        "parent_sequence": 23,
                        "field_values": {
                            "capture_task_id": 33,
                            "raw_record_id": 2023,
                            "source_component": "cainiao-cnprint",
                            "source_index": "2023",
                            "product_short_text": "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋全黑,43,【1】；",
                        },
                    },
                    {
                        "standard_detail_id": 2024,
                        "parent_sequence": 24,
                        "field_values": {
                            "capture_task_id": 33,
                            "raw_record_id": 2024,
                            "source_component": "cainiao-cnprint",
                            "source_index": "2024",
                            "product_short_text": "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋灰绿黑,45,【1】；",
                        },
                    },
                    {
                        "standard_detail_id": 2044,
                        "parent_sequence": 44,
                        "field_values": {
                            "capture_task_id": 33,
                            "raw_record_id": 2044,
                            "source_component": "cainiao-cnprint",
                            "source_index": "2044",
                            "product_short_text": "【HK】特2跑步鞋飞速轻轻减震防滑透气运动鞋男鞋女鞋2代联名厚底 黑白蓝\n36.5【1件】",
                        },
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["child_waybill_count"] == 3
    rows = body["rows"]
    assert rows[0]["product"] == "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋"
    assert rows[0]["sales_attr1"] == "全黑"
    assert rows[0]["sales_attr2"] == "43"
    assert rows[0]["quantity"] == 1
    assert rows[0]["status"] == "draft"
    assert rows[1]["product"] == "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋"
    assert rows[1]["sales_attr1"] == "灰绿黑"
    assert rows[1]["sales_attr2"] == "45"
    assert rows[1]["quantity"] == 1
    assert rows[1]["status"] == "draft"
    assert rows[2]["product"] == "【HK】特2跑步鞋飞速轻轻减震防滑透气运动鞋男鞋女鞋2代联名厚底"
    assert rows[2]["sales_attr1"] == "黑白蓝"
    assert rows[2]["sales_attr2"] == "36.5"
    assert rows[2]["quantity"] == 1
    assert rows[2]["status"] == "draft"


def test_waybill_parser_service_parses_semicolon_title_attr_size_quantity_sample() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 34,
                "rule_pack": valid_rule_pack_payload(),
                "waybill_samples": [
                    {
                        "raw_record_id": 812,
                        "task_id": 34,
                        "parent_sequence": 44,
                        "source_component": "cainiao-cnprint",
                        "source_index": "2543",
                        "sample_text": "【HK】特2跑步鞋飞速超轻减震防滑透气运动鞋男鞋女鞋2代联名厚底 黑白蓝;36.5 【1件】",
                        "text_blocks": [
                            {
                                "block_kind": "original",
                                "text": "【HK】特2跑步鞋飞速超轻减震防滑透气运动鞋男鞋女鞋2代联名厚底 黑白蓝;36.5 【1件】",
                                "source_path": "task.documents[0].contents[1].data.ITEM_INFO",
                                "order": 0,
                            },
                            {
                                "block_kind": "derived_child",
                                "text": "【HK】特2跑步鞋飞速超轻减震防滑透气运动鞋男鞋女鞋2代联名厚底 黑白蓝",
                                "source_path": "task.documents[0].contents[1].data.ITEM_INFO",
                                "order": 1,
                            },
                            {
                                "block_kind": "derived_child",
                                "text": "36.5 【1件】",
                                "source_path": "task.documents[0].contents[1].data.ITEM_INFO",
                                "order": 2,
                            },
                        ],
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    rows = body["rows"]
    assert body["summary"]["child_waybill_count"] == 1
    assert body["summary"]["needs_review_count"] == 0
    assert rows[0]["product"] == "【HK】特2跑步鞋飞速超轻减震防滑透气运动鞋男鞋女鞋2代联名厚底"
    assert rows[0]["sales_attr1"] == "黑白蓝"
    assert rows[0]["sales_attr2"] == "36.5"
    assert rows[0]["quantity"] == 1


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


def test_waybill_parser_service_raw_records_parse_item_info_documents() -> None:
    item_texts = [
        "2026赤足跑步鞋 5.0黑白蓝;42 【1件】",
        "2026赤足跑步鞋 5.0黑白紫;36 【1件】",
        "2026赤足跑步鞋 5.0灰橙;37.5 【1件】",
        "2026赤足跑步鞋 5.0黑白紫;36.5 【1件】",
        "2026超轻减震跑步鞋 4.0二代黑白;36.5 【1件】",
    ]

    def documents(values: list[str]) -> list[dict]:
        return [
            {
                "documentID": f"DOC-{index}",
                "contents": [
                    {"encryptedData": "AES:carrier-data"},
                    {"data": {"ITEM_INFO": value, "ITEM_TOTAL_COUNT": "1"}},
                ],
            }
            for index, value in enumerate(values, start=1)
        ]

    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 45,
                "rule_pack": structured_rule_pack_payload(),
                "raw_records": [
                    {
                        "raw_record_id": 1234,
                        "task_id": 45,
                        "source_component": "cainiao-cnprint",
                        "source_index": "2656",
                        "payload": {"task": {"documents": documents(item_texts[:4])}},
                    },
                    {
                        "raw_record_id": 1237,
                        "task_id": 45,
                        "source_component": "cainiao-cnprint",
                        "source_index": "2657",
                        "payload": {"task": {"documents": documents(item_texts[4:])}},
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "parent_waybill_count": 5,
        "child_waybill_count": 5,
        "draft_count": 5,
        "needs_review_count": 0,
        "special_count": 0,
    }
    assert [row["quantity"] for row in body["rows"]] == [1, 1, 1, 1, 1]
    assert [row["original_text"] for row in body["rows"]] == item_texts


def test_waybill_parser_service_raw_records_parse_custom_content_documents() -> None:
    item_text = "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋,全黑,42,【1】；"

    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 46,
                "rule_pack": valid_rule_pack_payload(),
                "raw_records": [
                    {
                        "raw_record_id": 1279,
                        "task_id": 46,
                        "source_component": "cainiao-cnprint",
                        "source_index": "2664",
                        "payload": {
                            "task": {
                                "documents": [
                                    {
                                        "documentID": "YT7633039580373",
                                        "contents": [
                                            {"data": None, "encryptedData": "AES:carrier-data"},
                                            {"data": {"customContent": item_text}},
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
    assert body["summary"]["needs_review_count"] == 0
    assert [
        (
            row["product"],
            row["sales_attr1"],
            row["sales_attr2"],
            row["quantity"],
            row["original_text"],
        )
        for row in body["rows"]
    ] == [
        (
            "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋",
            "全黑",
            "42",
            1,
            item_text,
        )
    ]


def test_waybill_parser_service_parses_bracketed_custom_content_comma_attrs() -> None:
    item_texts = [
        "【HK】特2跑步鞋飞速超轻减震防滑透气运动鞋男鞋女鞋2代联名厚底,粉银,38.5,【1】；",
        "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋,全白,42,【1】；",
        "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋,荧光绿,40,【1】；",
        "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋,黑灰白,44.5,【1】；",
    ]

    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 46,
                "rule_pack": valid_rule_pack_payload(),
                "raw_records": [
                    {
                        "raw_record_id": 1279,
                        "task_id": 46,
                        "source_component": "cainiao-cnprint",
                        "source_index": "2664",
                        "payload": {
                            "task": {
                                "documents": [
                                    {
                                        "documentID": f"DOC-{index}",
                                        "contents": [
                                            {"data": None, "encryptedData": "AES:carrier-data"},
                                            {"data": {"customContent": item_text}},
                                        ],
                                    }
                                    for index, item_text in enumerate(item_texts, start=1)
                                ]
                            }
                        },
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert [
        (row["product"], row["sales_attr1"], row["sales_attr2"], row["quantity"])
        for row in body["rows"]
    ] == [
        ("【HK】特2跑步鞋飞速超轻减震防滑透气运动鞋男鞋女鞋2代联名厚底", "粉银", "38.5", 1),
        ("【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋", "全白", "42", 1),
        ("【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋", "荧光绿", "40", 1),
        ("【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋", "黑灰白", "44.5", 1),
    ]


def test_waybill_parser_service_parses_bracketed_item_info_semicolon_attrs() -> None:
    item_texts = [
        "【HK】特2跑步鞋飞速超轻减震防滑透气运动鞋男鞋女鞋2代联名厚底 粉银;38.5 【1件】",
        "【HK】特2跑步鞋飞速超轻减震防滑透气运动鞋男鞋女鞋2代联名厚底 银灰色;37 【1件】",
    ]

    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 46,
                "rule_pack": valid_rule_pack_payload(),
                "raw_records": [
                    {
                        "raw_record_id": 1278,
                        "task_id": 46,
                        "source_component": "cainiao-cnprint",
                        "source_index": "2663",
                        "payload": {
                            "task": {
                                "documents": [
                                    {
                                        "documentID": f"DOC-{index}",
                                        "contents": [
                                            {"data": None, "encryptedData": "AES:carrier-data"},
                                            {"data": {"ITEM_INFO": item_text, "ITEM_TOTAL_COUNT": "1件"}},
                                        ],
                                    }
                                    for index, item_text in enumerate(item_texts, start=1)
                                ]
                            }
                        },
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert [
        (row["product"], row["sales_attr1"], row["sales_attr2"], row["quantity"])
        for row in body["rows"]
    ] == [
        ("【HK】特2跑步鞋飞速超轻减震防滑透气运动鞋男鞋女鞋2代联名厚底", "粉银", "38.5", 1),
        ("【HK】特2跑步鞋飞速超轻减震防滑透气运动鞋男鞋女鞋2代联名厚底", "银灰色", "37", 1),
    ]


def test_waybill_parser_service_preserves_attr_before_size_label_from_print_xml() -> None:
    item_text = "5.0二代全黑鞋码：40，,*1"

    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 46,
                "rule_pack": valid_rule_pack_payload(),
                "raw_records": [
                    {
                        "raw_record_id": 1291,
                        "task_id": 46,
                        "source_component": "cainiao-cnprint",
                        "source_index": "7392",
                        "parent_sequence": 100,
                        "payload": {
                            "task": {
                                "documents": [
                                    {
                                        "documentID": "DOC-100",
                                        "contents": [
                                            {"data": None, "encryptedData": "AES:carrier-data"},
                                            {"printXML": f"<text><![CDATA[{item_text}]]></text>"},
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
    assert [
        (
            row["product"],
            row["sales_attr1"],
            row["sales_attr2"],
            row["quantity"],
            row["image_match_text"],
            row["original_text"],
        )
        for row in body["rows"]
    ] == [
        (
            "",
            "5.0二代全黑",
            "40",
            1,
            "5.0二代全黑 40 1",
            item_text,
        )
    ]


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
