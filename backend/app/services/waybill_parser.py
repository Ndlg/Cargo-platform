from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CaptureTask,
    RawCaptureRecord,
    StandardDetail,
    StandardDetailBatch,
    WaybillMode,
)
from app.services.woda_printxml_parser import parse_woda_custom_structure
from app.services.woda_template_matcher import find_template_config, find_woda_template_config


@dataclass(frozen=True)
class ParsedWaybill:
    mode_code: str
    template_code: str
    parser_version: str
    field_values: dict[str, Any]
    full_text: str
    usable_for_business: bool = True
    limited_reason: str | None = None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return "".join(
        ch
        for ch in str(value)
        if (ch >= " " or ch in "\n\t") and unicodedata.category(ch) != "Cf"
    ).strip()


def _first_int(value: Any) -> int | None:
    match = re.search(r"\d+", _text(value))
    return int(match.group(0)) if match else None


SIZE_TOKEN_PATTERN = r"(?:[2-4]\d(?:\.5)?|50|XS|S|M|L|XL|XXL|XXXL)"
QUANTITY_PATTERN = re.compile(
    r"(?:[*xX×]\s*(\d+)|(\d+)\s*(?:件|双|雙|个|個|条|條|套|份|只|支|瓶|包|组|組))"
)
LABELED_SIZE_PATTERN = re.compile(
    rf"(?:鞋码|鞋碼|尺码|尺碼|码数|碼數|码|碼)\s*[:：]?\s*({SIZE_TOKEN_PATTERN})",
    re.I,
)
SIZE_WITH_SUFFIX_PATTERN = re.compile(
    rf"({SIZE_TOKEN_PATTERN})\s*(?:鞋码|鞋碼|尺码|尺碼|码数|碼數|码|碼)",
    re.I,
)
GENERIC_SIZE_PATTERN = re.compile(rf"(?<!\d)({SIZE_TOKEN_PATTERN})(?!\d)", re.I)


def _compact_spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip()


def _quantity_from_text(*values: Any) -> int | None:
    for value in values:
        text = _text(value)
        if not text:
            continue
        match = QUANTITY_PATTERN.search(text)
        if match:
            quantity = match.group(1) or match.group(2)
            if quantity:
                parsed = int(quantity)
                if parsed > 0:
                    return parsed
        if re.fullmatch(r"\d+", text):
            parsed = int(text)
            if parsed > 0:
                return parsed
    return None


def _strip_quantity_text(value: str) -> str:
    text = _compact_spaces(value)
    return _compact_spaces(QUANTITY_PATTERN.sub("", text))


def _size_text_from_values(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if not text:
            continue
        for pattern in (LABELED_SIZE_PATTERN, SIZE_WITH_SUFFIX_PATTERN, GENERIC_SIZE_PATTERN):
            matches = [match.group(1) for match in pattern.finditer(text)]
            if matches:
                return matches[-1].upper() if matches[-1].isalpha() else matches[-1]
    return ""


def _strip_size_text(value: str, size_text: str) -> str:
    text = _compact_spaces(value)
    size = _text(size_text)
    if not text or not size:
        return text
    labeled = re.compile(
        rf"(?:鞋码|鞋碼|尺码|尺碼|码数|碼數|码|碼)\s*[:：]?\s*{re.escape(size)}",
        re.I,
    )
    text = labeled.sub("", text)
    suffixed = re.compile(
        rf"{re.escape(size)}\s*(?:鞋码|鞋碼|尺码|尺碼|码数|碼數|码|碼)",
        re.I,
    )
    text = suffixed.sub("", text)
    generic = re.compile(rf"(?<!\d){re.escape(size)}(?!\d)", re.I)
    return _compact_spaces(generic.sub("", text))


def _guess_product_text(value: str) -> str:
    text = _compact_spaces(value)
    if not text:
        return ""
    numeric_prefix = re.match(r"^(\d+(?:\.\d+)?)(?=\s|[\u4e00-\u9fff]|$)", text)
    if numeric_prefix:
        return numeric_prefix.group(1)

    tokens = text.split()
    if len(tokens) >= 2 and re.search(r"[\u4e00-\u9fff]", tokens[0]) and re.search(r"[A-Za-z0-9]", tokens[1]):
        return f"{tokens[0]} {tokens[1]}"
    return tokens[0] if tokens else text


def _standard_item_from_platform_text(
    product_text: Any,
    *,
    quantity_text: Any = "",
    fallback_texts: list[Any] | None = None,
) -> dict[str, str]:
    item_text = _compact_spaces(product_text)
    fallback_values = fallback_texts or []
    quantity = _quantity_from_text(quantity_text, item_text) or 1
    without_quantity = _strip_quantity_text(item_text)
    size_text = _size_text_from_values(without_quantity, *fallback_values)
    sales_attr1_text = _strip_size_text(without_quantity, size_text)
    if not sales_attr1_text:
        sales_attr1_text = without_quantity or item_text
    product_key = _guess_product_text(sales_attr1_text) or item_text
    return {
        "product_text": product_key,
        "sales_attr1_text": sales_attr1_text,
        "sales_attr2_text": size_text,
        "quantity_text": str(quantity),
        "spec_text": sales_attr1_text,
        "size_text": size_text,
        "remark_text": _compact_spaces("\n".join(_text(value) for value in fallback_values if _text(value))),
        "raw_text": item_text,
    }


def _standard_item_fields(item: dict[str, str]) -> dict[str, Any]:
    return {
        "custom_product_text": item["product_text"],
        "custom_sales_attr1_text": item["sales_attr1_text"],
        "custom_sales_attr2_text": item["sales_attr2_text"],
        "custom_quantity_text": item["quantity_text"],
        "custom_spec_text": item["spec_text"],
        "custom_size_text": item["size_text"],
        "custom_item_remark_text": item["remark_text"],
        "custom_items": [item],
    }


def _mapping_extractor(config_payload: dict[str, Any] | None, field_code: str) -> str:
    if not isinstance(config_payload, dict):
        return ""
    mappings = config_payload.get("field_mappings")
    if not isinstance(mappings, dict):
        return ""
    mapping = mappings.get(field_code)
    if not isinstance(mapping, dict):
        return ""
    return _text(mapping.get("extractor"))


def _douyin_product_info_attrs(product_full_text: str) -> tuple[str, str]:
    text = _compact_spaces(product_full_text)
    if not text:
        return "", ""
    last_bracket = text.rfind("】")
    tail = text[last_bracket + 1 :].strip() if last_bracket >= 0 else text
    tail = _strip_quantity_text(tail)
    sales_attr2_text = _size_text_from_values(tail)
    sales_attr1_text = _strip_size_text(tail, sales_attr2_text)
    return sales_attr1_text, sales_attr2_text


def _douyin_standard_item_from_fields(
    *,
    product_full_text: str,
    product_count_text: str,
    remark_text: str,
    config_payload: dict[str, Any] | None = None,
) -> dict[str, str]:
    quantity = _quantity_from_text(product_count_text) or 1
    inferred_attr1, inferred_attr2 = _douyin_product_info_attrs(product_full_text)
    extractor_values = {
        "product_text": product_full_text,
        "sales_attr1_text": inferred_attr1,
        "sales_attr2_text": inferred_attr2,
        "quantity_text": str(quantity),
        "remark_text": _compact_spaces(remark_text),
    }
    sales_attr1_text = (
        extractor_values.get(_mapping_extractor(config_payload, "custom_sales_attr1_text"), "")
        or inferred_attr1
    )
    sales_attr2_text = (
        extractor_values.get(_mapping_extractor(config_payload, "custom_sales_attr2_text"), "")
        or inferred_attr2
    )
    return {
        "product_text": product_full_text,
        "sales_attr1_text": sales_attr1_text,
        "sales_attr2_text": sales_attr2_text,
        "quantity_text": str(quantity),
        "spec_text": sales_attr1_text,
        "size_text": sales_attr2_text,
        "remark_text": _compact_spaces(remark_text),
        "raw_text": product_full_text,
    }


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _load_json(raw_payload: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _full_text_from_fields(fields: dict[str, Any]) -> str:
    ordered_keys = [
        "logistics_no",
        "order_no",
        "shop_name",
        "source_platform",
        "product_short_text",
        "product_full_text",
        "product_count_text",
        "spec_text",
        "custom_area_raw_text",
        "buyer_remark",
        "seller_remark",
    ]
    return "\n".join(_text(fields.get(key)) for key in ordered_keys if _text(fields.get(key)))


def _find_douyin_data(payload: dict[str, Any]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -1
    target_keys = {"trackNo", "orderId", "shopName", "productInfo", "productShortInfo"}
    for item in _walk_dicts(payload):
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        score = len(target_keys.intersection(data.keys()))
        if score > best_score:
            best = data
            best_score = score
    return best if best_score > 0 else None


def _document_douyin_data(document: dict[str, Any]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -1
    target_keys = {"trackNo", "orderId", "shopName", "productInfo", "productShortInfo"}
    for content in _document_contents(document):
        data = content.get("data")
        if not isinstance(data, dict):
            continue
        score = len(target_keys.intersection(data.keys()))
        if score > best_score:
            best = data
            best_score = score
    return best if best_score > 0 else None


def _template_urls(payload: dict[str, Any]) -> list[str]:
    template_urls = []
    for item in _walk_dicts(payload):
        template_url = item.get("templateURL")
        if template_url and template_url not in template_urls:
            template_urls.append(template_url)
    return template_urls


def _task(payload: dict[str, Any]) -> dict[str, Any]:
    task = payload.get("task")
    return task if isinstance(task, dict) else {}


def _documents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    documents = _task(payload).get("documents")
    return [item for item in documents if isinstance(item, dict)] if isinstance(documents, list) else []


def _document_contents(document: dict[str, Any]) -> list[dict[str, Any]]:
    contents = document.get("contents")
    return [item for item in contents if isinstance(item, dict)] if isinstance(contents, list) else []


def _document_custom_data(document: dict[str, Any]) -> dict[str, Any] | None:
    target_keys = {
        "WAIBILLNO_BAR_CODE",
        "ORDER_ID",
        "SHOP_NAME",
        "ITEM_INFO",
        "BUYER_MEMO",
        "SELLER_MEMO",
    }
    for content in _document_contents(document):
        data = content.get("data")
        if isinstance(data, dict) and target_keys.intersection(data.keys()):
            return data
    return None


def _document_printxml_texts(document: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for content in _document_contents(document):
        print_xml = content.get("printXML")
        if not print_xml:
            continue
        texts.extend(
            _text(match)
            for match in re.findall(r"<!\[CDATA\[(.*?)\]\]>", str(print_xml), flags=re.DOTALL)
            if _text(match)
        )
    return texts


def _printxml_layout_fingerprint(print_xml: Any) -> str:
    xml = _text(print_xml)
    if not xml:
        return ""
    normalized = re.sub(r"<!\[CDATA\[.*?\]\]>", "<![CDATA[]]>", xml, flags=re.DOTALL)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _document_printxml_template_key(document: dict[str, Any]) -> str:
    fingerprints = [
        fingerprint
        for content in _document_contents(document)
        if (fingerprint := _printxml_layout_fingerprint(content.get("printXML")))
    ]
    return f"printxml:{'-'.join(fingerprints)}" if fingerprints else ""


def _document_template_key(document: dict[str, Any]) -> str:
    urls = _document_template_urls(document)
    return "url:" + "|".join(urls) if urls else _document_printxml_template_key(document)


def _document_add_data(document: dict[str, Any]) -> dict[str, Any] | None:
    for content in _document_contents(document):
        add_data = content.get("addData")
        if isinstance(add_data, dict):
            return add_data
    return None


def _document_template_urls(document: dict[str, Any]) -> list[str]:
    urls = []
    for item in _walk_dicts(document):
        template_url = item.get("templateURL")
        if template_url and template_url not in urls:
            urls.append(template_url)
    return urls


def _normalized_template_url_key(template_url: Any) -> str:
    text = _text(template_url)
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme and parsed.netloc and parsed.path:
        return parsed.path.strip("/")
    return re.sub(r"[?#].*$", "", text).strip("/")


def _douyin_template_key_from_urls(template_urls: list[str]) -> str:
    primary_urls = [
        url
        for url in template_urls
        if "/template/" in _text(url).replace("\\", "/")
    ]
    normalized_urls: list[str] = []
    for template_url in primary_urls or template_urls:
        normalized = _normalized_template_url_key(template_url)
        if normalized and normalized not in normalized_urls:
            normalized_urls.append(normalized)
    return f"url:{'|'.join(normalized_urls)}" if normalized_urls else ""


def _parsed_douyin_waybill(
    record: RawCaptureRecord,
    data: dict[str, Any],
    *,
    document_id: str,
    db: Session | None = None,
    document_sequence: int | None = None,
    template_urls: list[str] | None = None,
) -> ParsedWaybill:
    product_short_text = _text(data.get("productShortInfo"))
    product_full_text = _text(data.get("productInfo"))
    product_count_text = _text(data.get("productCount"))
    quantity = _quantity_from_text(product_count_text, product_short_text) or 1
    logistics_no = _text(data.get("trackNo")) or document_id or _text(record.document_id)
    order_no = _text(data.get("orderId"))
    template_code = _text(data.get("templateCode")) or "douyin_cloud_print_v1"
    douyin_template_urls = template_urls or []
    source_template_key = _douyin_template_key_from_urls(douyin_template_urls) or template_code
    print_template_source = "douyin_template_url" if source_template_key.startswith("url:") else "douyin_template_code"
    template_config = find_template_config(db, record.workspace_id, "douyin_cloud_print", source_template_key)
    template_config_payload = template_config.config if template_config and isinstance(template_config.config, dict) else None
    standard_item = _douyin_standard_item_from_fields(
        product_full_text=product_full_text,
        product_count_text=product_count_text,
        remark_text=_text(data.get("remark")),
        config_payload=template_config_payload,
    )

    field_values: dict[str, Any] = {
        "raw_record_id": record.id,
        "capture_task_id": record.task_id,
        "document_sequence": document_sequence,
        "source_component": record.source_component,
        "source_index": record.source_index,
        "raw_document_id": document_id or record.document_id,
        "source_platform": "douyin",
        "logistics_no": logistics_no,
        "order_no": order_no,
        "shop_name": _text(data.get("shopName")),
        "product_full_text": product_full_text,
        "product_short_text": product_short_text,
        "product_count_text": product_count_text,
        "quantity": quantity,
        "spec_text": product_short_text,
        "buyer_remark": _text(data.get("buyerRemark")),
        "seller_remark": _text(data.get("remark")),
        "sp_info": _text(data.get("sPInfo")),
        "sps_info": _text(data.get("sPSInfo")),
        "template_code": template_code,
        "template_urls": douyin_template_urls,
        "parent_template_code": _text(data.get("parentTemplateCode")),
        "print_template_key": source_template_key,
        "print_template_source_key": source_template_key,
        "print_template_config_key": template_config.template_key if template_config else None,
        "print_template_config_id": template_config.id if template_config else None,
        "print_template_config_label": template_config.template_label if template_config else None,
        "print_template_source": print_template_source,
        "parser_template_code": "douyin_cloud_print_v1",
    }
    field_values.update(_standard_item_fields(standard_item))
    return ParsedWaybill(
        mode_code="douyin_cloud_print",
        template_code="douyin_cloud_print_v1",
        parser_version="1",
        field_values=field_values,
        full_text=_full_text_from_fields(field_values),
    )


def parse_douyin_cloud_print(record: RawCaptureRecord, db: Session | None = None) -> list[ParsedWaybill]:
    payload = _load_json(record.raw_payload)
    if payload is None:
        return []

    parsed_items: list[ParsedWaybill] = []
    for index, document in enumerate(_documents(payload), start=1):
        data = _document_douyin_data(document)
        if data is None:
            continue
        parsed_items.append(
            _parsed_douyin_waybill(
                record,
                data,
                document_id=_text(document.get("documentID")) or _text(record.document_id),
                db=db,
                document_sequence=index,
                template_urls=_document_template_urls(document),
            )
        )
    if parsed_items:
        return parsed_items

    data = _find_douyin_data(payload)
    if data is None:
        return []
    return [
        _parsed_douyin_waybill(
            record,
            data,
            document_id=_text(record.document_id),
            db=db,
            document_sequence=None,
            template_urls=_template_urls(payload),
        )
    ]


def parse_cainiao_direct_shop(record: RawCaptureRecord, payload: dict[str, Any]) -> list[ParsedWaybill]:
    parsed_items: list[ParsedWaybill] = []
    for index, document in enumerate(_documents(payload)):
        data = _document_custom_data(document)
        if data is None:
            continue
        logistics_no = _text(data.get("WAIBILLNO_BAR_CODE")) or _text(document.get("documentID"))
        product_full_text = _text(data.get("ITEM_INFO"))
        buyer_remark = _text(data.get("BUYER_MEMO"))
        seller_remark = _text(data.get("SELLER_MEMO"))
        item_total_count = _text(data.get("ITEM_TOTAL_COUNT"))
        standard_item = _standard_item_from_platform_text(
            product_full_text,
            quantity_text=item_total_count,
            fallback_texts=[buyer_remark, seller_remark],
        )
        field_values: dict[str, Any] = {
            "raw_record_id": record.id,
            "capture_task_id": record.task_id,
            "document_sequence": index + 1,
            "source_component": record.source_component,
            "source_index": record.source_index,
            "source_platform": "cainiao_direct_shop",
            "raw_document_id": _text(document.get("documentID")) or record.document_id,
            "logistics_no": logistics_no,
            "order_no": _text(data.get("ORDER_ID")),
            "shop_name": _text(data.get("SHOP_NAME")),
            "product_full_text": product_full_text,
            "product_short_text": product_full_text,
            "spec_text": standard_item["spec_text"],
            "quantity": int(standard_item["quantity_text"]),
            "buyer_remark": buyer_remark,
            "seller_remark": seller_remark,
            "buyer_nick": _text(data.get("BUYER_NICK")),
            "pay_order_time": _text(data.get("PAY_ORDER_TIME")),
            "create_order_time": _text(data.get("CREATE_ORDER_TIME")),
            "print_time": _text(data.get("PRINT_TIME")),
            "item_total_price": _text(data.get("ITEM_TOTAL_PRICE")),
            "item_total_count": item_total_count,
            "daily_unique_order": _text(data.get("DAILY_UNIQUE_ORDER")),
            "template_urls": _document_template_urls(document),
            "print_template_key": _document_template_key(document),
            "print_template_source": "template_url",
            "parser_template_code": "cainiao_direct_shop_v1",
        }
        field_values.update(_standard_item_fields(standard_item))
        parsed_items.append(
            ParsedWaybill(
                mode_code="cainiao_direct_shop",
                template_code="cainiao_direct_shop_v1",
                parser_version="1",
                field_values=field_values,
                full_text=_full_text_from_fields(field_values),
            )
        )
    return parsed_items


def parse_cainiao_woda_printxml(
    record: RawCaptureRecord,
    payload: dict[str, Any],
    db: Session | None = None,
) -> list[ParsedWaybill]:
    parsed_items: list[ParsedWaybill] = []
    for index, document in enumerate(_documents(payload)):
        document_id = _text(document.get("documentID")) or _text(record.document_id)
        if "-woda-" not in document_id:
            continue
        custom_texts = _document_printxml_texts(document)
        custom_text = "\n".join(custom_texts)
        add_data = _document_add_data(document)
        print_template_key = _document_printxml_template_key(document)
        template_config = find_woda_template_config(db, record.workspace_id, print_template_key)
        template_config_payload = template_config.config if template_config and isinstance(template_config.config, dict) else None
        custom_structure = parse_woda_custom_structure(custom_text, template_config_payload)
        field_values: dict[str, Any] = {
            "raw_record_id": record.id,
            "capture_task_id": record.task_id,
            "document_sequence": index + 1,
            "source_component": record.source_component,
            "source_index": record.source_index,
            "source_platform": "woda",
            "raw_document_id": document_id,
            "logistics_no": "",
            "order_no": "",
            "product_full_text": custom_text,
            "product_short_text": custom_text,
            "spec_text": custom_text,
            "encrypted_waybill": True,
            "custom_area_kind": "printXML",
            "custom_area_raw_text": custom_text,
            "custom_area_lines": custom_texts,
            "template_urls": _document_template_urls(document),
            "print_template_key": print_template_key,
            "print_template_source_key": print_template_key,
            "print_template_config_key": template_config.template_key if template_config else None,
            "print_template_config_id": template_config.id if template_config else None,
            "print_template_config_label": template_config.template_label if template_config else None,
            "print_template_source": "printxml_layout",
            "sender_masked": add_data.get("sender") if isinstance(add_data, dict) else None,
            "recipient_masked": add_data.get("recipient") if isinstance(add_data, dict) else None,
            "parser_template_code": "cainiao_woda_printxml_v1",
        }
        field_values.update(custom_structure)
        parsed_items.append(
            ParsedWaybill(
                mode_code="cainiao_woda_printxml",
                template_code="cainiao_woda_printxml_v1",
                parser_version="1",
                field_values=field_values,
                full_text=_full_text_from_fields(field_values),
            )
        )
    return parsed_items


def parse_cainiao_cloud_print(record: RawCaptureRecord, db: Session | None = None) -> list[ParsedWaybill]:
    payload = _load_json(record.raw_payload)
    if payload is None:
        return []

    direct_items = parse_cainiao_direct_shop(record, payload)
    if direct_items:
        return direct_items

    woda_items = parse_cainiao_woda_printxml(record, payload, db)
    if woda_items:
        return woda_items

    return []


def parse_raw_record(record: RawCaptureRecord, db: Session | None = None) -> list[ParsedWaybill]:
    if record.source_component == "cloud-print-client":
        return parse_douyin_cloud_print(record, db)
    if record.source_component == "cainiao-cnprint":
        return parse_cainiao_cloud_print(record, db)
    return []


def _mode_id(db: Session, mode_code: str) -> int | None:
    mode = db.scalars(
        select(WaybillMode).where(
            WaybillMode.code == mode_code,
            WaybillMode.is_enabled.is_(True),
            WaybillMode.is_deleted.is_(False),
        )
    ).first()
    return mode.id if mode else None


def _batch_for_record(db: Session, record: RawCaptureRecord, parsed: ParsedWaybill) -> StandardDetailBatch:
    batch_key = f"capture_task:{record.task_id or 'unknown'}:{parsed.mode_code}"
    batch = db.scalars(
        select(StandardDetailBatch).where(
            StandardDetailBatch.workspace_id == record.workspace_id,
            StandardDetailBatch.source_type == "capture_task",
            StandardDetailBatch.file_path == batch_key,
            StandardDetailBatch.is_deleted.is_(False),
        )
    ).first()
    if batch is not None:
        return batch

    batch = StandardDetailBatch(
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        waybill_mode_id=_mode_id(db, parsed.mode_code),
        source_type="capture_task",
        file_path=batch_key,
        status="parsed",
    )
    db.add(batch)
    db.flush()
    return batch


def _reset_existing_parse(db: Session, record: RawCaptureRecord) -> None:
    existing_detail_ids = set()
    if record.standard_detail_id is not None:
        existing_detail_ids.add(record.standard_detail_id)
    if isinstance(record.parsed_payload, dict):
        detail_ids = record.parsed_payload.get("detail_ids")
        if isinstance(detail_ids, list):
            existing_detail_ids.update(detail_id for detail_id in detail_ids if isinstance(detail_id, int))

    for detail_id in existing_detail_ids:
        existing_detail = db.get(StandardDetail, detail_id)
        if existing_detail is not None:
            existing_detail.is_deleted = True

    record.standard_detail_id = None
    record.waybill_mode = None
    record.parsed_payload = None
    record.status = "pending"
    db.flush()


def parse_raw_capture_record(db: Session, record: RawCaptureRecord, *, force: bool = False) -> str:
    if record.standard_detail_id is not None and not force:
        return "skipped"
    if force:
        _reset_existing_parse(db, record)

    parsed_items = parse_raw_record(record, db)
    if not parsed_items:
        record.status = "unsupported"
        record.parsed_payload = {"parser_status": "unsupported"}
        return "unsupported"

    detail_ids = []
    parsed_payload_items = []
    for parsed in parsed_items:
        detail_id = None
        if parsed.usable_for_business:
            batch = _batch_for_record(db, record, parsed)
            detail = StandardDetail(
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                standard_detail_batch_id=batch.id,
                waybill_mode=parsed.mode_code,
                full_text=parsed.full_text,
                field_values=parsed.field_values,
                image_match_status="pending",
                stall_match_status="pending",
                raw_payload=record.raw_payload,
            )
            db.add(detail)
            db.flush()
            detail_id = detail.id
            detail_ids.append(detail.id)
        parsed_payload_items.append(
            {
                "standard_detail_id": detail_id,
                "mode_code": parsed.mode_code,
                "template_code": parsed.template_code,
                "parser_version": parsed.parser_version,
                "usable_for_business": parsed.usable_for_business,
                "limited_reason": parsed.limited_reason,
                "field_values": parsed.field_values,
            }
        )

    record.standard_detail_id = detail_ids[0] if detail_ids else None
    record.waybill_mode = parsed_items[0].mode_code
    record.parsed_payload = {
        "parser_status": "parsed" if detail_ids else "limited",
        "detail_ids": detail_ids,
        "documents": parsed_payload_items,
    }
    if detail_ids:
        record.status = "parsed"
        return "parsed"

    record.status = "limited"
    return "limited"


def parse_raw_capture_records(
    db: Session,
    records: list[RawCaptureRecord],
    *,
    force: bool = False,
) -> dict[str, int]:
    result = {"parsed": 0, "limited": 0, "unsupported": 0, "skipped": 0}
    for record in records:
        status = parse_raw_capture_record(db, record, force=force)
        result[status] = result.get(status, 0) + 1
    return result
