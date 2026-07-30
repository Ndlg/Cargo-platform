from __future__ import annotations

from pathlib import Path
import json
import sys

from fastapi.testclient import TestClient
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PARSER_ROOT = REPO_ROOT / "services" / "waybill-parser"
if str(PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSER_ROOT))

from service_app.rule_synthesizer import replay_rule, synthesize_rule  # noqa: E402
from service_app.main import app  # noqa: E402


def row(
    product: str,
    sales_attr1: str,
    sales_attr2: str,
    quantity: int,
    remark: str = "",
) -> dict[str, object]:
    return {
        "product": product,
        "sales_attr1": sales_attr1,
        "sales_attr2": sales_attr2,
        "quantity": quantity,
        "remark": remark,
    }


def print_xml(text: str) -> dict[str, object]:
    return {
        "contents": [
            {
                "printXML": (
                    '<layout id="CUSTOM_AREA"><text><![CDATA['
                    f"{text}"
                    "]]></text></layout>"
                )
            }
        ]
    }


def print_xml_with_private_text(private_text: str, business_text: str) -> dict[str, object]:
    return {
        "contents": [
            {
                "printXML": (
                    "<layout>"
                    f"<text>{private_text}</text>"
                    '<layout id="CUSTOM_AREA"><text><![CDATA['
                    f"{business_text}"
                    "]]></text></layout>"
                    "</layout>"
                )
            }
        ]
    }


def item_info(text: str) -> dict[str, object]:
    return {"contents": [{"data": {"ITEM_INFO": text}}]}


def structured_items(*items: dict[str, object]) -> dict[str, object]:
    return {
        "contents": [
            {
                "data": {
                    "packageItemDetail": list(items),
                }
            }
        ]
    }


def nested_payload(depth: int) -> dict[str, object]:
    payload: dict[str, object] = {}
    for _ in range(depth):
        payload = {"nested": payload}
    return payload


def test_synthesizer_compiles_source_order_without_model_rule() -> None:
    result = synthesize_rule(
        payload=print_xml("灰黑，38 商品名称*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品名称", "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert replay_rule(result["rule"], print_xml("黄色，43 另一个商品*2")) == [
        row("另一个商品", "黄色", "43", 2)
    ]


def test_synthesizer_compiles_delimited_items_into_one_reusable_rule() -> None:
    result = synthesize_rule(
        payload=item_info("【商品甲】红色 42.5 1 件;【商品甲】红色 42 1 件"),
        source_component="cainiao-cnprint",
        corrected_rows=[
            row("商品甲", "红色", "42.5", 1),
            row("商品甲", "红色", "42", 1),
        ],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["item_split"] == ";"
    assert replay_rule(
        result["rule"],
        item_info("【商品乙】蓝色 40.5 2 件;【商品丙】绿色 41 1 件"),
    ) == [
        row("商品乙", "蓝色", "40.5", 2),
        row("商品丙", "绿色", "41", 1),
    ]


def test_synthesizer_prefers_direct_structured_paths_and_preserves_duplicates() -> None:
    payload = structured_items(
        {"itemName": "商品甲", "skuFullName": "灰黑", "skuSize": "38", "itemNum": 1},
        {"itemName": "商品甲", "skuFullName": "灰黑", "skuSize": "38", "itemNum": 1},
    )
    expected = [
        row("商品甲", "灰黑", "38", 1),
        row("商品甲", "灰黑", "38", 1),
    ]

    result = synthesize_rule(
        payload=payload,
        source_component="cainiao-cnprint",
        corrected_rows=expected,
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["strategy"] == "structured_items_v1"
    assert replay_rule(result["rule"], payload) == expected


def test_synthesizer_refuses_rule_that_breaks_prior_gold() -> None:
    result = synthesize_rule(
        payload=print_xml("黄色，43 新商品*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("新商品", "黄色", "43", 1)],
        gold_samples=[
            {
                "raw_payload": print_xml("商品 灰色;39【1件】"),
                "source_component": "cainiao-cnprint",
                "rows": [row("商品", "灰色", "39", 1)],
            }
        ],
        negative_samples=[],
    )

    assert result["status"] == "rule_replay_failed"
    assert result["rule"] is None


def test_synthesizer_rejects_field_labels_and_negative_matches() -> None:
    labeled = synthesize_rule(
        payload=print_xml("灰黑，38 商品：名称*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品：名称", "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )
    assert labeled["status"] == "candidate_invalid"

    negative = synthesize_rule(
        payload=print_xml("灰黑，38 商品名称*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品名称", "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[
            {
                "raw_payload": print_xml("黄色，43 不应命中*2"),
                "source_component": "cainiao-cnprint",
            }
        ],
    )
    assert negative["status"] == "rule_replay_failed"
    assert negative["rule"] is None


def test_structured_synthesis_never_uses_excluded_raw_leaves() -> None:
    result = synthesize_rule(
        payload=structured_items(
            {
                "receiverName": "隐私姓名",
                "itemName": "正常商品",
                "skuFullName": "灰黑",
                "skuSize": "38",
                "itemNum": 1,
            }
        ),
        source_component="cainiao-cnprint",
        corrected_rows=[row("隐私姓名", "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiler_capability_missing"
    assert "receiverName" not in json.dumps(result, ensure_ascii=False)
    assert "隐私姓名" not in json.dumps(result, ensure_ascii=False)


def test_structured_synthesis_uses_only_tenant_selected_fields() -> None:
    payload = structured_items(
        {
            "itemName": "正常商品",
            "skuFullName": "不允许参与的规格",
            "skuSize": "38",
            "itemNum": 1,
        }
    )

    excluded = synthesize_rule(
        payload=payload,
        source_component="cainiao-cnprint",
        corrected_rows=[row("正常商品", "不允许参与的规格", "", 1)],
        gold_samples=[],
        negative_samples=[],
        selected_fields=["item_name", "item_quantity"],
    )
    included = synthesize_rule(
        payload=payload,
        source_component="cainiao-cnprint",
        corrected_rows=[row("正常商品", "不允许参与的规格", "38", 1)],
        gold_samples=[],
        negative_samples=[],
        selected_fields=[
            "item_name",
            "sku_full_name",
            "sku_size",
            "item_quantity",
        ],
    )

    assert excluded["status"] == "compiler_capability_missing"
    assert "不允许参与的规格" not in json.dumps(excluded, ensure_ascii=False)
    assert included["status"] == "compiled"


def test_negative_sample_fails_when_parser_emits_an_invalid_row() -> None:
    result = synthesize_rule(
        payload=print_xml("灰黑，38 商品名称*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品名称", "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[
            {
                "raw_payload": print_xml("灰黑，38 商品名称*0"),
                "source_component": "cainiao-cnprint",
            }
        ],
    )

    assert result["status"] == "rule_replay_failed"
    assert result["rule"] is None
    assert result["replay_report"][-1]["emitted_row_count"] == 1


def test_structured_synthesis_rejects_ambiguous_field_paths() -> None:
    result = synthesize_rule(
        payload=structured_items(
            {
                "itemName": "相同值",
                "skuFullName": "相同值",
                "skuSize": "38",
                "itemNum": 1,
            }
        ),
        source_component="cainiao-cnprint",
        corrected_rows=[row("相同值", "相同值", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiler_capability_missing"


def test_structured_synthesis_rejects_alphanumeric_delimiter() -> None:
    result = synthesize_rule(
        payload=structured_items(
            {
                "itemName": "商品",
                "skuFullName": "红色 SKU6173 38",
                "itemNum": 1,
            }
        ),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品", "红色", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiler_capability_missing"


def test_custom_area_rule_replays_only_the_allowlisted_xml_node() -> None:
    private_text = "张三 13900000000 厦门市某地址"
    result = synthesize_rule(
        payload=print_xml_with_private_text(private_text, "灰黑，38 商品名称*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品名称", "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["text_selector"] == {
        "kind": "print_xml_custom_area",
        "text_index": 1,
    }
    assert replay_rule(
        result["rule"],
        print_xml_with_private_text("李四 13800000000 另一地址", "黄色，43 另一个商品*2"),
    ) == [row("另一个商品", "黄色", "43", 2)]
    assert private_text not in json.dumps(result, ensure_ascii=False)


def test_text_rule_uses_selected_input_grammar_and_ignores_neighbor_gold() -> None:
    result = synthesize_rule(
        payload=item_info("灰黑，38 商品名称*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品名称", "灰黑", "38", 1)],
        gold_samples=[
            {
                "raw_payload": item_info("黄色;43;另一个商品【2件】"),
                "source_component": "cainiao-cnprint",
                "rows": [row("另一个商品", "黄色", "43", 2)],
            }
        ],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["grammar_signature"].startswith("grammar-v1:sha256:")
    assert replay_rule(
        result["rule"],
        item_info("蓝色，40 同语法商品*3"),
    ) == [row("同语法商品", "蓝色", "40", 3)]
    assert replay_rule(
        result["rule"],
        item_info("黄色;43;另一个商品【2件】"),
    ) == []
    assert result["replay_report"][-1] == {
        "kind": "gold_neighbor",
        "passed": True,
        "expected": [],
        "actual": [],
        "emitted_row_count": 0,
    }


@pytest.mark.parametrize("product", ["商品名称：鞋", "产品名称为鞋"])
def test_synthesizer_rejects_complete_product_field_labels(product: str) -> None:
    result = synthesize_rule(
        payload=print_xml(f"灰黑，38 {product}*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row(product, "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "candidate_invalid"


def test_parser_exposes_rule_synthesis_endpoint() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/rules/synthesize",
            json={
                "raw_payload": print_xml("灰黑，38 商品名称*1"),
                "source_component": "cainiao-cnprint",
                "corrected_rows": [row("商品名称", "灰黑", "38", 1)],
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "compiled"


def test_rule_synthesis_endpoint_forwards_selected_fields() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/rules/synthesize",
            json={
                "raw_payload": structured_items(
                    {
                        "itemName": "正常商品",
                        "skuFullName": "不允许参与的规格",
                        "itemNum": 1,
                    }
                ),
                "source_component": "cainiao-cnprint",
                "corrected_rows": [row("正常商品", "不允许参与的规格", "", 1)],
                "selected_fields": ["item_name", "item_quantity"],
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "compiler_capability_missing"


@pytest.mark.parametrize(
    "override",
    [
        {"source_component": "x" * 129},
        {"corrected_rows": [row(f"商品{index}", "灰黑", "38", 1) for index in range(101)]},
        {"gold_samples": [{} for _ in range(101)]},
        {"negative_samples": [{} for _ in range(101)]},
        {"corrected_rows": [{**row("商品", "灰黑", "38", 1), "unexpected": "x"}]},
        {"corrected_rows": [row("", "灰黑", "38", 1)]},
        {"corrected_rows": [row("商品", "灰黑", "38", 0)]},
        {"raw_payload": {"oversized": "x" * 2_000_001}},
        {"raw_payload": nested_payload(65)},
    ],
)
def test_synthesis_endpoint_rejects_unbounded_or_untyped_input(
    override: dict[str, object],
) -> None:
    request: dict[str, object] = {
        "raw_payload": print_xml("灰黑，38 商品名称*1"),
        "source_component": "cainiao-cnprint",
        "corrected_rows": [row("商品名称", "灰黑", "38", 1)],
    }
    request.update(override)

    with TestClient(app) as client:
        response = client.post("/api/v1/rules/synthesize", json=request)

    assert response.status_code == 422


def test_synthesis_endpoint_rejects_extreme_json_depth_without_500() -> None:
    nested = '{"nested":' * 1000 + "{}" + "}" * 1000
    request = (
        '{"raw_payload":'
        + nested
        + ',"source_component":"cainiao-cnprint","corrected_rows":'
        '[{"product":"商品","sales_attr1":"","sales_attr2":"","quantity":1,"remark":""}]}'
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/rules/synthesize",
            content=request.encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 422
