from types import SimpleNamespace

from app.api.routes.collector_runtime import export_field_value, standard_detail_export_rows


def test_standard_detail_export_rows_expands_custom_items() -> None:
    detail = SimpleNamespace(
        field_values={
            "raw_record_id": 118,
            "raw_document_id": "woda-118",
            "custom_area_raw_text": "秒67 175，,默认，默认*1\n颜色分类:Cloudtilt灰红;鞋码:44",
            "custom_items": [
                {
                    "product_text": "秒67 175",
                    "sales_attr1_text": "默认",
                    "sales_attr2_text": "默认",
                    "quantity_text": "1",
                    "remark_text": "颜色分类:Cloudtilt灰红;鞋码:44",
                    "raw_text": "秒67 175，,默认，默认*1\n颜色分类:Cloudtilt灰红;鞋码:44",
                },
                {
                    "product_text": "秒67 175",
                    "sales_attr1_text": "默认",
                    "sales_attr2_text": "42",
                    "quantity_text": "1",
                    "remark_text": "颜色分类:Cloudtilt联名2代白黑;鞋码:42",
                    "raw_text": "秒67 175，,默认，42*1\n颜色分类:Cloudtilt联名2代白黑;鞋码:42",
                },
            ],
        }
    )

    rows = standard_detail_export_rows(detail)

    assert len(rows) == 2
    assert rows[0]["custom_item_index"] == 1
    assert rows[0]["custom_item_count"] == 2
    assert rows[0]["custom_product_text"] == "秒67 175"
    assert rows[0]["custom_sales_attr1_text"] == "默认"
    assert rows[0]["custom_sales_attr2_text"] == "默认"
    assert rows[0]["custom_quantity_text"] == "1"
    assert rows[0]["custom_item_remark_text"] == "颜色分类:Cloudtilt灰红;鞋码:44"
    assert rows[1]["custom_item_index"] == 2
    assert rows[1]["custom_sales_attr2_text"] == "42"
    assert rows[1]["custom_item_remark_text"] == "颜色分类:Cloudtilt联名2代白黑;鞋码:42"

    assert export_field_value("product_display_text", rows[0]) == "秒67 175"
    assert export_field_value("quantity", rows[0]) == "1"
    assert export_field_value("custom_spec_text", {"custom_sales_attr1_text": "默认"}) == "默认"
    assert export_field_value("custom_size_text", {"custom_sales_attr2_text": "42"}) == "42"


def test_standard_detail_export_rows_does_not_copy_total_quantity_to_items() -> None:
    detail = SimpleNamespace(
        field_values={
            "raw_record_id": 119,
            "raw_document_id": "douyin-119",
            "quantity": 3,
            "custom_items": [
                {
                    "product_text": "鞋A",
                    "sales_attr1_text": "黑灰",
                    "sales_attr2_text": "42",
                    "quantity_text": "",
                },
                {
                    "product_text": "鞋B",
                    "sales_attr1_text": "黑紫",
                    "sales_attr2_text": "43",
                    "quantity_text": "",
                },
            ],
        }
    )

    rows = standard_detail_export_rows(detail)

    assert len(rows) == 2
    assert [row["quantity"] for row in rows] == ["", ""]
    assert [row["custom_quantity_text"] for row in rows] == ["", ""]
