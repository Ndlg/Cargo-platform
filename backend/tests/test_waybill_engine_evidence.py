from __future__ import annotations

import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

from services.shared.waybill_fingerprint import business_shape_fingerprint


REPO_ROOT = Path(__file__).resolve().parents[2]
PARSER_ROOT = REPO_ROOT / "services" / "waybill-parser"
if str(PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSER_ROOT))

from service_app.evidence import build_evidence  # noqa: E402
from service_app.main import app  # noqa: E402


def item_info(text: str, **extra: object) -> dict[str, object]:
    return {
        "task": {
            "documents": [
                {
                    "contents": [
                        {
                            "data": {
                                "ITEM_INFO": text,
                                **extra,
                            }
                        }
                    ]
                }
            ]
        }
    }


def print_xml(text: str) -> dict[str, object]:
    return {
        "contents": [
            {
                "printXML": (
                    "<layout><text><![CDATA["
                    f"{text}"
                    "]]></text></layout>"
                )
            }
        ]
    }


def test_evidence_retains_paths_but_excludes_pii() -> None:
    evidence = build_evidence(
        item_info(
            "范33 带木one帆布kw，木村-3M反光，40*1",
            receiverAddress="福建省某地址",
            mobile="13800000000",
        ),
        "cainiao-cnprint",
    )

    assert [span["source_path"] for span in evidence["spans"]] == [
        "task.documents[0].contents[0].data.ITEM_INFO"
    ]
    assert evidence["excluded_field_counts"] == {
        "non_business": 2,
        "unselected_business": 0,
    }
    encoded = json.dumps(evidence, ensure_ascii=False)
    assert "福建" not in encoded
    assert "13800000000" not in encoded


def test_value_changes_keep_the_same_grammar_signature() -> None:
    first = build_evidence(item_info("黄色，43 商品甲*1"), "cainiao-cnprint")
    second = build_evidence(item_info("灰色，39 商品乙*2"), "cainiao-cnprint")

    assert first["grammar_signature"] == second["grammar_signature"]


def test_quantity_unit_layout_changes_grammar_and_fingerprint() -> None:
    one_piece = print_xml("黄色，43 商品甲 1件")
    same_layout_new_values = print_xml("灰色，39 商品乙 2件")
    one_pair = print_xml("灰色，39 商品乙 2双")

    first = build_evidence(one_piece, "cainiao-cnprint")
    same_layout = build_evidence(same_layout_new_values, "cainiao-cnprint")
    changed_layout = build_evidence(one_pair, "cainiao-cnprint")

    assert first["grammar_signature"] == same_layout["grammar_signature"]
    assert first["grammar_signature"] != changed_layout["grammar_signature"]
    assert business_shape_fingerprint(one_piece, "cainiao-cnprint") == (
        business_shape_fingerprint(same_layout_new_values, "cainiao-cnprint")
    )
    assert business_shape_fingerprint(one_piece, "cainiao-cnprint") != (
        business_shape_fingerprint(one_pair, "cainiao-cnprint")
    )


def test_normalization_preserves_original_offsets_and_selected_fields() -> None:
    evidence = build_evidence(
        item_info(
            "  黄色，\t43  ",
            SELLER_MEMO="  加急；\t勿压  ",
        ),
        "cainiao-cnprint",
        selected_fields=["seller_memo"],
    )

    assert evidence["spans"] == [
        {
            "span_id": evidence["spans"][0]["span_id"],
            "source_path": "task.documents[0].contents[0].data.SELLER_MEMO",
            "original_text": "  加急；\t勿压  ",
            "normalized_text": "加急; 勿压",
            "start": 0,
            "end": 10,
            "token_class": "delimited_text",
        }
    ]
    assert evidence["excluded_field_counts"]["unselected_business"] == 1


def test_candidate_groups_are_stable_span_id_lists() -> None:
    payload = {
        "contents": [
            {
                "data": {
                    "packageItemDetail": [
                        {"itemName": "商品甲", "skuFullName": "黄色，43", "itemNum": 1},
                        {"itemName": "商品乙", "skuFullName": "灰色，39", "itemNum": 2},
                    ]
                }
            }
        ]
    }

    first = build_evidence(payload, "cainiao-cnprint")
    second = build_evidence(payload, "cainiao-cnprint")
    span_ids = {span["span_id"] for span in first["spans"]}

    assert first["candidate_groups"] == second["candidate_groups"]
    assert set(first["candidate_groups"]) == {
        "structured_list_item",
        "line",
        "delimiter_separated_segment",
        "positive_integer_quantity",
        "shoe_size_like_numeric_segment",
        "repeated_line_or_array_group",
    }
    assert all(
        group and set(group) <= span_ids
        for groups in first["candidate_groups"].values()
        for group in groups
    )
    assert len(first["candidate_groups"]["structured_list_item"]) == 2
    assert len(first["candidate_groups"]["positive_integer_quantity"]) == 2
    assert len(first["candidate_groups"]["shoe_size_like_numeric_segment"]) == 2
    assert len(first["candidate_groups"]["repeated_line_or_array_group"]) == 1


def test_parser_analyze_endpoint_returns_evidence() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analyze",
            json={
                "raw_payload": item_info("黄色，43 商品甲*1"),
                "source_component": "cainiao-cnprint",
                "selected_fields": ["item_info"],
            },
        )

    assert response.status_code == 200
    assert response.json()["spans"][0]["source_path"].endswith(".ITEM_INFO")
