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

def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def woda_mapping_source_field(mapping: Any, fallback: str) -> str:
    if not isinstance(mapping, dict):
        return fallback
    source_field = _text(mapping.get("source_field_key"))
    if source_field.startswith("token_") or source_field.startswith("candidate_"):
        return source_field
    if source_field in WODA_CUSTOM_FIELD_ITEM_KEYS:
        return source_field
    extractor = _text(mapping.get("extractor"))
    return WODA_EXTRACTOR_FIELD_CODES.get(extractor, fallback)


def woda_mapping_source_fields(mapping: Any, fallback: str) -> list[str]:
    if not isinstance(mapping, dict):
        return [fallback]
    selectors = mapping.get("token_selectors")
    source_fields: list[str] = []
    if isinstance(selectors, list):
        for selector in selectors:
            if not isinstance(selector, dict):
                continue
            source_field = _text(selector.get("source_field_key"))
            if source_field.startswith("token_") or source_field.startswith("candidate_") or source_field in WODA_CUSTOM_FIELD_ITEM_KEYS:
                source_fields.append(source_field)
    fallback_source = woda_mapping_source_field(mapping, fallback)
    if fallback_source and fallback_source not in source_fields:
        source_fields.insert(0, fallback_source)
    return source_fields
