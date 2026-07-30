from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient


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


def test_synthesizer_prefers_direct_structured_paths_and_preserves_duplicates() -> None:
    payload = structured_items(
        {"itemName": "商品甲", "skuColor": "灰黑", "skuSize": "38", "itemNum": 1},
        {"itemName": "商品甲", "skuColor": "灰黑", "skuSize": "38", "itemNum": 1},
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
