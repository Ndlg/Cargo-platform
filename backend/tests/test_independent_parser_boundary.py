from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO_ROOT / "backend" / "app"


def test_backend_has_no_embedded_order_row_parser_implementation() -> None:
    obsolete_parsers = [
        BACKEND_APP / "services" / "order_row_drafts.py",
        BACKEND_APP / "services" / "waybill_parser.py",
    ]
    existing = [str(path.relative_to(REPO_ROOT)).replace("\\", "/") for path in obsolete_parsers if path.exists()]
    assert existing == [], (
        "订单行解析实现必须只存在于 services/waybill-parser；"
        "主后端只能保留契约、调用客户端和业务消费代码。"
    )


def test_backend_runtime_does_not_import_embedded_order_row_parser() -> None:
    offenders: list[str] = []
    for path in BACKEND_APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "app.services.order_row_drafts" in text or "services.order_row_drafts" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)).replace("\\", "/"))

    assert offenders == []


def test_product_matching_route_does_not_own_waybill_parsing() -> None:
    route_path = BACKEND_APP / "api" / "routes" / "product_sku_linking.py"
    text = route_path.read_text(encoding="utf-8")

    forbidden = [
        "read_waybill_samples",
        "empty_waybill_reading_diagnostic",
        "parse_order_row_drafts_with_service",
        "waybill_parser_service_enabled",
        "def parser_standard_detail_input",
        "def parser_waybill_sample_input",
        "def order_row_sample_inputs_from_records",
        "def order_row_drafts_from_parser_payload",
        "def order_rows_for_task_scope",
        "def order_rows_for_selected_scope",
    ]
    offenders = [needle for needle in forbidden if needle in text]

    assert offenders == [], (
        "商品匹配路由只能消费订单行/五字段，不能直接读取原始面单或调用解析服务；"
        f"仍发现: {offenders}"
    )
