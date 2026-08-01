from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_CATALOG_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "ProductCatalogView.vue"
PRODUCT_MATCHING_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "ProductMatchingView.vue"
COLLECTOR_CONNECTIONS_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "CollectorConnectionsView.vue"
CAPTURE_RECORDS_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "CaptureRecordsView.vue"
WAYBILL_BATCHES_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "WaybillBatchesView.vue"
EXCEPTIONS_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "ExceptionsView.vue"
EXPORT_CENTER_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "ExportCenterView.vue"
EXPORT_HEADER_VIEW = PROJECT_ROOT / "frontend" / "src" / "views" / "workbench" / "ExportHeaderDefinitionView.vue"


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


def test_product_matching_page_does_not_duplicate_the_exception_checklist() -> None:
    source = PRODUCT_MATCHING_VIEW.read_text(encoding="utf-8")

    assert "商品/SKU 问题清单" not in source
    assert "runSavedRulesPreview" not in source
    assert "v-if=\"inboundFromExceptions\"" in source


def test_exception_actions_have_explicit_status_routes_without_default_jump() -> None:
    source = EXCEPTIONS_VIEW.read_text(encoding="utf-8")
    matching_source = PRODUCT_MATCHING_VIEW.read_text(encoding="utf-8")

    assert "sku_ambiguous" in source
    assert "指定 SKU 匹配" in source
    assert "暂无处理入口" in source
    assert "return '查看识别结果'" not in source
    assert "path: '/admin/product-matching'" in source
    assert "path: '/admin/products'" in source
    assert "path: '/admin/format-learning'" in source
    assert "path: '/waybill-batches'" not in source
    assert "route.query.rule_id" in matching_source
    assert "route.query.focus" in matching_source
    assert "sku-section" in matching_source


def test_collector_connection_ui_hides_raw_rowid_label() -> None:
    source = COLLECTOR_CONNECTIONS_VIEW.read_text(encoding="utf-8")
    template = source.split("<template>", 1)[1]

    assert "rowid" not in template.lower()
    assert "本地进度" in template
    assert "组件任务" not in template
    assert "taskCount" not in source


def test_capture_records_page_uses_waybill_language_and_task_scoped_raw_loading() -> None:
    source = CAPTURE_RECORDS_VIEW.read_text(encoding="utf-8")
    template = source.split("<template>", 1)[1]

    assert "/raw-capture-records?limit=2000" not in source
    assert "/raw-capture-records?task_id=${taskId}&limit=500" in source
    assert "/capture-tasks?limit=6&include_waybill_counts=true" in source
    assert "rawRecordsForTask(task.id).reduce" not in source
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


def test_waybill_source_counts_use_parent_waybills_for_total_and_breakdown() -> None:
    source = WAYBILL_BATCHES_VIEW.read_text(encoding="utf-8")

    assert "const parents = drafts.value?.parents ?? []" in source
    assert "total: parents.length" in source
    assert "sourceOptions.value.length" not in source
    assert "const rows = allRows.value" not in source


def test_product_matching_rule_labels_do_not_expose_database_ids() -> None:
    source = PRODUCT_MATCHING_VIEW.read_text(encoding="utf-8")

    assert "ruleCode(" not in source
    assert "`学习记录 ${ruleId}`" not in source
    assert "未命名学习记录" in source
    assert "版本 {{ row.revision }}" in source


def test_matching_repairs_route_to_the_responsible_screen_without_heavy_task_counts() -> None:
    matching_source = PRODUCT_MATCHING_VIEW.read_text(encoding="utf-8")
    exceptions_source = EXCEPTIONS_VIEW.read_text(encoding="utf-8")

    assert "应用规则到本轮" not in matching_source
    assert "path: '/admin/products'" in exceptions_source
    assert "actionLabel: '维护 SKU'" in exceptions_source
    for view in (PRODUCT_MATCHING_VIEW, EXCEPTIONS_VIEW, EXPORT_CENTER_VIEW, EXPORT_HEADER_VIEW):
        assert "include_waybill_counts=false" in view.read_text(encoding="utf-8")
