from types import SimpleNamespace

from app.services.product_recognition import (
    recognize_detail_items,
    recognition_summary,
    template_constraint_matches,
)


def rule(
    rule_id: int,
    *,
    product_id: int,
    sku_id: int,
    product_keyword: str,
    sku_keyword: str,
    fallback_keyword: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=rule_id,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="sku",
        target_id=sku_id,
        match_values={
            "mode_code": "cainiao_woda_printxml",
            "print_template_config_id": 5,
            "product_id": product_id,
            "product_field": "custom_product_text",
            "product_keyword": product_keyword,
            "sku_id": sku_id,
            "sku_field": "custom_sales_attr1_text",
            "sku_fallback_field": "custom_item_remark_text",
            "sku_fallback_keyword": fallback_keyword,
            "keyword": sku_keyword,
        },
    )


def test_recognize_woda_items_matches_primary_and_fallback_sku() -> None:
    detail = SimpleNamespace(
        id=118,
        waybill_mode="cainiao_woda_printxml",
        field_values={
            "capture_task_id": 6,
            "print_template_config_id": 5,
            "custom_items": [
                {
                    "product_text": "秒67 175",
                    "sales_attr1_text": "基础款",
                    "sales_attr2_text": "42",
                    "quantity_text": "1",
                    "remark_text": "颜色分类:tilt 灰白色;尺码:42",
                },
                {
                    "product_text": "秒67 175",
                    "sales_attr1_text": "默认",
                    "sales_attr2_text": "默认",
                    "quantity_text": "1",
                    "remark_text": "颜色分类:Cloudtilt联名2代白黑;鞋码:42",
                },
            ],
        },
    )
    products = [SimpleNamespace(id=10, name="昂跑")]
    skus = [
        SimpleNamespace(id=20, product_id=10, name="默认款"),
        SimpleNamespace(id=21, product_id=10, name="联名白黑"),
    ]
    rules = [
        rule(1, product_id=10, sku_id=20, product_keyword="秒67 175", sku_keyword="基础款"),
        rule(
            2,
            product_id=10,
            sku_id=21,
            product_keyword="秒67 175",
            sku_keyword="联名白黑",
            fallback_keyword="Cloudtilt联名2代白黑",
        ),
    ]

    rows = recognize_detail_items([detail], rules, products, skus)

    assert len(rows) == 2
    assert rows[0]["status"] == "matched"
    assert rows[0]["sku_id"] == 20
    assert rows[0]["match_type"] == "primary"
    assert rows[1]["status"] == "matched"
    assert rows[1]["sku_id"] == 21
    assert rows[1]["match_type"] == "fallback"
    assert rows[1]["match_field"] == "custom_item_remark_text"
    assert recognition_summary(rows)["fallback_matched"] == 1


def test_recognize_woda_item_reports_product_conflict() -> None:
    detail = SimpleNamespace(
        id=119,
        waybill_mode="cainiao_woda_printxml",
        field_values={
            "capture_task_id": 6,
            "print_template_config_id": 5,
            "custom_product_text": "秒67 175",
            "custom_sales_attr1_text": "默认",
            "custom_quantity_text": "1",
        },
    )
    products = [SimpleNamespace(id=10, name="昂跑"), SimpleNamespace(id=11, name="牛货")]
    skus = [
        SimpleNamespace(id=20, product_id=10, name="默认款"),
        SimpleNamespace(id=30, product_id=11, name="默认款"),
    ]
    rules = [
        rule(1, product_id=10, sku_id=20, product_keyword="秒67 175", sku_keyword="默认"),
        rule(2, product_id=11, sku_id=30, product_keyword="秒67 175", sku_keyword="默认"),
    ]

    rows = recognize_detail_items([detail], rules, products, skus)

    assert rows[0]["status"] == "conflict"
    assert "多个商品" in rows[0]["reason"]


def test_product_exact_keyword_beats_short_size_keyword_conflict() -> None:
    detail = SimpleNamespace(
        id=107,
        waybill_mode="douyin_cloud_print",
        field_values={
            "capture_task_id": 6,
            "product_short_text": "新款跑步鞋鞋子男鞋女鞋20",
            "product_full_text": "新款跑步鞋鞋子男鞋女鞋20",
            "custom_product_text": "VAP",
            "custom_sales_attr1_text": "一代黑金 金标",
            "custom_sales_attr2_text": "38",
            "quantity": 1,
        },
    )
    products = [SimpleNamespace(id=8, name="8"), SimpleNamespace(id=20, name="VAP")]
    rules = [
        SimpleNamespace(
            id=101,
            priority=100,
            is_enabled=True,
            is_deleted=False,
            target_type="product",
            target_id=8,
            match_values={
                "mode_code": "douyin_cloud_print",
                "product_id": 8,
                "keyword": "8",
                "fields": ["custom_product_text", "custom_sales_attr2_text"],
            },
        ),
        SimpleNamespace(
            id=102,
            priority=100,
            is_enabled=True,
            is_deleted=False,
            target_type="product",
            target_id=20,
            match_values={
                "mode_code": "douyin_cloud_print",
                "product_id": 20,
                "keyword": "VAP",
                "fields": ["custom_product_text", "custom_sales_attr2_text"],
            },
        ),
    ]

    rows = recognize_detail_items([detail], rules, products, [])

    assert rows[0]["status"] == "sku_unmatched"
    assert rows[0]["product_id"] == 20
    assert rows[0]["product_name"] == "VAP"
    assert rows[0]["match_keyword"] == "VAP"


def test_product_exact_name_beats_longer_product_names_with_same_keyword() -> None:
    detail = SimpleNamespace(
        id=108,
        waybill_mode="douyin_cloud_print",
        field_values={
            "capture_task_id": 6,
            "product_short_text": "新款跑步鞋鞋子男鞋女鞋20",
            "product_full_text": "新款跑步鞋鞋子男鞋女鞋20",
            "custom_product_text": "VAP",
            "custom_sales_attr1_text": "一代黑金 金标",
            "custom_sales_attr2_text": "38",
            "quantity": 1,
        },
    )
    products = [
        SimpleNamespace(id=10, name="范5 VAP"),
        SimpleNamespace(id=11, name="范6 VAP"),
        SimpleNamespace(id=13, name="VAP"),
    ]
    rules = [
        SimpleNamespace(
            id=110 + product.id,
            priority=100,
            is_enabled=True,
            is_deleted=False,
            target_type="product",
            target_id=product.id,
            target_name=product.name,
            match_values={
                "mode_code": "douyin_cloud_print",
                "product_id": product.id,
                "product_name": product.name,
                "keyword": "VAP",
                "fields": ["custom_product_text"],
            },
        )
        for product in products
    ]

    rows = recognize_detail_items([detail], rules, products, [])

    assert rows[0]["status"] == "sku_unmatched"
    assert rows[0]["product_id"] == 13
    assert rows[0]["product_name"] == "VAP"


def test_recognize_legacy_woda_product_rule_matches_product_only() -> None:
    detail = SimpleNamespace(
        id=120,
        waybill_mode="cainiao_woda_printxml",
        field_values={
            "capture_task_id": 6,
            "print_template_config_id": 5,
            "custom_product_text": "4.0 秒31",
            "custom_sales_attr1_text": "4.0二代灰白",
            "custom_sales_attr2_text": "39",
        },
    )
    products = [SimpleNamespace(id=5, name="4.0")]
    legacy_rule = SimpleNamespace(
        id=6,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=5,
        match_values={
            "mode_code": "cainiao_woda_printxml",
            "print_template_config_id": 5,
            "keyword": "4.0 秒31",
            "fields": ["custom_product_text"],
        },
    )

    rows = recognize_detail_items([detail], [legacy_rule], products, [])

    assert rows[0]["product_id"] == 5
    assert rows[0]["product_name"] == "4.0"
    assert rows[0]["status"] == "sku_unmatched"


def test_product_rule_auto_matches_sku_from_sales_attributes() -> None:
    detail = SimpleNamespace(
        id=121,
        waybill_mode="cainiao_woda_printxml",
        field_values={
            "capture_task_id": 6,
            "print_template_config_id": 5,
            "custom_product_text": "5.0 秒38",
            "custom_sales_attr1_text": "5.0黑白蓝",
            "custom_sales_attr2_text": "37.5",
            "custom_quantity_text": "1",
        },
    )
    products = [SimpleNamespace(id=10, name="5.0")]
    skus = [
        SimpleNamespace(id=20, product_id=10, name="5.0黑白", code=None, keywords=None),
        SimpleNamespace(id=21, product_id=10, name="5.0黑白蓝", code=None, keywords=None),
    ]
    product_rule = SimpleNamespace(
        id=7,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=10,
        match_values={
            "mode_code": "cainiao_woda_printxml",
            "print_template_config_id": 5,
            "product_id": 10,
            "product_keyword": "5.0 秒38",
            "keyword": "5.0 秒38",
            "fields": ["custom_product_text"],
            "sku_auto_match": True,
        },
    )

    rows = recognize_detail_items([detail], [product_rule], products, skus)

    assert rows[0]["status"] == "matched"
    assert rows[0]["product_id"] == 10
    assert rows[0]["sku_id"] == 21
    assert rows[0]["match_type"] == "auto"
    assert rows[0]["match_field"] == "custom_sales_attr1_text"


def test_product_rule_auto_matches_structured_sku_from_short_text() -> None:
    detail = SimpleNamespace(
        id=122,
        waybill_mode="douyin_cloud_print",
        field_values={
            "capture_task_id": 6,
            "product_full_text": "2025新款网面女鞋男鞋情侣透气跑步鞋",
            "product_short_text": "5.0二代灰色 42 *1",
            "quantity": 1,
        },
    )
    products = [SimpleNamespace(id=10, name="5.0", stall_id=5)]
    skus = [
        SimpleNamespace(id=20, product_id=10, name="5.0二代绿色", code=None, keywords=None, image_asset_id=None),
        SimpleNamespace(id=21, product_id=10, name="5.0二代灰色", code=None, keywords=None, image_asset_id=88, stall_id=7),
    ]
    product_rule = SimpleNamespace(
        id=8,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=10,
        match_values={
            "mode_code": "douyin_cloud_print",
            "product_id": 10,
            "keyword": "2025新款网面女鞋男鞋情侣透气跑步鞋",
            "fields": ["product_full_text", "product_short_text"],
            "sku_auto_match": True,
        },
    )

    rows = recognize_detail_items([detail], [product_rule], products, skus)

    assert rows[0]["status"] == "matched"
    assert rows[0]["sku_id"] == 21
    assert rows[0]["sku_image_asset_id"] == 88
    assert rows[0]["stall_id"] == 7
    assert rows[0]["match_type"] == "auto"
    assert rows[0]["match_field"] == "product_short_text"


def test_recognition_uses_product_stall_when_sku_has_no_override() -> None:
    detail = SimpleNamespace(
        id=124,
        waybill_mode="douyin_cloud_print",
        field_values={
            "capture_task_id": 6,
            "product_full_text": "2025新款网面女鞋男鞋情侣透气跑步鞋",
            "product_short_text": "5.0二代灰色 42 *1",
            "quantity": 1,
        },
    )
    products = [SimpleNamespace(id=10, name="5.0", stall_id=5)]
    skus = [
        SimpleNamespace(id=21, product_id=10, name="5.0二代灰色", code=None, keywords=None, image_asset_id=88),
    ]
    product_rule = SimpleNamespace(
        id=12,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=10,
        match_values={
            "mode_code": "douyin_cloud_print",
            "product_id": 10,
            "keyword": "2025新款网面女鞋男鞋情侣透气跑步鞋",
            "fields": ["product_full_text", "product_short_text"],
            "sku_auto_match": True,
        },
    )

    rows = recognize_detail_items([detail], [product_rule], products, skus)

    assert rows[0]["status"] == "matched"
    assert rows[0]["sku_id"] == 21
    assert rows[0]["stall_id"] == 5


def test_auto_sku_exact_value_beats_longer_containing_sku_names() -> None:
    detail = SimpleNamespace(
        id=125,
        waybill_mode="douyin_cloud_print",
        field_values={
            "capture_task_id": 6,
            "product_full_text": "2026东足透气减震经典男女通用运动跑步鞋时尚休闲黑色",
            "product_short_text": "5.0黑白 42.5 *1",
            "custom_sales_attr1_text": "5.0黑白",
            "custom_sales_attr2_text": "42.5",
            "quantity": 1,
        },
    )
    products = [SimpleNamespace(id=10, name="5.0")]
    skus = [
        SimpleNamespace(id=20, product_id=10, name="5.0黑白", code=None, keywords=None, image_asset_id=201),
        SimpleNamespace(id=21, product_id=10, name="5.0黑白蓝", code=None, keywords=None, image_asset_id=202),
        SimpleNamespace(id=22, product_id=10, name="5.0黑白紫", code=None, keywords=None, image_asset_id=203),
    ]
    product_rule = SimpleNamespace(
        id=18,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=10,
        match_values={
            "mode_code": "douyin_cloud_print",
            "product_id": 10,
            "keyword": "2026东足透气减震经典男女通用运动跑步鞋时尚休闲黑色",
            "fields": ["product_full_text", "product_short_text"],
            "sku_auto_match": True,
        },
    )

    rows = recognize_detail_items([detail], [product_rule], products, skus)

    assert rows[0]["status"] == "matched"
    assert rows[0]["sku_id"] == 20
    assert rows[0]["sku_name"] == "5.0黑白"
    assert rows[0]["match_keyword"] == "5.0黑白"


def test_auto_sku_keeps_fuzzy_fallback_when_exact_sku_is_missing() -> None:
    detail = SimpleNamespace(
        id=126,
        waybill_mode="douyin_cloud_print",
        field_values={
            "capture_task_id": 6,
            "product_full_text": "2026东足透气减震经典男女通用运动跑步鞋时尚休闲黑色",
            "product_short_text": "5.0黑白 42.5 *1",
            "custom_sales_attr1_text": "5.0黑白",
            "custom_sales_attr2_text": "42.5",
            "quantity": 1,
        },
    )
    products = [SimpleNamespace(id=10, name="5.0")]
    skus = [
        SimpleNamespace(id=21, product_id=10, name="5.0黑白蓝", code=None, keywords=None, image_asset_id=202),
    ]
    product_rule = SimpleNamespace(
        id=19,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=10,
        match_values={
            "mode_code": "douyin_cloud_print",
            "product_id": 10,
            "keyword": "2026东足透气减震经典男女通用运动跑步鞋时尚休闲黑色",
            "fields": ["product_full_text", "product_short_text"],
            "sku_auto_match": True,
        },
    )

    rows = recognize_detail_items([detail], [product_rule], products, skus)

    assert rows[0]["status"] == "matched"
    assert rows[0]["sku_id"] == 21
    assert rows[0]["match_keyword"] == "5.0黑白蓝"


def test_product_rule_auto_match_ignores_shared_sku_keywords() -> None:
    detail = SimpleNamespace(
        id=231,
        waybill_mode="douyin_cloud_print",
        field_values={
            "capture_task_id": 14,
            "product_full_text": "【新款特价黑色反光一星木村Low麂皮男鞋女鞋滑板鞋158477C 158369C】",
            "product_short_text": "黑色反光一星158477C 43(偏大一码，建议拍小) 1 件",
            "custom_product_text": "新款特价黑色反光一星木村",
            "custom_sales_attr1_text": "黑色反光一星158477C",
            "custom_sales_attr2_text": "43",
            "custom_quantity_text": "1",
        },
    )
    products = [SimpleNamespace(id=1, name="匡威")]
    skus = [
        SimpleNamespace(id=1, product_id=1, name="情人节", code=None, keywords=["匡威", "木村", "情人节"]),
        SimpleNamespace(id=2, product_id=1, name="黑色", code=None, keywords=["匡威", "木村", "黑色"]),
        SimpleNamespace(id=3, product_id=1, name="灰色", code=None, keywords=["匡威", "木村", "灰色"]),
    ]
    product_rule = SimpleNamespace(
        id=36,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=1,
        match_values={
            "mode_code": "douyin_cloud_print",
            "product_id": 1,
            "product_name": "匡威",
            "keyword": "新款特价黑色反光一星木村",
            "fields": [
                "product_short_text",
                "product_full_text",
                "custom_product_text",
                "custom_sales_attr1_text",
            ],
            "sku_auto_match": True,
        },
    )

    rows = recognize_detail_items([detail], [product_rule], products, skus)

    assert rows[0]["status"] == "matched"
    assert rows[0]["product_id"] == 1
    assert rows[0]["sku_id"] == 2
    assert rows[0]["match_keyword"] == "黑色"


def test_woda_rule_matches_stale_template_id_by_source_fingerprint() -> None:
    detail = SimpleNamespace(
        id=123,
        waybill_mode="cainiao_woda_printxml",
        field_values={
            "capture_task_id": 6,
            "print_template_config_id": 1,
            "print_template_key": "printxml:c863897829c2",
            "custom_product_text": "5.0秒69",
            "custom_sales_attr1_text": "5.0灰绿",
            "custom_sales_attr2_text": "41",
            "custom_quantity_text": "1",
        },
    )
    products = [SimpleNamespace(id=10, name="5.0")]
    skus = [SimpleNamespace(id=21, product_id=10, name="5.0灰绿", code=None, keywords=None)]
    product_rule = SimpleNamespace(
        id=9,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=10,
        match_values={
            "mode_code": "cainiao_woda_printxml",
            "print_template_config_id": 5,
            "print_template_key": "custom:4:1781371888411",
            "print_template_source_key": "printxml:c863897829c2",
            "product_id": 10,
            "product_keyword": "5.0秒69",
            "keyword": "5.0秒69",
            "fields": ["custom_product_text", "custom_sales_attr1_text"],
            "sku_auto_match": True,
        },
    )

    rows = recognize_detail_items([detail], [product_rule], products, skus)

    assert rows[0]["status"] == "matched"
    assert rows[0]["product_id"] == 10
    assert rows[0]["sku_id"] == 21


def test_woda_rule_uses_config_id_when_layout_fingerprint_changes() -> None:
    detail = SimpleNamespace(
        id=187,
        waybill_mode="cainiao_woda_printxml",
        field_values={
            "capture_task_id": 6,
            "print_template_config_id": 7,
            "print_template_key": "printxml:cb9c8469df0b",
            "print_template_source_key": "printxml:cb9c8469df0b",
            "custom_product_text": "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身",
            "custom_sales_attr1_text": "5.0二代灰色",
            "custom_sales_attr2_text": "40",
            "custom_quantity_text": "1",
        },
    )
    products = [SimpleNamespace(id=10, name="5.0")]
    skus = [SimpleNamespace(id=21, product_id=10, name="5.0二代灰色", code=None, keywords=None)]
    product_rule = SimpleNamespace(
        id=9,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=10,
        match_values={
            "mode_code": "cainiao_woda_printxml",
            "print_template_config_id": 7,
            "print_template_key": "custom:4:1781425110060",
            "print_template_source_key": "printxml:c863897829c2",
            "product_id": 10,
            "product_keyword": "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身",
            "keyword": "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身",
            "fields": ["custom_product_text", "custom_sales_attr1_text"],
            "sku_auto_match": True,
            "sku_auto_fields": ["custom_sales_attr1_text"],
        },
    )

    rows = recognize_detail_items([detail], [product_rule], products, skus)

    assert rows[0]["status"] == "matched"
    assert rows[0]["product_id"] == 10
    assert rows[0]["sku_id"] == 21


def test_legacy_comma_separated_fields_are_read_for_product_rules() -> None:
    detail = SimpleNamespace(
        id=124,
        waybill_mode="cainiao_woda_printxml",
        field_values={
            "capture_task_id": 6,
            "print_template_key": "printxml:c863897829c2",
            "custom_product_text": "4.0 秒31",
            "custom_sales_attr1_text": "4.0二代黑灰",
            "custom_sales_attr2_text": "42.5",
            "custom_quantity_text": "1",
        },
    )
    products = [SimpleNamespace(id=12, name="4.0")]
    legacy_rule = SimpleNamespace(
        id=10,
        priority=100,
        is_enabled=True,
        is_deleted=False,
        target_type="product",
        target_id=12,
        match_values={
            "mode_code": "cainiao_woda_printxml",
            "print_template_source_key": "printxml:c863897829c2",
            "product_id": 12,
            "keyword": "4.0 秒31",
            "fields": "custom_product_text,custom_sales_attr1_text",
        },
    )

    rows = recognize_detail_items([detail], [legacy_rule], products, [])

    assert rows[0]["status"] == "sku_unmatched"
    assert rows[0]["product_id"] == 12


def test_template_constraint_matches_by_key_or_config_id_without_short_circuiting() -> None:
    rule_values = {
        "print_template_config_id": 7,
        "print_template_key": "custom:4:1781425110060",
        "print_template_source_key": "printxml:c863897829c2",
    }

    assert template_constraint_matches(
        rule_values,
        {
            "print_template_config_id": 1,
            "print_template_key": "printxml:c863897829c2",
        },
    )
    assert template_constraint_matches(
        rule_values,
        {
            "print_template_config_id": 7,
            "print_template_key": "printxml:cb9c8469df0b",
        },
    )
    assert not template_constraint_matches(
        rule_values,
        {
            "print_template_config_id": 9,
            "print_template_key": "printxml:other",
        },
    )
