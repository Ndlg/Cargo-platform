from __future__ import annotations

import re
from typing import Any

from .fingerprint import _print_text


SENSITIVE_KEY_PARTS = {
    "address",
    "buyerid",
    "buyername",
    "consignee",
    "expressno",
    "logisticsno",
    "mailno",
    "mobile",
    "ordercode",
    "orderid",
    "orderno",
    "phone",
    "receiver",
    "recipient",
    "tracking",
    "waybill",
    "买家",
    "地址",
    "手机",
    "收件",
    "电话",
    "订单号",
    "运单号",
    "快递单号",
}

BUSINESS_KEY_PARTS = {
    "attr",
    "color",
    "customcontent",
    "itemdetail",
    "iteminfo",
    "itemname",
    "itemtitle",
    "itemtotalcount",
    "product",
    "quantity",
    "remark",
    "size",
    "sku",
    "spec",
    "商品",
    "备注",
    "尺码",
    "数量",
    "款式",
    "规格",
    "颜色",
}
ITEM_CONTAINER_KEY_PARTS = {
    "items",
    "orderitems",
    "packageitem",
    "productitems",
    "skus",
}
GENERIC_ITEM_KEYS = {
    "attr1",
    "attr2",
    "color",
    "count",
    "name",
    "quantity",
    "remark",
    "size",
    "spec",
    "title",
}


def normalized_key(value: object) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).lower())


def sensitive_key(value: object) -> bool:
    normalized = normalized_key(value)
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def business_key(value: object, *, item_context: bool) -> bool:
    normalized = normalized_key(value)
    return any(part in normalized for part in BUSINESS_KEY_PARTS) or (
        item_context and normalized in GENERIC_ITEM_KEYS
    )


def item_container_key(value: object) -> bool:
    normalized = normalized_key(value)
    return any(part in normalized for part in ITEM_CONTAINER_KEY_PARTS)


def sanitize_payload(
    value: Any,
    *,
    depth: int = 0,
    item_context: bool = False,
    allowed_source_keys: set[str] | None = None,
) -> Any:
    if depth > 12:
        return "[depth-limit]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 200:
                break
            selected_key = allowed_source_keys is not None and str(key) in allowed_source_keys
            if normalized_key(key) == "printxml" and isinstance(item, str):
                if (allowed_source_keys is None or selected_key) and (
                    text := _print_text({"printXML": item})
                ):
                    sanitized[str(key)[:256]] = text
                continue
            if sensitive_key(key) and not selected_key:
                continue
            child_context = item_context or item_container_key(key)
            child = sanitize_payload(
                item,
                depth=depth + 1,
                item_context=child_context,
                allowed_source_keys=allowed_source_keys,
            )
            if isinstance(item, (dict, list)):
                if child:
                    sanitized[str(key)[:256]] = child
            elif selected_key or (
                allowed_source_keys is None and business_key(key, item_context=item_context)
            ):
                sanitized[str(key)[:256]] = sanitize_payload(
                    item,
                    depth=depth + 1,
                    item_context=True,
                    allowed_source_keys=allowed_source_keys,
                )
        return sanitized
    if isinstance(value, list):
        return [
            sanitized
            for item in value[:100]
            if (
                sanitized := sanitize_payload(
                    item,
                    depth=depth + 1,
                    item_context=item_context,
                    allowed_source_keys=allowed_source_keys,
                )
            )
            not in ({}, [], "", None)
        ]
    if isinstance(value, str):
        if not item_context:
            return ""
        stripped = value.strip()
        if re.fullmatch(r"1\d{10}", stripped) or re.fullmatch(r"[A-Z]{0,4}\d{10,}", stripped, re.I):
            return "[redacted]"
        return value[:4000]
    if value is None or isinstance(value, (bool, int, float)):
        if not item_context:
            return None
        return value
    return str(value)[:4000] if item_context else ""
