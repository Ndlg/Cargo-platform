from app.services.woda_printxml_parser import parse_woda_custom_structure


def _field_mapping(source_field_key: str) -> dict[str, str]:
    return {"source_field_key": source_field_key}


def _template_config(
    product_source: str = "custom_product_text",
    attr1_source: str = "custom_sales_attr1_text",
    attr2_source: str = "custom_sales_attr2_text",
    quantity_source: str = "custom_quantity_text",
    remark_source: str = "custom_item_remark_text",
) -> dict:
    return {
        "field_mappings": {
            "custom_product_text": _field_mapping(product_source),
            "custom_sales_attr1_text": _field_mapping(attr1_source),
            "custom_sales_attr2_text": _field_mapping(attr2_source),
            "custom_quantity_text": _field_mapping(quantity_source),
            "custom_item_remark_text": _field_mapping(remark_source),
        }
    }


def _line_mapping(line_index: int, extractor: str) -> dict[str, object]:
    return {"line_index": line_index, "extractor": extractor}


def _segment_mapping(line_index: int, extractor: str, segment_text: str) -> dict[str, object]:
    return {"line_index": line_index, "extractor": extractor, "segment_text": segment_text}


def test_template_segment_mapping_uses_user_selected_raw_text() -> None:
    text = "低帮黑色，42\n2026登山鞋越野低帮LOW复古户外休闲鞋机能男鞋女鞋防水耐磨ACG*1"
    config = {
        "field_mappings": {
            "custom_product_text": _segment_mapping(1, "product_text", "2026登山鞋越野低帮LOW复古户外休闲鞋机能男鞋女鞋防水耐磨ACG"),
            "custom_sales_attr1_text": _segment_mapping(0, "sales_attr1_text", "低帮黑色"),
            "custom_sales_attr2_text": _segment_mapping(0, "sales_attr2_text", "42"),
            "custom_quantity_text": _segment_mapping(1, "quantity_text", "1"),
        }
    }

    result = parse_woda_custom_structure(text, config)

    assert result["woda_structure_kind"] == "template_mapped"
    assert result["custom_product_text"] == "2026登山鞋越野低帮LOW复古户外休闲鞋机能男鞋女鞋防水耐磨ACG"
    assert result["custom_sales_attr1_text"] == "低帮黑色"
    assert result["custom_sales_attr2_text"] == "42"
    assert result["custom_quantity_text"] == "1"


def test_template_segment_mapping_does_not_reuse_sample_text_for_new_waybill() -> None:
    text = "5.0范52，,5.0灰绿，42*1"
    config = {
        "field_mappings": {
            "custom_product_text": _segment_mapping(0, "product_text", "4.0 秒9"),
            "custom_sales_attr1_text": _segment_mapping(0, "sales_attr1_text", "4.0二代黑灰"),
            "custom_sales_attr2_text": _segment_mapping(0, "sales_attr2_text", "41"),
            "custom_quantity_text": _segment_mapping(0, "quantity_text", "1"),
        }
    }

    result = parse_woda_custom_structure(text, config)

    assert result["woda_structure_kind"] == "template_mapped"
    assert result["custom_product_text"] == "5.0范52"
    assert result["custom_sales_attr1_text"] == "5.0灰绿"
    assert result["custom_sales_attr2_text"] == "42"
    assert result["custom_quantity_text"] == "1"


def test_template_segment_mapping_does_not_freeze_sample_values() -> None:
    text = "5.0 秒39 2，,5.0二代米色，38*1"
    config = {
        "field_mappings": {
            "custom_product_text": _segment_mapping(0, "product_text", "4.0 秒9"),
            "custom_sales_attr1_text": _segment_mapping(0, "sales_attr1_text", "4.0二代黑灰"),
            "custom_sales_attr2_text": _segment_mapping(0, "sales_attr2_text", "41"),
            "custom_quantity_text": _segment_mapping(0, "quantity_text", "1"),
        }
    }

    result = parse_woda_custom_structure(text, config)

    assert result["woda_structure_kind"] == "template_mapped"
    assert result["custom_product_text"] == "5.0 秒39 2"
    assert result["custom_sales_attr1_text"] == "5.0二代米色"
    assert result["custom_sales_attr2_text"] == "38"
    assert result["custom_quantity_text"] == "1"


def test_template_line_mapping_overrides_reverse_auto_structure() -> None:
    text = "低帮黑色，42\n2026登山鞋越野低帮LOW复古户外休闲鞋机能男鞋女鞋防水耐磨ACG*1"
    config = {
        "field_mappings": {
            "custom_product_text": _line_mapping(1, "product_text"),
            "custom_sales_attr1_text": _line_mapping(0, "sales_attr1_text"),
            "custom_sales_attr2_text": _line_mapping(0, "sales_attr2_text"),
            "custom_quantity_text": _line_mapping(1, "quantity_text"),
        }
    }

    result = parse_woda_custom_structure(text, config)

    assert result["woda_structure_kind"] == "template_mapped"
    assert result["custom_product_text"] == "2026登山鞋越野低帮LOW复古户外休闲鞋机能男鞋女鞋防水耐磨ACG"
    assert result["custom_sales_attr1_text"] == "低帮黑色"
    assert result["custom_sales_attr2_text"] == "42"
    assert result["custom_quantity_text"] == "1"


def test_template_line_mapping_reads_standalone_quantity_line() -> None:
    text = "【牛货】春秋网面透气训练C5男女款户外运动鞋百搭休闲联合跑步鞋\nC5-黑白\n41\n1"
    config = {
        "field_mappings": {
            "custom_product_text": _line_mapping(0, "product_text"),
            "custom_sales_attr1_text": _line_mapping(1, "text"),
            "custom_sales_attr2_text": _line_mapping(2, "sales_attr2_text"),
            "custom_quantity_text": _line_mapping(3, "quantity_text"),
        }
    }

    result = parse_woda_custom_structure(text, config)

    assert result["woda_structure_kind"] == "template_mapped"
    assert result["custom_product_text"] == "【牛货】春秋网面透气训练C5男女款户外运动鞋百搭休闲联合跑步鞋"
    assert result["custom_sales_attr1_text"] == "C5-黑白"
    assert result["custom_sales_attr2_text"] == "41"
    assert result["custom_quantity_text"] == "1"


def test_parse_woda_custom_structure_expands_repeated_items() -> None:
    text = (
        "5.0\u830351\uff0c5.0\u4e8c\u4ee3\u7687\u5bb6\u84dd\uff0c44*1\n"
        "5.0\u830351\uff0c5.0\u4e8c\u4ee3\u7eff\u8272\uff0c39*1"
    )

    result = parse_woda_custom_structure(text, _template_config())

    assert result["woda_item_count"] == 2
    assert result["custom_items"][0]["product_text"] == "5.0\u830351"
    assert result["custom_items"][0]["sales_attr1_text"] == "5.0\u4e8c\u4ee3\u7687\u5bb6\u84dd"
    assert result["custom_items"][0]["sales_attr2_text"] == "44"
    assert result["custom_items"][0]["quantity_text"] == "1"
    assert result["custom_items"][1]["sales_attr1_text"] == "5.0\u4e8c\u4ee3\u7eff\u8272"
    assert result["custom_items"][1]["sales_attr2_text"] == "39"


def test_parse_woda_custom_structure_expands_repeated_reverse_items() -> None:
    text = (
        "5.0二代灰色，40\n"
        "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1\n"
        "5.0二代灰黑，40\n"
        "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1"
    )

    result = parse_woda_custom_structure(text, _template_config())

    assert result["woda_item_count"] == 2
    assert result["custom_items"][0]["product_text"] == "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身"
    assert result["custom_items"][0]["sales_attr1_text"] == "5.0二代灰色"
    assert result["custom_items"][0]["sales_attr2_text"] == "40"
    assert result["custom_items"][0]["quantity_text"] == "1"
    assert result["custom_items"][1]["product_text"] == "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身"
    assert result["custom_items"][1]["sales_attr1_text"] == "5.0二代灰黑"
    assert result["custom_items"][1]["sales_attr2_text"] == "40"
    assert result["custom_items"][1]["quantity_text"] == "1"


def test_template_field_mapping_is_applied_to_every_item() -> None:
    text = (
        "\u79d247 AC\uff0c\u4f4e\u5e2e\u9ed1\u8272\uff0c40*2\n"
        "\u79d247 AC\uff0c\u4f4e\u5e2e\u9ed1\u8272\uff0c41*2"
    )
    config = _template_config(
        product_source="custom_sales_attr1_text",
        attr1_source="custom_product_text",
    )

    result = parse_woda_custom_structure(text, config)

    assert result["woda_item_count"] == 2
    assert result["custom_items"][0]["product_text"] == "\u4f4e\u5e2e\u9ed1\u8272"
    assert result["custom_items"][0]["sales_attr1_text"] == "\u79d247 AC"
    assert result["custom_items"][0]["sales_attr2_text"] == "40"
    assert result["custom_items"][1]["product_text"] == "\u4f4e\u5e2e\u9ed1\u8272"
    assert result["custom_items"][1]["sales_attr1_text"] == "\u79d247 AC"
    assert result["custom_items"][1]["sales_attr2_text"] == "41"


def test_default_standard_line_uses_named_remark_attributes() -> None:
    text = (
        "\u79d267 175\uff0c\u9ed8\u8ba4\uff0c\u9ed8\u8ba4*1\n"
        "\u989c\u8272\u5206\u7c7b:Cloudtilt\u8054\u540d2\u4ee3\u767d\u9ed1;\u978b\u7801:42"
    )

    result = parse_woda_custom_structure(text, _template_config())

    item = result["custom_items"][0]
    assert item["product_text"] == "\u79d267 175"
    assert item["sales_attr1_text"] == "Cloudtilt\u8054\u540d2\u4ee3\u767d\u9ed1"
    assert item["sales_attr2_text"] == "42"
    assert item["quantity_text"] == "1"
    assert "Cloudtilt" in item["remark_text"]


def test_named_remark_attributes_override_noisy_standard_attributes() -> None:
    text = (
        "\u79d267 175\uff0c\u65e0\u5173\u5b57\u6bb5\uff0c\u9ed8\u8ba4*1\n"
        "\u989c\u8272\u5206\u7c7b:Cloudtilt\u767d\u7ea2 \u86c7\u5e74\u9650\u5b9a;\u978b\u7801:36"
    )

    result = parse_woda_custom_structure(text, _template_config())

    item = result["custom_items"][0]
    assert item["product_text"] == "\u79d267 175"
    assert item["sales_attr1_text"] == "Cloudtilt\u767d\u7ea2 \u86c7\u5e74\u9650\u5b9a"
    assert item["sales_attr2_text"] == "36"
    assert item["quantity_text"] == "1"


def test_named_remark_size_overrides_noisy_standard_size() -> None:
    text = (
        "\u79d267 175\uff0c\u9ed8\u8ba4\uff0c\u4e71\u7801*1\n"
        "\u989c\u8272\u5206\u7c7b:Cloudtilt\u767d\u7ea2 \u86c7\u5e74\u9650\u5b9a;\u978b\u7801:37"
    )

    result = parse_woda_custom_structure(text, _template_config())

    item = result["custom_items"][0]
    assert item["product_text"] == "\u79d267 175"
    assert item["sales_attr1_text"] == "Cloudtilt\u767d\u7ea2 \u86c7\u5e74\u9650\u5b9a"
    assert item["sales_attr2_text"] == "37"
    assert item["quantity_text"] == "1"


def test_color_remark_does_not_replace_product_line() -> None:
    text = (
        "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋，,*1\n"
        "颜色分类:全黑;尺码:42.5"
    )

    result = parse_woda_custom_structure(text, _template_config())

    item = result["custom_items"][0]
    assert item["product_text"] == "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋"
    assert item["sales_attr1_text"] == "全黑"
    assert item["sales_attr2_text"] == "42.5"
    assert item["quantity_text"] == "1"
    assert item["remark_text"] == "颜色分类:全黑;尺码:42.5"
