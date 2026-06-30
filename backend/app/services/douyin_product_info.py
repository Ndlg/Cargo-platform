from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any


DOUYIN_FIELD_STRUCTURE_VERSION = 1
STAR_MULTIPLIER_QUANTITY_TEXT = r"[*×]\s*(\d+)(?![A-Za-z0-9\u4e00-\u9fff])"
LETTER_MULTIPLIER_QUANTITY_TEXT = r"(?<![A-Za-z0-9])[xX]\s*(\d+)(?![A-Za-z0-9\u4e00-\u9fff])"
COUNT_QUANTITY_TEXT = r"(\d+)\s*(?:件|双|雙|个|個|条|條|套|份|只|支|瓶|包|组|組)"
MULTIPLIER_QUANTITY_TEXT = rf"(?:{STAR_MULTIPLIER_QUANTITY_TEXT}|{LETTER_MULTIPLIER_QUANTITY_TEXT})"
QUANTITY_PATTERN = re.compile(
    rf"(?:{MULTIPLIER_QUANTITY_TEXT}|{COUNT_QUANTITY_TEXT})"
)
TRAILING_QUANTITY_PATTERN = re.compile(
    rf"(?:\s*(?:{MULTIPLIER_QUANTITY_TEXT}|{COUNT_QUANTITY_TEXT})\s*)$"
)
ITEM_BOUNDARY_QUANTITY_PATTERN = re.compile(
    rf"(?:[【\[\(（]\s*)?(?:{MULTIPLIER_QUANTITY_TEXT}|\d+\s*(?:件|双|雙|个|個|条|條|套|份|只|支|瓶|包|组|組))(?:\s*[】\]\)）])?"
)


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return "".join(
        ch
        for ch in str(value)
        if (ch >= " " or ch in "\n\t") and unicodedata.category(ch) != "Cf"
    ).strip()


def compact_spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", text_value(value)).strip()


def quantity_from_text(*values: Any) -> int | None:
    for value in values:
        text = text_value(value)
        if not text:
            continue
        match = QUANTITY_PATTERN.search(text)
        if match:
            quantity = next((group for group in match.groups() if group), "")
            if quantity:
                parsed = int(quantity)
                if parsed > 0:
                    return parsed
        if re.fullmatch(r"\d+", text):
            parsed = int(text)
            if parsed > 0:
                return parsed
    return None


def strip_trailing_quantity_text(value: str) -> tuple[str, str]:
    text = compact_spaces(value)
    if not text:
        return "", ""
    match = TRAILING_QUANTITY_PATTERN.search(text)
    if not match:
        return text, ""
    quantity = match.group(1) or match.group(2) or ""
    return compact_spaces(text[: match.start()]), quantity


def standard_items_fields(items: list[dict[str, str]]) -> dict[str, Any]:
    first_item = items[0] if len(items) == 1 else {}
    return {
        "custom_product_text": first_item.get("product_text", ""),
        "custom_sales_attr1_text": first_item.get("sales_attr1_text", ""),
        "custom_sales_attr2_text": first_item.get("sales_attr2_text", ""),
        "custom_quantity_text": first_item.get("quantity_text", ""),
        "custom_spec_text": first_item.get("spec_text", ""),
        "custom_size_text": first_item.get("size_text", ""),
        "custom_item_remark_text": first_item.get("remark_text", ""),
        "custom_item_count": len(items),
        "custom_items": items,
    }


def mapping_extractor(config_payload: dict[str, Any] | None, field_code: str) -> str:
    if not isinstance(config_payload, dict):
        return ""
    mappings = config_payload.get("field_mappings")
    if not isinstance(mappings, dict):
        return ""
    mapping = mappings.get(field_code)
    if not isinstance(mapping, dict):
        return ""
    return text_value(mapping.get("extractor"))


def douyin_product_info_attrs(product_full_text: str) -> tuple[str, str]:
    text = compact_spaces(product_full_text)
    if not text:
        return "", ""
    last_bracket = text.rfind("】")
    has_bracketed_title = last_bracket >= 0
    tail = text[last_bracket + 1 :].strip() if last_bracket >= 0 else text
    return split_douyin_attr_tail(tail, has_bracketed_title=has_bracketed_title)


def douyin_sales_attr1_tail(value: str, *, has_bracketed_title: bool) -> str:
    text = compact_spaces(value)
    if not text:
        return ""
    text = re.sub(r"[，,、;；:\s]+$", "", text).strip()
    if has_bracketed_title:
        return re.sub(r"[/／\\]+$", "", text).strip()

    slash_left = re.split(r"[/／\\]", text, maxsplit=1)[0].strip()
    if slash_left and slash_left != text:
        text = slash_left
    head, separator, tail = text.rpartition(" ")
    if separator and len(head.replace(" ", "")) >= 12 and tail:
        return tail.strip()
    return text


def split_douyin_parenthesized_attr2(value: str) -> tuple[str, str] | None:
    text = compact_spaces(value)
    if not text.endswith((")", "）")):
        return None

    open_index = max(text.rfind("("), text.rfind("（"))
    if open_index <= 0:
        return None

    prefix = text[:open_index].rstrip()
    left, separator, _attr2_head = prefix.rpartition(" ")
    if not separator:
        return None

    attr2_start = len(left) + 1
    return text[:attr2_start].strip(), text[attr2_start:].strip()


def split_douyin_attr_tail(value: str, *, has_bracketed_title: bool) -> tuple[str, str]:
    text, _quantity_text = strip_trailing_quantity_text(value)
    if not text:
        return "", ""

    slash_split = re.split(r"[/／\\](?=[^/／\\]*$)", text, maxsplit=1)
    if len(slash_split) == 2:
        sales_attr1_text = douyin_sales_attr1_tail(
            slash_split[0],
            has_bracketed_title=has_bracketed_title,
        )
        return sales_attr1_text, slash_split[1].strip()

    parenthesized_attr2 = split_douyin_parenthesized_attr2(text)
    if parenthesized_attr2 is not None:
        sales_attr1_raw, sales_attr2_text = parenthesized_attr2
        return (
            douyin_sales_attr1_tail(sales_attr1_raw, has_bracketed_title=has_bracketed_title),
            sales_attr2_text,
        )

    sales_attr1_raw, separator, sales_attr2_text = text.rpartition(" ")
    if separator and sales_attr2_text:
        return (
            douyin_sales_attr1_tail(sales_attr1_raw, has_bracketed_title=has_bracketed_title),
            sales_attr2_text.strip(),
        )

    return douyin_sales_attr1_tail(text, has_bracketed_title=has_bracketed_title), ""


def trim_douyin_item_separators(value: str) -> str:
    return compact_spaces(value).strip(" ;；")


def has_trailing_quantity(value: str) -> bool:
    _text_without_quantity, quantity_text = strip_trailing_quantity_text(
        trim_douyin_item_separators(value)
    )
    return bool(quantity_text)


def skip_item_delimiters(text: str, index: int) -> int:
    current_index = index
    while current_index < len(text) and text[current_index] in " \t/／\\|;；":
        current_index += 1
    return current_index


def split_repeated_quantity_items(value: str) -> list[str]:
    text = compact_spaces(value)
    if not text:
        return []

    matches = list(ITEM_BOUNDARY_QUANTITY_PATTERN.finditer(text))
    if len(matches) < 2:
        return []

    items: list[str] = []
    start = 0
    for match in matches:
        body = compact_spaces(text[start : match.start()]).strip(" /／\\|;；")
        candidate = trim_douyin_item_separators(text[start : match.end()])
        if body and candidate:
            items.append(candidate)
        start = skip_item_delimiters(text, match.end())

    tail = compact_spaces(text[start:]).strip(" /／\\|;；")
    if tail:
        return []
    return items if len(items) > 1 else []


def previous_non_space_char(text: str, index: int) -> str:
    for current_index in range(index - 1, -1, -1):
        if not text[current_index].isspace():
            return text[current_index]
    return ""


def bracket_item_start_indexes(text: str) -> list[int]:
    starts: list[int] = []
    for match in re.finditer("【", text):
        start = match.start()
        if start > 0 and text[start - 1] == "【":
            continue
        if start == 0:
            starts.append(start)
            continue

        previous = previous_non_space_char(text, start)
        if previous in {";", "；"}:
            starts.append(start)
            continue

        previous_start = starts[-1] if starts else 0
        if has_trailing_quantity(text[previous_start:start]):
            starts.append(start)

    return starts


def split_douyin_product_line_with_boundary(value: str) -> tuple[list[str], str]:
    text = compact_spaces(value)
    if not text:
        return [], "empty"

    bracket_starts = bracket_item_start_indexes(text)
    if len(bracket_starts) > 1:
        items = [
            trim_douyin_item_separators(text[start:end])
            for start, end in zip(bracket_starts, [*bracket_starts[1:], len(text)])
        ]
        boundary_type = "semicolon_repeated_title" if re.search(r"[;；]", text) else "repeated_title"
        return [item for item in items if re.search(r"[\w\u4e00-\u9fff]", item)], boundary_type

    quantity_items = split_repeated_quantity_items(text)
    if quantity_items:
        return quantity_items, "repeated_quantity"

    semicolon_parts = [
        trim_douyin_item_separators(part)
        for part in re.split(r"[;；]+", text)
        if trim_douyin_item_separators(part)
    ]
    if len(semicolon_parts) > 1 and (
        any(part.startswith("【") for part in semicolon_parts)
        or sum(1 for part in semicolon_parts if has_trailing_quantity(part)) >= 2
    ):
        return semicolon_parts, "semicolon"

    return [text], "single_item"


def split_douyin_product_line(value: str) -> list[str]:
    items, _boundary_type = split_douyin_product_line_with_boundary(value)
    return items


def split_douyin_product_lines_with_boundary(value: Any) -> tuple[list[str], str]:
    text = text_value(value)
    if not text:
        return [], "empty"

    lines = [line for line in re.split(r"[\r\n]+", text) if compact_spaces(line)]
    if len(lines) > 1:
        line_results = [split_douyin_product_line_with_boundary(line) for line in lines]
        line_items = [item for items, _boundary_type in line_results for item in items]
        if len(line_items) > 1:
            nested_boundary = next(
                (
                    boundary_type
                    for items, boundary_type in line_results
                    if len(items) > 1 and boundary_type != "single_item"
                ),
                "",
            )
            return line_items, f"newline+{nested_boundary}" if nested_boundary else "newline"

    return split_douyin_product_line_with_boundary(text)


def split_douyin_product_lines(value: Any) -> list[str]:
    items, _boundary_type = split_douyin_product_lines_with_boundary(value)
    return items


def douyin_item_tail_structure(value: str) -> dict[str, Any]:
    text = trim_douyin_item_separators(value)
    if not text:
        return {"has_title_block": False, "attr2_delimiter": "empty", "quantity_source": "absent"}

    last_bracket = text.rfind("】")
    has_title_block = last_bracket >= 0
    tail = text[last_bracket + 1 :].strip() if has_title_block else text
    text_without_quantity, quantity_text = strip_trailing_quantity_text(tail)
    if re.search(r"[/／\\](?=[^/／\\]*$)", text_without_quantity):
        attr2_delimiter = "slash"
    elif split_douyin_parenthesized_attr2(text_without_quantity) is not None:
        attr2_delimiter = "space_parenthesized"
    elif text_without_quantity.rpartition(" ")[1]:
        attr2_delimiter = "space"
    elif text_without_quantity:
        attr2_delimiter = "attr1_only"
    else:
        attr2_delimiter = "none"
    return {
        "has_title_block": has_title_block,
        "attr2_delimiter": attr2_delimiter,
        "quantity_source": "item_tail" if quantity_text else "absent",
    }


def item_field_presence(item: dict[str, str]) -> dict[str, bool]:
    return {
        "product_text": bool(text_value(item.get("product_text"))),
        "sales_attr1_text": bool(text_value(item.get("sales_attr1_text") or item.get("spec_text"))),
        "sales_attr2_text": bool(text_value(item.get("sales_attr2_text") or item.get("size_text"))),
        "quantity_text": bool(text_value(item.get("quantity_text"))),
        "remark_text": True,
    }


def douyin_field_structure_payload(
    *,
    product_full_text: str,
    product_short_text: str = "",
    product_count_text: str = "",
    remark_text: str = "",
    items: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    product_lines, product_boundary_type = split_douyin_product_lines_with_boundary(product_full_text)
    short_lines, short_boundary_type = split_douyin_product_lines_with_boundary(product_short_text)
    standard_items = items or douyin_standard_items_from_fields(
        product_full_text=product_full_text,
        product_short_text=product_short_text,
        product_count_text=product_count_text,
        remark_text=remark_text,
        config_payload=None,
    )
    item_count = len(standard_items)
    boundary_type = (
        product_boundary_type
        if product_boundary_type not in {"empty", "single_item"} or not short_boundary_type
        else short_boundary_type
    )
    if item_count <= 1:
        boundary_type = "single_item"
    item_source_lines = product_lines if len(product_lines) == item_count else short_lines
    item_structures = []
    for index, item in enumerate(standard_items):
        source_line = item_source_lines[index] if index < len(item_source_lines) else text_value(item.get("raw_text"))
        item_structures.append(
            {
                "fields": item_field_presence(item),
                **douyin_item_tail_structure(source_line),
            }
        )

    item_signatures = [
        {
            "fields": item_structure["fields"],
            "has_title_block": item_structure["has_title_block"],
            "attr2_delimiter": item_structure["attr2_delimiter"],
            "quantity_source": item_structure["quantity_source"],
        }
        for item_structure in item_structures
    ]
    unique_item_signatures: list[dict[str, Any]] = []
    for signature in item_signatures:
        if signature not in unique_item_signatures:
            unique_item_signatures.append(signature)

    return {
        "version": DOUYIN_FIELD_STRUCTURE_VERSION,
        "waybill_mode": "douyin_cloud_print",
        "source_platform": "douyin",
        "item_count_mode": "single" if item_count <= 1 else "multi",
        "boundary_type": boundary_type,
        "item_signature_count": len(unique_item_signatures),
        "item_signatures": unique_item_signatures,
        "has_product_count_text": bool(text_value(product_count_text)),
        "has_remark_text": True,
    }


def douyin_field_structure_key(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "douyin-structure:" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def douyin_field_structure_label(payload: dict[str, Any]) -> str:
    count_label = "单商品" if payload.get("item_count_mode") == "single" else "多商品"
    boundary_type = text_value(payload.get("boundary_type")) or "unknown"
    field_names = {
        "product_text": "商品文字",
        "sales_attr1_text": "销售属性1",
        "sales_attr2_text": "销售属性2",
        "quantity_text": "数量",
        "remark_text": "备注",
    }
    signatures = payload.get("item_signatures")
    first_signature = signatures[0] if isinstance(signatures, list) and signatures else {}
    fields = first_signature.get("fields") if isinstance(first_signature, dict) else {}
    present_fields = [
        label
        for code, label in field_names.items()
        if isinstance(fields, dict) and fields.get(code)
    ]
    return f"{count_label} / {boundary_type} / {'+'.join(present_fields) if present_fields else '无字段'}"


def douyin_field_structure_from_texts(
    *,
    product_full_text: str,
    product_short_text: str = "",
    product_count_text: str = "",
    remark_text: str = "",
    items: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    payload = douyin_field_structure_payload(
        product_full_text=product_full_text,
        product_short_text=product_short_text,
        product_count_text=product_count_text,
        remark_text=remark_text,
        items=items,
    )
    key = douyin_field_structure_key(payload)
    return {
        "field_structure_key": key,
        "source_structure_key": key,
        "source_structure_label": douyin_field_structure_label(payload),
        "source_structure_payload": payload,
    }


def douyin_standard_item_from_fields(
    *,
    product_full_text: str,
    product_short_text: str = "",
    product_count_text: str,
    remark_text: str,
    config_payload: dict[str, Any] | None = None,
    default_quantity: int | None = 1,
) -> dict[str, str]:
    quantity = quantity_from_text(product_count_text, product_short_text, product_full_text)
    if quantity is None:
        quantity = default_quantity
    quantity_text = str(quantity) if quantity is not None else ""
    inferred_attr1, inferred_attr2 = douyin_product_info_attrs(product_full_text)
    fallback_attr1, fallback_attr2 = douyin_product_info_attrs(product_short_text)
    inferred_attr1 = inferred_attr1 or fallback_attr1
    inferred_attr2 = inferred_attr2 or fallback_attr2
    extractor_values = {
        "product_text": product_full_text,
        "product_short_text": product_short_text,
        "sales_attr1_text": inferred_attr1,
        "sales_attr2_text": inferred_attr2,
        "quantity_text": quantity_text,
        "remark_text": compact_spaces(remark_text),
    }
    mapped_product_text = (
        extractor_values.get(mapping_extractor(config_payload, "custom_product_text"), "")
        or product_full_text
    )
    sales_attr1_text = (
        extractor_values.get(mapping_extractor(config_payload, "custom_sales_attr1_text"), "")
        or inferred_attr1
    )
    sales_attr2_text = (
        extractor_values.get(mapping_extractor(config_payload, "custom_sales_attr2_text"), "")
        or inferred_attr2
    )
    mapped_quantity_text = (
        extractor_values.get(mapping_extractor(config_payload, "custom_quantity_text"), "")
        or quantity_text
    )
    mapped_remark_text = (
        extractor_values.get(mapping_extractor(config_payload, "custom_item_remark_text"), "")
        or compact_spaces(remark_text)
    )
    return {
        "product_text": mapped_product_text,
        "sales_attr1_text": sales_attr1_text,
        "sales_attr2_text": sales_attr2_text,
        "quantity_text": mapped_quantity_text,
        "spec_text": sales_attr1_text,
        "size_text": sales_attr2_text,
        "remark_text": mapped_remark_text,
        "raw_text": product_full_text,
    }


def douyin_standard_items_from_fields(
    *,
    product_full_text: str,
    product_short_text: str,
    product_count_text: str,
    remark_text: str,
    config_payload: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    product_lines = split_douyin_product_lines(product_full_text)
    short_lines = split_douyin_product_lines(product_short_text)
    count_lines = split_douyin_product_lines(product_count_text)
    item_count = max(len(product_lines), len(short_lines), 1 if product_full_text or product_short_text else 0)
    if item_count <= 1:
        return [
            douyin_standard_item_from_fields(
                product_full_text=product_full_text,
                product_short_text=product_short_text,
                product_count_text=product_count_text,
                remark_text=remark_text,
                config_payload=config_payload,
                default_quantity=1,
            )
        ]

    items: list[dict[str, str]] = []
    for index in range(item_count):
        if len(product_lines) == 1 and item_count > 1:
            item_product_text = product_lines[0]
        else:
            item_product_text = product_lines[index] if index < len(product_lines) else ""
        item_short_text = short_lines[index] if index < len(short_lines) else ""
        item_count_text = count_lines[index] if len(count_lines) == item_count else ""
        items.append(
            douyin_standard_item_from_fields(
                product_full_text=item_product_text or item_short_text,
                product_short_text=item_short_text,
                product_count_text=item_count_text,
                remark_text=remark_text,
                config_payload=config_payload,
                default_quantity=None,
            )
        )
    return items


def douyin_standard_fields_from_texts(
    *,
    product_full_text: str,
    product_short_text: str = "",
    product_count_text: str = "",
    remark_text: str = "",
    config_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = douyin_standard_items_from_fields(
        product_full_text=product_full_text,
        product_short_text=product_short_text,
        product_count_text=product_count_text,
        remark_text=remark_text,
        config_payload=config_payload,
    )
    fields = standard_items_fields(items)
    fields.update(
        douyin_field_structure_from_texts(
            product_full_text=product_full_text,
            product_short_text=product_short_text,
            product_count_text=product_count_text,
            remark_text=remark_text,
            items=items,
        )
    )
    return fields


def douyin_total_quantity(product_count_text: str, items: list[dict[str, str]]) -> int:
    count_lines = split_douyin_product_lines(product_count_text)
    if len(count_lines) > 1:
        line_quantities = [quantity_from_text(line) for line in count_lines]
        if all(quantity is not None for quantity in line_quantities):
            return sum(quantity for quantity in line_quantities if quantity is not None)

    product_count_quantity = quantity_from_text(product_count_text)
    if product_count_quantity is not None:
        return product_count_quantity

    item_quantities = [quantity_from_text(item.get("quantity_text")) for item in items]
    known_item_quantities = [quantity for quantity in item_quantities if quantity is not None]
    if known_item_quantities:
        return sum(known_item_quantities)
    return 1
