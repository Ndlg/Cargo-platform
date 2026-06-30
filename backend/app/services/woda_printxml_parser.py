from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.services.waybill_text_blocks import format_field_output, split_candidate_segments
from app.services.woda_fields import WODA_CUSTOM_FIELD_ITEM_KEYS, woda_mapping_source_field, woda_mapping_source_fields


_SIZE_TEXT = r"(?:[2-4]\d(?:\.5)?|50)"
_QTY_PATTERN = re.compile(r"(?:\*|x|X|×)\s*(\d+)")
_SIZE_QTY_PATTERN = re.compile(rf"(?:^|[^\d])({_SIZE_TEXT})\s*(?:\*|x|X|×)\s*(\d+)(?!\d)")
_NAMED_SIZE_PATTERN = re.compile(rf"(?:销售属性2|属性2|鞋码|尺码|码)[:：\s]*({_SIZE_TEXT})(?!\d)")
_LOOSE_SIZE_PATTERN = re.compile(rf"(?:^|[^\d])({_SIZE_TEXT})(?!\d)")
_REMARK_FIELD_PATTERN = re.compile(
    r"(颜色分类|商品名称|商品名|销售属性1|销售属性2|属性1|属性2|规格|鞋码|尺码|买家备注|卖家备注|卖家留言|备注|留言|color|size|sku)",
    re.IGNORECASE,
)
_IGNORED_LINE_PATTERN = re.compile(r"(运单号|物流单号|快递单号|YT\d{8,}|\[[A-Z]{1,4}\d{8,})", re.IGNORECASE)
_STRUCTURE_LABELS = {
    "template_mapped": "按模板定义",
    "single_line_standard": "单行标准",
    "reverse_field_order": "属性在前",
    "multi_item": "多商品",
    "remark_product": "备注补充",
    "unknown": "需复核结构",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return "".join(
        ch
        for ch in str(value)
        if (ch >= " " or ch in "\n\t") and unicodedata.category(ch) != "Cf"
    ).strip()


def _format_configured_field_output(target_field: str, value: Any) -> str:
    return format_field_output(target_field, value)


def _lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in _text(text).splitlines()
        if line.strip() and not _IGNORED_LINE_PATTERN.search(line)
    ]


def _split_parts(line: str) -> list[str]:
    normalized = re.sub(r"[；;]", "，", _text(line))
    return [part.strip() for part in re.split(r"[，,、]+", normalized) if part.strip()]


def _count_quantities(text: str) -> int:
    return len(_QTY_PATTERN.findall(_text(text)))


def _has_quantity(text: str) -> bool:
    return _count_quantities(text) > 0


def _is_remark_line(text: str) -> bool:
    return bool(_REMARK_FIELD_PATTERN.search(_text(text)))


def _quantity(text: str) -> str:
    match = _QTY_PATTERN.search(_text(text))
    return match.group(1) if match else ""


def _quantity_tail(text: str) -> tuple[str, str] | None:
    match = re.search(r"(.+?)\s*(?:\*|x|X|×)\s*(\d+)\s*$", _text(text))
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _size(text: str) -> str:
    cleaned = _text(text)
    for pattern in (_NAMED_SIZE_PATTERN, _SIZE_QTY_PATTERN, _LOOSE_SIZE_PATTERN):
        match = pattern.search(cleaned)
        if match and match.group(1):
            return match.group(1)
    return ""


def _strip_quantity_suffix(text: str) -> str:
    cleaned = _text(text)
    cleaned = re.sub(rf"[，,、\s]*(?:{_SIZE_TEXT})?\s*(?:\*|x|X|×)\s*\d+\s*$", "", cleaned)
    return re.sub(r"[，,、；;:\s]+$", "", cleaned).strip()


def _strip_size_from_spec(text: str, size: str) -> str:
    cleaned = _text(text)
    if size:
        cleaned = re.sub(rf"[，,、\s]*{re.escape(size)}\s*$", "", cleaned)
    return re.sub(r"[，,、；;:\s]+$", "", cleaned).strip()


def _quantity_from_line(text: str) -> str:
    quantity = _quantity(text)
    if quantity:
        return quantity
    cleaned = _text(text)
    return cleaned if re.fullmatch(r"\d+", cleaned) else ""


def _extract_configured_line_value(line: str, extractor: str) -> str:
    cleaned = _text(line)
    if not cleaned:
        return ""
    if extractor == "product_text":
        return _strip_quantity_suffix(cleaned)
    if extractor == "sales_attr1_text":
        without_quantity = _strip_quantity_suffix(cleaned)
        return _strip_size_from_spec(without_quantity, _size(without_quantity))
    if extractor == "sales_attr2_text":
        return _size(cleaned)
    if extractor == "quantity_text":
        return _quantity_from_line(cleaned)
    if extractor == "remark_text":
        return cleaned
    return cleaned


def _candidate_segments(text: str) -> list[str]:
    return split_candidate_segments("\n".join(_lines(text)) or _text(text))


def _candidate_segment_value(lines: list[str], source_field: str) -> str:
    match = re.fullmatch(r"(?:token|candidate)_(\d+)", source_field)
    if not match:
        return ""
    index = int(match.group(1)) - 1
    segments = _candidate_segments("\n".join(lines))
    return segments[index] if 0 <= index < len(segments) else ""


def _named_value(text: str, names: list[str]) -> str:
    name_pattern = "|".join(re.escape(name) for name in names)
    match = re.search(rf"(?:{name_pattern})[:：]\s*([^;；\n]+)", _text(text))
    return match.group(1).strip() if match else ""


def _remark_text(lines: list[str]) -> str:
    return "\n".join(_text(line) for line in lines if _is_remark_line(line) and _text(line))


def _item_from_single_standard_line(line: str) -> dict[str, str] | None:
    parts = _split_parts(line)
    if len(parts) < 2:
        return None
    if len(parts) == 2 and _has_quantity(parts[1]):
        sales_attr2_text = _size(parts[1])
        sales_attr1_text = _strip_quantity_suffix(parts[1])
        return {
            "product_text": parts[0],
            "sales_attr1_text": sales_attr1_text,
            "sales_attr2_text": sales_attr2_text,
            "spec_text": sales_attr1_text,
            "size_text": sales_attr2_text,
            "quantity_text": _quantity(parts[1]),
            "raw_text": line,
        }
    if len(parts) < 3:
        return None
    tail_quantity = _quantity_tail(parts[-1])
    if not tail_quantity:
        return None
    size_qty = _SIZE_QTY_PATTERN.search(parts[-1]) or _SIZE_QTY_PATTERN.search(line)
    sales_attr1_text = "，".join(parts[1:-1])
    sales_attr2_text = size_qty.group(1) if size_qty else tail_quantity[0]
    quantity_text = size_qty.group(2) if size_qty else tail_quantity[1]
    return {
        "product_text": parts[0],
        "sales_attr1_text": sales_attr1_text,
        "sales_attr2_text": sales_attr2_text,
        "spec_text": sales_attr1_text,
        "size_text": sales_attr2_text,
        "quantity_text": quantity_text,
        "raw_text": line,
    }


def _item_from_reverse_lines(lines: list[str]) -> dict[str, str] | None:
    if len(lines) == 1:
        parts = _split_parts(lines[0])
        if len(parts) != 2 or not _has_quantity(parts[1]):
            return None
        product_candidate = _strip_quantity_suffix(parts[1])
        if len(product_candidate) < 8 or len(product_candidate) <= len(parts[0]):
            return None
        size = _size(parts[0]) or _size(parts[1])
        return {
            "product_text": product_candidate,
            "spec_text": _strip_size_from_spec(parts[0], size),
            "size_text": size,
            "quantity_text": _quantity(parts[1]),
            "raw_text": lines[0],
        }

    if len(lines) != 2:
        return None
    first, second = lines
    if _has_quantity(first) or not _has_quantity(second):
        return None
    if _is_remark_line(first) or _is_remark_line(second):
        return None
    size = _size(first) or _size(second)
    return {
        "product_text": _strip_quantity_suffix(second),
        "spec_text": _strip_size_from_spec(first, size),
        "size_text": size,
        "quantity_text": _quantity(second),
        "raw_text": "\n".join(lines),
    }


def _item_from_remark(lines: list[str]) -> dict[str, str] | None:
    remark_lines = [line for line in lines if _is_remark_line(line)]
    if not remark_lines:
        return None
    product_line = next((line for line in lines if not _is_remark_line(line) and _has_quantity(line)), "")
    product_line = product_line or next((line for line in lines if not _is_remark_line(line)), "")
    remark_line = remark_lines[0]
    product_from_remark = _named_value(remark_line, ["商品名称", "商品名", "商品"])
    product_from_line = _strip_quantity_suffix(product_line)
    spec_from_remark = _named_value(remark_line, ["销售属性1", "属性1", "规格", "颜色分类"])
    sales_attr2_from_remark = _named_value(remark_line, ["销售属性2", "属性2", "鞋码", "尺码", "码"])
    remark_text = _remark_text(remark_lines)
    return {
        "product_text": product_from_remark or product_from_line or remark_line,
        "spec_text": spec_from_remark or product_from_line,
        "sales_attr1_text": spec_from_remark or product_from_line,
        "sales_attr2_text": sales_attr2_from_remark or _size(remark_line) or _size(product_line),
        "remark_text": remark_text,
        "size_text": sales_attr2_from_remark or _size(remark_line) or _size(product_line),
        "quantity_text": _quantity(product_line) or "1",
        "raw_text": "\n".join(lines),
    }


def _apply_remark_overrides(item: dict[str, str], product_line: str, remark_line: str) -> dict[str, str]:
    remark_item = _item_from_remark([product_line, remark_line])
    if not remark_item:
        return item

    updated = dict(item)
    remark_text = remark_item.get("remark_text") or _text(remark_line)
    raw_text = _text(updated.get("raw_text") or product_line)
    if remark_text and remark_text not in raw_text:
        raw_text = f"{raw_text}\n{remark_text}" if raw_text else remark_text
    updated["remark_text"] = remark_text
    updated["raw_text"] = raw_text

    remark_attr1 = _text(remark_item.get("sales_attr1_text") or remark_item.get("spec_text"))
    if remark_attr1:
        updated["sales_attr1_text"] = remark_attr1
        updated["spec_text"] = remark_attr1

    remark_attr2 = _text(remark_item.get("sales_attr2_text") or remark_item.get("size_text"))
    if remark_attr2:
        updated["sales_attr2_text"] = remark_attr2
        updated["size_text"] = remark_attr2

    if not _text(updated.get("quantity_text")) and _text(remark_item.get("quantity_text")):
        updated["quantity_text"] = _text(remark_item.get("quantity_text"))

    return updated


def _items_from_multi(lines: list[str]) -> list[dict[str, str]]:
    product_lines = [(index, line) for index, line in enumerate(lines) if _has_quantity(line)]
    remark_lines = [line for line in lines if _is_remark_line(line)]
    items: list[dict[str, str]] = []
    for index, (line_index, line) in enumerate(product_lines):
        standard = _item_from_single_standard_line(line)
        remark_line = remark_lines[index] if index < len(remark_lines) else ""
        if standard:
            if remark_line:
                standard = _apply_remark_overrides(standard, line, remark_line)
            items.append(standard)
            continue

        previous_line = lines[line_index - 1] if line_index > 0 else ""
        if previous_line and not _has_quantity(previous_line) and not _is_remark_line(previous_line):
            reverse_item = _item_from_reverse_lines([previous_line, line])
            if reverse_item:
                if remark_line:
                    reverse_item = {
                        **reverse_item,
                        "remark_text": _text(remark_line),
                        "raw_text": f'{reverse_item["raw_text"]}\n{remark_line}',
                    }
                items.append(reverse_item)
                continue

        if remark_line:
            item = _item_from_remark([line, remark_line])
            if item:
                items.append(item)
                continue

        items.append(
            {
                "product_text": _strip_quantity_suffix(line),
                "remark_text": "",
                "spec_text": "",
                "size_text": _size(line),
                "quantity_text": _quantity(line),
                "raw_text": line,
            }
        )
    return [
        item
        for item in items
        if item["product_text"] or item["spec_text"] or item["size_text"] or item["quantity_text"]
    ]


def _has_remark_product_shape(lines: list[str]) -> bool:
    if not any(_is_remark_line(line) for line in lines):
        return False
    for line in lines:
        if not _has_quantity(line):
            continue
        parts = _split_parts(line)
        if len(parts) < 3:
            continue
        tail = parts[-1]
        if _quantity_tail(tail) and not _size(tail):
            return True
    return False


def _item_with_sales_attrs(item: dict[str, str]) -> dict[str, str]:
    return {
        **item,
        "sales_attr1_text": item.get("sales_attr1_text") or item.get("spec_text", ""),
        "sales_attr2_text": item.get("sales_attr2_text") or item.get("size_text", ""),
    }


def _auto_item_from_lines(lines: list[str]) -> dict[str, str] | None:
    raw_text = "\n".join(lines)
    if not lines:
        return None

    quantity_count = _count_quantities(raw_text)
    quantity_line_count = len([line for line in lines if _has_quantity(line)])
    remark_line_count = len([line for line in lines if _is_remark_line(line)])
    if remark_line_count >= 1 and _has_remark_product_shape(lines):
        items = _items_from_multi(lines)
        return items[0] if len(items) == 1 else None
    if quantity_count > 1 or quantity_line_count > 1 or (quantity_count >= 1 and remark_line_count > 1):
        items = _items_from_multi(lines)
        return items[0] if len(items) == 1 else None
    if (remark_item := _item_from_remark(lines)) and remark_line_count >= 1:
        return remark_item
    if reverse_item := _item_from_reverse_lines(lines):
        return reverse_item
    if len(lines) == 1:
        return _item_from_single_standard_line(lines[0])
    return None


def _apply_config_field_mappings(item: dict[str, str], config: dict[str, Any] | None) -> dict[str, str]:
    if not config or not isinstance(config.get("field_mappings"), dict):
        return item

    source_item = _item_with_sales_attrs(item)
    mapped_item = dict(item)
    for target_field, target_item_key in WODA_CUSTOM_FIELD_ITEM_KEYS.items():
        mapping = config["field_mappings"].get(target_field)
        source_field = woda_mapping_source_field(mapping, target_field)
        source_item_key = WODA_CUSTOM_FIELD_ITEM_KEYS.get(source_field)
        if not source_item_key:
            continue
        mapped_item[target_item_key] = _format_configured_field_output(target_field, source_item.get(source_item_key))

    mapped_item["spec_text"] = mapped_item.get("sales_attr1_text", "")
    mapped_item["size_text"] = mapped_item.get("sales_attr2_text", "")
    return mapped_item


def _configured_line_index(mapping: Any) -> int | None:
    if not isinstance(mapping, dict):
        return None
    try:
        line_index = int(mapping.get("line_index"))
    except (TypeError, ValueError):
        return None
    return line_index if line_index >= 0 else None


def _configured_line_item(lines: list[str], config: dict[str, Any] | None) -> dict[str, str] | None:
    if not config or not isinstance(config.get("field_mappings"), dict):
        return None

    item: dict[str, str] = {}
    used_indexes: list[int] = []
    auto_item = _item_with_sales_attrs(_auto_item_from_lines(lines) or {})
    for target_field, target_item_key in WODA_CUSTOM_FIELD_ITEM_KEYS.items():
        mapping = config["field_mappings"].get(target_field)
        line_index = _configured_line_index(mapping)
        source_fields = woda_mapping_source_fields(mapping, target_field)
        if not source_fields:
            continue
        if not any(source_field.startswith("token_") or source_field.startswith("candidate_") for source_field in source_fields) and (line_index is None or line_index >= len(lines)):
            continue
        extractor = _text(mapping.get("extractor")) if isinstance(mapping, dict) else ""
        segment_text = _text(mapping.get("segment_text")) if isinstance(mapping, dict) else ""
        values: list[str] = []
        for source_field in source_fields:
            source_item_key = WODA_CUSTOM_FIELD_ITEM_KEYS.get(source_field)
            if source_field.startswith("token_") or source_field.startswith("candidate_"):
                values.append(_candidate_segment_value(lines, source_field))
            elif source_item_key:
                values.append(_text(auto_item.get(source_item_key)))
        value = " ".join(part for part in values if part).strip()
        value = value or (_extract_configured_line_value(lines[line_index], extractor) if line_index is not None and line_index < len(lines) else "")
        value = value or segment_text
        if value:
            item[target_item_key] = _format_configured_field_output(target_field, value)
            if line_index is not None and line_index < len(lines):
                used_indexes.append(line_index)

    if not any(_text(item.get(key)) for key in ("product_text", "sales_attr1_text", "sales_attr2_text", "quantity_text")):
        return None

    if item.get("sales_attr1_text"):
        item["spec_text"] = item["sales_attr1_text"]
    if item.get("sales_attr2_text"):
        item["size_text"] = item["sales_attr2_text"]
    item.setdefault("remark_text", "")
    item.setdefault("quantity_text", "")
    item["raw_text"] = _text(auto_item.get("raw_text")) or "\n".join(lines[index] for index in sorted(set(used_indexes)))
    return item


def _config_remark_text(lines: list[str], config: dict[str, Any] | None) -> str:
    if not config:
        return ""
    line_index = config.get("remark_line_index")
    if isinstance(line_index, int) and 0 <= line_index < len(lines):
        return _text(lines[line_index])
    segment_text = _text(config.get("item_remark_segment_text") or config.get("remark_segment_text"))
    if not segment_text:
        return ""
    for line in lines:
        if segment_text in line:
            return _text(line)
    return segment_text


def parse_woda_custom_structure(text: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    lines = _lines(text)
    raw_text = "\n".join(lines)
    template_item = _configured_line_item(lines, config)
    template_mapped = template_item is not None
    if template_item:
        kind = "template_mapped"
        items = [template_item]
        reason = "按用户保存的模板字段位置读取"
    elif not lines:
        kind = "unknown"
        items: list[dict[str, str]] = []
        reason = "没有可识别文字"
    else:
        quantity_count = _count_quantities(raw_text)
        quantity_line_count = len([line for line in lines if _has_quantity(line)])
        remark_line_count = len([line for line in lines if _is_remark_line(line)])
        if remark_line_count >= 1 and _has_remark_product_shape(lines):
            kind = "remark_product"
            items = _items_from_multi(lines)
            reason = "商品行带备注补充，按备注商品结构识别"
        elif quantity_count > 1 or quantity_line_count > 1 or (quantity_count >= 1 and remark_line_count > 1):
            kind = "multi_item"
            items = _items_from_multi(lines)
            reason = "出现多个数量或多条备注商品文字"
        elif (remark_item := _item_from_remark(lines)) and remark_line_count >= 1:
            kind = "remark_product"
            items = [remark_item]
            reason = (
                "商品行带备注补充，按备注商品结构识别"
                if any(not _is_remark_line(line) for line in lines)
                else "商品文字需要从备注/自定义字段中读取"
            )
        elif reverse_item := _item_from_reverse_lines(lines):
            kind = "reverse_field_order"
            items = [reverse_item]
            reason = "属性在前，商品和数量在后"
        elif len(lines) == 1 and (standard_item := _item_from_single_standard_line(lines[0])):
            kind = "single_line_standard"
            items = [standard_item]
            reason = "单行包含商品、销售属性和数量"
        else:
            kind = "unknown"
            items = []
            reason = "没有命中已知商品结构"

    config_remark_text = _config_remark_text(lines, config)
    if config_remark_text:
        items = [
            {
                **item,
                "remark_text": item.get("remark_text") or config_remark_text,
            }
            for item in items
        ]

    mapped_items = items if template_mapped else [_apply_config_field_mappings(item, config) for item in items]
    normalized_items = [_item_with_sales_attrs(item) for item in mapped_items]
    first_item = normalized_items[0] if len(normalized_items) == 1 else {}
    return {
        "woda_structure_kind": kind,
        "woda_structure_label": _STRUCTURE_LABELS[kind],
        "woda_structure_reason": reason,
        "woda_item_count": len(normalized_items),
        "custom_items": normalized_items,
        "custom_product_text": first_item.get("product_text", ""),
        "custom_sales_attr1_text": first_item.get("sales_attr1_text", ""),
        "custom_sales_attr2_text": first_item.get("sales_attr2_text", ""),
        "custom_item_remark_text": first_item.get("remark_text", ""),
        "custom_spec_text": first_item.get("spec_text", ""),
        "custom_size_text": first_item.get("size_text", ""),
        "custom_quantity_text": first_item.get("quantity_text", ""),
    }
