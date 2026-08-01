from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys
from typing import Any

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = REPO_ROOT / "services" / "waybill-parser"
SERVICE_MAIN = SERVICE_ROOT / "service_app" / "main.py"


def load_parser():
    service_root = str(SERVICE_ROOT)
    if service_root not in sys.path:
        sys.path.insert(0, service_root)
    spec = importlib.util.spec_from_file_location("waybill_parser_declarative_main", SERVICE_MAIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    rules = importlib.import_module("service_app.declarative_rules")
    return module.app, rules


def one_document(product: str = "范74", quantity: object = 2) -> dict[str, Any]:
    return {
        "task": {
            "documents": [
                {
                    "contents": [
                        {
                            "data": {
                                "packageItemDetail": [
                                    {
                                        "itemName": product,
                                        "color": "5代白金",
                                        "size": "45",
                                        "itemNum": quantity,
                                        "remark": "",
                                    },
                                    {
                                        "itemName": "秒45 跑鞋",
                                        "color": "Cloud 6",
                                        "size": "43",
                                        "itemNum": 1,
                                        "remark": "加急",
                                    },
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    }


def structured_profile(rules: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "fingerprint": rules.structural_fingerprint(payload, "cainiao-cnprint"),
        "strategy": "structured_items_v1",
        "items_path": "task.documents[].contents[].data.packageItemDetail[]",
        "fields": {
            "product": "itemName",
            "sales_attr1": "color",
            "sales_attr2": "size",
            "quantity": "itemNum",
            "remark": "remark",
        },
    }


def declarative_pack(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract_version": "recognition_rule_pack_v1",
        "pack": {"code": "adaptive-recognition-main", "name": "自适应识别规则包", "version": "1.0.0"},
        "parser_policy": {
            "requires_active_rule_pack": True,
            "order_row_parser": "declarative_v1",
            "format_profiles": profiles,
        },
    }


def raw_record(raw_record_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "raw_record_id": raw_record_id,
        "task_id": 61,
        "source_component": "cainiao-cnprint",
        "source_index": str(raw_record_id),
        "payload": payload,
    }


def test_completeness_rejects_collapsed_multi_product_fields() -> None:
    _app, rules = load_parser()
    parent_label = "第1批-第1单"
    row = rules.OrderRowDraft(
        raw_record_id=901,
        task_id=61,
        parent_label=parent_label,
        child_label=f"{parent_label}-子1",
        child_index=1,
        child_count=1,
        source_component="cainiao-cnprint",
        source_index="901",
        product="2026赤足跑步鞋 5.0黑白紫 37.5\n2026赤足跑步鞋 5.0黑白紫",
        sales_attr1="5.0黑白紫",
        sales_attr2="36.5 【1件】",
        quantity=1,
        remark="",
        image_match_text="",
        original_text="",
        status="draft",
        review_reason="",
    )
    parent = rules.ParentWaybillDraft(
        raw_record_id=901,
        task_id=61,
        parent_label=parent_label,
        source_component="cainiao-cnprint",
        source_index="901",
        child_count=1,
        rows=[row],
    )

    complete, reasons = rules.check_parent_completeness(parent)

    assert complete is False
    assert "multiple_products_collapsed" in reasons
    assert "quantity_marker_in_sales_attribute" in reasons


def test_structured_profile_splits_documents_and_products_without_deduplication() -> None:
    app, rules = load_parser()
    document = one_document()
    two_documents = {
        "task": {
            "documents": [
                document["task"]["documents"][0],
                document["task"]["documents"][0],
            ]
        }
    }
    pack = declarative_pack([structured_profile(rules, document)])

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 61,
                "raw_records": [raw_record(901, two_documents)],
                "rule_pack": pack,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "parsed"
    assert body["summary"]["parent_waybill_count"] == 2
    assert body["summary"]["child_waybill_count"] == 4
    assert [row["product"] for row in body["rows"]] == [
        "范74",
        "秒45 跑鞋",
        "范74",
        "秒45 跑鞋",
    ]
    assert [row["quantity"] for row in body["rows"]] == [2, 1, 2, 1]
    assert body["rows"][0]["source_trace"]["product"].endswith(".itemName")


def test_same_structure_new_values_reuses_profile() -> None:
    app, rules = load_parser()
    baseline = one_document("范74")
    new_values = one_document("范30/联名")
    pack = declarative_pack([structured_profile(rules, baseline)])

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={"task_id": 61, "raw_records": [raw_record(902, new_values)], "rule_pack": pack},
        )

    assert response.json()["status"] == "parsed"
    assert response.json()["rows"][0]["product"] == "范30/联名"


def test_confirmed_learning_rule_reports_compiled_rule_provenance() -> None:
    app, rules = load_parser()
    document = one_document()
    profile = structured_profile(rules, document)
    profile["grammar_signature"] = rules.build_evidence(
        document,
        "cainiao-cnprint",
        None,
    )["grammar_signature"]
    profile["provenance"] = {
        "source": "confirmed_learning_rule",
        "learning_record_id": "sample-provenance",
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 61,
                "raw_records": [raw_record(9021, document)],
                "rule_pack": declarative_pack([profile]),
            },
        )

    expected = {
        "source": "confirmed_learning_rule",
        "learning_record_id": "sample-provenance",
        "rule_pack_code": "adaptive-recognition-main",
        "rule_pack_version": "1.0.0",
        "fingerprint": profile["fingerprint"],
        "grammar_signature": profile["grammar_signature"],
        "strategy": "structured_items_v1",
    }
    body = response.json()
    assert body["status"] == "parsed"
    assert body["diagnostics"][0]["compiled_rule"] == expected
    assert all(row["source_trace"]["compiled_rule"] == expected for row in body["rows"])


def test_declarative_profile_rejects_untrusted_provenance() -> None:
    app, rules = load_parser()
    document = one_document()
    profile = {
        **structured_profile(rules, document),
        "provenance": {
            "source": "hidden_builtin",
            "learning_record_id": "sample-provenance",
        },
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/rule-packs/validate",
            json={"rule_pack": declarative_pack([profile])},
        )

    assert response.json()["status"] == "invalid"
    assert "parser_policy.format_profiles[0].provenance" in response.json()["errors"]


def test_matching_fingerprint_with_incomplete_row_is_not_accepted() -> None:
    app, rules = load_parser()
    baseline = one_document()
    incomplete = one_document(quantity="")
    pack = declarative_pack([structured_profile(rules, baseline)])

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={"task_id": 61, "raw_records": [raw_record(903, incomplete)], "rule_pack": pack},
        )

    body = response.json()
    assert body["status"] == "format_profile_incomplete"
    assert body["rows"] == []
    assert body["summary"]["needs_review_count"] == 1
    assert body["diagnostics"][0]["reason"] == "missing_quantity"


def test_unknown_fingerprint_is_explicit_and_preserves_parent_coverage() -> None:
    app, rules = load_parser()
    baseline = one_document()
    changed_structure = one_document()
    changed_structure["task"]["documents"][0]["contents"][0]["data"]["newField"] = "new"
    pack = declarative_pack([structured_profile(rules, baseline)])

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 61,
                "raw_records": [raw_record(904, changed_structure)],
                "rule_pack": pack,
            },
        )

    body = response.json()
    assert body["status"] == "format_profile_missing"
    assert body["summary"]["parent_waybill_count"] == 1
    assert body["summary"]["child_waybill_count"] == 0
    assert body["diagnostics"][0]["reason"] == "format_profile_missing"
    assert body["diagnostics"][0]["fingerprint"].startswith("sha256:")


def test_text_pipeline_uses_literal_operations_and_multiple_item_lines() -> None:
    app, rules = load_parser()
    payload = {
        "task": {
            "documents": [
                {
                    "contents": [
                        {
                            "data": {
                                "customContent": "范74|5代白金|45|2\n秒45|Cloud 6|43|1"
                            }
                        }
                    ]
                }
            ]
        }
    }
    profile = {
        "fingerprint": rules.structural_fingerprint(payload, "cloud-print-client"),
        "strategy": "text_pipeline_v1",
        "text_path": "task.documents[].contents[].data.customContent",
        "field_roles": {"sales_attr2": "shoe_size_like_numeric_segment"},
        "item_split": "\n",
        "steps": [
            {
                "op": "split",
                "source": "text",
                "delimiter": "|",
                "targets": ["product", "sales_attr1", "sales_attr2", "quantity"],
            },
            {"op": "to_positive_int", "target": "quantity"},
        ],
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 61,
                "raw_records": [
                    {
                        **raw_record(905, payload),
                        "source_component": "cloud-print-client",
                    }
                ],
                "rule_pack": declarative_pack([profile]),
            },
        )

    body = response.json()
    assert body["status"] == "parsed"
    assert [(row["product"], row["quantity"]) for row in body["rows"]] == [
        ("范74", 2),
        ("秒45", 1),
    ]


def test_text_pipeline_reads_plain_text_from_print_xml() -> None:
    app, rules = load_parser()
    payload = {
        "task": {
            "documents": [
                {
                    "contents": [
                        {
                            "printXML": (
                                "<layout><text><![CDATA["
                                "范33 带木one帆布kw，木村-3M反光，40*1"
                                "]]></text></layout>"
                            )
                        }
                    ]
                }
            ]
        }
    }
    profile = {
        "fingerprint": rules.structural_fingerprint(payload, "cainiao-cnprint"),
        "strategy": "text_pipeline_v1",
        "text_path": "task.documents[].contents[].printXML",
        "field_roles": {"sales_attr2": "shoe_size_like_numeric_segment"},
        "steps": [
            {"op": "rsplit", "source": "text", "delimiter": "*", "targets": ["text", "quantity"]},
            {"op": "to_positive_int", "target": "quantity"},
            {"op": "rsplit", "source": "text", "delimiter": "，", "targets": ["text", "sales_attr2"]},
            {"op": "split", "source": "text", "delimiter": " ", "targets": ["product", "sales_attr1"]},
        ],
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 61,
                "raw_records": [raw_record(908, payload)],
                "rule_pack": declarative_pack([profile]),
            },
        )

    body = response.json()
    assert body["status"] == "parsed"
    assert [
        (
            row["product"],
            row["sales_attr1"],
            row["sales_attr2"],
            row["quantity"],
        )
        for row in body["rows"]
    ] == [("范33", "带木one帆布kw，木村-3M反光", "40", 1)]


def test_text_pipeline_extract_between_can_preserve_business_delimiters() -> None:
    app, rules = load_parser()

    def payload(product: str, color: str, size: str, quantity: int) -> dict[str, Any]:
        return {
            "task": {
                "documents": [
                    {
                        "contents": [
                            {
                                "data": {
                                    "productInfo": f"【{product}】{color} {size} {quantity} 件",
                                }
                            }
                        ]
                    }
                ]
            }
        }

    baseline = payload("2026 户外登山鞋", "紫色", "42.5", 1)
    profile = {
        "fingerprint": rules.structural_fingerprint(baseline, "cloud-print-client"),
        "strategy": "text_pipeline_v1",
        "text_path": "task.documents[].contents[].data.productInfo",
        "field_roles": {"sales_attr2": "shoe_size_like_numeric_segment"},
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
                "targets": ["sales_attr1", "sales_attr2", "quantity"],
            },
            {"op": "to_positive_int", "target": "quantity"},
        ],
        "defaults": {"remark": ""},
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 61,
                "raw_records": [
                    {
                        **raw_record(909, baseline),
                        "source_component": "cloud-print-client",
                    },
                    {
                        **raw_record(910, payload("夏季透气跑鞋", "二代灰黑", "39", 2)),
                        "source_component": "cloud-print-client",
                    },
                ],
                "rule_pack": declarative_pack([profile]),
            },
        )

    body = response.json()
    assert body["status"] == "parsed"
    assert [
        (
            row["product"],
            row["sales_attr1"],
            row["sales_attr2"],
            row["quantity"],
            row["remark"],
        )
        for row in body["rows"]
    ] == [
        ("【2026 户外登山鞋】", "紫色", "42.5", 1, ""),
        ("【夏季透气跑鞋】", "二代灰黑", "39", 2, ""),
    ]

    with TestClient(app) as client:
        too_many = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 61,
                "raw_records": [
                    {
                        **raw_record(911, payload("夏季透气跑鞋", "二代灰黑", "39", 100_001)),
                        "source_component": "cloud-print-client",
                    }
                ],
                "rule_pack": declarative_pack([profile]),
            },
        )
    assert too_many.json()["status"] == "format_profile_incomplete"
    assert too_many.json()["rows"] == []
    assert too_many.json()["diagnostics"][0]["reason"] == "missing_quantity"

    profile["steps"][0]["include_delimiters"] = "yes"
    with TestClient(app) as client:
        invalid = client.post(
            "/api/v1/rule-packs/validate",
            json={"rule_pack": declarative_pack([profile])},
        )
    assert invalid.json()["status"] == "invalid"
    assert any("include_delimiters" in error for error in invalid.json()["errors"])

    profile["steps"][0]["include_delimiters"] = True
    profile["defaults"]["quantity"] = 100_001
    with TestClient(app) as client:
        invalid_default = client.post(
            "/api/v1/rule-packs/validate",
            json={"rule_pack": declarative_pack([profile])},
        )
    assert invalid_default.json()["status"] == "invalid"
    assert any("defaults.quantity" in error for error in invalid_default.json()["errors"])


def test_text_profiles_execute_across_grammar_and_fail_closed_on_conflict() -> None:
    _app, rules = load_parser()
    synthesizer = importlib.import_module("service_app.rule_synthesizer")

    def item_info(text: str) -> dict[str, Any]:
        return {"contents": [{"data": {"ITEM_INFO": text}}]}

    def row(
        product: str,
        sales_attr1: str,
        sales_attr2: str,
        quantity: int,
    ) -> dict[str, Any]:
        return {
            "product": product,
            "sales_attr1": sales_attr1,
            "sales_attr2": sales_attr2,
            "quantity": quantity,
            "remark": "",
        }

    comma = synthesizer.synthesize_rule(
        payload=item_info("灰黑，38 商品甲*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品甲", "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )["rule"]
    semicolon = synthesizer.synthesize_rule(
        payload=item_info("蓝色;39;商品乙*2"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品乙", "蓝色", "39", 2)],
        gold_samples=[],
        negative_samples=[],
    )["rule"]
    conflicting = synthesizer.synthesize_rule(
        payload=item_info("蓝色，39.5 商品乙*2"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品乙", "39.5", "蓝色", 2)],
        gold_samples=[],
        negative_samples=[],
    )["rule"]

    assert comma["fingerprint"] == semicolon["fingerprint"]
    assert comma["grammar_signature"] != semicolon["grammar_signature"]
    assert comma["grammar_signature"] != conflicting["grammar_signature"]
    assert rules.validate_format_profiles([comma, semicolon, conflicting]) == []

    parent, diagnostic = rules.parse_declarative_payload(
        item_info("绿色，40.5 商品-丙*3"),
        [comma],
        raw_record_id=1,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="1",
        parent_sequence=1,
        fingerprint_strategy="business_shape_v2",
    )
    assert diagnostic["reason"] == ""
    assert [
        (item.product, item.sales_attr1, item.sales_attr2, item.quantity)
        for item in parent.rows
    ] == [("商品-丙", "绿色", "40.5", 3)]

    swapped, diagnostic = rules.parse_declarative_payload(
        item_info("42（标准），黑色 商品丙*3"),
        [comma],
        raw_record_id=5,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="5",
        parent_sequence=5,
        fingerprint_strategy="business_shape_v2",
    )
    assert swapped.rows == []
    assert diagnostic["reason"] == "missing_order_rows"

    second_layout, diagnostic = rules.parse_declarative_payload(
        item_info("绿色;40;商品丙*3"),
        [comma, semicolon],
        raw_record_id=2,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="2",
        parent_sequence=2,
        fingerprint_strategy="business_shape_v2",
    )
    assert diagnostic["reason"] == ""
    assert [
        (item.product, item.sales_attr1, item.sales_attr2, item.quantity)
        for item in second_layout.rows
    ] == [("商品丙", "绿色", "40", 3)]

    incomplete, diagnostic = rules.parse_declarative_payload(
        item_info("绿色，40.5 商品丙"),
        [comma],
        raw_record_id=3,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="3",
        parent_sequence=3,
        fingerprint_strategy="business_shape_v2",
    )
    assert incomplete.rows == []
    assert diagnostic["reason"] == "missing_quantity"

    ambiguous, diagnostic = rules.parse_declarative_payload(
        item_info("绿色，40.5 商品丙*3"),
        [comma, conflicting],
        raw_record_id=4,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="4",
        parent_sequence=4,
        fingerprint_strategy="business_shape_v2",
    )
    assert ambiguous.rows == []
    assert diagnostic["reason"] == "profile_ambiguous"

    duplicate = rules.validate_format_profiles([comma, dict(comma)])
    assert "parser_policy.format_profiles[1].fingerprint" in duplicate
    invalid = {**comma, "grammar_signature": "not-a-grammar"}
    assert "parser_policy.format_profiles[0].grammar_signature" in (
        rules.validate_format_profiles([invalid])
    )
    duplicate_roles = {
        **comma,
        "field_roles": {
            "sales_attr1": "shoe_size_like_numeric_segment",
            "sales_attr2": "shoe_size_like_numeric_segment",
        },
    }
    assert "parser_policy.format_profiles[0].field_roles" in (
        rules.validate_format_profiles([duplicate_roles])
    )
    duplicate_role_parent, diagnostic = rules.parse_declarative_payload(
        item_info("42，39 商品甲*1"),
        [duplicate_roles],
        raw_record_id=6,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="6",
        parent_sequence=6,
        fingerprint_strategy="business_shape_v2",
    )
    assert duplicate_role_parent.rows == []
    assert diagnostic["reason"] == "missing_order_rows"

    missing_roles = {key: value for key, value in comma.items() if key != "field_roles"}
    assert "parser_policy.format_profiles[0].field_roles" in (
        rules.validate_format_profiles([missing_roles])
    )
    missing_role_parent, diagnostic = rules.parse_declarative_payload(
        item_info("灰黑，38 商品甲*1"),
        [missing_roles],
        raw_record_id=7,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="7",
        parent_sequence=7,
        fingerprint_strategy="business_shape_v2",
    )
    assert missing_role_parent.rows == []
    assert diagnostic["reason"] == "format_profile_missing"

    empty_roles_without_grammar = {
        key: value
        for key, value in comma.items()
        if key != "grammar_signature"
    }
    empty_roles_without_grammar["field_roles"] = {}
    assert "parser_policy.format_profiles[0].grammar_signature" in (
        rules.validate_format_profiles([empty_roles_without_grammar])
    )


def test_same_grammar_text_profiles_keep_distinct_parsing_shapes() -> None:
    _app, rules = load_parser()
    synthesizer = importlib.import_module("service_app.rule_synthesizer")

    def payload(text: str) -> dict[str, Any]:
        return {
            "task": {
                "documents": [
                    {"contents": [{"data": {"customContent": text}}]}
                ]
            }
        }

    with_attribute = synthesizer.synthesize_rule(
        payload=payload("微信至尚--NB 白灰 42,,*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[
            {
                "product": "NB",
                "sales_attr1": "白灰",
                "sales_attr2": "42",
                "quantity": 1,
                "remark": "",
            }
        ],
        gold_samples=[],
        negative_samples=[],
    )["rule"]
    without_attribute = synthesizer.synthesize_rule(
        payload=payload("微信至尚--拖鞋 42,,*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[
            {
                "product": "拖鞋",
                "sales_attr1": "",
                "sales_attr2": "42",
                "quantity": 1,
                "remark": "",
            }
        ],
        gold_samples=[],
        negative_samples=[],
    )["rule"]

    assert with_attribute["grammar_signature"] == without_attribute["grammar_signature"]
    assert with_attribute["steps"] != without_attribute["steps"]
    assert rules.validate_format_profiles([with_attribute, without_attribute]) == []

    for index, (raw_payload, expected) in enumerate(
        (
            (
                payload("微信至尚--NIKE 白黑黄 40.5,,*2"),
                ("NIKE", "白黑黄", "40.5", 2),
            ),
            (payload("微信至尚--拖鞋 41,,*1"), ("拖鞋", "", "41", 1)),
        ),
        start=1,
    ):
        parent, diagnostic = rules.parse_declarative_payload(
            raw_payload,
            [with_attribute, without_attribute],
            raw_record_id=index,
            task_id=1,
            source_component="cainiao-cnprint",
            source_index=str(index),
            parent_sequence=index,
            fingerprint_strategy="business_shape_v2",
        )
        assert diagnostic["reason"] == ""
        assert [
            (
                row.product,
                row.sales_attr1,
                row.sales_attr2,
                row.quantity,
            )
            for row in parent.rows
        ] == [expected]


def test_source_projection_profiles_have_one_rule_per_grammar_slot() -> None:
    _app, rules = load_parser()
    part = {
        "source_path": "task.documents[].contents[].data.productInfo",
        "token_class": "text",
        "occurrence": 0,
    }
    base = {
        "fingerprint": f"sha256:{'8' * 64}",
        "strategy": "source_projection_v1",
        "grammar_signature": f"grammar-v1:sha256:{'9' * 64}",
        "rows": [
            {
                "product": [part],
                "sales_attr1": [],
                "sales_attr2": [],
                "quantity": [part],
                "remark": [],
            }
        ],
    }
    changed = {
        **base,
        "rows": [
            {
                **base["rows"][0],
                "product": [
                    {
                        **part,
                        "source_path": "task.documents[].contents[].data.productShortInfo",
                    }
                ],
            }
        ],
    }

    assert "parser_policy.format_profiles[1].fingerprint" in (
        rules.validate_format_profiles([base, changed])
    )
    assert "parser_policy.format_profiles[0].selected_fields" in (
        rules.validate_format_profiles([{**base, "selected_fields": []}])
    )


def test_structured_format_reuses_paths_and_fails_closed_on_conflict() -> None:
    _app, rules = load_parser()
    synthesizer = importlib.import_module("service_app.rule_synthesizer")

    def payload(product: str) -> dict[str, Any]:
        return {
            "task": {
                "documents": [
                    {
                        "contents": [
                            {
                                "data": {
                                    "packageItemDetail": [
                                        {
                                            "itemName": product,
                                            "skuFullName": "5代白金 45",
                                            "skuSize": "45",
                                            "itemNum": 1,
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        }

    def row(product: str) -> dict[str, Any]:
        return {
            "product": product,
            "sales_attr1": "5代白金",
            "sales_attr2": "45",
            "quantity": 1,
            "remark": "",
        }

    compact = synthesizer.synthesize_rule(
        payload=payload("商品甲"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品甲")],
        gold_samples=[],
        negative_samples=[],
    )["rule"]
    second = synthesizer.synthesize_rule(
        payload=payload("商品乙。"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品乙。")],
        gold_samples=[],
        negative_samples=[],
    )["rule"]

    assert compact == second
    assert "grammar_signature" not in compact
    assert "parser_policy.format_profiles[1].fingerprint" in (
        rules.validate_format_profiles([compact, second])
    )

    parent, diagnostic = rules.parse_declarative_payload(
        payload("商品丙。"),
        [compact],
        raw_record_id=1,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="1",
        parent_sequence=1,
        fingerprint_strategy="business_shape_v2",
    )
    assert diagnostic["reason"] == ""
    assert [item.product for item in parent.rows] == ["商品丙。"]

    punctuation, diagnostic = rules.parse_declarative_payload(
        payload("商品;丁"),
        [compact],
        raw_record_id=2,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="2",
        parent_sequence=2,
        fingerprint_strategy="business_shape_v2",
    )
    assert diagnostic["reason"] == ""
    assert [item.product for item in punctuation.rows] == ["商品;丁"]

    subset = synthesizer.synthesize_rule(
        payload=payload("商品甲"),
        source_component="cainiao-cnprint",
        corrected_rows=[
            {
                **row("商品甲"),
                "sales_attr1": "",
                "sales_attr2": "",
            }
        ],
        gold_samples=[],
        negative_samples=[],
        selected_fields=["item_name", "item_quantity"],
    )["rule"]
    parent, diagnostic = rules.parse_declarative_payload(
        payload("商品甲"),
        [subset, compact],
        raw_record_id=3,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="3",
        parent_sequence=3,
        fingerprint_strategy="business_shape_v2",
    )
    assert parent.rows == []
    assert diagnostic["reason"] == "profile_ambiguous"

    image_by_product = {
        **compact,
        "grammar_signature": f"grammar-v1:sha256:{'a' * 64}",
        "fields": {
            **compact["fields"],
            "image_match_text": "itemName",
        },
    }
    image_by_spec = {
        **compact,
        "grammar_signature": f"grammar-v1:sha256:{'b' * 64}",
        "fields": {
            **compact["fields"],
            "image_match_text": "skuFullName",
        },
    }
    assert rules.validate_format_profiles([image_by_product, image_by_spec]) == []

    parent, diagnostic = rules.parse_declarative_payload(
        payload("商品甲"),
        [image_by_product, image_by_spec],
        raw_record_id=4,
        task_id=1,
        source_component="cainiao-cnprint",
        source_index="4",
        parent_sequence=4,
        fingerprint_strategy="business_shape_v2",
    )
    assert parent.rows == []
    assert diagnostic["reason"] == "profile_ambiguous"


def test_rule_pack_validation_rejects_regex_script_and_unbounded_paths() -> None:
    app, _rules = load_parser()
    profiles = [
        {
            "fingerprint": "sha256:" + "a" * 64,
            "strategy": "text_pipeline_v1",
            "text_path": ".".join(["nested"] * 20),
            "steps": [{"op": "regex", "pattern": "(a+)+$"}],
            "script": "open('data.db')",
        }
    ]

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/rule-packs/validate",
            json={"rule_pack": declarative_pack(profiles)},
        )

    body = response.json()
    assert body["status"] == "invalid"
    assert any("format_profiles[0]" in error for error in body["errors"])


def test_rule_pack_explain_identifies_declarative_parser() -> None:
    app, rules = load_parser()
    rule_pack = declarative_pack([structured_profile(rules, one_document())])

    with TestClient(app) as client:
        response = client.post("/api/v1/rule-packs/explain", json={"rule_pack": rule_pack})

    assert "declarative format profile parser" in response.json()["capabilities"]


def test_structured_profile_requires_quantity_path_or_positive_default() -> None:
    app, _rules = load_parser()
    profile = {
        "fingerprint": "sha256:" + "b" * 64,
        "strategy": "structured_items_v1",
        "items_path": "task.documents[].contents[].data.items[]",
        "fields": {"product": "name"},
        "defaults": {},
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/rule-packs/validate",
            json={"rule_pack": declarative_pack([profile])},
        )

    assert response.json()["status"] == "invalid"
    assert "parser_policy.format_profiles[0].fields" in response.json()["errors"]


def test_declarative_parser_never_silently_drops_legacy_inputs() -> None:
    app, rules = load_parser()
    profile = structured_profile(rules, one_document())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 61,
                "standard_details": [
                    {
                        "standard_detail_id": 77,
                        "parent_sequence": 1,
                        "field_values": {"product": "旧派生数据"},
                    }
                ],
                "rule_pack": declarative_pack([profile]),
            },
        )

    body = response.json()
    assert body["status"] == "format_profile_incomplete"
    assert body["summary"]["parent_waybill_count"] == 1
    assert body["diagnostics"][0]["reason"] == "declarative_raw_payload_required"


def test_two_identical_raw_records_remain_two_parents() -> None:
    app, rules = load_parser()
    payload = one_document()
    pack = declarative_pack([structured_profile(rules, payload)])

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 61,
                "raw_records": [raw_record(906, payload), raw_record(907, payload)],
                "rule_pack": pack,
            },
        )

    body = response.json()
    assert body["summary"]["parent_waybill_count"] == 2
    assert body["summary"]["child_waybill_count"] == 4
    assert [parent["raw_record_id"] for parent in body["parents"]] == [906, 907]


def test_business_shape_v2_structured_profile_applies_row_steps_without_deduplication() -> None:
    app, _rules = load_parser()
    payload = one_document()
    items = payload["task"]["documents"][0]["contents"][0]["data"]["packageItemDetail"]
    items[0]["color"] = "5代白金 45"
    items[1]["color"] = "Cloud 6 43"
    from services.shared.waybill_fingerprint import fingerprint_for_payload

    profile = {
        "fingerprint": fingerprint_for_payload(payload, "cainiao-cnprint", "business_shape_v2"),
        "strategy": "structured_items_v1",
        "items_path": "task.documents[].contents[].data.packageItemDetail[]",
        "fields": {"product": "itemName", "sales_attr1": "color", "quantity": "itemNum"},
        "steps": [
            {
                "op": "rsplit",
                "source": "sales_attr1",
                "delimiter": " ",
                "targets": ["sales_attr1", "sales_attr2"],
            }
        ],
    }
    pack = declarative_pack([profile])
    pack["parser_policy"]["fingerprint_strategy"] = "business_shape_v2"

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={"task_id": 61, "raw_records": [raw_record(912, payload)], "rule_pack": pack},
        )

    body = response.json()
    assert body["status"] == "parsed"
    assert [(row["product"], row["sales_attr1"], row["sales_attr2"], row["quantity"]) for row in body["rows"]] == [
        ("范74", "5代白金", "45", 2),
        ("秒45 跑鞋", "Cloud 6", "43", 1),
    ]


def test_business_shape_v2_does_not_fall_back_to_legacy_profile() -> None:
    app, rules = load_parser()
    baseline = one_document()
    changed = one_document()
    changed["task"]["documents"][0]["contents"][0]["data"]["packageItemDetail"][0]["itemNum"] = "2"
    from services.shared.waybill_fingerprint import fingerprint_for_payload

    v2_profile = structured_profile(rules, baseline)
    v2_profile["fingerprint"] = fingerprint_for_payload(baseline, "cainiao-cnprint", "business_shape_v2")
    pack = declarative_pack([structured_profile(rules, baseline), v2_profile])
    pack["parser_policy"]["fingerprint_strategy"] = "business_shape_v2"

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={"task_id": 61, "raw_records": [raw_record(913, changed)], "rule_pack": pack},
        )

    body = response.json()
    assert body["status"] == "format_profile_missing"
    assert body["diagnostics"][0]["reason"] == "format_profile_missing"
    assert body["diagnostics"][0]["fingerprint"].startswith("v2:")


def test_structured_profile_steps_reject_text_state() -> None:
    app, rules = load_parser()
    profile = structured_profile(rules, one_document())
    profile["steps"] = [{"op": "trim", "target": "text"}]

    with TestClient(app) as client:
        response = client.post("/api/v1/rule-packs/validate", json={"rule_pack": declarative_pack([profile])})

    assert response.json()["status"] == "invalid"
    assert "parser_policy.format_profiles[0].steps[0].target" in response.json()["errors"]
