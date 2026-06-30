from types import SimpleNamespace

from app.services.product_sku_linking import (
    preview_product_sku_linking,
    product_sku_linking_contract,
)


def record(record_id: int, name: str, **values):
    return SimpleNamespace(id=record_id, name=name, **values)


def test_product_sku_linking_preview_matches_user_bound_five_field_rule() -> None:
    product = record(1, "帆布鞋", stall_id=101, stall_name="至尚")
    image = record(11, "黑色图", file_path="storage/black.png")
    sku = record(21, "黑色", product_id=product.id, image_asset_id=image.id)

    preview = preview_product_sku_linking(
        [{"product": "鞋", "sales_attr1": "黑色", "sales_attr2": "42", "quantity": "2", "remark": ""}],
        [
            {
                "name": "鞋-黑色",
                "match_fields": ["product", "sales_attr1"],
                "field_values": {"product": "鞋", "sales_attr1": "黑色"},
                "field_sources": {"product": "field_definition.product", "sales_attr1": "field_definition.sales_attr1"},
                "source_samples": [{"product": "鞋", "sales_attr1": "黑色", "quantity": "2"}],
                "product_id": product.id,
                "sku_id": sku.id,
                "revision_note": "用户确认黑色鞋关联黑色 SKU",
            }
        ],
        products=[product],
        skus=[sku],
        images=[image],
    )

    assert preview["summary"]["matched"] == 1
    assert preview["rows"][0]["product"] == {"id": product.id, "name": "帆布鞋"}
    assert preview["rows"][0]["sku"] == {"id": sku.id, "name": "黑色"}
    assert preview["rows"][0]["image"]["id"] == image.id
    assert preview["rows"][0]["stall"] == {"id": 101, "name": "至尚"}
    assert preview["rows"][0]["match_status"] == "matched"
    assert preview["samples"]["matched"][0]["match_status"] == "matched"
    assert preview["linking_rules"][0]["preview"]["matched_count"] == 1
    assert preview["rows"][0]["matched_linking_rule"]["source_samples"] == [
        {"product": "鞋", "sales_attr1": "黑色", "quantity": "2"}
    ]
    assert preview["rows"][0]["matched_linking_rule"]["field_sources"] == {
        "product": "field_definition.product",
        "sales_attr1": "field_definition.sales_attr1",
    }


def test_product_sku_linking_preview_keeps_unmatched_rows_as_exceptions() -> None:
    product = record(1, "帆布鞋")
    sku = record(21, "黑色", product_id=product.id)

    preview = preview_product_sku_linking(
        [{"product": "鞋", "sales_attr1": "红色", "sales_attr2": "42", "quantity": "2", "remark": ""}],
        [
            {
                "match_fields": ["product", "sales_attr1"],
                "field_values": {"product": "鞋", "sales_attr1": "黑色"},
                "product_id": product.id,
                "sku_id": sku.id,
            }
        ],
        products=[product],
        skus=[sku],
        images=[],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "product_unmatched"
    assert row["exception_reason"] == "没有用户确认的商品匹配学习记录命中这行订单。"
    assert preview["samples"]["product_unmatched"][0]["match_status"] == "product_unmatched"
    assert preview["samples"]["product_unmatched"][0]["exception_reason"] == "没有用户确认的商品匹配学习记录命中这行订单。"
    assert preview["summary"]["product_unmatched"] == 1


def test_product_sku_linking_keeps_special_order_rows_out_of_unmatched_counts() -> None:
    preview = preview_product_sku_linking(
        [
            {
                "product": "",
                "sales_attr1": "",
                "sales_attr2": "",
                "quantity": "",
                "remark": "",
                "_order_row_status": "special",
                "_order_row_review_reason": "wechat_special_waybill",
            }
        ],
        [],
        products=[],
        skus=[],
        images=[],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "special"
    assert row["exception_reason"] == "特殊单不参与商品、SKU、图片匹配。"
    assert preview["summary"]["special"] == 1
    assert preview["summary"]["product_unmatched"] == 0
    assert preview["samples"]["special"][0]["match_status"] == "special"


def test_product_sku_linking_does_not_use_catalog_assets_without_user_rule() -> None:
    product = record(1, "VAP", keywords=["vap2025"])
    image = record(11, "VAP 黑色图", file_path="storage/vap-black.png")
    sku = record(21, "二代黑白", product_id=product.id, keywords=["二代黑白"], image_asset_id=image.id)

    preview = preview_product_sku_linking(
        [{"product": "秒21 vap2025", "sales_attr1": "二代黑白", "sales_attr2": "43", "quantity": "1", "remark": ""}],
        [],
        products=[product],
        skus=[sku],
        images=[image],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "product_unmatched"
    assert row["product"] is None
    assert row["sku"] is None
    assert row["image"] is None
    assert row["match_source"] == "user_learning_rule"
    assert row["exception_reason"] == "没有用户确认的商品匹配学习记录命中这行订单。"
    assert preview["summary"]["product_unmatched"] == 1
    assert preview["summary"]["matched"] == 0


def test_product_sku_linking_ignores_product_assets_even_when_multiple_products_match() -> None:
    shoe = record(1, "户外登山鞋", keywords=["鞋"])
    sandal = record(2, "凉鞋", keywords=["鞋"])

    preview = preview_product_sku_linking(
        [{"product": "鞋", "sales_attr1": "黑色"}],
        [],
        products=[shoe, sandal],
        skus=[],
        images=[],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "product_unmatched"
    assert row["match_source"] == "user_learning_rule"
    assert row["product"] is None
    assert preview["summary"]["product_unmatched"] == 1
    assert preview["summary"]["conflict"] == 0


def test_product_sku_linking_ignores_catalog_assets_before_user_rule_selects_product() -> None:
    product = record(1, "户外登山鞋", keywords=["登山鞋"])
    black = record(21, "黑色", product_id=product.id, keywords=["黑"])
    black_white = record(22, "黑白", product_id=product.id, keywords=["黑"])

    preview = preview_product_sku_linking(
        [{"product": "登山鞋", "sales_attr1": "黑"}],
        [],
        products=[product],
        skus=[black, black_white],
        images=[],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "product_unmatched"
    assert row["match_source"] == "user_learning_rule"
    assert preview["summary"]["product_unmatched"] == 1
    assert preview["summary"]["sku_unmatched"] == 0


def test_product_sku_linking_user_rule_takes_priority_over_product_asset_names() -> None:
    product = record(1, "户外登山鞋", keywords=["登山鞋"])
    image = record(11, "用户规则图", file_path="storage/user.png")
    catalog_sku = record(21, "低帮黑白", product_id=product.id, keywords=["低帮黑白"], image_asset_id=image.id)
    user_sku = record(22, "用户确认 SKU", product_id=product.id, keywords=["低帮黑白"], image_asset_id=image.id)

    preview = preview_product_sku_linking(
        [{"product": "登山鞋", "sales_attr1": "低帮黑白"}],
        [
            {
                "id": 101,
                "name": "用户确认规则",
                "product_match_fields": ["product"],
                "product_keyword": "登山鞋",
                "product_id": product.id,
                "sku_id": user_sku.id,
            }
        ],
        products=[product],
        skus=[catalog_sku, user_sku],
        images=[image],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "matched"
    assert row.get("match_source") == "user_learning_rule"
    assert row["sku"] == {"id": user_sku.id, "name": "用户确认 SKU"}
    assert row["matched_linking_rule"]["id"] == 101


def test_product_sku_linking_does_not_conflict_when_user_rule_field_does_not_match() -> None:
    four = record(1, "4.0", keywords=["4.0"])
    five = record(2, "5.0", keywords=["5.0"])

    preview = preview_product_sku_linking(
        [
            {
                "product": "现货新款4.0鞋子跑步鞋公路四代男鞋女鞋网面透气系带跑道5.0",
                "sales_attr1": "4.0灰蓝",
                "sales_attr2": "42.5",
                "quantity": "1",
                "remark": "",
            }
        ],
        [
            {
                "id": 101,
                "name": "5.0 只看销售属性1",
                "product_match_fields": ["sales_attr1"],
                "product_keyword": "5.0",
                "product_id": five.id,
            }
        ],
        products=[four, five],
        skus=[],
        images=[],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "product_unmatched"
    assert row["match_source"] == "user_learning_rule"
    assert preview["summary"]["conflict"] == 0
    assert preview["summary"]["product_unmatched"] == 1


def test_product_sku_linking_asset_binding_conflict_is_not_reported_as_rule_conflict() -> None:
    shoe = record(1, "跑步鞋")
    wrong_product = record(2, "拖鞋")
    wrong_sku = record(21, "拖鞋黑色", product_id=wrong_product.id)

    preview = preview_product_sku_linking(
        [{"product": "跑步鞋", "sales_attr1": "黑色", "sales_attr2": "42", "quantity": "1"}],
        [
            {
                "id": 101,
                "name": "跑步鞋错误 SKU 绑定",
                "product_match_fields": ["product"],
                "product_keyword": "跑步鞋",
                "product_id": shoe.id,
                "sku_id": wrong_sku.id,
            }
        ],
        products=[shoe, wrong_product],
        skus=[wrong_sku],
        images=[],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "conflict"
    assert row["conflict_kind"] == "asset_binding"
    assert row["matched_linking_rule"]["id"] == 101
    assert row["conflict_linking_rules"] == []
    assert row["exception_reason"] == "SKU 不属于当前商品绑定。"


def test_product_sku_linking_preview_reports_sku_and_image_exceptions_without_guessing() -> None:
    product = record(1, "帆布鞋")
    sku = record(21, "黑色", product_id=product.id, image_asset_id=None)

    sku_preview = preview_product_sku_linking(
        [{"product": "鞋", "sales_attr1": "黑色"}],
        [
            {
                "match_fields": ["product"],
                "field_values": {"product": "鞋"},
                "product_id": product.id,
                "sku_id": 999,
            }
        ],
        products=[product],
        skus=[sku],
        images=[],
    )
    assert sku_preview["rows"][0]["match_status"] == "sku_unmatched"

    image_preview = preview_product_sku_linking(
        [{"product": "鞋", "sales_attr1": "黑色"}],
        [
            {
                "match_fields": ["product"],
                "field_values": {"product": "鞋"},
                "product_id": product.id,
                "sku_id": sku.id,
            }
        ],
        products=[product],
        skus=[sku],
        images=[],
    )
    assert image_preview["rows"][0]["match_status"] == "image_unmatched"


def test_product_sku_linking_preview_prefers_more_specific_auto_sku_match() -> None:
    product = record(1, "5.0")
    image = record(31, "黑白紫图", file_path="storage/black-purple.png")
    black_purple = record(21, "5.0黑白紫", product_id=product.id, image_asset_id=image.id)
    purple = record(22, "黑白紫", product_id=product.id, image_asset_id=image.id)

    preview = preview_product_sku_linking(
        [{"product": "跑步鞋", "sales_attr1": "5.0黑白紫", "sales_attr2": "42", "quantity": "1"}],
        [
            {
                "id": 101,
                "product_match_fields": ["sales_attr1"],
                "product_keyword": "5.0",
                "product_id": product.id,
                "sku_match_fields": ["sales_attr1"],
            }
        ],
        products=[product],
        skus=[black_purple, purple],
        images=[image],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "matched"
    assert row["sku"] == {"id": black_purple.id, "name": "5.0黑白紫"}
    assert preview["summary"]["sku_unmatched"] == 0
    assert preview["summary"]["matched"] == 1


def test_product_sku_linking_preview_treats_equal_auto_sku_score_as_sku_ambiguous() -> None:
    product = record(1, "5.0")
    sku_a = record(21, "黑白紫 A", product_id=product.id, keywords=["黑白紫"])
    sku_b = record(22, "黑白紫 B", product_id=product.id, keywords=["黑白紫"])

    preview = preview_product_sku_linking(
        [{"product": "跑步鞋", "sales_attr1": "5.0黑白紫", "sales_attr2": "42", "quantity": "1"}],
        [
            {
                "id": 101,
                "product_match_fields": ["sales_attr1"],
                "product_keyword": "5.0",
                "product_id": product.id,
                "sku_match_fields": ["sales_attr1"],
            }
        ],
        products=[product],
        skus=[sku_a, sku_b],
        images=[],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "sku_ambiguous"
    assert "命中多个 SKU" in row["exception_reason"]
    assert row["matched_linking_rule"]["id"] == 101
    assert row["conflict_linking_rules"] == []
    assert [candidate["name"] for candidate in row["sku_candidates"]] == ["黑白紫 A", "黑白紫 B"]
    assert preview["summary"]["sku_ambiguous"] == 1
    assert preview["summary"]["conflict"] == 0


def test_product_sku_linking_preview_conflicts_on_multiple_user_bindings() -> None:
    product = record(1, "帆布鞋")
    black_sku = record(21, "黑色", product_id=product.id)
    red_sku = record(22, "红色", product_id=product.id)

    preview = preview_product_sku_linking(
        [{"product": "鞋"}],
        [
            {
                "id": 101,
                "name": "鞋-黑色规则",
                "match_fields": ["product"],
                "field_values": {"product": "鞋"},
                "product_id": product.id,
                "sku_id": black_sku.id,
            },
            {
                "id": 102,
                "name": "鞋-红色规则",
                "match_fields": ["product"],
                "field_values": {"product": "鞋"},
                "product_id": product.id,
                "sku_id": red_sku.id,
            },
        ],
        products=[product],
        skus=[black_sku, red_sku],
        images=[],
    )

    assert preview["rows"][0]["match_status"] == "conflict"
    assert [rule["id"] for rule in preview["rows"][0]["conflict_linking_rules"]] == [101, 102]
    assert [rule["id"] for rule in preview["samples"]["conflict"][0]["conflict_linking_rules"]] == [101, 102]
    assert preview["summary"]["conflict"] == 1


def test_product_sku_linking_never_reports_conflict_with_single_user_binding() -> None:
    product = record(1, "VAP")
    sku_a = record(21, "二代黑红", product_id=product.id, keywords=["二代黑红"])
    sku_b = record(22, "二代黑红 黄标", product_id=product.id, keywords=["二代黑红"])

    preview = preview_product_sku_linking(
        [{"product": "秒21 vap2025", "sales_attr1": "二代黑红", "sales_attr2": "42.5", "quantity": "1"}],
        [
            {
                "id": 13,
                "name": "VAP",
                "product_match_fields": ["product"],
                "product_keyword": "vap",
                "product_id": product.id,
                "sku_match_fields": ["sales_attr1"],
            }
        ],
        products=[product],
        skus=[sku_a, sku_b],
        images=[],
    )

    row = preview["rows"][0]
    assert row["match_status"] == "sku_ambiguous"
    assert row["conflict_linking_rules"] == []
    assert preview["summary"]["conflict"] == 0
    assert preview["summary"]["sku_ambiguous"] == 1


def test_product_sku_linking_contract_excludes_waybill_internals() -> None:
    contract = product_sku_linking_contract()

    assert contract["module"] == "product_sku_linking"
    assert contract["module_name"] == "商品匹配模块"
    assert contract["technical_name"] == "product_sku_linking"
    assert contract["input_fields"] == ["product", "sales_attr1", "sales_attr2", "quantity", "remark"]
    assert contract["output_fields"] == ["product", "sku", "image", "match_status", "exception_reason"]
    assert contract["output_name"] == "商品匹配结果"
    assert contract["rule_learning_model"] == "progressive_user_confirmed_five_field_product_matching_rules"
    assert "source_five_field_samples" in contract["rule_requirements"]
    assert "preview_match_counts" in contract["rule_requirements"]
    assert "can_disable_or_revise" in contract["rule_requirements"]
    assert "raw_capture_record" in contract["forbidden_inputs"]
    assert "waybill_similarity" in contract["forbidden_inputs"]
    assert "same_or_similar_waybill_auto_grouping" in contract["forbidden_methods"]
