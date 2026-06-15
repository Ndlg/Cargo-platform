from __future__ import annotations

import json
from typing import Any, Iterable

from app.services.woda_fields import (
    DEFAULT_WODA_SKU_AUTO_FIELDS,
    WODA_PRODUCT_TEXT_FIELD_CODES,
    WODA_SKU_FALLBACK_FIELD_CODES,
    WODA_SKU_SOURCE_FIELD_SCORES,
)


WODA_MODE_CODE = "cainiao_woda_printxml"

PRODUCT_SOURCE_FIELD_SCORES = {
    "custom_product_text": 360,
    "product_short_text": 340,
    "product_full_text": 320,
    "custom_sales_attr1_text": 240,
    "custom_spec_text": 240,
    "spec_text": 220,
    "custom_item_remark_text": 160,
    "buyer_remark": 140,
    "seller_remark": 130,
    "custom_sales_attr2_text": 90,
    "custom_size_text": 90,
    "quantity": 40,
    "custom_quantity_text": 40,
}

FIELD_ALIASES = {
    "custom_sales_attr1_text": ("custom_sales_attr1_text", "custom_spec_text"),
    "custom_spec_text": ("custom_spec_text", "custom_sales_attr1_text"),
    "custom_sales_attr2_text": ("custom_sales_attr2_text", "custom_size_text"),
    "custom_size_text": ("custom_size_text", "custom_sales_attr2_text"),
    "quantity": ("quantity", "custom_quantity_text"),
}


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "\n".join(text_value(item) for item in value if text_value(item))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def normalize_match_text(value: Any) -> str:
    return text_value(value).lower().replace(" ", "").replace("\t", "").replace("\n", "")


def keyword_matches(text: Any, keyword: Any) -> bool:
    haystack = normalize_match_text(text)
    needle = normalize_match_text(keyword)
    if not haystack or not needle:
        return False
    return needle in haystack or haystack in needle


def match_quality(source_text: Any, term_value: Any) -> int | None:
    source = normalize_match_text(source_text)
    term = normalize_match_text(term_value)
    if not source or not term:
        return None
    if source == term:
        return 3
    if term in source:
        return 2
    if source in term:
        return 1
    return None


def sku_match_quality(source_text: Any, sku_term: Any) -> int | None:
    return match_quality(source_text, sku_term)


def product_identity_quality(source_text: Any, keyword: Any, product_name: Any) -> int:
    candidates = [
        quality
        for quality in (
            match_quality(source_text, product_name),
            match_quality(keyword, product_name),
        )
        if quality is not None
    ]
    return max(candidates, default=0)


def product_keyword_quality(source_text: Any, keyword: Any) -> int | None:
    return match_quality(source_text, keyword)


def product_keyword_allowed_for_product(keyword: Any, product_name: Any) -> bool:
    normalized_keyword = normalize_match_text(keyword)
    normalized_name = normalize_match_text(product_name)
    if not normalized_keyword or not normalized_name:
        return True
    if normalized_keyword == normalized_name:
        return True
    return normalized_keyword not in normalized_name


def product_match_score(field_code: str, keyword: Any, quality: int, identity_quality: int = 0) -> int:
    field_score = PRODUCT_SOURCE_FIELD_SCORES.get(field_code, 180)
    return quality * 1000 + identity_quality * 500 + field_score + len(normalize_match_text(keyword))


def sku_match_score(source_score: int, sku_term: Any, quality: int) -> int:
    return quality * 100 + source_score + len(normalize_match_text(sku_term))


def best_sku_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not matches:
        return []
    top_score = max(int(match.get("score") or 0) for match in matches)
    top = [match for match in matches if int(match.get("score") or 0) == top_score]
    top_by_sku: dict[int, dict[str, Any]] = {}
    for match in top:
        top_by_sku.setdefault(int(match["sku_id"]), match)
    return list(top_by_sku.values())


def best_product_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not matches:
        return []
    top_score = max(int(match.get("score") or 0) for match in matches)
    top = [match for match in matches if int(match.get("score") or 0) == top_score]
    top_by_product: dict[int, dict[str, Any]] = {}
    for match in top:
        top_by_product.setdefault(int(match["product_id"]), match)
    return list(top_by_product.values())


def field_text(values: dict[str, Any], field_code: str | None) -> str:
    if not field_code:
        return ""
    for code in FIELD_ALIASES.get(field_code, (field_code,)):
        value = text_value(values.get(code))
        if value:
            return value
    return ""


def fields_text(values: dict[str, Any], field_codes: Iterable[str]) -> str:
    texts: list[str] = []
    seen = set()
    for code in field_codes:
        text = field_text(values, code)
        if text and text not in seen:
            texts.append(text)
            seen.add(text)
    return "\n".join(texts)


def field_codes_from_text(value: str, fallback: str) -> list[str]:
    raw_codes = [item.strip() for item in value.split(",") if item.strip()]
    return raw_codes or [fallback]


def product_field_match(
    values: dict[str, Any],
    field_codes: Iterable[str],
    keyword: str,
    product_name: Any = "",
) -> dict[str, Any] | None:
    if not product_keyword_allowed_for_product(keyword, product_name):
        return None
    candidates: list[dict[str, Any]] = []
    seen = set()
    for field_code in field_codes:
        code = text_value(field_code)
        if not code or code in seen:
            continue
        seen.add(code)
        source_text = field_text(values, code)
        quality = product_keyword_quality(source_text, keyword)
        if quality is None:
            continue
        identity_quality = product_identity_quality(source_text, keyword, product_name)
        candidates.append(
            {
                "field": code,
                "keyword": keyword,
                "source_text": source_text,
                "match_quality": quality,
                "product_identity_quality": identity_quality,
                "score": product_match_score(code, keyword, quality, identity_quality),
            }
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item["score"]))


def int_value(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def normalized_template_values(*values: Any) -> set[str]:
    return {normalized for value in values if (normalized := normalize_match_text(value))}


def template_key_candidates(values: dict[str, Any]) -> set[str]:
    return normalized_template_values(
        values.get("print_template_source_key"),
        values.get("source_template_key"),
        values.get("template_source_key"),
        values.get("print_template_key"),
        values.get("source_template"),
    )


def template_label_candidates(values: dict[str, Any]) -> set[str]:
    return normalized_template_values(
        values.get("print_template_label"),
        values.get("print_template_config_label"),
        values.get("template_label"),
        values.get("print_template_name"),
    )


def template_constraint_matches(match_values: dict[str, Any], detail_values: dict[str, Any]) -> bool:
    rule_config_id = int_value(match_values.get("print_template_config_id"))
    rule_template_keys = template_key_candidates(match_values)
    rule_template_labels = template_label_candidates(match_values)
    if rule_config_id is None and not rule_template_keys and not rule_template_labels:
        return True

    detail_template_keys = template_key_candidates(detail_values)
    if rule_template_keys and detail_template_keys and rule_template_keys & detail_template_keys:
        return True

    detail_config_id = int_value(detail_values.get("print_template_config_id"))
    if rule_config_id is not None and detail_config_id is not None and rule_config_id == detail_config_id:
        return True

    detail_template_labels = template_label_candidates(detail_values)
    return bool(rule_template_labels and detail_template_labels and rule_template_labels & detail_template_labels)


def custom_item_values(
    base_values: dict[str, Any],
    item: dict[str, Any],
    *,
    item_index: int,
    item_count: int,
) -> dict[str, Any]:
    values = dict(base_values)
    remark_text = text_value(item.get("remark_text"))
    sales_attr1 = text_value(item.get("sales_attr1_text") or item.get("spec_text"))
    sales_attr2 = text_value(item.get("sales_attr2_text") or item.get("size_text"))
    quantity_text = text_value(item.get("quantity_text"))
    values.update(
        {
            "custom_item_index": item_index,
            "custom_item_count": item_count,
            "custom_product_text": text_value(item.get("product_text")),
            "custom_sales_attr1_text": sales_attr1,
            "custom_sales_attr2_text": sales_attr2,
            "custom_spec_text": text_value(item.get("spec_text")) or sales_attr1,
            "custom_size_text": sales_attr2,
            "custom_quantity_text": quantity_text,
            "custom_item_remark_text": remark_text,
            "custom_item_raw_text": text_value(item.get("raw_text")),
        }
    )
    if quantity_text:
        values["quantity"] = quantity_text
    return values


def detail_item_rows(detail: Any) -> list[dict[str, Any]]:
    base_values = dict(getattr(detail, "field_values", None) or {})
    custom_items = base_values.get("custom_items")
    item_dicts = [item for item in custom_items if isinstance(item, dict)] if isinstance(custom_items, list) else []
    if not item_dicts:
        return [
            {
                "detail": detail,
                "values": base_values,
                "item_index": None,
                "item_count": 1,
            }
        ]

    item_count = len(item_dicts)
    return [
        {
            "detail": detail,
            "values": custom_item_values(base_values, item, item_index=index, item_count=item_count),
            "item_index": index,
            "item_count": item_count,
        }
        for index, item in enumerate(item_dicts, start=1)
    ]


def rule_sort_key(rule: Any) -> tuple[int, int]:
    return (int(getattr(rule, "priority", 100) or 100), int(getattr(rule, "id", 0) or 0))


def rule_match_values(rule: Any) -> dict[str, Any]:
    values = getattr(rule, "match_values", None)
    return values if isinstance(values, dict) else {}


def rule_applies_to_row(rule: Any, detail: Any, values: dict[str, Any]) -> bool:
    if getattr(rule, "is_enabled", True) is False or getattr(rule, "is_deleted", False):
        return False
    match_values = rule_match_values(rule)
    mode_code = text_value(match_values.get("mode_code"))
    detail_mode = text_value(getattr(detail, "waybill_mode", "") or values.get("waybill_mode"))
    if mode_code and detail_mode and mode_code != detail_mode:
        return False

    return template_constraint_matches(match_values, values)


def product_match_for_rule(
    rule: Any,
    detail: Any,
    values: dict[str, Any],
    products_by_id: dict[int, Any],
) -> dict[str, Any] | None:
    match_values = rule_match_values(rule)
    detail_mode = text_value(getattr(detail, "waybill_mode", "") or values.get("waybill_mode"))
    product_id = int_value(match_values.get("product_id"))
    if product_id is None and getattr(rule, "target_type", "") == "product":
        product_id = int_value(getattr(rule, "target_id", None))
    if product_id is None:
        return None

    if detail_mode == WODA_MODE_CODE:
        legacy_fields = list_value(match_values.get("fields"))
        field_code = text_value(match_values.get("product_field")) or (
            ",".join(legacy_fields) if legacy_fields else "custom_product_text"
        )
        field_codes = legacy_fields or field_codes_from_text(field_code, "custom_product_text")
        keyword = text_value(match_values.get("product_keyword")) or text_value(match_values.get("keyword"))
    else:
        field_codes = list_value(match_values.get("fields"))
        field_code = ",".join(field_codes)
        keyword = text_value(match_values.get("keyword"))

    product_name = (
        record_name(products_by_id, product_id)
        or text_value(match_values.get("product_name"))
        or text_value(getattr(rule, "target_name", ""))
    )
    field_match = product_field_match(values, field_codes, keyword, product_name)
    if field_match is None:
        return None
    return {
        "rule": rule,
        "product_id": product_id,
        **field_match,
    }


def sku_match_for_rule(rule: Any, detail: Any, values: dict[str, Any]) -> dict[str, Any] | None:
    if getattr(rule, "target_type", "") != "sku":
        return None
    sku_id = int_value(getattr(rule, "target_id", None) or rule_match_values(rule).get("sku_id"))
    if sku_id is None:
        return None

    match_values = rule_match_values(rule)
    if match_values.get("sku_lock") is True:
        return {
            "rule": rule,
            "sku_id": sku_id,
            "match_type": "forced",
            "field": "",
            "keyword": "",
            "source_text": "",
            "match_quality": 4,
            "score": 40000,
        }

    detail_mode = text_value(getattr(detail, "waybill_mode", "") or values.get("waybill_mode"))
    if detail_mode == WODA_MODE_CODE:
        field_code = text_value(match_values.get("sku_field")) or "custom_sales_attr1_text"
        keyword = text_value(match_values.get("keyword"))
        source_text = field_text(values, field_code)
        if not keyword:
            return {
                "rule": rule,
                "sku_id": sku_id,
                "match_type": "forced",
                "field": "",
                "keyword": "",
                "source_text": "",
                "match_quality": 4,
                "score": 40000,
            }
        quality = sku_match_quality(source_text, keyword)
        if quality is not None:
            return {
                "rule": rule,
                "sku_id": sku_id,
                "match_type": "primary",
                "field": field_code,
                "keyword": keyword,
                "source_text": source_text,
                "match_quality": quality,
                "score": sku_match_score(350, keyword, quality),
            }

        fallback_keyword = text_value(match_values.get("sku_fallback_keyword"))
        fallback_field = text_value(match_values.get("sku_fallback_field")) or "custom_item_remark_text"
        fallback_text = field_text(values, fallback_field)
        fallback_quality = sku_match_quality(fallback_text, fallback_keyword)
        if fallback_quality is not None:
            return {
                "rule": rule,
                "sku_id": sku_id,
                "match_type": "fallback",
                "field": fallback_field,
                "keyword": fallback_keyword,
                "source_text": fallback_text,
                "match_quality": fallback_quality,
                "score": sku_match_score(330, fallback_keyword, fallback_quality),
            }
        return None

    field_codes = list_value(match_values.get("fields"))
    keyword = text_value(match_values.get("keyword"))
    source_text = fields_text(values, field_codes)
    if not keyword:
        return {
            "rule": rule,
            "sku_id": sku_id,
            "match_type": "forced",
            "field": "",
            "keyword": "",
            "source_text": "",
            "match_quality": 4,
            "score": 40000,
        }
    quality = sku_match_quality(source_text, keyword)
    if quality is not None:
        return {
            "rule": rule,
            "sku_id": sku_id,
            "match_type": "primary",
            "field": ",".join(field_codes),
            "keyword": keyword,
            "source_text": source_text,
            "match_quality": quality,
            "score": sku_match_score(350, keyword, quality),
        }
    return None


def record_name(records_by_id: dict[int, Any], record_id: int | None, fallback: Any = "") -> str:
    if record_id is None:
        return text_value(fallback)
    record = records_by_id.get(record_id)
    return text_value(getattr(record, "name", fallback))


def record_keywords(record: Any) -> list[str]:
    keywords = getattr(record, "keywords", None)
    return [item for item in keywords if isinstance(item, str) and item.strip()] if isinstance(keywords, list) else []


def effective_stall_id(
    products_by_id: dict[int, Any],
    skus_by_id: dict[int, Any],
    *,
    product_id: int | None,
    sku_id: int | None = None,
) -> int | None:
    sku_stall_id = int_value(getattr(skus_by_id.get(sku_id or 0), "stall_id", None))
    if sku_stall_id is not None:
        return sku_stall_id
    return int_value(getattr(products_by_id.get(product_id or 0), "stall_id", None))


def sku_terms(sku: Any) -> list[str]:
    terms = [
        text_value(getattr(sku, "name", "")),
        text_value(getattr(sku, "code", "")),
        *record_keywords(sku),
    ]
    seen = set()
    unique_terms: list[str] = []
    for term in terms:
        normalized = normalize_match_text(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_terms.append(term)
    return unique_terms


def sku_source_fields(
    values: dict[str, Any],
    field_codes: Iterable[str] | None = None,
) -> list[tuple[str, str, int, str]]:
    if field_codes is None:
        return [
            ("custom_sales_attr1_text", field_text(values, "custom_sales_attr1_text"), 300, "auto"),
            ("custom_sales_attr2_text", field_text(values, "custom_sales_attr2_text"), 200, "auto"),
            ("spec_text", field_text(values, "spec_text"), 260, "auto"),
            ("product_short_text", field_text(values, "product_short_text"), 240, "auto"),
            ("product_full_text", field_text(values, "product_full_text"), 220, "auto"),
            ("custom_item_remark_text", field_text(values, "custom_item_remark_text"), 100, "auto_fallback"),
            ("buyer_remark", field_text(values, "buyer_remark"), 120, "auto_fallback"),
            ("seller_remark", field_text(values, "seller_remark"), 110, "auto_fallback"),
        ]

    rows: list[tuple[str, str, int, str]] = []
    seen = set()
    for code in field_codes:
        normalized = text_value(code)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        match_type = "auto_fallback" if normalized in WODA_SKU_FALLBACK_FIELD_CODES else "auto"
        rows.append(
            (
                normalized,
                field_text(values, normalized),
                WODA_SKU_SOURCE_FIELD_SCORES.get(normalized, 100),
                match_type,
            )
        )
    return rows


def sku_auto_fields_for_rules(rules: Iterable[Any]) -> list[str]:
    fields: list[str] = []
    for rule in rules:
        match_values = rule_match_values(rule)
        selected_fields = list_value(match_values.get("fields"))
        rule_fields = [
            code
            for code in selected_fields
            if code in WODA_SKU_SOURCE_FIELD_SCORES
            and code not in WODA_PRODUCT_TEXT_FIELD_CODES
        ]
        configured = list_value(match_values.get("sku_auto_fields"))
        if configured:
            fields.extend(
                [code for code in configured if code in rule_fields]
                if rule_fields
                else DEFAULT_WODA_SKU_AUTO_FIELDS
            )
            continue
        fields.extend(rule_fields or DEFAULT_WODA_SKU_AUTO_FIELDS)

    unique: list[str] = []
    seen = set()
    for field in fields:
        if field in seen:
            continue
        seen.add(field)
        unique.append(field)
    return unique


def auto_sku_matches(
    product_id: int,
    values: dict[str, Any],
    skus_by_id: dict[int, Any],
    field_codes: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    product_skus = [
        sku
        for sku in skus_by_id.values()
        if int_value(getattr(sku, "product_id", None)) == product_id
    ]
    term_sku_ids: dict[str, set[int]] = {}
    for sku in product_skus:
        sku_id = int_value(getattr(sku, "id", None))
        if sku_id is None:
            continue
        for term in sku_terms(sku):
            normalized = normalize_match_text(term)
            if normalized:
                term_sku_ids.setdefault(normalized, set()).add(sku_id)

    candidates: list[dict[str, Any]] = []
    for sku in product_skus:
        for field_code, source_text, source_score, match_type in sku_source_fields(values, field_codes):
            if not source_text:
                continue
            for term in sku_terms(sku):
                normalized_term = normalize_match_text(term)
                if len(term_sku_ids.get(normalized_term, set())) > 1:
                    continue
                quality = sku_match_quality(source_text, term)
                if quality is None:
                    continue
                candidates.append(
                    {
                        "sku_id": int(sku.id),
                        "match_type": match_type,
                        "field": field_code,
                        "keyword": term,
                        "source_text": source_text,
                        "match_quality": quality,
                        "score": sku_match_score(source_score, term, quality),
                    }
                )

    return best_sku_matches(candidates)


def recognition_row(
    item_row: dict[str, Any],
    rules: list[Any],
    products_by_id: dict[int, Any],
    skus_by_id: dict[int, Any],
) -> dict[str, Any]:
    detail = item_row["detail"]
    values = item_row["values"]
    item_index = item_row["item_index"]
    item_count = item_row["item_count"]
    source_label = f"面单 {detail.id}-{item_index}" if item_index and item_count > 1 else f"面单 {detail.id}"
    remark_text = field_text(values, "custom_item_remark_text")
    sales_attr2_text = field_text(values, "custom_sales_attr2_text")
    base_row = {
        "detail_id": detail.id,
        "candidate_key": f"{detail.id}:{item_index or 0}",
        "source_label": source_label,
        "waybill_mode": getattr(detail, "waybill_mode", None),
        "item_index": item_index,
        "item_count": item_count,
        "print_template_config_id": int_value(values.get("print_template_config_id")),
        "print_template_config_label": text_value(values.get("print_template_config_label")),
        "raw_document_id": text_value(values.get("raw_document_id")),
        "logistics_no": text_value(values.get("logistics_no")),
        "order_no": text_value(values.get("order_no")),
        "shop_name": text_value(values.get("shop_name")),
        "source_platform": text_value(values.get("source_platform")),
        "product_text": field_text(values, "custom_product_text")
        or field_text(values, "product_short_text")
        or field_text(values, "product_full_text"),
        "sales_attr1_text": field_text(values, "custom_sales_attr1_text"),
        "sales_attr2_text": sales_attr2_text,
        "quantity_text": field_text(values, "custom_quantity_text") or field_text(values, "quantity"),
        "remark_text": remark_text,
        "product_id": None,
        "product_name": "",
        "sku_id": None,
        "sku_name": "",
        "sku_image_asset_id": None,
        "stall_id": None,
        "stall_name": "",
        "rule_id": None,
        "match_type": "",
        "match_field": "",
        "match_keyword": "",
        "status": "product_unmatched",
        "reason": "商品名称没有命中任何规则。",
    }

    applicable_rules = [rule for rule in rules if rule_applies_to_row(rule, detail, values)]
    all_product_matches = [
        match
        for rule in applicable_rules
        if (match := product_match_for_rule(rule, detail, values, products_by_id)) is not None
    ]
    product_matches = best_product_matches(all_product_matches)
    product_ids = sorted({match["product_id"] for match in product_matches})
    if not product_ids:
        return base_row
    if len(product_ids) > 1:
        names = [record_name(products_by_id, product_id, product_id) for product_id in product_ids]
        return {
            **base_row,
            "status": "conflict",
            "reason": f"商品名称同时命中多个商品：{'、'.join(names)}。",
            "product_name": "、".join(names),
        }

    product_id = product_ids[0]
    product_match = next(match for match in product_matches if match["product_id"] == product_id)
    product_rule = product_match["rule"]
    product_rules = [match["rule"] for match in all_product_matches if match["product_id"] == product_id]
    sku_matches = [
        match
        for rule in product_rules
        if (match := sku_match_for_rule(rule, detail, values)) is not None
    ]
    detail_mode = text_value(getattr(detail, "waybill_mode", "") or values.get("waybill_mode"))
    sku_auto_fields = sku_auto_fields_for_rules(product_rules) if detail_mode == WODA_MODE_CODE else None
    sku_matches = best_sku_matches(
        [
            *sku_matches,
            *auto_sku_matches(product_id, values, skus_by_id, sku_auto_fields),
        ]
    )
    sku_ids = sorted({match["sku_id"] for match in sku_matches})
    if not sku_ids:
        return {
            **base_row,
            "product_id": product_id,
            "product_name": record_name(products_by_id, product_id, product_id),
            "stall_id": effective_stall_id(products_by_id, skus_by_id, product_id=product_id),
            "rule_id": getattr(product_rule, "id", None),
            "match_field": product_match["field"],
            "match_keyword": product_match["keyword"],
            "status": "sku_unmatched",
            "reason": "已命中商品，但当前规则设置的 SKU 字段没有命中 SKU。",
        }
    if len(sku_ids) > 1:
        sku_names = [record_name(skus_by_id, sku_id, sku_id) for sku_id in sku_ids]
        return {
            **base_row,
            "product_id": product_id,
            "product_name": record_name(products_by_id, product_id, product_id),
            "rule_id": getattr(product_rule, "id", None),
            "match_field": product_match["field"],
            "match_keyword": product_match["keyword"],
            "status": "conflict",
            "reason": f"同一商品行命中多个 SKU：{'、'.join(sku_names)}。",
            "sku_name": "、".join(sku_names),
        }

    sku_id = sku_ids[0]
    first_match = next(match for match in sku_matches if match["sku_id"] == sku_id)
    rule = first_match.get("rule") or product_rule
    match_type = first_match["match_type"]
    return {
        **base_row,
        "product_id": product_id,
        "product_name": record_name(products_by_id, product_id, product_id),
        "sku_id": sku_id,
        "sku_name": record_name(skus_by_id, sku_id, sku_id),
        "sku_image_asset_id": int_value(getattr(skus_by_id.get(sku_id), "image_asset_id", None)),
        "stall_id": effective_stall_id(products_by_id, skus_by_id, product_id=product_id, sku_id=sku_id),
        "rule_id": getattr(rule, "id", None) if rule is not None else None,
        "match_type": match_type,
        "match_field": first_match["field"],
        "match_keyword": first_match["keyword"],
        "status": "matched",
        "reason": (
            "SKU 已通过备注字段自动命中。"
            if match_type == "auto_fallback"
            else "SKU 已通过销售属性自动命中。"
            if match_type == "auto"
            else "SKU 已通过备注字段兜底命中。"
            if match_type == "fallback"
            else "SKU 已按规则锁定命中。"
            if match_type == "forced"
            else "SKU 已通过销售属性命中。"
        ),
    }


def recognize_detail_items(
    details: Iterable[Any],
    rules: Iterable[Any],
    products: Iterable[Any],
    skus: Iterable[Any],
) -> list[dict[str, Any]]:
    enabled_rules = sorted(
        [rule for rule in rules if getattr(rule, "is_enabled", True) is not False and not getattr(rule, "is_deleted", False)],
        key=rule_sort_key,
    )
    products_by_id = {int(product.id): product for product in products}
    skus_by_id = {int(sku.id): sku for sku in skus}

    rows: list[dict[str, Any]] = []
    for detail in details:
        for item_row in detail_item_rows(detail):
            rows.append(recognition_row(item_row, enabled_rules, products_by_id, skus_by_id))
    return rows


def recognition_summary(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total": 0,
        "matched": 0,
        "fallback_matched": 0,
        "product_unmatched": 0,
        "sku_unmatched": 0,
        "conflict": 0,
    }
    for row in rows:
        summary["total"] += 1
        status = text_value(row.get("status"))
        if status in summary:
            summary[status] += 1
        if row.get("status") == "matched" and row.get("match_type") in {"fallback", "auto_fallback"}:
            summary["fallback_matched"] += 1
    return summary
