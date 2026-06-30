from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from openpyxl import load_workbook

from app.api.routes.collector_runtime import (
    RECOGNITION_EXCEPTION_SHEET_TITLE,
    append_recognition_exception_sheet,
    append_xlsx_rows,
    recognition_exception_export_rows,
    recognition_report_export_rows,
    recognition_report_headers,
    recognition_report_line_items,
    recognition_report_workbook,
    recognition_report_rows_by_stall,
    recognition_rows_from_product_sku_linking_results,
    report_quantity_value,
)


def test_export_contract_consumes_product_sku_linking_results() -> None:
    details = [
        SimpleNamespace(
            id=101,
            field_values={
                "product_sku_linking_result": {
                    "match_status": "matched",
                    "product": "鞋款A",
                    "sku": "黑色42",
                    "image": {"id": 1, "name": "黑色图"},
                    "stall": {"id": 9, "name": "至尚"},
                    "standard_fields": {
                        "sales_attr1": "黑色",
                        "sales_attr2": "42",
                        "quantity": "2",
                        "remark": "加急",
                    },
                    "image_match_text": "鞋款A 黑色 42",
                },
            },
        ),
    ]

    rows = recognition_rows_from_product_sku_linking_results(details)

    assert rows[0]["status"] == "matched"
    assert rows[0]["stall_name"] == "至尚"
    assert rows[0]["stall_id"] == 9
    line_items = recognition_report_line_items(rows)
    assert line_items[0]["stall_name"] == "至尚"
    assert line_items[0]["image_label"] == "黑色图"
    assert recognition_report_rows_by_stall(line_items)["至尚"][0]["product_category"] == "鞋款A"
    assert recognition_report_export_rows(rows) == [["鞋款A", "黑色", "", "42", 2, "加急", "鞋款A 黑色 42"]]


def test_export_routes_non_matched_rows_to_exception_sheet() -> None:
    details = [
        SimpleNamespace(
            id=102,
            field_values={
                "product_sku_linking_result": {
                    "match_status": "product_unmatched",
                    "standard_fields": {
                        "product": "未维护鞋款",
                        "sales_attr1": "蓝色",
                        "sales_attr2": "41",
                        "quantity": "1",
                        "remark": "",
                    },
                    "image_match_text": "未维护鞋款 蓝色 41",
                    "exception_reason": "商品未命中",
                },
            },
        ),
    ]

    rows = recognition_rows_from_product_sku_linking_results(details)

    assert recognition_report_export_rows(rows) == []
    assert recognition_exception_export_rows(rows) == [["未维护鞋款 蓝色 41"]]


def test_export_routes_matched_rows_with_missing_sales_attrs_to_exception_sheet() -> None:
    details = [
        SimpleNamespace(
            id=103,
            field_values={
                "product_sku_linking_result": {
                    "match_status": "matched",
                    "product": "鞋款A",
                    "sku": "黑色图",
                    "image": {"id": 1, "name": "黑色图"},
                    "standard_fields": {
                        "sales_attr1": "5.0二代灰色",
                        "sales_attr2": "",
                        "quantity": "1",
                        "remark": "",
                    },
                    "image_match_text": "鞋款A 5.0二代灰色",
                },
            },
        ),
    ]

    rows = recognition_rows_from_product_sku_linking_results(details)

    assert recognition_report_export_rows(rows) == []
    assert recognition_exception_export_rows(rows) == [["鞋款A 5.0二代灰色"]]


def test_recognition_report_export_respects_sales_attr1_stack_layout() -> None:
    rows = [
        {
            "status": "matched",
            "product_name": "4.0",
            "product_id": 10,
            "sku_id": 20,
            "sku_image_asset_id": 30,
            "image_label": "4.0图",
            "sales_attr1_text": "二代灰白",
            "sales_attr2_text": "41",
            "quantity_text": "1",
            "item_count": 1,
            "remark_text": "",
            "image_match_text": "4.0 二代灰白 41",
        },
        {
            "status": "matched",
            "product_name": "4.0",
            "product_id": 10,
            "sku_id": 20,
            "sku_image_asset_id": 30,
            "image_label": "4.0图",
            "sales_attr1_text": "二代黑白",
            "sales_attr2_text": "42",
            "quantity_text": "1",
            "item_count": 1,
            "remark_text": "",
            "image_match_text": "4.0 二代黑白 42",
        },
    ]

    exported = recognition_report_export_rows(rows, {"stack_sales_attr1": True})

    assert len(exported) == 1
    assert exported[0][0] == "4.0"
    assert exported[0][1] == "二代灰白 二代黑白"
    assert exported[0][2] == ""
    assert exported[0][3] == "41 42"
    assert exported[0][4] == 2


def test_recognition_report_workbook_matches_preview_pixel_layout() -> None:
    report_rows = [
        {
            "product_category": "4.0",
            "stall_name": "1199",
            "spec": "二代灰白",
            "image_label": "4.0图",
            "sku_image_asset_id": 30,
            "size_text": "41",
            "quantity": 1,
            "remark_text": "",
            "image_match_text": "4.0 二代灰白 41",
        }
    ]
    layout = {
        "columns": [
            {"key": "product_name", "label": "商品", "visible": True, "width": 16},
            {"key": "sales_attr1", "label": "销售属性1", "visible": True, "width": 24},
            {"key": "sku_image", "label": "图片", "visible": True, "width": 18},
            {"key": "sales_attr2", "label": "销售属性2", "visible": True, "width": 18},
            {"key": "quantity", "label": "数量", "visible": True, "width": 12},
        ],
        "header_row_height": 32,
        "row_height": 120,
        "image_width": 96,
        "image_height": 96,
    }

    workbook = recognition_report_workbook(
        report_rows=report_rows,
        report_layout=layout,
        images_by_id={},
    )
    sheet = workbook.active

    assert sheet["C2"].value is None
    assert sheet.column_dimensions["C"].width == pytest.approx((18 * 9 - 5) / 7, abs=0.1)
    assert sheet.row_dimensions[1].height == pytest.approx(32 * 0.75, abs=0.1)
    assert sheet.row_dimensions[2].height == pytest.approx(120 * 0.75, abs=0.1)


def test_recognition_exception_sheet_is_created_even_when_empty() -> None:
    workbook = Workbook()
    sheet = workbook.active
    append_xlsx_rows(sheet, recognition_report_headers(), [["鞋款A", "黑色", "", "42", 1, "", "鞋款A 黑色 42"]])
    append_recognition_exception_sheet(workbook, [])
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    loaded = load_workbook(buffer)

    assert RECOGNITION_EXCEPTION_SHEET_TITLE in loaded.sheetnames
    assert loaded[RECOGNITION_EXCEPTION_SHEET_TITLE].max_row == 1


def test_report_quantity_value_accepts_common_text_formats() -> None:
    assert report_quantity_value("*2") == 2
    assert report_quantity_value("2件") == 2
    assert report_quantity_value("1") == 1
    assert report_quantity_value("") == 1


def test_recognition_report_headers_are_business_columns() -> None:
    assert recognition_report_headers() == ["商品", "销售属性1", "图片", "销售属性2", "数量", "备注", "图片匹配文本"]
