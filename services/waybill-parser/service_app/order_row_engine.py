from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from service_app.douyin_product_info import (
    compact_spaces,
    quantity_from_text,
    split_douyin_attr_tail,
    split_douyin_product_lines,
    strip_trailing_quantity_text,
    text_value,
)


ORDER_ROW_DRAFTS_CONTRACT_VERSION = "order_row_drafts_v1"
TRAILING_ORDER_QUANTITY_PATTERN = re.compile(
    r"\s*(?:[【\[\(（]\s*)?(?:[*xX×]\s*(\d+)|(\d+)\s*(?:件|双|雙|个|個|条|條|套|份|只|支|瓶|包|组|組))(?:\s*[】\]\)）])?\s*$"
)
LOGISTICS_TAIL_PATTERN = re.compile(
    r"\s*(?:运单号|物流单号|快递单号)\s*[:：]\s*(?:\[[^\]]*\]|【[^】]*】|[A-Z]{1,8}\d[\w,，\-\s]*)\s*$",
    re.IGNORECASE,
)
SHOE_SIZE_VALUE_PATTERN = re.compile(
    r"^(?P<size>(?:2[0-9]|3[0-9]|4[0-9]|5[0-5])(?:\.\d)?)(?:\s*(?:码|[（(].*[）)]?|[,，;；/].*|偏.*|建议.*))?$"
)
NOISY_SHOE_SIZE_PREFIX_PATTERN = re.compile(
    r"^(?P<size>(?:2[0-9]|3[0-9]|4[0-9]|5[0-5])(?:\.\d)?)(?=\s*(?:[*xX×]\s*\d+|\d+\s*(?:件|双|雙)|运单号|物流单号|快递单号|$))"
)


@dataclass(frozen=True)
class OrderRowDraft:
    raw_record_id: int
    task_id: int | None
    parent_label: str
    child_label: str
    child_index: int
    child_count: int
    source_component: str
    source_index: str
    product: str
    sales_attr1: str
    sales_attr2: str
    quantity: int | None
    remark: str
    image_match_text: str
    original_text: str
    status: str
    review_reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParentWaybillDraft:
    raw_record_id: int
    task_id: int | None
    parent_label: str
    source_component: str
    source_index: str
    child_count: int
    rows: list[OrderRowDraft]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["rows"] = [row.as_dict() for row in self.rows]
        return payload


def business_parent_label(
    source_index: str | None,
    raw_record_id: int,
    *,
    parent_sequence: int | None = None,
) -> str:
    if parent_sequence is not None and parent_sequence > 0:
        return f"第1批-第{parent_sequence}单"
    cleaned = text_value(source_index)
    if not cleaned:
        return f"第1批-第{raw_record_id}单"
    if re.fullmatch(r"\d+-\d+", cleaned):
        batch_no, order_no = cleaned.split("-", 1)
        return f"第{int(batch_no)}批-第{int(order_no)}单"
    if re.fullmatch(r"\d+", cleaned):
        return f"第1批-第{int(cleaned)}单"
    return cleaned


def remove_field_label(value: str) -> str:
    text = compact_spaces(value)
    return re.sub(r"^(?:颜色分类|颜色|鞋码|尺码|规格|款式)\s*[:：]\s*", "", text).strip()


def normalize_sales_attr2(value: str) -> str:
    text = strip_logistics_tail(remove_field_label(value))
    noisy_match = NOISY_SHOE_SIZE_PREFIX_PATTERN.match(text)
    if noisy_match:
        return noisy_match.group("size")
    match = SHOE_SIZE_VALUE_PATTERN.match(text)
    if not match:
        return text
    return match.group("size")


def normalize_product_text(value: str, *, compact_short_bracket_title: bool = False) -> str:
    text = clean_product_line_text(value)
    if compact_short_bracket_title:
        match = re.match(r"^【(?P<title>[^】]{1,8})】(?P<body>.+)$", text)
        if match:
            title = compact_spaces(match.group("title"))
            body = compact_spaces(match.group("body"))
            if title and body:
                return f"{title}-{body}"
    return text.strip("【】[]")


def split_commas_outside_brackets(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char in "([{（【":
            depth += 1
            continue
        if char in ")]}）】" and depth > 0:
            depth -= 1
            continue
        if char in "，," and depth == 0:
            part = compact_spaces(value[start:index])
            if part:
                parts.append(part)
            start = index + 1
    tail = compact_spaces(value[start:])
    if tail:
        parts.append(tail)
    return parts


def int_value(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def split_simple_attr_line(value: str) -> tuple[str, str]:
    text = compact_spaces(value)
    if not text:
        return "", ""
    parts = [compact_spaces(part) for part in re.split(r"[，,|/／]+", text) if compact_spaces(part)]
    if len(parts) >= 2:
        return remove_field_label(parts[0]), normalize_sales_attr2(parts[1])
    head, separator, tail = text.rpartition(" ")
    if separator and tail:
        return remove_field_label(head), normalize_sales_attr2(tail)
    return remove_field_label(text), ""


def parse_labeled_attr_line(value: str) -> tuple[str, str] | None:
    text = compact_spaces(value)
    if not text:
        return None

    fields: dict[str, str] = {}
    for part in re.split(r"[;；,，]+", text):
        item = compact_spaces(part)
        match = re.match(r"^(?P<label>颜色分类|颜色|鞋码|尺码|规格|款式)\s*[:：]\s*(?P<value>.+)$", item)
        if not match:
            continue
        label = match.group("label")
        field_value = compact_spaces(match.group("value"))
        if label in {"颜色分类", "颜色", "规格", "款式"} and "sales_attr1" not in fields:
            fields["sales_attr1"] = remove_field_label(field_value)
        elif label in {"鞋码", "尺码"} and "sales_attr2" not in fields:
            fields["sales_attr2"] = normalize_sales_attr2(field_value)

    sales_attr1 = fields.get("sales_attr1", "")
    sales_attr2 = fields.get("sales_attr2", "")
    if not sales_attr1 and not sales_attr2:
        return None
    return sales_attr1, sales_attr2


def clean_product_line_text(value: str) -> str:
    return compact_spaces(value).strip(" ，,;；")


def strip_logistics_tail(value: str) -> str:
    text = text_value(value).strip()
    if not text:
        return ""
    compacted = compact_spaces(text)
    if not LOGISTICS_TAIL_PATTERN.search(compacted):
        return text
    return compact_spaces(LOGISTICS_TAIL_PATTERN.sub("", compacted)).strip(" ，,;；")


def strip_order_quantity_suffix(value: str) -> tuple[str, str]:
    text = compact_spaces(strip_logistics_tail(value))
    if not text:
        return "", ""
    match = TRAILING_ORDER_QUANTITY_PATTERN.search(text)
    if match:
        quantity = match.group(1) or match.group(2) or ""
        return compact_spaces(text[: match.start()]), quantity
    return strip_trailing_quantity_text(text)


def special_wechat_waybill_text(value: str) -> bool:
    text = compact_spaces(value)
    return "微信" in text


def parse_delimited_product_attrs(value: str) -> tuple[str, str, str] | None:
    text = compact_spaces(value)
    if not text or has_product_title_marker(text):
        return None

    parts = split_commas_outside_brackets(text)
    if len(parts) < 3:
        return None

    if re.match(r"^(?:颜色分类|颜色|鞋码|尺码|规格|款式)\s*[:：]", parts[0]):
        return None

    product = compact_spaces("，".join(parts[:-2]))
    sales_attr1 = remove_field_label(parts[-2])
    sales_attr2 = normalize_sales_attr2(parts[-1])
    if not product or not sales_attr1 or not sales_attr2:
        return None
    return product, sales_attr1, sales_attr2


def parse_semicolon_product_attrs(value: str) -> tuple[str, str, str] | None:
    text = compact_spaces(value)
    if not text or has_product_title_marker(text):
        return None

    match = re.match(
        r"^(?P<product>.+)\s+(?P<sales_attr1>[^;；]+)[;；]\s*(?P<sales_attr2>\d+(?:\.\d+)?)$",
        text,
    )
    if not match:
        return None

    product = compact_spaces(match.group("product"))
    sales_attr1 = remove_field_label(match.group("sales_attr1"))
    sales_attr2 = normalize_sales_attr2(match.group("sales_attr2"))
    if not product or not sales_attr1 or not sales_attr2:
        return None
    return product, sales_attr1, sales_attr2


def parse_leading_attr_size_product_text(
    item_text: str,
    *,
    fallback_quantity_text: str = "",
    remark_text: str = "",
) -> dict[str, Any] | None:
    if re.search(r"[\r\n]", text_value(item_text)):
        return None

    original_text = compact_spaces(item_text)
    without_quantity, trailing_quantity_text = strip_order_quantity_suffix(original_text)
    match = re.match(
        r"^(?P<sales_attr1>[^,，;；]+)[,，]\s*"
        r"(?P<sales_attr2>(?:2[0-9]|3[0-9]|4[0-9]|5[0-5])(?:\.\d)?)"
        r"\s+(?P<product>.+)$",
        without_quantity,
    )
    if not match:
        return None

    product = clean_product_line_text(match.group("product"))
    sales_attr1 = remove_field_label(match.group("sales_attr1"))
    sales_attr2 = normalize_sales_attr2(match.group("sales_attr2"))
    if not product or not sales_attr1 or not sales_attr2:
        return None

    quantity = quantity_from_text(trailing_quantity_text, fallback_quantity_text, original_text) or 1
    remark = compact_spaces(remark_text)
    image_match_text = compact_spaces(" ".join(str(part) for part in (product, sales_attr1, sales_attr2, quantity, remark) if part))
    return {
        "product": product,
        "sales_attr1": sales_attr1,
        "sales_attr2": sales_attr2,
        "quantity": quantity,
        "remark": remark,
        "image_match_text": image_match_text,
        "original_text": original_text,
        "status": "draft",
        "review_reason": "",
    }


def parse_trailing_attr_size_product_text(
    item_text: str,
    *,
    fallback_quantity_text: str = "",
    remark_text: str = "",
) -> dict[str, Any] | None:
    if re.search(r"[\r\n]", text_value(item_text)):
        return None

    original_text = compact_spaces(item_text)
    without_quantity, trailing_quantity_text = strip_order_quantity_suffix(original_text)
    match = re.match(
        r"^(?P<product>.+?)\s+"
        r"(?P<sales_attr1>[^/／\\，,;；]{1,36})[/／\\]"
        r"(?P<sales_attr2>(?:2[0-9]|3[0-9]|4[0-9]|5[0-5])(?:\.\d)?(?:\s*.*)?)$",
        without_quantity,
    )
    if not match:
        return None

    product = clean_product_line_text(match.group("product"))
    sales_attr1 = remove_field_label(match.group("sales_attr1"))
    sales_attr2 = normalize_sales_attr2(match.group("sales_attr2"))
    if len(product.replace(" ", "")) < 8 or not sales_attr1 or not sales_attr2:
        return None

    quantity = quantity_from_text(trailing_quantity_text, fallback_quantity_text, original_text) or 1
    remark = compact_spaces(remark_text)
    image_match_text = compact_spaces(" ".join(str(part) for part in (product, sales_attr1, sales_attr2, quantity, remark) if part))
    return {
        "product": product,
        "sales_attr1": sales_attr1,
        "sales_attr2": sales_attr2,
        "quantity": quantity,
        "remark": remark,
        "image_match_text": image_match_text,
        "original_text": original_text,
        "status": "draft",
        "review_reason": "",
    }


def unstructured_review_fields(original_text: str, reason: str) -> dict[str, Any]:
    text = compact_spaces(original_text)
    return {
        "product": "",
        "sales_attr1": "",
        "sales_attr2": "",
        "quantity": None,
        "remark": "",
        "image_match_text": text,
        "original_text": text,
        "status": "needs_review",
        "review_reason": reason,
    }


def special_waybill_fields(original_text: str, reason: str) -> dict[str, Any]:
    text = compact_spaces(original_text)
    return {
        "product": "",
        "sales_attr1": "",
        "sales_attr2": "",
        "quantity": None,
        "remark": "",
        "image_match_text": text,
        "original_text": text,
        "status": "special",
        "review_reason": reason,
    }


def parse_label_only_attr_text(
    item_text: str,
    *,
    fallback_quantity_text: str = "",
    remark_text: str = "",
) -> dict[str, Any] | None:
    if re.search(r"[\r\n]", text_value(item_text)):
        return None

    original_text = compact_spaces(item_text)
    without_quantity, trailing_quantity_text = strip_order_quantity_suffix(original_text)
    labeled_attrs = parse_labeled_attr_line(without_quantity)
    if labeled_attrs is None:
        return None

    sales_attr1, sales_attr2 = labeled_attrs
    quantity = quantity_from_text(trailing_quantity_text, fallback_quantity_text, original_text) or 1
    remark = compact_spaces(remark_text)
    image_match_text = compact_spaces(" ".join(str(part) for part in (sales_attr1, sales_attr2, quantity, remark) if part))
    return {
        "product": "",
        "sales_attr1": sales_attr1,
        "sales_attr2": sales_attr2,
        "quantity": quantity,
        "remark": remark,
        "image_match_text": image_match_text,
        "original_text": original_text,
        "status": "draft",
        "review_reason": "",
    }


def parse_two_line_attr_product_text(
    item_text: str,
    *,
    fallback_quantity_text: str = "",
    remark_text: str = "",
) -> dict[str, Any] | None:
    lines = [compact_spaces(line) for line in re.split(r"[\r\n]+", text_value(item_text)) if compact_spaces(line)]
    if len(lines) != 2:
        return None

    attr_line, product_line = lines
    if has_product_title_marker(attr_line):
        return None
    if has_item_quantity(attr_line):
        return None
    if parse_delimited_product_attrs(attr_line) is not None or parse_semicolon_product_attrs(attr_line) is not None:
        return None
    if parse_labeled_attr_line(product_line) is not None:
        return None

    product_without_quantity, trailing_quantity_text = strip_order_quantity_suffix(product_line)
    quantity = quantity_from_text(trailing_quantity_text, fallback_quantity_text, product_line)
    if not product_without_quantity or quantity is None:
        return None

    sales_attr1, sales_attr2 = split_simple_attr_line(attr_line)
    product = normalize_product_text(product_without_quantity, compact_short_bracket_title=True)
    remark = compact_spaces(remark_text)
    image_match_text = compact_spaces(" ".join(str(part) for part in (product, sales_attr1, sales_attr2, quantity, remark) if part))
    return {
        "product": product,
        "sales_attr1": sales_attr1,
        "sales_attr2": sales_attr2,
        "quantity": quantity,
        "remark": remark,
        "image_match_text": image_match_text,
        "original_text": text_value(item_text),
        "status": "draft",
        "review_reason": "",
    }


def parse_two_line_product_attr_text(
    item_text: str,
    *,
    fallback_quantity_text: str = "",
    remark_text: str = "",
) -> dict[str, Any] | None:
    lines = [compact_spaces(line) for line in re.split(r"[\r\n]+", text_value(item_text)) if compact_spaces(line)]
    if len(lines) != 2:
        return None

    product_line, attr_line = lines
    labeled_attrs = parse_labeled_attr_line(attr_line)
    if labeled_attrs is None:
        return None

    product_without_quantity, trailing_quantity_text = strip_order_quantity_suffix(product_line)
    product = clean_product_line_text(product_without_quantity)
    delimited_fields = parse_delimited_product_attrs(product_without_quantity)
    if delimited_fields is not None:
        product = delimited_fields[0]
    else:
        semicolon_fields = parse_semicolon_product_attrs(product_without_quantity)
        if semicolon_fields is not None:
            product = semicolon_fields[0]
    if not product:
        return None

    sales_attr1, sales_attr2 = labeled_attrs
    quantity = quantity_from_text(trailing_quantity_text, fallback_quantity_text, product_line) or 1
    remark = compact_spaces(remark_text)
    image_match_text = compact_spaces(" ".join(str(part) for part in (product, sales_attr1, sales_attr2, quantity, remark) if part))
    return {
        "product": product,
        "sales_attr1": sales_attr1,
        "sales_attr2": sales_attr2,
        "quantity": quantity,
        "remark": remark,
        "image_match_text": image_match_text,
        "original_text": text_value(item_text),
        "status": "draft",
        "review_reason": "",
    }


def parse_two_line_waybill_text(
    item_text: str,
    *,
    fallback_quantity_text: str = "",
    remark_text: str = "",
) -> dict[str, Any] | None:
    return parse_multiline_product_with_free_remark_text(
        item_text,
        fallback_quantity_text=fallback_quantity_text,
        remark_text=remark_text,
    ) or parse_two_line_attr_product_text(
        item_text,
        fallback_quantity_text=fallback_quantity_text,
        remark_text=remark_text,
    ) or parse_two_line_product_attr_text(
        item_text,
        fallback_quantity_text=fallback_quantity_text,
        remark_text=remark_text,
    )


def combine_remark_text(*values: str) -> str:
    parts = [compact_spaces(value) for value in values if compact_spaces(value)]
    return "；".join(parts)


def parse_multiline_product_with_free_remark_text(
    item_text: str,
    *,
    fallback_quantity_text: str = "",
    remark_text: str = "",
) -> dict[str, Any] | None:
    lines = [compact_spaces(line) for line in re.split(r"[\r\n]+", text_value(item_text)) if compact_spaces(line)]
    if len(lines) < 2:
        return None

    remark_lines: list[str] = []
    while len(lines) >= 2 and is_free_remark_line(lines[-1]):
        remark_lines.insert(0, lines.pop())

    if not remark_lines or not lines:
        return None

    base_text = "\n".join(lines)
    if not any(has_item_quantity(line) for line in lines):
        if parse_two_line_waybill_text(base_text, fallback_quantity_text=fallback_quantity_text, remark_text=remark_text) is None:
            return None

    fields = parse_item_text(
        base_text,
        fallback_quantity_text=fallback_quantity_text,
        remark_text=combine_remark_text(remark_text, *remark_lines),
    )
    return {
        **fields,
        "original_text": text_value(item_text),
    }


def pair_product_lines_with_labeled_attr_lines(value: str) -> list[str]:
    lines = [compact_spaces(line) for line in re.split(r"[\r\n]+", text_value(value)) if compact_spaces(line)]
    if len(lines) < 4:
        return []

    for attr_start in range(1, len(lines)):
        product_lines = lines[:attr_start]
        attr_lines = lines[attr_start:]
        if len(product_lines) != len(attr_lines):
            continue
        if any(parse_labeled_attr_line(line) is not None for line in product_lines):
            continue
        if not all(parse_labeled_attr_line(line) is not None for line in attr_lines):
            continue
        return [f"{product_line}\n{attr_line}" for product_line, attr_line in zip(product_lines, attr_lines)]
    return []


def product_item_texts(
    product_text: str,
    *,
    quantity_text: str = "",
    remark_text: str = "",
) -> list[str]:
    product_text = strip_logistics_tail(product_text)
    if parse_two_line_waybill_text(
        product_text,
        fallback_quantity_text=quantity_text,
        remark_text=remark_text,
    ):
        return [product_text]

    paired_item_texts = pair_product_lines_with_labeled_attr_lines(product_text)
    if paired_item_texts:
        return paired_item_texts

    return split_douyin_product_lines(product_text)


def has_product_title_marker(value: str) -> bool:
    text = compact_spaces(value)
    return "【" in text and "】" in text


def parse_item_text(item_text: str, *, fallback_quantity_text: str = "", remark_text: str = "") -> dict[str, Any]:
    parse_text = strip_logistics_tail(item_text)
    original_text = compact_spaces(parse_text)
    if special_wechat_waybill_text(original_text):
        return special_waybill_fields(original_text, "wechat_special_waybill")

    label_only_fields = parse_label_only_attr_text(
        parse_text,
        fallback_quantity_text=fallback_quantity_text,
        remark_text=remark_text,
    )
    if label_only_fields is not None:
        return label_only_fields

    two_line_fields = parse_two_line_waybill_text(
        parse_text,
        fallback_quantity_text=fallback_quantity_text,
        remark_text=remark_text,
    )
    if two_line_fields is not None:
        return two_line_fields

    leading_attr_size_product_fields = parse_leading_attr_size_product_text(
        parse_text,
        fallback_quantity_text=fallback_quantity_text,
        remark_text=remark_text,
    )
    if leading_attr_size_product_fields is not None:
        return leading_attr_size_product_fields

    trailing_attr_size_product_fields = parse_trailing_attr_size_product_text(
        parse_text,
        fallback_quantity_text=fallback_quantity_text,
        remark_text=remark_text,
    )
    if trailing_attr_size_product_fields is not None:
        return trailing_attr_size_product_fields

    without_quantity, trailing_quantity_text = strip_order_quantity_suffix(original_text)
    quantity = quantity_from_text(trailing_quantity_text, fallback_quantity_text, original_text)

    product = ""
    tail = without_quantity
    bracket_match = re.match(r"^【(?P<title>[^】]+)】(?P<tail>.*)$", without_quantity)
    if bracket_match:
        product = compact_spaces(bracket_match.group("title"))
        tail = compact_spaces(bracket_match.group("tail"))
        sales_attr1, sales_attr2 = split_douyin_attr_tail(tail, has_bracketed_title=True)
    else:
        delimited_fields = parse_delimited_product_attrs(tail)
        if delimited_fields is not None:
            product, sales_attr1, sales_attr2 = delimited_fields
        else:
            semicolon_fields = parse_semicolon_product_attrs(tail)
            if semicolon_fields is not None:
                product, sales_attr1, sales_attr2 = semicolon_fields
            else:
                sales_attr1, sales_attr2 = split_douyin_attr_tail(tail, has_bracketed_title=False)
                product = compact_spaces(tail)

    sales_attr1 = remove_field_label(sales_attr1)
    sales_attr2 = normalize_sales_attr2(sales_attr2)
    remark = compact_spaces(remark_text)
    status = "draft"
    missing = []
    if not product:
        missing.append("product")
    if quantity is None:
        missing.append("quantity")
    if missing:
        status = "needs_review"

    image_match_text = compact_spaces(" ".join(str(part) for part in (product, sales_attr1, sales_attr2, quantity or "", remark) if part))
    return {
        "product": product,
        "sales_attr1": sales_attr1,
        "sales_attr2": sales_attr2,
        "quantity": quantity,
        "remark": remark,
        "image_match_text": image_match_text,
        "original_text": original_text,
        "status": status,
        "review_reason": ",".join(missing),
    }


def block_source_path(block: dict[str, Any]) -> str:
    trace = block.get("trace")
    if isinstance(trace, dict):
        return text_value(trace.get("source_path") or block.get("source_path"))
    return text_value(block.get("source_path"))


def unique_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = compact_spaces(value)
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def unique_item_texts_preserving_lines(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = text_value(value)
        key = compact_spaces(text)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return unique


def is_cancelled_placeholder_line(value: str) -> bool:
    text_without_quantity, _quantity = strip_order_quantity_suffix(value)
    normalized = re.sub(r"\s+", "", text_without_quantity)
    return bool(re.fullmatch(r"[xXｘＸ]{3,}", normalized))


def is_non_business_line(value: str) -> bool:
    text = compact_spaces(value)
    return not text or text.lower() == "auto" or is_cancelled_placeholder_line(text)


def is_plain_attr_size_line(value: str) -> bool:
    text = compact_spaces(value)
    if not text or quantity_from_text(text):
        return False
    if parse_labeled_attr_line(text) is not None:
        return True
    if not re.search(r"[，,|/／\\]", text):
        return False
    sales_attr1, sales_attr2 = split_simple_attr_line(text)
    if not sales_attr1 or not sales_attr2:
        return False
    return bool(SHOE_SIZE_VALUE_PATTERN.match(sales_attr2))


def is_free_remark_line(value: str) -> bool:
    text = compact_spaces(value)
    if is_non_business_line(text):
        return False
    if has_product_title_marker(text):
        return False
    if quantity_from_text(text):
        return False
    if parse_labeled_attr_line(text) is not None:
        return False
    if is_plain_attr_size_line(text):
        return False
    if parse_delimited_product_attrs(text) is not None or parse_semicolon_product_attrs(text) is not None:
        return False
    if re.search(r"(?:颜色分类|颜色|鞋码|尺码|规格|款式)\s*[:：]", text):
        return False
    return True


def has_item_quantity(value: str) -> bool:
    return quantity_from_text(value) is not None and not is_cancelled_placeholder_line(value)


def smart_candidate_texts_from_original_lines(lines: list[str]) -> list[str]:
    original_lines = [compact_spaces(line) for line in lines if not is_non_business_line(line)]
    if not original_lines:
        return []

    joined = "\n".join(original_lines)
    if parse_two_line_waybill_text(joined) is not None:
        return [joined]

    paired_labeled_items = pair_product_lines_with_labeled_attr_lines(joined)
    if paired_labeled_items:
        return paired_labeled_items

    candidates: list[str] = []
    index = 0
    while index < len(original_lines):
        current_line = original_lines[index]
        next_line = original_lines[index + 1] if index + 1 < len(original_lines) else ""
        following_line = original_lines[index + 2] if index + 2 < len(original_lines) else ""

        if next_line:
            if has_item_quantity(current_line) and parse_labeled_attr_line(next_line) is not None:
                if following_line and is_free_remark_line(following_line):
                    candidates.append(f"{current_line}\n{next_line}\n{following_line}")
                    index += 3
                else:
                    candidates.append(f"{current_line}\n{next_line}")
                    index += 2
                continue
            if is_plain_attr_size_line(current_line) and has_item_quantity(next_line):
                if following_line and is_free_remark_line(following_line):
                    candidates.append(f"{current_line}\n{next_line}\n{following_line}")
                    index += 3
                else:
                    candidates.append(f"{current_line}\n{next_line}")
                    index += 2
                continue
            if has_item_quantity(current_line) and is_free_remark_line(next_line):
                candidates.append(f"{current_line}\n{next_line}")
                index += 2
                continue

        candidates.append(current_line)
        index += 1

    return candidates


def text_blocks_for_first_source_path(
    text_blocks: list[dict[str, Any]],
    *,
    source_path_keyword: str,
) -> list[dict[str, Any]]:
    matching_blocks = [
        block
        for block in text_blocks
        if source_path_keyword.lower() in block_source_path(block).lower()
    ]
    if not matching_blocks:
        return []
    first_path = block_source_path(matching_blocks[0])
    return [block for block in matching_blocks if block_source_path(block) == first_path]


def original_texts_from_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    return [
        text_value(block.get("text"))
        for block in blocks
        if block.get("block_kind") == "original"
    ]


def text_blocks_for_source_keyword(
    text_blocks: list[dict[str, Any]],
    *,
    source_path_keyword: str,
) -> list[dict[str, Any]]:
    source_path_keyword = source_path_keyword.lower()
    return [
        block
        for block in text_blocks
        if source_path_keyword in block_source_path(block).lower()
    ]


def preferred_waybill_sample_texts(sample: dict[str, Any]) -> list[str]:
    text_blocks = sample.get("text_blocks")
    blocks = text_blocks if isinstance(text_blocks, list) else []
    valid_blocks = [block for block in blocks if isinstance(block, dict)]

    for source_keyword in ("productShortInfo", "productInfo", "ITEM_INFO"):
        source_blocks = text_blocks_for_first_source_path(valid_blocks, source_path_keyword=source_keyword)
        if not source_blocks:
            continue
        original_texts = original_texts_from_blocks(source_blocks)
        candidates = smart_candidate_texts_from_original_lines(original_texts)
        if candidates:
            return candidates

    print_xml_blocks = text_blocks_for_source_keyword(valid_blocks, source_path_keyword="printXML")
    if print_xml_blocks:
        print_xml_original_texts = original_texts_from_blocks(print_xml_blocks)
        candidates = smart_candidate_texts_from_original_lines(print_xml_original_texts)
        if candidates:
            return candidates

    return smart_candidate_texts_from_original_lines([text_value(sample.get("sample_text"))]) or unique_texts([text_value(sample.get("sample_text"))])


def iter_content_data(payload: dict[str, Any]) -> list[dict[str, Any]]:
    task = payload.get("task")
    documents = task.get("documents") if isinstance(task, dict) else None
    if not isinstance(documents, list):
        return []

    content_data: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        contents = document.get("contents")
        if not isinstance(contents, list):
            continue
        for content in contents:
            if not isinstance(content, dict):
                continue
            data = content.get("data")
            if isinstance(data, dict):
                content_data.append(data)
    return content_data


def content_product_text(data: dict[str, Any]) -> str:
    for key in ("productShortInfo", "productInfo", "SPInfo", "ITEM_NAME", "itemInfo"):
        value = text_value(data.get(key))
        if value:
            return value
    return ""


def content_product_full_text(data: dict[str, Any]) -> str:
    for key in ("productInfo", "productShortInfo", "SPInfo", "ITEM_NAME", "itemInfo"):
        value = text_value(data.get(key))
        if value:
            return value
    return ""


def content_quantity_text(data: dict[str, Any]) -> str:
    for key in ("productCount", "ITEM_TOTAL_COUNT", "quantity", "count"):
        value = text_value(data.get(key))
        if value:
            return value
    return ""


def content_remark_text(data: dict[str, Any]) -> str:
    for key in ("remark", "buyerRemark", "sellerRemark", "memo", "buyerMemo", "sellerMemo"):
        value = text_value(data.get(key))
        if value:
            return value
    return ""


def standard_detail_product_text(values: dict[str, Any]) -> str:
    for key in ("product_short_text", "product_full_text", "spec_text", "sp_info", "sps_info"):
        value = text_value(values.get(key))
        if value:
            return value
    return ""


def standard_detail_quantity_text(values: dict[str, Any]) -> str:
    for key in ("product_count_text", "quantity", "item_total_count"):
        value = text_value(values.get(key))
        if value:
            return value
    return ""


def standard_detail_remark_text(values: dict[str, Any]) -> str:
    for key in ("buyer_remark", "seller_remark", "remark"):
        value = text_value(values.get(key))
        if value:
            return value
    return ""


def draft_rows_from_payload(
    payload: dict[str, Any],
    *,
    raw_record_id: int,
    task_id: int | None,
    source_component: str | None,
    source_index: str | None,
    parent_sequence: int | None = None,
) -> ParentWaybillDraft:
    parent_label = business_parent_label(source_index, raw_record_id, parent_sequence=parent_sequence)
    component = text_value(source_component)
    source = text_value(source_index)
    rows: list[OrderRowDraft] = []

    for data in iter_content_data(payload):
        product_text = content_product_text(data)
        if not product_text:
            product_text = content_product_full_text(data)
        if not product_text:
            continue
        quantity_text = content_quantity_text(data)
        remark_text = content_remark_text(data)
        item_texts = product_item_texts(
            product_text,
            quantity_text=quantity_text,
            remark_text=remark_text,
        )
        if not item_texts:
            item_texts = [product_text]
        child_count = len(item_texts)
        for index, item_text in enumerate(item_texts, start=1):
            fields = parse_item_text(
                item_text,
                fallback_quantity_text=quantity_text,
                remark_text=remark_text,
            )
            rows.append(
                OrderRowDraft(
                    raw_record_id=raw_record_id,
                    task_id=task_id,
                    parent_label=parent_label,
                    child_label=f"{parent_label}-子{index}",
                    child_index=index,
                    child_count=child_count,
                    source_component=component,
                    source_index=source,
                    product=fields["product"],
                    sales_attr1=fields["sales_attr1"],
                    sales_attr2=fields["sales_attr2"],
                    quantity=fields["quantity"],
                    remark=fields["remark"],
                    image_match_text=fields["image_match_text"],
                    original_text=fields["original_text"],
                    status=fields["status"],
                    review_reason=fields["review_reason"],
                )
            )

    if not rows:
        rows.append(
            OrderRowDraft(
                raw_record_id=raw_record_id,
                task_id=task_id,
                parent_label=parent_label,
                child_label=f"{parent_label}-子1",
                child_index=1,
                child_count=1,
                source_component=component,
                source_index=source,
                product="",
                sales_attr1="",
                sales_attr2="",
                quantity=None,
                remark="",
                image_match_text="",
                original_text="",
                status="needs_review",
                review_reason="no_product_text",
            )
        )

    total_children = len(rows)
    if total_children:
        rows = [
            OrderRowDraft(
                **{
                    **row.as_dict(),
                    "child_count": total_children,
                    "child_label": f"{parent_label}-子{index}",
                    "child_index": index,
                }
            )
            for index, row in enumerate(rows, start=1)
        ]
    return ParentWaybillDraft(
        raw_record_id=raw_record_id,
        task_id=task_id,
        parent_label=parent_label,
        source_component=component,
        source_index=source,
        child_count=total_children,
        rows=rows,
    )


def draft_rows_from_waybill_sample(
    sample: dict[str, Any],
    *,
    parent_sequence: int,
) -> ParentWaybillDraft:
    raw_record_id = int_value(sample.get("raw_record_id")) or parent_sequence
    task_id = int_value(sample.get("task_id"))
    parent_label = f"第1批-第{parent_sequence}单"
    component = text_value(sample.get("source_component"))
    source = text_value(sample.get("source_index"))
    sample_text = text_value(sample.get("sample_text"))

    item_texts: list[str] = []
    for candidate_text in preferred_waybill_sample_texts(sample):
        parsed_items = product_item_texts(candidate_text)
        item_texts.extend(parsed_items or [candidate_text])
    item_texts = unique_item_texts_preserving_lines(item_texts)

    rows: list[OrderRowDraft] = []
    for index, item_text in enumerate(item_texts, start=1):
        fields = parse_item_text(item_text)
        rows.append(
            OrderRowDraft(
                raw_record_id=raw_record_id,
                task_id=task_id,
                parent_label=parent_label,
                child_label=f"{parent_label}-子{index}",
                child_index=index,
                child_count=len(item_texts),
                source_component=component,
                source_index=source,
                product=fields["product"],
                sales_attr1=fields["sales_attr1"],
                sales_attr2=fields["sales_attr2"],
                quantity=fields["quantity"],
                remark=fields["remark"],
                image_match_text=fields["image_match_text"],
                original_text=fields["original_text"],
                status=fields["status"],
                review_reason=fields["review_reason"],
            )
        )

    if not rows:
        rows.append(
            OrderRowDraft(
                raw_record_id=raw_record_id,
                task_id=task_id,
                parent_label=parent_label,
                child_label=f"{parent_label}-子1",
                child_index=1,
                child_count=1,
                source_component=component,
                source_index=source,
                product="",
                sales_attr1="",
                sales_attr2="",
                quantity=None,
                remark="",
                image_match_text=sample_text,
                original_text=sample_text,
                status="needs_review",
                review_reason="no_readable_waybill_text" if not sample_text else "no_product_text",
            )
        )

    total_children = len(rows)
    rows = [
        OrderRowDraft(
            **{
                **row.as_dict(),
                "child_count": total_children,
                "child_label": f"{parent_label}-子{index}",
                "child_index": index,
            }
        )
        for index, row in enumerate(rows, start=1)
    ]
    return ParentWaybillDraft(
        raw_record_id=raw_record_id,
        task_id=task_id,
        parent_label=parent_label,
        source_component=component,
        source_index=source,
        child_count=total_children,
        rows=rows,
    )


def draft_rows_from_standard_detail_values(
    values: dict[str, Any],
    *,
    standard_detail_id: int,
    parent_sequence: int,
) -> ParentWaybillDraft:
    task_id = int_value(values.get("capture_task_id"))
    raw_record_id = int_value(values.get("raw_record_id")) or standard_detail_id
    parent_label = f"第1批-第{parent_sequence}单"
    component = text_value(values.get("source_component"))
    source = text_value(values.get("source_index"))
    product_text = standard_detail_product_text(values)
    quantity_text = standard_detail_quantity_text(values)
    remark_text = standard_detail_remark_text(values)

    item_texts = product_item_texts(
        product_text,
        quantity_text=quantity_text,
        remark_text=remark_text,
    )
    if not item_texts and product_text:
        item_texts = [product_text]

    rows: list[OrderRowDraft] = []
    for index, item_text in enumerate(item_texts, start=1):
        fields = parse_item_text(
            item_text,
            fallback_quantity_text=quantity_text,
            remark_text=remark_text,
        )
        rows.append(
            OrderRowDraft(
                raw_record_id=raw_record_id,
                task_id=task_id,
                parent_label=parent_label,
                child_label=f"{parent_label}-子{index}",
                child_index=index,
                child_count=len(item_texts),
                source_component=component,
                source_index=source,
                product=fields["product"],
                sales_attr1=fields["sales_attr1"],
                sales_attr2=fields["sales_attr2"],
                quantity=fields["quantity"],
                remark=fields["remark"],
                image_match_text=fields["image_match_text"],
                original_text=fields["original_text"],
                status=fields["status"],
                review_reason=fields["review_reason"],
            )
        )

    if not rows:
        rows.append(
            OrderRowDraft(
                raw_record_id=raw_record_id,
                task_id=task_id,
                parent_label=parent_label,
                child_label=f"{parent_label}-子1",
                child_index=1,
                child_count=1,
                source_component=component,
                source_index=source,
                product="",
                sales_attr1="",
                sales_attr2="",
                quantity=None,
                remark="",
                image_match_text="",
                original_text=product_text,
                status="needs_review",
                review_reason="no_product_text",
            )
        )

    total_children = len(rows)
    rows = [
        OrderRowDraft(
            **{
                **row.as_dict(),
                "child_count": total_children,
                "child_label": f"{parent_label}-子{index}",
                "child_index": index,
            }
        )
        for index, row in enumerate(rows, start=1)
    ]
    return ParentWaybillDraft(
        raw_record_id=raw_record_id,
        task_id=task_id,
        parent_label=parent_label,
        source_component=component,
        source_index=source,
        child_count=total_children,
        rows=rows,
    )


def order_row_draft_summary(parents: list[ParentWaybillDraft]) -> dict[str, int]:
    rows = [row for parent in parents for row in parent.rows]
    return {
        "parent_waybill_count": len(parents),
        "child_waybill_count": len(rows),
        "draft_count": sum(1 for row in rows if row.status == "draft"),
        "needs_review_count": sum(1 for row in rows if row.status == "needs_review"),
        "special_count": sum(1 for row in rows if row.status == "special"),
    }
