from __future__ import annotations

import re
from typing import Any


FIELD_TO_ITEM_KEY = {
    "custom_product_text": "product_text",
    "custom_sales_attr1_text": "sales_attr1_text",
    "custom_sales_attr2_text": "sales_attr2_text",
    "custom_quantity_text": "quantity_text",
    "custom_item_remark_text": "remark_text",
    "custom_spec_text": "spec_text",
    "custom_size_text": "size_text",
}

ITEM_KEY_ALIASES = {
    "product_text": ("product_text", "product", "title"),
    "sales_attr1_text": ("sales_attr1_text", "spec_text", "sales_attr1"),
    "sales_attr2_text": ("sales_attr2_text", "size_text", "sales_attr2"),
    "quantity_text": ("quantity_text", "quantity", "count"),
    "remark_text": ("remark_text", "remark"),
    "spec_text": ("spec_text", "sales_attr1_text", "sales_attr1"),
    "size_text": ("size_text", "sales_attr2_text", "sales_attr2"),
}


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def is_candidate_field(value: str) -> bool:
    return bool(re.fullmatch(r"(?:token|candidate)_\d+", text_value(value)))


def item_field_text(item: dict[str, Any], item_key: str) -> str:
    for alias in ITEM_KEY_ALIASES.get(item_key, (item_key,)):
        text = text_value(item.get(alias))
        if text:
            return text
    return ""


def candidate_item_text(item: dict[str, Any], candidate_key: str) -> str:
    return text_value(item.get(candidate_key)) if is_candidate_field(candidate_key) else ""


def item_value_for_source_field(item: dict[str, Any], source_field: str) -> str:
    if is_candidate_field(source_field):
        return candidate_item_text(item, source_field)
    item_key = FIELD_TO_ITEM_KEY.get(source_field)
    return item_field_text(item, item_key) if item_key else ""


def format_field_output(target_field: str, value: Any) -> str:
    text = text_value(value)
    if not text:
        return ""
    if target_field == "custom_quantity_text":
        cleaned = re.sub(r"^\s*(?:[*xX×]\s*)+", "", text)
        cleaned = re.sub(r"\s*(?:件|双|个|条|套|份)\s*$", "", cleaned)
        return cleaned.strip()
    if target_field in {"custom_sales_attr1_text", "custom_sales_attr2_text"}:
        cleaned = re.sub(
            r"^\s*(?:销售属性1|销售属性2|属性1|属性2|颜色分类|颜色|鞋码|尺码|规格|sku|SKU)\s*[:：]\s*",
            "",
            text,
        )
        return cleaned.strip()
    if target_field == "custom_item_remark_text":
        return text.strip()
    return text


def split_candidate_segments(text: Any, *, max_segments: int = 30) -> list[str]:
    source = text_value(text)
    if not source:
        return []
    normalized = re.sub(r"[；;]\s*", "\n", source)
    chunks = re.findall(r"【[^】]+】|[^【】]+", normalized)
    segments: list[str] = []

    def append_segment(value: str) -> None:
        cleaned_value = re.sub(r"^[，,、；;\s]+|[，,、；;\s]+$", "", value).strip()
        if cleaned_value:
            segments.append(cleaned_value)

    def split_plain_chunk(value: str) -> list[str]:
        cleaned_value = re.sub(r"[，,、；;]{2,}", "，", value.strip())
        if not cleaned_value:
            return []
        delimiter_parts = [part.strip() for part in re.split(r"[\n,，、；;|/]+", cleaned_value) if part.strip()]
        if len(delimiter_parts) > 1:
            parts: list[str] = []
            if (
                len(delimiter_parts) >= 2
                and re.fullmatch(r"\d+(?:\.\d+)?", delimiter_parts[-1])
                and re.search(r"\s(?:[2-4]\d(?:\.5)?|50)$", delimiter_parts[-2])
            ):
                prefix, size = delimiter_parts[-2].rsplit(None, 1)
                delimiter_parts = [*delimiter_parts[:-2], prefix, size, delimiter_parts[-1]]
            for part in delimiter_parts:
                parts.extend(split_plain_chunk(part))
            return parts
        whole_match = re.fullmatch(r"(.+?)\s*(?:[*xX×]\s*)(\d+(?:\.\d+)?)", cleaned_value)
        if not whole_match:
            whole_match = re.fullmatch(r"(.+?)\s+(\d+(?:\.\d+)?)\s*件", cleaned_value)
        if whole_match:
            return [whole_match.group(1).strip(), whole_match.group(2).strip()]
        spaced_tail = re.fullmatch(
            r"(.+?)\s+((?:[2-4]\d(?:\.5)?|50))\s+(\d+(?:\.\d+)?)",
            cleaned_value,
        )
        if spaced_tail:
            return [spaced_tail.group(1).strip(), spaced_tail.group(2).strip(), spaced_tail.group(3).strip()]
        space_parts = [part.strip() for part in re.split(r"\s+", cleaned_value) if part.strip()]
        if len(space_parts) >= 2:
            tail = space_parts[-1]
            head = " ".join(space_parts[:-1]).strip()
            if re.fullmatch(r"(?:[2-4]\d(?:\.5)?|50|\d+(?:\.\d+)?)", tail):
                return [head, tail] if head else [tail]
            if (
                len(space_parts) <= 4
                and all(len(re.sub(r"\s+", "", part)) <= 12 for part in space_parts)
                and any(re.search(r"\d", part) for part in space_parts)
            ):
                return space_parts
        if len(space_parts) >= 2 and re.search(r"[\u4e00-\u9fff]", cleaned_value):
            head = " ".join(space_parts[:-1]).strip()
            tail = space_parts[-1]
            if len(head) >= 12 and len(tail) <= 12:
                return [head, tail]
        if len(space_parts) >= 2 and not any(re.search(r"[\u4e00-\u9fff]", part) for part in space_parts):
            return space_parts
        return [cleaned_value]

    for chunk in chunks:
        cleaned = chunk.strip()
        if not cleaned:
            continue
        if cleaned.startswith("【") and cleaned.endswith("】"):
            inner = cleaned[1:-1].strip()
            quantity_match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*件", inner)
            if quantity_match:
                append_segment(quantity_match.group(1))
                continue
            match = re.fullmatch(r"(.+?)\s*(\d+(?:\.\d+)?)\s*件", inner)
            if match:
                append_segment(match.group(1))
                append_segment(match.group(2))
            else:
                append_segment(cleaned)
            continue
        for part in split_plain_chunk(cleaned):
            append_segment(part)
    return segments[:max_segments]
