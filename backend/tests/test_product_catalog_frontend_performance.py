from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_CATALOG_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "ProductCatalogView.vue"
PRODUCT_MATCHING_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "ProductMatchingView.vue"
COLLECTOR_CONNECTIONS_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "CollectorConnectionsView.vue"
CAPTURE_RECORDS_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "CaptureRecordsView.vue"
WAYBILL_BATCHES_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "WaybillBatchesView.vue"


def test_product_catalog_does_not_load_all_skus_for_selected_product() -> None:
    source = PRODUCT_CATALOG_VIEW.read_text(encoding="utf-8")

    assert "product-skus?limit=2000&product_id" not in source
    assert "offset=${skuOffset}" in source
    assert "skuTotal" in source


def test_product_catalog_does_not_load_all_products_on_entry() -> None:
    source = PRODUCT_CATALOG_VIEW.read_text(encoding="utf-8")

    assert "/products?limit=2000" not in source
    assert "productOffset" in source
    assert "encodeURIComponent(productSearch.value.trim())" in source


def test_product_matching_rule_editor_does_not_load_all_skus_for_selected_product() -> None:
    source = PRODUCT_MATCHING_VIEW.read_text(encoding="utf-8")

    assert "product-skus?limit=2000&product_id" not in source
    assert ":remote-method=\"searchSelectedProductSkus\"" in source
    assert "skuSearchKeyword" in source


def test_product_matching_rule_editor_does_not_load_all_products() -> None:
    source = PRODUCT_MATCHING_VIEW.read_text(encoding="utf-8")

    assert "/products?limit=2000" not in source
    assert ":remote-method=\"searchProducts\"" in source
    assert "productSearchKeyword" in source
    assert "rowsWithRequiredProducts" in source


def test_product_matching_rule_editor_does_not_load_all_images() -> None:
    source = PRODUCT_MATCHING_VIEW.read_text(encoding="utf-8")

    assert "image-assets?limit=2000" not in source
    assert ":remote-method=\"searchImageAssets\"" in source
    assert "imageSearchKeyword" in source


def test_collector_connection_ui_hides_raw_rowid_label() -> None:
    source = COLLECTOR_CONNECTIONS_VIEW.read_text(encoding="utf-8")
    template = source.split("<template>", 1)[1]

    assert "rowid" not in template.lower()
    assert "本地进度" in template


def test_capture_records_page_uses_waybill_language_and_task_scoped_raw_loading() -> None:
    source = CAPTURE_RECORDS_VIEW.read_text(encoding="utf-8")
    template = source.split("<template>", 1)[1]

    assert "/raw-capture-records?limit=2000" not in source
    assert "/raw-capture-records?task_id=${taskId}&limit=500" in source
    assert "面单数量" in template
    assert "批次ID" not in template
    assert "内部采集记录" not in template
    assert "内部定位" not in template


def test_waybill_batches_page_hides_internal_source_positioning_language() -> None:
    source = WAYBILL_BATCHES_VIEW.read_text(encoding="utf-8")
    template = source.split("<template>", 1)[1]

    assert "内部定位" not in source
    assert "来源诊断" not in template
    assert "采集来源" in template
    assert "可追溯到原始面单" in source
