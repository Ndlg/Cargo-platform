from __future__ import annotations

import re
from typing import Any


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


def normalized_key(value: object) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).lower())


def sensitive_key(value: object) -> bool:
    normalized = normalized_key(value)
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def sanitize_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return "[depth-limit]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 200:
                break
            if sensitive_key(key):
                continue
            sanitized[str(key)[:256]] = sanitize_payload(item, depth=depth + 1)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"1\d{10}", stripped) or re.fullmatch(r"[A-Z]{0,4}\d{10,}", stripped, re.I):
            return "[redacted]"
        return value[:4000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4000]
