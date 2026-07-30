from __future__ import annotations

import re
from typing import Any
import unicodedata

from pydantic import ValidationError

from .contracts import AiOrderRow, SpanSelectionCandidate


MAX_SPANS = 200
MAX_GROUPS = 100
MAX_GROUP_SIZE = 100
MAX_SPAN_VALUE_LENGTH = 512
GROUP_KEYS = (
    "structured_list_item",
    "line",
    "delimiter_separated_segment",
    "positive_integer_quantity",
    "shoe_size_like_numeric_segment",
    "repeated_line_or_array_group",
)
SPAN_LABELS = {
    "text",
    "delimiter_segment",
    "positive_integer_quantity",
    "shoe_size_like_numeric_segment",
}
_SENSITIVE_VALUE_RE = re.compile(
    r"(?:(?<!\d)1[3-9]\d{9}(?!\d)|(?<![A-Z0-9])[A-Z]{0,4}\d{10,}(?![A-Z0-9])|"
    r"收件人|收货地址|详细地址|联系电话|手机号|快递单号|运单号|订单号)",
    re.IGNORECASE,
)
_SENSITIVE_PATH_RE = re.compile(
    r"(?:receiver|recipient|consignee|sender|buyer)"
    r"|address|phone|mobile|telephone"
    r"|(?:waybill|tracking|logistics|order)(?:id|no|number|code)"
    r"|收件人|收货人|收货地址|详细地址|联系电话|手机号|快递单号|运单号|订单号",
    re.IGNORECASE,
)
_UNLABELLED_ADDRESS_RE = re.compile(
    r"(?:省|自治区|特别行政区).{0,30}(?:市|自治州|区|县|旗)"
    r"|(?:市|自治州|区|县|旗).{0,30}(?:大道|路|街|巷|弄)\s*\d+\s*(?:号|室)?"
    r"|(?:大道|路|街|巷|弄)\s*\d+\s*(?:号|室)"
)
_PERSON_NAME_RE = re.compile(
    r"^(?:(?:欧阳|司马|上官|诸葛)[\u4e00-\u9fff]{1,2}|"
    r"[王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于"
    r"蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦傅方白邹孟熊秦邱江尹"
    r"薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严赖][\u4e00-\u9fff]{1,2})$"
)
_PRODUCT_TEXT_PATH_RE = re.compile(
    r"iteminfo|itemname|productinfo|productshortinfo|productname|customcontent"
    r"|skufullname|skusize|specname|specsimplename|goodsname|(?:items\d+|data)product$",
    re.IGNORECASE,
)


def _short_text(value: object, limit: int) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())[:limit]


def sanitize_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if evidence.get("contract_version") != "waybill_evidence_v1":
        raise ValueError("unsupported evidence contract")
    raw_spans = evidence.get("spans")
    raw_groups = evidence.get("candidate_groups")
    if not isinstance(raw_spans, list) or not isinstance(raw_groups, dict):
        raise ValueError("evidence is incomplete")
    fingerprint_code = _short_text(evidence.get("fingerprint_code", ""), 128)
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,127}", fingerprint_code):
        raise ValueError("evidence fingerprint code is invalid")
    if not raw_spans or len(raw_spans) > MAX_SPANS:
        raise ValueError("evidence span count is invalid")

    spans: list[dict[str, str]] = []
    span_ids: set[str] = set()
    raw_span_ids: set[str] = set()
    for raw_span in raw_spans:
        if not isinstance(raw_span, dict):
            raise ValueError("evidence span is invalid")
        source_path = str(raw_span.get("source_path") or "")
        span_id = _short_text(raw_span.get("span_id", ""), 128)
        label = _short_text(raw_span.get("token_class", ""), 64)
        value = _short_text(raw_span.get("normalized_text", ""), MAX_SPAN_VALUE_LENGTH)
        if (
            not source_path
            or not span_id
            or label not in SPAN_LABELS
            or not value
            or span_id in raw_span_ids
        ):
            raise ValueError("evidence span is invalid")
        raw_span_ids.add(span_id)
        normalized_path = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", source_path)
        if (
            _SENSITIVE_PATH_RE.search(normalized_path)
            or _SENSITIVE_VALUE_RE.search(value)
            or _UNLABELLED_ADDRESS_RE.search(value)
            or (
                _PERSON_NAME_RE.fullmatch(value)
                and not _PRODUCT_TEXT_PATH_RE.search(normalized_path)
            )
        ):
            continue
        span_ids.add(span_id)
        spans.append({"span_id": span_id, "label": label, "value": value})
    if not spans:
        raise ValueError("evidence contains no safe business spans")

    groups: dict[str, list[list[str]]] = {}
    for key in GROUP_KEYS:
        raw_entries = raw_groups.get(key, [])
        if not isinstance(raw_entries, list) or len(raw_entries) > MAX_GROUPS:
            raise ValueError("evidence candidate groups are invalid")
        entries: list[list[str]] = []
        for raw_group in raw_entries:
            if not isinstance(raw_group, list) or not raw_group or len(raw_group) > MAX_GROUP_SIZE:
                raise ValueError("evidence candidate group is invalid")
            group = [str(span_id) for span_id in raw_group]
            if len(group) != len(set(group)) or any(
                span_id not in raw_span_ids for span_id in group
            ):
                raise ValueError("evidence candidate group is invalid")
            group = [span_id for span_id in group if span_id in span_ids]
            if not group:
                continue
            entries.append(group)
        groups[key] = entries
    return {
        "fingerprint_code": fingerprint_code,
        "spans": spans,
        "candidate_groups": groups,
    }


def _row_limit(evidence: dict[str, Any]) -> int:
    groups = evidence["candidate_groups"]
    structured_count = len(groups.get("structured_list_item", []))
    if structured_count:
        return structured_count
    repeated_count = max(
        (len(group) for group in groups.get("repeated_line_or_array_group", [])),
        default=0,
    )
    return max(1, repeated_count)


def validate_selection(selection: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    try:
        candidate = SpanSelectionCandidate.model_validate(selection)
    except ValidationError as exc:
        return {"status": "candidate_invalid", "error": str(exc)[:2000]}
    if len(candidate.rows) > _row_limit(evidence):
        return {
            "status": "candidate_invalid",
            "error": "selected row count exceeds evidence repeat groups",
        }

    span_order = {
        span["span_id"]: (index, span["value"])
        for index, span in enumerate(evidence["spans"])
    }
    used_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for selected_row in candidate.rows:
        selected = selected_row.model_dump()
        field_ids = {
            field: (
                [value]
                if field == "quantity_span_id"
                else value
            )
            for field, value in selected.items()
        }
        all_ids = [span_id for ids in field_ids.values() for span_id in ids]
        if any(span_id not in span_order for span_id in all_ids):
            return {"status": "candidate_invalid", "error": "selection references an unknown span"}
        if len(all_ids) != len(set(all_ids)):
            return {
                "status": "candidate_invalid",
                "error": "one span cannot populate conflicting fields",
            }
        if used_ids.intersection(all_ids):
            return {
                "status": "candidate_invalid",
                "error": "one span cannot be reused across rows",
            }
        used_ids.update(all_ids)

        def text(ids: list[str]) -> str:
            return " ".join(
                span_order[span_id][1]
                for span_id in sorted(ids, key=lambda span_id: span_order[span_id][0])
            ).strip()

        quantity_text = text(field_ids["quantity_span_id"])
        if not re.fullmatch(r"[1-9]\d*", quantity_text):
            return {"status": "candidate_invalid", "error": "quantity is not a positive integer"}
        try:
            row = AiOrderRow.model_validate(
                {
                    "product": text(field_ids["product_span_ids"]),
                    "sales_attr1": text(field_ids["sales_attr1_span_ids"]),
                    "sales_attr2": text(field_ids["sales_attr2_span_ids"]),
                    "quantity": int(quantity_text),
                    "remark": text(field_ids["remark_span_ids"]),
                }
            ).model_dump(mode="json")
        except ValidationError as exc:
            return {"status": "candidate_invalid", "error": str(exc)[:2000]}
        rows.append(row)

    return {
        "status": "candidate_valid",
        "selection": candidate.model_dump(mode="json"),
        "rows": rows,
    }
