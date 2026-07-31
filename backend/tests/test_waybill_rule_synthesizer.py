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

from service_app.declarative_rules import projection_grammar_signature  # noqa: E402
from service_app.evidence import build_evidence  # noqa: E402
import service_app.rule_synthesizer as rule_synthesizer  # noqa: E402
from service_app.rule_synthesizer import (  # noqa: E402
    _projection_candidates,
    replay_rule,
    synthesize_rule,
)
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


def labeled_variant_document(size: str) -> dict[str, object]:
    return {
        "task": {
            "documents": [
                {
                    "contents": [
                        {},
                        print_xml(
                            f"颜色分类:4.0二代黑白;鞋码:{size}，,*1"
                        )["contents"][0],
                    ]
                }
            ]
        }
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


def test_synthesizer_compiles_repeated_text_values_with_wrapped_quantities() -> None:
    payload = {
        "contents": [
            {"data": {"ITEM_INFO": "商品甲 红色;42 【1件】"}},
            {"data": {"ITEM_INFO": "商品甲 红色;42 【1件】"}},
        ]
    }
    result = synthesize_rule(
        payload=payload,
        source_component="cainiao-cnprint",
        corrected_rows=[
            row("商品甲", "红色", "42", 1),
            row("商品甲", "红色", "42", 1),
        ],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert replay_rule(
        result["rule"],
        {
            "contents": [
                {"data": {"ITEM_INFO": "商品乙 蓝色;43 【2件】"}},
                {"data": {"ITEM_INFO": "商品丙 绿色;44 【3件】"}},
            ]
        },
    ) == [
        row("商品乙", "蓝色", "43", 2),
        row("商品丙", "绿色", "44", 3),
    ]


def test_synthesizer_compiles_lines_in_one_value_as_distinct_rows() -> None:
    result = synthesize_rule(
        payload=item_info(
            "商品甲 红色;42 【1件】\n"
            "商品乙 蓝色;43 【2件】"
        ),
        source_component="cainiao-cnprint",
        corrected_rows=[
            row("商品甲", "红色", "42", 1),
            row("商品乙", "蓝色", "43", 2),
        ],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert replay_rule(
        result["rule"],
        item_info(
            "商品丙 绿色;44 【3件】\n"
            "商品丁 黄色;45 【4件】"
        ),
    ) == [
        row("商品丙", "绿色", "44", 3),
        row("商品丁", "黄色", "45", 4),
    ]


def test_synthesizer_projects_multiple_safe_sources_and_reuses_one_source() -> None:
    payload = {
        "contents": [
            {
                "data": {
                    "ITEM_INFO": "补差价",
                    "ITEM_TOTAL_COUNT": "180",
                }
            }
        ]
    }
    result = synthesize_rule(
        payload=payload,
        source_component="cainiao-cnprint",
        corrected_rows=[row("补差价", "补差价", "", 180)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["strategy"] == "source_projection_v1"
    assert replay_rule(
        result["rule"],
        {
            "contents": [
                {
                    "data": {
                        "ITEM_INFO": "另一个补差商品",
                        "ITEM_TOTAL_COUNT": "2",
                    }
                }
            ]
        },
    ) == [row("另一个补差商品", "另一个补差商品", "", 2)]


def test_synthesizer_projects_xml_lines_and_replays_same_grammar() -> None:
    result = synthesize_rule(
        payload=print_xml("黄色，43\n商品甲*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品甲", "黄色", "43", 1)],
        gold_samples=[
            {
                "raw_payload": print_xml("蓝色，42\n商品乙*2"),
                "source_component": "cainiao-cnprint",
                "rows": [row("商品乙", "蓝色", "42", 2)],
            }
        ],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["strategy"] == "source_projection_v1"
    assert replay_rule(
        result["rule"],
        print_xml("绿色，44\n商品丙*3"),
    ) == [row("商品丙", "绿色", "44", 3)]


def test_synthesizer_projects_multiple_rows_without_deduplicating() -> None:
    result = synthesize_rule(
        payload=print_xml(
            "低帮深卡其，42.5\n"
            "商品甲*1\n"
            "属性乙\n"
            "商品乙*1"
        ),
        source_component="cainiao-cnprint",
        corrected_rows=[
            row("商品甲", "低帮深卡其", "42.5", 1),
            row("属性乙 商品乙", "属性乙", "商品乙", 1),
        ],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert replay_rule(result["rule"], print_xml(
        "低帮黑白色，43.5\n"
        "商品丙*2\n"
        "属性丁\n"
        "商品丁*2"
    )) == [
        row("商品丙", "低帮黑白色", "43.5", 2),
        row("属性丁 商品丁", "属性丁", "商品丁", 2),
    ]


def test_synthesizer_projects_safe_transforms_and_concatenation() -> None:
    result = synthesize_rule(
        payload=print_xml(
            "秒67 175，,灰色，默认*1\n"
            "秒67 175，,默认，默认*1\n"
            "颜色分类:C6全黑;鞋码:44"
        ),
        source_component="cainiao-cnprint",
        corrected_rows=[
            row(
                "秒67 175，灰色，默认*1 秒67 175",
                "C6全黑",
                "44",
                1,
            )
        ],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert replay_rule(
        result["rule"],
        print_xml(
            "秒45 按跑，,白色，默认*2\n"
            "秒45 按跑，,默认，默认*2\n"
            "颜色分类:Cloud黑灰;鞋码:43"
        ),
    ) == [
        row(
            "秒45 按跑，白色，默认*2 秒45 按跑",
            "Cloud黑灰",
            "43",
            2,
        )
    ]


def test_synthesizer_compiles_labeled_variant_text_with_shared_dynamic_product() -> None:
    learning = labeled_variant_document("41")
    holdout = labeled_variant_document("39")
    special = print_xml("补差价，拍多少数量提前沟通")
    learning_row = row("4.0二代黑白", "4.0二代黑白", "41", 1)
    holdout_row = row("4.0二代黑白", "4.0二代黑白", "39", 1)

    result = synthesize_rule(
        payload=learning,
        source_component="cainiao-cnprint",
        corrected_rows=[learning_row],
        gold_samples=[
            {
                "raw_payload": holdout,
                "source_component": "cainiao-cnprint",
                "rows": [holdout_row],
            }
        ],
        negative_samples=[
            {
                "raw_payload": special,
                "source_component": "cainiao-cnprint",
            }
        ],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["strategy"] == "source_projection_v1"
    assert replay_rule(result["rule"], learning) == [learning_row]
    assert replay_rule(result["rule"], holdout) == [holdout_row]
    assert replay_rule(result["rule"], special) == []


def test_projection_synthesis_rejects_ambiguous_same_path_occurrences() -> None:
    result = synthesize_rule(
        payload=print_xml("商品甲\n商品甲\n1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品甲", "", "", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiler_capability_missing"
    assert result["rule"] is None


def test_projection_synthesis_rejects_ambiguous_concatenations() -> None:
    result = synthesize_rule(
        payload=print_xml("商品甲\n商品甲\n属性乙*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品甲 属性乙", "", "", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiler_capability_missing"
    assert result["rule"] is None


def test_projection_uses_a_unique_parallel_field_to_resolve_occurrences() -> None:
    def payload(products: str, attributes: str) -> dict[str, object]:
        return {
            "contents": [
                {
                    "printXML": (
                        '<layout id="CUSTOM_AREA">'
                        f"<text><![CDATA[{products}]]></text>"
                        f"<text><![CDATA[{attributes}]]></text>"
                        "</layout>"
                    )
                }
            ]
        }

    result = synthesize_rule(
        payload=payload(
            "款式甲，,灰色，默认*1\n款式甲，,默认，默认*1",
            "颜色分类:红色;鞋码:42\n颜色分类:红色;鞋码:43",
        ),
        source_component="cainiao-cnprint",
        corrected_rows=[row("款式甲，灰色，默认*1 款式甲", "红色", "42", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert replay_rule(
        result["rule"],
        payload(
            "款式乙，,蓝色，默认*2\n款式乙，,默认，默认*2",
            "颜色分类:绿色;鞋码:44\n颜色分类:绿色;鞋码:45",
        ),
    ) == [row("款式乙，蓝色，默认*2 款式乙", "绿色", "44", 2)]


def test_projection_grammar_includes_source_paths_and_occurrences() -> None:
    evidence = build_evidence(print_xml("商品甲*1"), "cainiao-cnprint")
    changed = json.loads(json.dumps(evidence))
    changed["spans"][0]["source_path"] = "contents[0].data.ITEM_INFO"

    assert projection_grammar_signature(evidence) != projection_grammar_signature(changed)


def test_projection_alignment_requires_a_shared_repeated_axis() -> None:
    axis = rule_synthesizer._projection_alignment_axis

    assert axis("task.documents[].contents[].printXML.text[0]") == axis(
        "task.documents[].contents[].printXML.text[1]"
    )
    assert axis("task.documents[].contents[].data.ITEM_INFO") == axis(
        "task.documents[].contents[].data.SELLER_MEMO"
    )
    assert axis("left.values[].item") != axis("right.values[].attribute")
    assert axis("left.item") is None


def test_projection_does_not_align_unrelated_repeated_paths() -> None:
    evidence = {
        "spans": [
            {
                "token_class": "text",
                "source_path": "left.values[0].product",
                "original_text": "主商品*1",
            },
            {
                "token_class": "text",
                "source_path": "left.values[1].product",
                "original_text": "附加商品*1",
            },
            {
                "token_class": "text",
                "source_path": "right.values[0].attribute",
                "original_text": "红色",
            },
            {
                "token_class": "text",
                "source_path": "right.values[1].attribute",
                "original_text": "红色",
            },
        ]
    }

    assert rule_synthesizer._compile_projection_rule(
        evidence,
        [row("主商品 附加商品", "红色", "", 1)],
        None,
    ) is None

    single_product = {
        "spans": [
            {
                "token_class": "text",
                "source_path": "left.values[1].product",
                "original_text": "目标商品*1",
            },
            *evidence["spans"][2:],
        ]
    }
    assert rule_synthesizer._compile_projection_rule(
        single_product,
        [row("目标商品", "红色", "", 1)],
        None,
    ) is None


def test_projection_candidate_enumeration_is_bounded() -> None:
    evidence = build_evidence(
        print_xml("商品" + "，".join(str(index) for index in range(25))),
        "cainiao-cnprint",
    )

    assert _projection_candidates(evidence) is None


def test_projection_operation_and_search_budgets_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = build_evidence(print_xml("商品甲*1"), "cainiao-cnprint")
    monkeypatch.setattr(
        rule_synthesizer,
        "MAX_PROJECTION_OPERATION_VARIANTS",
        5,
    )
    assert _projection_candidates(evidence) is None

    monkeypatch.setattr(rule_synthesizer, "MAX_PROJECTION_SEARCH_NODES", 1)
    candidates = [
        {
            "order": index,
            "value": value,
            "part": {
                "source_path": f"contents[].data.FIELD{index}",
                "token_class": "text",
                "occurrence": 0,
            },
        }
        for index, value in enumerate(("商品甲", "红色"))
    ]
    assert rule_synthesizer._find_projection_parts(
        "product",
        "商品甲 红色",
        candidates,
        -1,
        remaining_occurrences=1,
    ) is None


def test_structured_synthesis_reuses_equivalent_specs_and_collapses_whitespace() -> None:
    def payload(*, reverse_specs: bool = False) -> dict[str, object]:
        specs = (
            {"specName": "雾蓝 42", "skuFullName": "雾蓝 42"}
            if reverse_specs
            else {"skuFullName": "雾蓝 42", "specName": "雾蓝 42"}
        )
        return structured_items(
            {
                "itemName": "商品  甲",
                **specs,
                "skuSize": "42",
                "itemNum": 1,
            }
        )

    expected = [row("商品 甲", "雾蓝", "42", 1)]
    first = synthesize_rule(
        payload=payload(),
        source_component="cainiao-cnprint",
        corrected_rows=expected,
        gold_samples=[],
        negative_samples=[],
    )
    second = synthesize_rule(
        payload=payload(reverse_specs=True),
        source_component="cainiao-cnprint",
        corrected_rows=expected,
        gold_samples=[],
        negative_samples=[],
    )

    assert first["status"] == second["status"] == "compiled"
    assert first["rule"] == second["rule"]
    assert replay_rule(
        first["rule"],
        structured_items(
            {
                "itemName": "另一个   商品",
                "skuFullName": "深蓝 43",
                "specName": "错误值 99",
                "skuSize": "43",
                "itemNum": 2,
            }
        ),
    ) == [row("另一个 商品", "深蓝", "43", 2)]


def test_structured_rule_reuses_paths_for_different_values_and_gold() -> None:
    current = structured_items(
        {
            "itemName": "5.0秒70",
            "skuFullName": "5.0黑白紫 36",
            "skuSize": "36",
            "itemNum": 1,
        }
    )
    neighbor = structured_items(
        {
            "itemName": "秒25  阿尔。fa 12025",
            "skuFullName": "白色 44",
            "skuSize": "44",
            "itemNum": 1,
        }
    )
    result = synthesize_rule(
        payload=current,
        source_component="cainiao-cnprint",
        corrected_rows=[row("5.0秒70", "5.0黑白紫", "36", 1)],
        gold_samples=[
            {
                "raw_payload": neighbor,
                "source_component": "cainiao-cnprint",
                "rows": [row("秒25  阿尔。fa 12025", "白色", "44", 1)],
            }
        ],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["strategy"] == "structured_items_v1"
    assert "grammar_signature" not in result["rule"]
    assert replay_rule(result["rule"], neighbor) == [
        row("秒25  阿尔。fa 12025", "白色", "44", 1)
    ]
    assert result["replay_report"][-1]["kind"] == "gold"


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


def test_structured_format_reuses_one_rule_for_a_different_item_count() -> None:
    learned = synthesize_rule(
        payload=structured_items(
            {"itemName": "商品甲", "skuFullName": "灰黑", "skuSize": "38", "itemNum": 1}
        ),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品甲", "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert learned["status"] == "compiled"
    assert replay_rule(
        learned["rule"],
        structured_items(
            {"itemName": "商品乙", "skuFullName": "雾蓝", "skuSize": "39", "itemNum": 2},
            {"itemName": "商品丙", "skuFullName": "白色", "skuSize": "40", "itemNum": 3},
        ),
    ) == [
        row("商品乙", "雾蓝", "39", 2),
        row("商品丙", "白色", "40", 3),
    ]


def test_synthesizer_refuses_text_rule_that_conflicts_with_prior_gold() -> None:
    result = synthesize_rule(
        payload=print_xml("黄色，43 新商品*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("新商品", "黄色", "43", 1)],
        gold_samples=[
            {
                "raw_payload": print_xml("灰色，39 旧商品*2"),
                "source_component": "cainiao-cnprint",
                "rows": [row("另一个确认商品", "灰色", "39", 2)],
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
    assert included["rule"]["selected_fields"] == [
        "item_name",
        "sku_full_name",
        "sku_size",
        "item_quantity",
    ]


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


def test_text_rule_replays_integer_decimal_and_product_changes_as_gold() -> None:
    result = synthesize_rule(
        payload=item_info("灰黑，38 商品名称*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品名称", "灰黑", "38", 1)],
        gold_samples=[
            {
                "raw_payload": item_info("黄色，40.5 另一个-商品*2"),
                "source_component": "cainiao-cnprint",
                "rows": [row("另一个-商品", "黄色", "40.5", 2)],
            }
        ],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["grammar_signature"].startswith("grammar-v1:sha256:")
    assert replay_rule(
        result["rule"],
        item_info("蓝色，41.5 同语法-商品*3"),
    ) == [row("同语法-商品", "蓝色", "41.5", 3)]
    assert replay_rule(
        result["rule"],
        item_info("蓝色，41.5 缺数量分隔符"),
    ) == []
    assert result["replay_report"][-1] == {
        "kind": "gold",
        "passed": True,
        "expected": [row("另一个-商品", "黄色", "40.5", 2)],
        "actual": [row("另一个-商品", "黄色", "40.5", 2)],
        "emitted_row_count": 1,
    }


def test_text_rule_rejects_a_field_role_swap_with_the_same_delimiters() -> None:
    result = synthesize_rule(
        payload=item_info("灰黑，38 范74*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("范74", "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["field_roles"] == {
        "sales_attr2": "shoe_size_like_numeric_segment"
    }
    assert replay_rule(
        result["rule"],
        item_info("蓝色，40.5 范75-新款*2"),
    ) == [row("范75-新款", "蓝色", "40.5", 2)]
    assert replay_rule(
        result["rule"],
        item_info("42（标准），黑色 范74*2"),
    ) == []
    assert replay_rule(result["rule"], item_info("42，39 范74*2")) == []


def test_text_rule_rejects_two_fields_with_the_same_restrictive_role() -> None:
    result = synthesize_rule(
        payload=item_info("42，39 范74*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("范74", "42", "39", 1)],
        gold_samples=[
            {
                "raw_payload": item_info("39，42 范75*2"),
                "source_component": "cainiao-cnprint",
                "rows": [row("范75", "39", "42", 2)],
            }
        ],
        negative_samples=[],
    )

    assert result["status"] == "compiler_capability_missing"
    assert result["rule"] is None


def test_text_rule_without_restrictive_roles_keeps_exact_grammar() -> None:
    result = synthesize_rule(
        payload=item_info("蓝色，均码 范74*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("范74", "蓝色", "均码", 1)],
        gold_samples=[],
        negative_samples=[],
    )

    assert result["status"] == "compiled"
    assert result["rule"]["field_roles"] == {}
    assert replay_rule(
        result["rule"],
        item_info("红色，均码 范75*2"),
    ) == [row("范75", "红色", "均码", 2)]
    assert replay_rule(
        result["rule"],
        item_info("40.5，均码 范75-新款*2"),
    ) == []


def test_text_rule_skips_a_prior_gold_layout_that_it_cannot_complete() -> None:
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
    assert replay_rule(
        result["rule"],
        item_info("黄色;43;另一个商品【2件】"),
    ) == []
    assert result["replay_report"][-1] == {
        "kind": "gold_not_applicable",
        "passed": True,
        "expected": [row("另一个商品", "黄色", "43", 2)],
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
