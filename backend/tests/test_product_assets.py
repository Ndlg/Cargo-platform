from app.api.routes.product_assets import _sku_name_from_file


def test_sku_zip_import_keeps_c5_prefix_after_order_prefix() -> None:
    assert _sku_name_from_file("SKU图_03_C5-冰川灰.png") == "C5-冰川灰"


def test_sku_zip_import_still_removes_plain_order_prefix() -> None:
    assert _sku_name_from_file("SKU图_01-冰白.png") == "冰白"
