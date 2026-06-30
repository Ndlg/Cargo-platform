from __future__ import annotations

import json
from typing import Any, Iterable


PRODUCT_SKU_LINKING_FIELDS = ("product", "sales_attr1", "sales_attr2", "quantity", "remark")
PRODUCT_SKU_LINKING_MATCH_STATUSES = {
    "matched",
    "product_unmatched",
    "sku_unmatched",
    "sku_ambiguous",
    "image_unmatched",
    "conflict",
    "special",
}
PRODUCT_SKU_LINKING_MATCH_TYPES = {"exact", "contains"}
PRODUCT_SKU_LINKING_RESULTS_KEY = "product_sku_linking_results"
PRODUCT_SKU_LINKING_RESULT_KEY = "product_sku_linking_result"
PRODUCT_SKU_LINKING_RESULTS_CONTRACT = "product-sku-linking-results-v1"


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


def int_value(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_match_text(value: Any) -> str:
    return text_value(value).lower().replace(" ", "").replace("\t", "").replace("\n", "")


def object_name(record: Any | None) -> str:
    return text_value(getattr(record, "name", ""))


def normalize_five_fields(values: dict[str, Any] | None) -> dict[str, str]:
    source = values if isinstance(values, dict) else {}
    return {field: text_value(source.get(field)) for field in PRODUCT_SKU_LINKING_FIELDS}


def is_special_order_row(values: dict[str, Any] | None) -> bool:
    source = values if isinstance(values, dict) else {}
    status = text_value(source.get("_order_row_status") or source.get("status")).lower()
    return status == "special"


def normalize_field_list(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else [value]
    fields: list[str] = []
    seen = set()
    for raw_item in raw_items:
        field = text_value(raw_item)
        if field not in PRODUCT_SKU_LINKING_FIELDS or field in seen:
            continue
        fields.append(field)
        seen.add(field)
    return fields


def normalize_field_values(value: Any) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    return {
        field: text_value(source.get(field))
        for field in PRODUCT_SKU_LINKING_FIELDS
        if text_value(source.get(field))
    }


def object_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def normalize_binding(binding: dict[str, Any] | None) -> dict[str, Any]:
    source = binding if isinstance(binding, dict) else {}
    product_match_type = text_value(source.get("product_match_type")) or text_value(source.get("match_type")) or "contains"
    if product_match_type not in PRODUCT_SKU_LINKING_MATCH_TYPES:
        product_match_type = "contains"
    match_type = text_value(source.get("match_type")) or product_match_type
    if match_type not in PRODUCT_SKU_LINKING_MATCH_TYPES:
        match_type = product_match_type
    product_match_fields = normalize_field_list(source.get("product_match_fields"))
    if not product_match_fields:
        product_match_fields = normalize_field_list(source.get("match_fields"))
    sku_match_fields = normalize_field_list(source.get("sku_match_fields"))
    return {
        "id": int_value(source.get("id")),
        "name": text_value(source.get("name")),
        "match_fields": normalize_field_list(source.get("match_fields")),
        "field_values": normalize_field_values(source.get("field_values")),
        "scope_type": text_value(source.get("scope_type")) or "global",
        "scope_payload": object_value(source.get("scope_payload")),
        "product_match_fields": product_match_fields,
        "product_keyword": text_value(source.get("product_keyword")),
        "product_match_type": product_match_type,
        "sku_match_fields": sku_match_fields,
        "match_type": match_type,
        "product_id": int_value(source.get("product_id")),
        "sku_id": int_value(source.get("sku_id")),
        "image_asset_id": int_value(source.get("image_asset_id")),
        "source_samples": list_of_dicts(source.get("source_samples")),
        "field_sources": normalize_field_values(source.get("field_sources")),
        "preview_summary": object_value(source.get("preview_summary")),
        "revision": int_value(source.get("revision")) or 1,
        "revision_note": text_value(source.get("revision_note")),
        "priority": int_value(source.get("priority")) or 100,
        "is_enabled": source.get("is_enabled") is not False,
    }


def field_matches(actual_value: Any, expected_value: Any, *, match_type: str) -> bool:
    actual = normalize_match_text(actual_value)
    expected = normalize_match_text(expected_value)
    if not actual or not expected:
        return False
    if match_type == "contains":
        return expected in actual
    return actual == expected


def term_match_score(actual_value: Any, term_value: Any, *, bidirectional: bool = False) -> int:
    actual = normalize_match_text(actual_value)
    term = normalize_match_text(term_value)
    if not actual or not term:
        return 0
    if actual == term:
        return 10_000 + len(term)
    if term in actual:
        return 5_000 + len(term)
    if bidirectional and actual in term:
        return 1_000 + len(actual)
    return 0


def top_scored_matches(
    matches: list[tuple[Any, list[dict[str, Any]], int]],
) -> tuple[Any | None, list[dict[str, Any]], list[tuple[Any, list[dict[str, Any]], int]]]:
    if not matches:
        return None, [], []
    best_score = max(score for _target, _trace, score in matches)
    top_matches = [(target, trace, score) for target, trace, score in matches if score == best_score]
    if len(top_matches) == 1:
        target, trace, _score = top_matches[0]
        return target, trace, top_matches
    return None, [], top_matches


def binding_matches_fields(fields: dict[str, str], binding: dict[str, Any]) -> tuple[bool, list[dict[str, str]]]:
    product_keyword = text_value(binding.get("product_keyword"))
    product_match_fields = list(binding.get("product_match_fields") or [])
    match_fields = product_match_fields or binding["match_fields"]
    field_values = binding["field_values"]
    if not binding.get("is_enabled"):
        return False, []
    if not match_fields:
        return False, []

    trace: list[dict[str, str]] = []
    if product_keyword:
        any_matched = False
        for field in match_fields:
            actual = fields.get(field, "")
            matched = field_matches(actual, product_keyword, match_type=binding["product_match_type"])
            trace.append(
                {
                    "field": field,
                    "expected": product_keyword,
                    "actual": actual,
                    "matched": "yes" if matched else "no",
                }
            )
            any_matched = any_matched or matched
        return any_matched, trace

    for field in match_fields:
        expected = field_values.get(field)
        actual = fields.get(field, "")
        matched = field_matches(actual, expected, match_type=binding["match_type"])
        trace.append(
            {
                "field": field,
                "expected": text_value(expected),
                "actual": actual,
                "matched": "yes" if matched else "no",
            }
        )
        if not matched:
            return False, trace
    return True, trace


def list_of_text(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text_value(item) for item in value if text_value(item)]
    text = text_value(value)
    return [text] if text else []


def sku_terms(sku: Any) -> list[str]:
    terms = [object_name(sku), text_value(getattr(sku, "code", ""))]
    terms.extend(list_of_text(getattr(sku, "keywords", None)))
    return [term for term in terms if term]


def sku_public_summary(sku: Any) -> dict[str, Any]:
    return {
        "id": int_value(getattr(sku, "id", None)),
        "name": object_name(sku),
        "product_id": int_value(getattr(sku, "product_id", None)),
    }


def stall_summary(product: Any | None, sku: Any | None = None) -> dict[str, Any] | None:
    sku_stall_id = int_value(getattr(sku, "stall_id", None)) if sku is not None else None
    sku_stall_name = text_value(getattr(sku, "stall_name", "")) if sku is not None else ""
    product_stall_id = int_value(getattr(product, "stall_id", None)) if product is not None else None
    product_stall_name = text_value(getattr(product, "stall_name", "")) if product is not None else ""

    stall_id = sku_stall_id or product_stall_id
    stall_name = sku_stall_name or product_stall_name
    if stall_id is None and not stall_name:
        return None
    return {"id": stall_id, "name": stall_name}


def auto_sku_for_rule(
    fields: dict[str, str],
    binding: dict[str, Any],
    *,
    product_id: int,
    skus_by_id: dict[int, Any],
) -> tuple[Any | None, str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    match_fields = list(binding.get("sku_match_fields") or [])
    if not match_fields:
        return None, None, [], []

    field_terms = [(field, fields.get(field, "")) for field in match_fields if fields.get(field)]
    if not field_terms:
        return None, None, [], []

    matches: list[tuple[Any, list[dict[str, Any]], int]] = []
    for sku in skus_by_id.values():
        if int_value(getattr(sku, "product_id", None)) != product_id:
            continue
        terms = sku_terms(sku)
        trace: list[dict[str, Any]] = []
        best_trace: dict[str, Any] | None = None
        best_score = 0
        for field, field_value in field_terms:
            for term in terms:
                score = term_match_score(field_value, term, bidirectional=True)
                trace.append(
                    {
                        "field": field,
                        "actual": field_value,
                        "sku_term": term,
                        "matched": "yes" if score > 0 else "no",
                        "score": score,
                    }
                )
                if score > best_score:
                    best_score = score
                    best_trace = trace[-1]
        if best_score > 0:
            matches.append((sku, [best_trace] if best_trace else trace, best_score))

    if not matches:
        return None, None, [], []
    sku, trace, top_matches = top_scored_matches(matches)
    if sku is None:
        return (
            None,
            "sku_ambiguous",
            [trace for _sku, trace, _score in top_matches],
            [sku_public_summary(candidate_sku) for candidate_sku, _trace, _score in top_matches],
        )
    return sku, None, trace, []


def image_for_linking_rule(
    binding: dict[str, Any],
    *,
    sku: Any | None,
    images_by_id: dict[int, Any],
) -> Any | None:
    explicit_image_id = int_value(binding.get("image_asset_id"))
    if explicit_image_id is not None:
        return images_by_id.get(explicit_image_id)
    sku_image_id = int_value(getattr(sku, "image_asset_id", None))
    if sku_image_id is not None:
        return images_by_id.get(sku_image_id)
    return None


def product_sku_linking_result(
    fields: dict[str, str],
    binding: dict[str, Any],
    *,
    products_by_id: dict[int, Any],
    skus_by_id: dict[int, Any],
    images_by_id: dict[int, Any],
) -> dict[str, Any]:
    product_id = int_value(binding.get("product_id"))
    sku_id = int_value(binding.get("sku_id"))
    product = products_by_id.get(product_id or 0)
    sku = skus_by_id.get(sku_id or 0)
    sku_match_trace: list[dict[str, Any]] = []
    if product is None:
        return linking_exception_result(
            fields,
            "product_unmatched",
            "商品匹配学习记录缺少有效商品绑定。",
            binding=binding,
        )
    if sku is None and sku_id is None:
        sku, sku_issue, sku_match_trace, sku_candidates = auto_sku_for_rule(
            fields,
            binding,
            product_id=product_id or 0,
            skus_by_id=skus_by_id,
        )
        if sku_issue == "sku_ambiguous":
            return linking_exception_result(
                fields,
                "sku_ambiguous",
                "商品已命中，但 SKU 匹配字段命中多个 SKU，请选择或绑定具体 SKU。",
                binding=binding,
                product=product,
            ) | {"sku_match_trace": sku_match_trace, "sku_candidates": sku_candidates}
        sku_id = int_value(getattr(sku, "id", None))
    if sku is None:
        return linking_exception_result(
            fields,
            "sku_unmatched",
            "商品已命中，但没有可用 SKU 绑定或 SKU 匹配字段未命中。",
            binding=binding,
            product=product,
        )
    if int_value(getattr(sku, "product_id", None)) != product_id:
        return linking_exception_result(
            fields,
            "conflict",
            "SKU 不属于当前商品绑定。",
            binding=binding,
            product=product,
            sku=sku,
            conflict_kind="asset_binding",
        )

    image = image_for_linking_rule(binding, sku=sku, images_by_id=images_by_id)
    if image is None:
        return linking_exception_result(
            fields,
            "image_unmatched",
            "商品和 SKU 已命中，但没有可用图片绑定。",
            binding=binding,
            product=product,
            sku=sku,
        )

    return {
        "input": fields,
        "product": {"id": product_id, "name": object_name(product)},
        "sku": {"id": sku_id, "name": object_name(sku)},
        "image": {"id": int(image.id), "name": object_name(image), "file_path": text_value(getattr(image, "file_path", ""))},
        "stall": stall_summary(product, sku),
        "match_status": "matched",
        "exception_reason": "",
        "matched_linking_rule": linking_rule_public_summary(binding),
        "match_source": "user_learning_rule",
        "sku_match_trace": sku_match_trace,
    }


def linking_exception_result(
    fields: dict[str, str],
    match_status: str,
    exception_reason: str,
    *,
    binding: dict[str, Any] | None = None,
    product: Any | None = None,
    sku: Any | None = None,
    conflict_kind: str = "",
    conflict_linking_rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    matched_rule = linking_rule_public_summary(binding) if binding else None
    return {
        "input": fields,
        "product": {"id": int(product.id), "name": object_name(product)} if product is not None else None,
        "sku": {"id": int(sku.id), "name": object_name(sku)} if sku is not None else None,
        "image": None,
        "stall": stall_summary(product, sku),
        "match_status": match_status,
        "exception_reason": exception_reason,
        "matched_linking_rule": matched_rule,
        "conflict_kind": conflict_kind,
        "conflict_linking_rules": conflict_linking_rules or [],
        "match_source": "user_learning_rule" if binding else "",
    }


def linking_rule_public_summary(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int_value(binding.get("id")),
        "name": text_value(binding.get("name")),
        "scope_type": text_value(binding.get("scope_type")) or "global",
        "scope_payload": dict(binding.get("scope_payload") or {}),
        "product_match_fields": list(binding.get("product_match_fields") or []),
        "product_keyword": text_value(binding.get("product_keyword")),
        "product_match_type": text_value(binding.get("product_match_type")) or "contains",
        "sku_match_fields": list(binding.get("sku_match_fields") or []),
        "match_fields": list(binding.get("match_fields") or []),
        "field_values": dict(binding.get("field_values") or {}),
        "match_type": text_value(binding.get("match_type")) or "exact",
        "product_id": int_value(binding.get("product_id")),
        "sku_id": int_value(binding.get("sku_id")),
        "image_asset_id": int_value(binding.get("image_asset_id")),
        "source_samples": list(binding.get("source_samples") or []),
        "field_sources": dict(binding.get("field_sources") or {}),
        "preview_summary": dict(binding.get("preview_summary") or {}),
        "revision": int_value(binding.get("revision")) or 1,
        "revision_note": text_value(binding.get("revision_note")),
        "priority": int_value(binding.get("priority")) or 100,
        "is_enabled": binding.get("is_enabled") is not False,
    }


def linking_rule_preview_rows(
    result_rows: Iterable[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rule in rules:
        summary = linking_rule_public_summary(rule)
        exact_target = (
            text_value(summary.get("product_id")),
            text_value(summary.get("sku_id")),
            text_value(summary.get("image_asset_id")),
        )
        matched = 0
        conflict = 0
        unmatched = 0
        for row in result_rows:
            if row.get("match_status") == "special":
                continue
            row_rule = row.get("matched_linking_rule")
            if not isinstance(row_rule, dict):
                unmatched += 1
                continue
            row_target = (
                text_value(row_rule.get("product_id")),
                text_value(row_rule.get("sku_id")),
                text_value(row_rule.get("image_asset_id")),
            )
            if row_target != exact_target:
                continue
            if row.get("match_status") == "conflict":
                conflict += 1
            elif row.get("match_status") == "matched":
                matched += 1
        rows.append(
            {
                **summary,
                "preview": {
                    "matched_count": matched,
                    "conflict_count": conflict,
                    "unmatched_count": unmatched,
                },
            }
        )
    return rows


def product_sku_linking_samples(
    rows: Iterable[dict[str, Any]],
    *,
    sample_limit: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    samples = {status: [] for status in sorted(PRODUCT_SKU_LINKING_MATCH_STATUSES)}
    for row in rows:
        status = text_value(row.get("match_status"))
        if status not in samples or len(samples[status]) >= sample_limit:
            continue
        samples[status].append(
            {
                "row_index": row.get("row_index"),
                "match_status": status,
                "input": dict(row.get("input") or {}),
                "product": row.get("product"),
                "sku": row.get("sku"),
                "image": row.get("image"),
                "stall": row.get("stall"),
                "exception_reason": text_value(row.get("exception_reason")),
                "matched_linking_rule": row.get("matched_linking_rule"),
                "match_source": text_value(row.get("match_source")),
                "catalog_match_trace": row.get("catalog_match_trace") if isinstance(row.get("catalog_match_trace"), list) else [],
                "conflict_kind": text_value(row.get("conflict_kind")),
                "conflict_linking_rules": row.get("conflict_linking_rules") if isinstance(row.get("conflict_linking_rules"), list) else [],
                "sku_candidates": row.get("sku_candidates") if isinstance(row.get("sku_candidates"), list) else [],
            }
        )
    return samples


def image_match_text_for_result(row: dict[str, Any]) -> str:
    parts: list[str] = []
    fields = object_value(row.get("input"))
    for field in PRODUCT_SKU_LINKING_FIELDS:
        value = text_value(fields.get(field))
        if value:
            parts.append(value)
    for key in ("product", "sku", "image"):
        target = row.get(key)
        if isinstance(target, dict):
            value = text_value(target.get("name"))
            if value:
                parts.append(value)
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part in seen:
            continue
        deduped.append(part)
        seen.add(part)
    return " ".join(deduped)


def exportable_product_sku_linking_result(row: dict[str, Any]) -> dict[str, Any]:
    product = object_value(row.get("product"))
    sku = object_value(row.get("sku"))
    image = object_value(row.get("image"))
    stall = object_value(row.get("stall"))
    return {
        "contract": PRODUCT_SKU_LINKING_RESULTS_CONTRACT,
        "standard_fields": dict(row.get("input") or {}),
        "product": text_value(product.get("name")),
        "product_id": int_value(product.get("id")),
        "sku": text_value(sku.get("name")),
        "sku_id": int_value(sku.get("id")),
        "image": text_value(image.get("name")),
        "image_asset_id": int_value(image.get("id")),
        "stall": {"id": int_value(stall.get("id")), "name": text_value(stall.get("name"))} if stall else None,
        "stall_id": int_value(stall.get("id")),
        "stall_name": text_value(stall.get("name")),
        "match_status": text_value(row.get("match_status")),
        "exception_reason": text_value(row.get("exception_reason")),
        "matched_rule": row.get("matched_linking_rule"),
        "conflict_kind": text_value(row.get("conflict_kind")),
        "conflict_linking_rules": row.get("conflict_linking_rules") if isinstance(row.get("conflict_linking_rules"), list) else [],
        "sku_candidates": row.get("sku_candidates") if isinstance(row.get("sku_candidates"), list) else [],
        "match_source": text_value(row.get("match_source")),
        "catalog_match_trace": row.get("catalog_match_trace") if isinstance(row.get("catalog_match_trace"), list) else [],
        "match_trace": row.get("match_trace") if isinstance(row.get("match_trace"), list) else [],
        "sku_match_trace": row.get("sku_match_trace") if isinstance(row.get("sku_match_trace"), list) else [],
        "image_match_text": image_match_text_for_result(row),
    }


def preview_product_sku_linking(
    rows: Iterable[dict[str, Any]],
    definitions: Iterable[dict[str, Any]],
    *,
    products: Iterable[Any],
    skus: Iterable[Any],
    images: Iterable[Any],
) -> dict[str, Any]:
    normalized_definitions = [normalize_binding(definition) for definition in definitions]
    products_by_id = {int(product.id): product for product in products}
    skus_by_id = {int(sku.id): sku for sku in skus}
    images_by_id = {int(image.id): image for image in images}
    result_rows: list[dict[str, Any]] = []

    for index, row in enumerate(rows, start=1):
        fields = normalize_five_fields(row)
        if is_special_order_row(row):
            result_rows.append(
                linking_exception_result(
                    fields,
                    "special",
                    "特殊单不参与商品、SKU、图片匹配。",
                )
                | {
                    "row_index": index,
                    "match_trace": [],
                    "match_source": "",
                    "review_reason": text_value(row.get("_order_row_review_reason") or row.get("review_reason")),
                }
            )
            continue
        matches: list[tuple[dict[str, Any], list[dict[str, str]]]] = []
        for definition in normalized_definitions:
            matched, trace = binding_matches_fields(fields, definition)
            if matched:
                matches.append((definition, trace))

        if not matches:
            result_rows.append(
                linking_exception_result(
                    fields,
                    "product_unmatched",
                    "没有用户确认的商品匹配学习记录命中这行订单。",
                )
                | {"row_index": index, "match_trace": [], "match_source": "user_learning_rule"}
            )
            continue

        matched_targets = {
            (
                text_value(int_value(definition.get("product_id"))),
                text_value(int_value(definition.get("sku_id"))),
                text_value(int_value(definition.get("image_asset_id"))),
            )
            for definition, _trace in matches
        }
        if len(matched_targets) > 1:
            conflict_rules = [linking_rule_public_summary(definition) for definition, _trace in matches]
            result_rows.append(
                linking_exception_result(
                    fields,
                    "conflict",
                    "同一行订单命中多个商品匹配学习记录绑定。",
                    binding=matches[0][0],
                    conflict_kind="multiple_rules",
                    conflict_linking_rules=conflict_rules,
                )
                | {
                    "row_index": index,
                    "match_trace": [trace for _definition, trace in matches],
                }
            )
            continue

        definition, trace = matches[0]
        result_rows.append(
            product_sku_linking_result(
                fields,
                definition,
                products_by_id=products_by_id,
                skus_by_id=skus_by_id,
                images_by_id=images_by_id,
            )
            | {"row_index": index, "match_trace": trace}
        )

    return {
        "contract": product_sku_linking_contract(),
        "summary": product_sku_linking_summary(result_rows),
        "samples": product_sku_linking_samples(result_rows),
        "linking_rules": linking_rule_preview_rows(result_rows, normalized_definitions),
        "rows": result_rows,
    }


def product_sku_linking_summary(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    summary = {status: 0 for status in sorted(PRODUCT_SKU_LINKING_MATCH_STATUSES)}
    summary["total"] = 0
    for row in rows:
        summary["total"] += 1
        status = text_value(row.get("match_status"))
        if status in summary:
            summary[status] += 1
    return summary


def product_sku_linking_contract() -> dict[str, Any]:
    return {
        "module": "product_sku_linking",
        "module_name": "商品匹配模块",
        "technical_name": "product_sku_linking",
        "input_fields": list(PRODUCT_SKU_LINKING_FIELDS),
        "output_fields": ["product", "sku", "image", "match_status", "exception_reason"],
        "output_name": "商品匹配结果",
        "match_statuses": sorted(PRODUCT_SKU_LINKING_MATCH_STATUSES),
        "allowed_assets": ["products", "product_skus", "image_assets"],
        "rule_learning_model": "progressive_user_confirmed_five_field_product_matching_rules",
        "rule_requirements": [
            "workspace",
            "global_rule_scope",
            "explainable_match_fields",
            "product_keyword",
            "sku_match_settings",
            "source_five_field_samples",
            "field_sources",
            "target_product_sku_image",
            "preview_match_counts",
            "conflict_handling",
            "can_disable_or_revise",
        ],
        "persistence": {
            "rule_table": "product_matching_rules",
            "result_key": PRODUCT_SKU_LINKING_RESULTS_KEY,
            "single_result_key": PRODUCT_SKU_LINKING_RESULT_KEY,
        },
        "minimal_endpoints": [
            "GET /product-sku-linking/contract",
            "GET /product-sku-linking/rules",
            "POST /product-sku-linking/rules",
            "PATCH /product-sku-linking/rules/{rule_id}",
            "DELETE /product-sku-linking/rules/{rule_id}",
            "POST /product-sku-linking/preview",
            "POST /product-sku-linking/apply",
        ],
        "forbidden_inputs": [
            "raw_capture_record",
            "waybill_sample",
            "text_blocks",
            "waybill_similarity",
            "cluster_key",
        ],
        "forbidden_methods": [
            "same_or_similar_waybill_auto_grouping",
            "automatic_product_classification_from_waybill_similarity",
            "upstream_mapping_ui_maintenance",
            "hard_coded_business_guessing",
        ],
    }
