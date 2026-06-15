from types import SimpleNamespace

from app.api.routes.collector_runtime import (
    RECOGNITION_EXCEPTION_HEADERS,
    RECOGNITION_REPORT_HEADERS,
    enriched_recognition_rules,
    recognition_exception_export_rows,
    recognition_report_export_rows,
    recognition_report_headers,
    recognition_report_line_items,
    recognition_report_rows_by_stall,
)


def test_recognition_report_rows_group_matched_items_for_delivery_report() -> None:
    rows = [
        {
            "source_label": "面单 13-1",
            "source_platform": "woda",
            "logistics_no": "YT001",
            "order_no": "ORDER001",
            "item_index": 1,
            "item_count": 2,
            "print_template_config_label": "模板1",
            "product_text": "5.0范51",
            "sales_attr1_text": "5.0二代皇家蓝",
            "sales_attr2_text": "44",
            "quantity_text": "1",
            "product_name": "5.0",
            "product_id": 5,
            "sku_id": 21,
            "sku_name": "5.0二代皇家蓝",
            "sku_image_asset_id": 88,
            "status": "matched",
            "match_type": "auto",
            "match_field": "custom_sales_attr1_text",
            "match_keyword": "5.0二代皇家蓝",
            "reason": "SKU 已通过销售属性自动命中。",
            "remark_text": "",
        },
        {
            "source_label": "面单 14-1",
            "source_platform": "woda",
            "logistics_no": "YT002",
            "order_no": "ORDER002",
            "item_index": 1,
            "item_count": 1,
            "print_template_config_label": "模板1",
            "product_text": "5.0范51",
            "sales_attr1_text": "5.0二代皇家蓝",
            "sales_attr2_text": "39",
            "quantity_text": "1",
            "product_name": "5.0",
            "product_id": 5,
            "sku_id": 21,
            "sku_name": "5.0二代皇家蓝",
            "sku_image_asset_id": 88,
            "status": "matched",
            "match_type": "auto",
            "match_field": "custom_sales_attr1_text",
            "match_keyword": "5.0二代皇家蓝",
            "reason": "SKU 已通过销售属性自动命中。",
            "remark_text": "",
        },
        {
            "source_label": "面单 13-2",
            "source_platform": "douyin",
            "item_index": 2,
            "item_count": 2,
            "print_template_config_label": "模板1",
            "product_text": "未知商品",
            "sales_attr1_text": "默认",
            "sales_attr2_text": "39",
            "quantity_text": "1",
            "product_name": "",
            "sku_name": "",
            "status": "product_unmatched",
            "match_type": "",
            "match_field": "",
            "match_keyword": "",
            "reason": "商品名称没有命中任何规则。",
            "remark_text": "买家备注",
        },
    ]

    layout = {"stack_sales_attr1": True}
    exported = recognition_report_export_rows(rows, layout)
    line_items = recognition_report_line_items(rows, layout)
    exceptions = recognition_exception_export_rows(rows)

    assert RECOGNITION_REPORT_HEADERS == ["商品名称", "销售属性1", "SKU图片", "销售属性2", "数量"]
    assert RECOGNITION_EXCEPTION_HEADERS[0] == "面单"
    assert len(exported) == 1
    assert exported[0] == ["5.0", "二代皇家蓝", "", "39 44", 2]
    assert line_items[0]["sku_image_asset_id"] == 88
    assert exceptions[0][0] == "面单 13-2"
    assert exceptions[0][5] == "商品未命中"
    assert exceptions[0][6] == "商品名称没有命中任何规则。"


def test_recognition_report_splits_source_items_when_sales_attr1_grouping_is_off() -> None:
    rows = [
        {
            "detail_id": 1,
            "candidate_key": "1:1",
            "source_label": "waybill 1",
            "item_index": 1,
            "item_count": 2,
            "product_text": "product text",
            "sales_attr1_text": "black",
            "sales_attr2_text": "42",
            "quantity_text": "2",
            "product_name": "Product",
            "product_id": 1,
            "sku_id": 10,
            "sku_name": "Product black",
            "sku_image_asset_id": None,
            "status": "matched",
            "reason": "",
            "remark_text": "",
        },
        {
            "detail_id": 1,
            "candidate_key": "1:2",
            "source_label": "waybill 1",
            "item_index": 2,
            "item_count": 2,
            "product_text": "product text",
            "sales_attr1_text": "black",
            "sales_attr2_text": "43",
            "quantity_text": "1",
            "product_name": "Product",
            "product_id": 1,
            "sku_id": 10,
            "sku_name": "Product black",
            "sku_image_asset_id": None,
            "status": "matched",
            "reason": "",
            "remark_text": "",
        },
    ]

    assert recognition_report_export_rows(rows) == [
        ["Product", "black", "", "42 42", 2],
        ["Product", "black", "", "43", 1],
    ]
    assert recognition_report_export_rows(rows, {"stack_sales_attr1": True}) == [
        ["Product", "black", "", "42 42 43", 3],
    ]


def test_recognition_report_leaves_image_cell_blank_when_sku_has_no_image() -> None:
    rows = [
        {
            "source_label": "waybill 1",
            "item_index": 1,
            "item_count": 1,
            "product_text": "product text",
            "sales_attr1_text": "black",
            "sales_attr2_text": "42",
            "quantity_text": "1",
            "product_name": "Product",
            "product_id": 1,
            "sku_id": 10,
            "sku_name": "Product black",
            "sku_image_asset_id": None,
            "status": "matched",
            "reason": "",
            "remark_text": "",
        },
    ]

    exported = recognition_report_export_rows(rows)
    line_items = recognition_report_line_items(rows)
    exceptions = recognition_exception_export_rows(rows)

    assert exported == [["Product", "black", "", "42", 1]]
    assert line_items[0]["sku_id"] == 10
    assert line_items[0]["sku_image_asset_id"] is None
    assert exceptions == []


def test_recognition_report_layout_can_reorder_and_hide_columns() -> None:
    rows = [
        {
            "source_label": "waybill 1",
            "item_index": 1,
            "item_count": 1,
            "product_text": "product text",
            "sales_attr1_text": "black",
            "sales_attr2_text": "42",
            "quantity_text": "2",
            "product_name": "Product",
            "product_id": 1,
            "sku_id": 10,
            "sku_name": "Product black",
            "sku_image_asset_id": None,
            "status": "matched",
            "reason": "",
            "remark_text": "",
        },
    ]
    layout = {
        "columns": [
            {"key": "sales_attr1", "label": "规格", "visible": True, "width": 18},
            {"key": "product_name", "label": "货号分类", "visible": True, "width": 16},
            {"key": "quantity", "label": "数量", "visible": True, "width": 10},
            {"key": "sku_image", "label": "图片", "visible": False, "width": 18},
            {"key": "sales_attr2", "label": "尺码", "visible": False, "width": 12},
        ],
        "row_height": 40,
        "image_width": 80,
        "image_height": 80,
    }

    assert recognition_report_headers(layout) == ["规格", "货号分类", "数量"]
    assert recognition_report_export_rows(rows, layout) == [["black", "Product", 2]]


def test_recognition_report_can_keep_stalls_separate() -> None:
    rows = [
        {
            "detail_id": 1,
            "candidate_key": "1:1",
            "source_label": "waybill 1",
            "item_index": 1,
            "item_count": 1,
            "product_text": "product text",
            "sales_attr1_text": "black",
            "sales_attr2_text": "42",
            "quantity_text": "1",
            "product_name": "Product",
            "product_id": 1,
            "sku_id": 10,
            "sku_name": "Product black",
            "sku_image_asset_id": None,
            "stall_id": 1,
            "stall_name": "八月",
            "status": "matched",
            "reason": "",
            "remark_text": "",
        },
        {
            "detail_id": 2,
            "candidate_key": "2:1",
            "source_label": "waybill 2",
            "item_index": 1,
            "item_count": 1,
            "product_text": "product text",
            "sales_attr1_text": "black",
            "sales_attr2_text": "43",
            "quantity_text": "1",
            "product_name": "Product",
            "product_id": 1,
            "sku_id": 10,
            "sku_name": "Product black",
            "sku_image_asset_id": None,
            "stall_id": 2,
            "stall_name": "至尚",
            "status": "matched",
            "reason": "",
            "remark_text": "",
        },
    ]
    layout = {
        "columns": [
            {"key": "stall_name", "label": "档口", "visible": True, "width": 14},
            {"key": "product_name", "label": "商品", "visible": True, "width": 16},
            {"key": "sales_attr1", "label": "规格", "visible": True, "width": 18},
            {"key": "sku_image", "label": "图片", "visible": False, "width": 18},
            {"key": "sales_attr2", "label": "尺码", "visible": True, "width": 12},
            {"key": "quantity", "label": "数量", "visible": True, "width": 10},
        ],
        "stack_sales_attr1": True,
    }

    line_items = recognition_report_line_items(rows, layout)

    assert recognition_report_headers(layout) == ["档口", "商品", "规格", "尺码", "数量"]
    assert recognition_report_export_rows(rows, layout) == [
        ["八月", "Product", "black", "42", 1],
        ["至尚", "Product", "black", "43", 1],
    ]
    assert {stall: len(stall_rows) for stall, stall_rows in recognition_report_rows_by_stall(line_items).items()} == {
        "八月": 1,
        "至尚": 1,
    }


def test_recognition_report_repeats_and_sorts_sales_attr2_by_default() -> None:
    rows = [
        {
            "source_label": "waybill 1",
            "item_index": 1,
            "item_count": 1,
            "product_text": "product text",
            "sales_attr1_text": "green",
            "sales_attr2_text": "42",
            "quantity_text": "2",
            "product_name": "Product",
            "product_id": 1,
            "sku_id": 10,
            "sku_name": "Product green",
            "sku_image_asset_id": None,
            "status": "matched",
            "reason": "",
            "remark_text": "",
        },
        {
            "source_label": "waybill 2",
            "item_index": 1,
            "item_count": 1,
            "product_text": "product text",
            "sales_attr1_text": "green",
            "sales_attr2_text": "37.5",
            "quantity_text": "1",
            "product_name": "Product",
            "product_id": 1,
            "sku_id": 10,
            "sku_name": "Product green",
            "sku_image_asset_id": None,
            "status": "matched",
            "reason": "",
            "remark_text": "",
        },
    ]

    assert recognition_report_export_rows(rows, {"stack_sales_attr1": True}) == [["Product", "green", "", "37.5 42 42", 3]]
    assert recognition_report_export_rows(rows, {"stack_sales_attr1": True, "stack_sales_attr2": True}) == [
        ["Product", "green", "", "37.5 42", 3],
    ]


def test_enriched_rules_can_recover_fingerprint_from_deleted_template_config() -> None:
    rule = SimpleNamespace(
        id=1,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=5,
        target_name="5.0",
        match_values={
            "print_template_config_id": 1,
            "print_template_key": "custom:4:old",
            "keyword": "5.0",
        },
    )
    deleted_config = SimpleNamespace(
        id=1,
        template_key="custom:4:old",
        template_label="旧模板",
        config={
            "template_match": {
                "source_template_key": "printxml:c863897829c2",
            },
        },
        is_deleted=True,
    )

    enriched = enriched_recognition_rules([rule], [deleted_config])

    assert enriched[0].match_values["print_template_source_key"] == "printxml:c863897829c2"
