from __future__ import annotations

from services.shared.waybill_fingerprint import (
    business_shape_fingerprint,
    fingerprint_catalog,
    fingerprint_for_payload,
    grammar_signature_for_texts,
    inspect_fingerprint,
    legacy_structural_fingerprint,
)


def _xml(text: str) -> dict[str, object]:
    return {
        "contents": [
            {
                "printXML": (
                    '<layout><text x="12" y="8"><![CDATA['
                    f"{text}"
                    "]]></text><text>颜色：白色</text></layout>"
                )
            }
        ]
    }


def _xml_lines(*lines: str) -> dict[str, object]:
    return {
        "contents": [
            {
                "printXML": "<layout>"
                + "".join(f"<text><![CDATA[{line}]]></text>" for line in lines)
                + "</layout>",
            }
        ]
    }


def _package(items: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {
        "contents": [
            {
                "data": {
                    "packageItemDetail": items,
                    **extra,
                }
            }
        ]
    }


def test_business_print_xml_fingerprint_ignores_business_text_and_layout() -> None:
    integer_size = _xml("范33 帆布鞋，39")
    decimal_size_and_product_punctuation = _xml("范-74 运动鞋，40.5")
    different_xml_layout = {
        "contents": [
            {
                "printXML": (
                    "<layout><text>范74 运动鞋</text>"
                    "<line/><text>40.5</text><text>第二行商品</text></layout>"
                ),
            }
        ]
    }

    fingerprint = business_shape_fingerprint(integer_size, "cainiao-cnprint")
    assert fingerprint == business_shape_fingerprint(
        decimal_size_and_product_punctuation,
        "cainiao-cnprint",
    )
    assert fingerprint == business_shape_fingerprint(
        different_xml_layout,
        "cainiao-cnprint",
    )
    assert fingerprint != business_shape_fingerprint(
        integer_size,
        "cloud-print-client",
    )


def test_business_print_xml_fingerprint_ignores_newline_and_space_layout() -> None:
    space_layout = _xml_lines("范33 帆布鞋，42")
    space_layout_new_values = _xml_lines("范74 运动鞋，39")
    newline_layout = _xml_lines("范74\n运动鞋，39")

    assert business_shape_fingerprint(space_layout, "cainiao-cnprint") == (
        business_shape_fingerprint(space_layout_new_values, "cainiao-cnprint")
    )
    assert business_shape_fingerprint(space_layout, "cainiao-cnprint") == (
        business_shape_fingerprint(newline_layout, "cainiao-cnprint")
    )


def test_business_print_xml_fingerprint_ignores_quantity_unit_layout() -> None:
    one_piece = _xml_lines("范33 帆布鞋，42 1件")
    same_shape_new_values = _xml_lines("范74 运动鞋，39 2件")
    one_pair = _xml_lines("范74 运动鞋，39 1双")

    assert business_shape_fingerprint(one_piece, "cainiao-cnprint") == (
        business_shape_fingerprint(same_shape_new_values, "cainiao-cnprint")
    )
    assert business_shape_fingerprint(one_piece, "cainiao-cnprint") == (
        business_shape_fingerprint(one_pair, "cainiao-cnprint")
    )


def test_business_text_fingerprint_ignores_repeated_same_shape_lines() -> None:
    single_line = _xml_lines("范33 帆布鞋，42")
    repeated_same_shape_lines = _xml_lines("范74 运动鞋，39", "范88 休闲鞋，41")

    assert business_shape_fingerprint(single_line, "cainiao-cnprint") == (
        business_shape_fingerprint(repeated_same_shape_lines, "cainiao-cnprint")
    )
    assert grammar_signature_for_texts(["商品甲，42"]) == grammar_signature_for_texts(
        ["商品乙，39", "商品丙，41"]
    )


def test_business_package_fingerprint_ignores_values_counts_and_unrelated_keys() -> None:
    package_payload_a = _package(
        [{"itemName": "范33", "skuFullName": "白色 42", "itemNum": 1}]
    )
    package_payload_with_unrelated_keys = _package(
        [
            {"itemName": "范74", "skuFullName": "黑色 39", "itemNum": 2},
            {"itemName": "范88", "skuFullName": "红色 38", "itemNum": 3},
        ],
        receiverName="张三",
        internalTrace={"requestId": "hidden"},
    )

    assert business_shape_fingerprint(package_payload_a, "cainiao-cnprint") == (
        business_shape_fingerprint(package_payload_with_unrelated_keys, "cainiao-cnprint")
    )


def test_package_scalar_type_and_unknown_layout_do_not_collide() -> None:
    package = _package([{"itemName": "范33", "itemNum": 1}])
    item_num_as_text = _package([{"itemName": "范33", "itemNum": "1"}])
    unknown_a = {"data": {"shape": {"left": "x"}}}
    unknown_b = {"data": {"shape": ["x"]}}

    assert business_shape_fingerprint(package, "cainiao-cnprint") != (
        business_shape_fingerprint(item_num_as_text, "cainiao-cnprint")
    )
    unknown_a_fingerprint = business_shape_fingerprint(unknown_a, "unknown-printer")
    assert unknown_a_fingerprint.startswith("v2:UNKNOWN:sha256:")
    assert unknown_a_fingerprint != business_shape_fingerprint(unknown_b, "unknown-printer")


def test_catalog_inspection_and_strategy_keep_legacy_compatible() -> None:
    payload = _package([{"itemName": "范33", "itemNum": 1}])

    assert [item["code"] for item in fingerprint_catalog()] == [
        "CN-ITEM-INFO",
        "CN-PRINT-XML",
        "CN-CUSTOM-CONTENT",
        "CN-PACKAGE-ITEMS",
        "CLOUD-PRODUCT-INFO",
    ]
    assert inspect_fingerprint(payload, "cainiao-cnprint")["fingerprint_code"] == "CN-PACKAGE-ITEMS"
    assert fingerprint_for_payload(payload, "cainiao-cnprint", "legacy_structure_v1") == legacy_structural_fingerprint(
        payload, "cainiao-cnprint"
    )
    assert fingerprint_for_payload(payload, "cainiao-cnprint", "business_shape_v2") == business_shape_fingerprint(
        payload, "cainiao-cnprint"
    )
