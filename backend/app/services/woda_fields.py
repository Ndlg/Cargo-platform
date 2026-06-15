from __future__ import annotations

from typing import Any


WODA_CUSTOM_FIELD_ITEM_KEYS = {
    "custom_product_text": "product_text",
    "custom_sales_attr1_text": "sales_attr1_text",
    "custom_sales_attr2_text": "sales_attr2_text",
    "custom_quantity_text": "quantity_text",
    "custom_item_remark_text": "remark_text",
}

WODA_EXTRACTOR_FIELD_CODES = {
    "product_text": "custom_product_text",
    "sales_attr1_text": "custom_sales_attr1_text",
    "spec_text": "custom_sales_attr1_text",
    "sales_attr2_text": "custom_sales_attr2_text",
    "size_text": "custom_sales_attr2_text",
    "quantity_text": "custom_quantity_text",
    "remark_text": "custom_item_remark_text",
}

WODA_SKU_SOURCE_FIELD_SCORES = {
    "custom_sales_attr1_text": 300,
    "custom_spec_text": 300,
    "spec_text": 260,
    "custom_sales_attr2_text": 200,
    "custom_size_text": 200,
    "product_short_text": 240,
    "product_full_text": 220,
    "custom_item_remark_text": 100,
    "buyer_remark": 120,
    "seller_remark": 110,
}

WODA_SKU_FALLBACK_FIELD_CODES = {"custom_item_remark_text", "buyer_remark", "seller_remark"}
WODA_PRODUCT_TEXT_FIELD_CODES = {"custom_product_text", "product_short_text", "product_full_text"}
DEFAULT_WODA_SKU_AUTO_FIELDS = ("custom_sales_attr1_text", "custom_sales_attr2_text")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def woda_mapping_source_field(mapping: Any, fallback: str) -> str:
    if not isinstance(mapping, dict):
        return fallback
    source_field = _text(mapping.get("source_field_key"))
    if source_field in WODA_CUSTOM_FIELD_ITEM_KEYS:
        return source_field
    extractor = _text(mapping.get("extractor"))
    return WODA_EXTRACTOR_FIELD_CODES.get(extractor, fallback)
