import json
from types import SimpleNamespace

from app.api.routes.collector_runtime import recognition_report_export_rows
from app.services.product_recognition import recognize_detail_items
from app.services.waybill_parser import parse_cainiao_direct_shop, parse_douyin_cloud_print


def _record(raw_payload: dict, *, component: str, document_id: str = "DOC-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        tenant_id=1,
        workspace_id=1,
        task_id=2,
        source_component=component,
        source_index="1",
        document_id=document_id,
        raw_payload=json.dumps(raw_payload, ensure_ascii=False),
    )


def _product_rule(mode_code: str, *, product_id: int, keyword: str, fields: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        id=10,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=product_id,
        match_values={
            "mode_code": mode_code,
            "product_id": product_id,
            "keyword": keyword,
            "fields": fields,
            "sku_auto_match": True,
        },
    )


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self, _statement):
        return _ScalarResult(self._rows)


def _douyin_payload() -> dict:
    return {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "YT100",
                    "contents": [
                        {
                            "templateURL": "https://lf3-cm.ecombdstatic.com/obj/logistics-davinci/template/v2/yuantong_76_130.xml",
                            "data": {
                                "trackNo": "YT100",
                                "orderId": "ORDER-100",
                                "shopName": "抖店",
                                "templateCode": "DOUYIN-TPL-1",
                                "productInfo": "【现货新款】5.0 黑白紫 38.5 1 件",
                                "productShortInfo": "5.0 黑白紫 38.5 1 件",
                                "productCount": "1件",
                                "remark": "加急",
                            }
                        }
                    ],
                }
            ]
        },
    }


def test_douyin_direct_print_uses_structured_fields_without_template_guessing() -> None:
    parsed = parse_douyin_cloud_print(_record(_douyin_payload(), component="cloud-print-client", document_id="YT100"))

    assert len(parsed) == 1
    values = parsed[0].field_values
    assert values["template_code"] == "DOUYIN-TPL-1"
    assert values["print_template_key"] == "url:obj/logistics-davinci/template/v2/yuantong_76_130.xml"
    assert values["print_template_source"] == "douyin_template_url"
    assert values["custom_product_text"] == "【现货新款】5.0 黑白紫 38.5 1 件"
    assert values["custom_sales_attr1_text"] == "5.0 黑白紫"
    assert values["custom_sales_attr2_text"] == "38.5"
    assert values["custom_quantity_text"] == "1"
    assert values["custom_item_remark_text"] == "加急"


def test_douyin_direct_print_applies_product_info_template_for_sales_attrs() -> None:
    config = SimpleNamespace(
        id=31,
        template_key="custom:douyin:1",
        template_label="抖店模板",
        template_source="douyin_template_url",
        config={
            "template_match": {"source_template_key": "url:obj/logistics-davinci/template/v2/yuantong_76_130.xml"},
            "field_mappings": {
                "custom_sales_attr1_text": {"extractor": "sales_attr1_text", "segment_text": "样本旧属性不应固定"},
                "custom_sales_attr2_text": {"extractor": "sales_attr2_text", "segment_text": "样本旧尺码不应固定"},
            },
        },
    )
    parsed = parse_douyin_cloud_print(
        _record(_douyin_payload(), component="cloud-print-client", document_id="YT100"),
        _FakeDb([config]),
    )

    assert len(parsed) == 1
    values = parsed[0].field_values
    assert values["custom_product_text"] == "【现货新款】5.0 黑白紫 38.5 1 件"
    assert values["custom_sales_attr1_text"] == "5.0 黑白紫"
    assert values["custom_sales_attr2_text"] == "38.5"
    assert values["custom_quantity_text"] == "1"
    assert values["custom_item_remark_text"] == "加急"
    assert values["print_template_config_id"] == 31


def test_douyin_template_url_groups_different_shop_template_codes() -> None:
    payload = _douyin_payload()
    second_document = json.loads(json.dumps(payload["task"]["documents"][0], ensure_ascii=False))
    second_document["documentID"] = "YT101"
    second_document["contents"][0]["data"]["trackNo"] = "YT101"
    second_document["contents"][0]["data"]["templateCode"] = "SHOP-SPECIFIC-CODE"
    second_document["contents"][0]["data"]["shopName"] = "另一个店铺"
    payload["task"]["documents"].append(second_document)

    parsed = parse_douyin_cloud_print(_record(payload, component="cloud-print-client", document_id="YT100"))

    assert len(parsed) == 2
    assert {item.field_values["template_code"] for item in parsed} == {"DOUYIN-TPL-1", "SHOP-SPECIFIC-CODE"}
    assert {item.field_values["print_template_key"] for item in parsed} == {
        "url:obj/logistics-davinci/template/v2/yuantong_76_130.xml"
    }
    assert {item.field_values["print_template_source"] for item in parsed} == {"douyin_template_url"}


def test_douyin_direct_print_is_standardized_for_sku_report() -> None:
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "YT100",
                    "contents": [
                        {
                            "templateURL": "https://lf3-cm.ecombdstatic.com/obj/logistics-davinci/template/v2/yuantong_76_130.xml",
                            "data": {
                                "trackNo": "YT100",
                                "orderId": "ORDER-100",
                                "shopName": "抖店",
                                "productInfo": "【现货新款】5.0 黑白紫 38.5 1 件",
                                "productShortInfo": "5.0 黑白紫 38.5 1 件",
                                "productCount": "1件",
                            }
                        }
                    ],
                }
            ]
        },
    }

    config = SimpleNamespace(
        id=32,
        template_key="custom:douyin:report",
        template_label="抖店报表模板",
        template_source="douyin_template_url",
        config={
            "template_match": {"source_template_key": "url:obj/logistics-davinci/template/v2/yuantong_76_130.xml"},
            "field_mappings": {
                "custom_sales_attr1_text": {"segment_text": "黑白紫"},
                "custom_sales_attr2_text": {"segment_text": "38.5"},
            },
        },
    )
    parsed = parse_douyin_cloud_print(
        _record(payload, component="cloud-print-client", document_id="YT100"),
        _FakeDb([config]),
    )

    assert len(parsed) == 1
    values = parsed[0].field_values
    assert values["custom_product_text"] == "【现货新款】5.0 黑白紫 38.5 1 件"
    assert values["custom_sales_attr1_text"] == "5.0 黑白紫"
    assert values["custom_sales_attr2_text"] == "38.5"
    assert values["custom_quantity_text"] == "1"

    detail = SimpleNamespace(id=100, waybill_mode="douyin_cloud_print", field_values=values)
    rows = recognize_detail_items(
        [detail],
        [
            _product_rule(
                "douyin_cloud_print",
                product_id=5,
                keyword="现货新款",
                fields=["custom_product_text"],
            )
        ],
        [SimpleNamespace(id=5, name="5.0")],
        [SimpleNamespace(id=21, product_id=5, name="5.0黑白紫", code=None, keywords=None, image_asset_id=88)],
    )

    assert rows[0]["status"] == "matched"
    assert rows[0]["match_field"] in {"custom_sales_attr1_text", "spec_text"}
    assert recognition_report_export_rows(rows) == [["5.0", "黑白紫", "", "38.5", 1]]


def test_cainiao_direct_print_is_standardized_for_sku_report() -> None:
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "YT200",
                    "contents": [
                        {"encryptedData": "AES:encrypted"},
                        {
                            "data": {
                                "SHOP_NAME": "菜鸟店铺",
                                "ORDER_ID": "ORDER-200",
                                "WAIBILLNO_BAR_CODE": "YT200",
                                "ITEM_INFO": "4.0 中国红 42 *2",
                                "ITEM_TOTAL_COUNT": "2",
                                "BUYER_MEMO": "",
                                "SELLER_MEMO": "",
                            }
                        },
                    ],
                }
            ]
        },
    }

    parsed = parse_cainiao_direct_shop(_record(payload, component="cainiao-cnprint", document_id="YT200"), payload)

    assert len(parsed) == 1
    values = parsed[0].field_values
    assert values["custom_product_text"] == "4.0"
    assert values["custom_sales_attr1_text"] == "4.0 中国红"
    assert values["custom_sales_attr2_text"] == "42"
    assert values["custom_quantity_text"] == "2"

    detail = SimpleNamespace(id=200, waybill_mode="cainiao_direct_shop", field_values=values)
    rows = recognize_detail_items(
        [detail],
        [
            _product_rule(
                "cainiao_direct_shop",
                product_id=4,
                keyword="4.0",
                fields=["custom_product_text", "product_full_text"],
            )
        ],
        [SimpleNamespace(id=4, name="4.0")],
        [SimpleNamespace(id=41, product_id=4, name="4.0中国红", code=None, keywords=None, image_asset_id=None)],
    )

    assert rows[0]["status"] == "matched"
    assert rows[0]["match_field"] == "custom_sales_attr1_text"
    assert recognition_report_export_rows(rows) == [["4.0", "中国红", "", "42 42", 2]]
