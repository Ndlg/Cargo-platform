from __future__ import annotations

from dataclasses import dataclass
import html
import json
import re
import unicodedata
from typing import Any, Iterable

from app.models import RawCaptureRecord


WAYBILL_READING_CONTRACT_VERSION = "waybill_reading_sample_v1"
TEXT_BLOCK_CONTRACT_FIELDS = (
    "block_id",
    "text",
    "source",
    "block_kind",
    "line_index",
    "order",
    "raw_record_id",
    "trace",
)


def trace_selector_key(
    *,
    source: str,
    source_path: str,
    source_text_order: int,
    source_line_index: int,
    segment_index: int | str,
) -> str:
    return (
        f"{source}:{source_path}:"
        f"text-{source_text_order}:line-{source_line_index}:segment-{segment_index}"
    )


@dataclass(frozen=True)
class SourceText:
    text: str
    source: str
    source_path: str
    document_sequence: int | None = None
    document_id: str | None = None


@dataclass(frozen=True)
class HiddenRawField:
    text: str
    source_path: str
    filter_reason: str
    document_sequence: int | None = None
    document_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source": "raw_field",
            "source_path": self.source_path,
            "filter_reason": self.filter_reason,
            "document_sequence": self.document_sequence,
            "document_id": self.document_id,
        }


TECHNICAL_RAW_FIELD_NAMES = {
    "rowid",
    "id",
    "component_task_id",
    "task_time",
    "db_path",
    "created_at",
    "updated_at",
    "collector_id",
    "token",
    "machine_id",
    "source_path",
    "source_component",
    "source_index",
    "dedupe_key",
    "captured_at",
    "document_id",
    "workspace_id",
    "tenant_id",
    "shop",
    "shopname",
    "shop_name",
    "store",
    "storename",
    "store_name",
    "nick",
    "nickname",
    "buyer_nick",
    "buyernick",
    "buyer_nickname",
    "item_total_price",
    "item_total_count",
    "total_line",
    "showiteminfo",
    "show_item_info",
}
TECHNICAL_RAW_FIELD_KEYWORDS = (
    "token",
    "password",
    "secret",
    "db_path",
    "database",
    "machine_id",
    "collector_id",
)
BUSINESS_RAW_FIELD_KEYWORDS = (
    "product",
    "productinfo",
    "goods",
    "item",
    "title",
    "sku",
    "quantity",
    "qty",
    "count",
    "spec",
    "color",
    "colour",
    "size",
    "remark",
    "memo",
    "message",
    "buyer_message",
    "seller_remark",
    "buyerremark",
    "sellerremark",
    "buyer_memo",
    "seller_memo",
    "item_info",
)
TECHNICAL_VALUE_PATTERNS = (
    re.compile(r"^[A-Za-z]:\\", re.I),
    re.compile(r"^/[\w./-]+$"),
    re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"),
    re.compile(r"^[A-Za-z0-9_-]{16,}$"),
)
QUANTITY_RAW_FIELD_KEYWORDS = (
    "quantity",
    "qty",
    "count",
)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return "".join(
        ch
        for ch in str(value)
        if (ch >= " " or ch in "\n\t") and unicodedata.category(ch) != "Cf"
    ).strip()


def raw_field_name(path: str) -> str:
    cleaned = re.sub(r"\[\d+\]", "", path)
    return cleaned.rsplit(".", 1)[-1].strip().lower()


def raw_field_path_text(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")


def is_obvious_technical_value(text: str) -> bool:
    cleaned = normalize_text(text)
    if not cleaned:
        return True
    if cleaned.lower() in {"true", "false"}:
        return True
    if any(pattern.search(cleaned) for pattern in TECHNICAL_VALUE_PATTERNS):
        return True
    return bool(re.fullmatch(r"\d{1,8}", cleaned))


def is_obvious_technical_business_value(path_text: str, text: str) -> bool:
    cleaned = normalize_text(text)
    if any(pattern.search(cleaned) for pattern in TECHNICAL_VALUE_PATTERNS):
        return True
    if any(keyword in path_text for keyword in QUANTITY_RAW_FIELD_KEYWORDS):
        return False
    return bool(re.fullmatch(r"\d{1,8}", cleaned))


def text_looks_business_relevant(text: str) -> bool:
    cleaned = normalize_text(text)
    if not cleaned or is_obvious_technical_value(cleaned):
        return False
    if re.search(r"[\u4e00-\u9fff]", cleaned):
        return len(cleaned) >= 2
    if re.search(r"[:：]", cleaned) and len(cleaned) >= 4:
        return True
    return bool(re.search(r"\s", cleaned) and len(cleaned) >= 8)


def raw_field_filter_reason(path: str, text: str) -> str | None:
    name = raw_field_name(path)
    path_text = raw_field_path_text(path)
    if name in TECHNICAL_RAW_FIELD_NAMES:
        return "technical_field_name"
    if any(keyword in path_text for keyword in TECHNICAL_RAW_FIELD_KEYWORDS):
        return "technical_field_path"
    if any(keyword in path_text for keyword in BUSINESS_RAW_FIELD_KEYWORDS):
        return "technical_value" if is_obvious_technical_business_value(path_text, text) else None
    if text_looks_business_relevant(text):
        return None
    return "not_business_relevant"


def should_expose_raw_field(path: str, text: str) -> bool:
    return raw_field_filter_reason(path, text) is None


def load_json_payload(raw_payload: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def task_documents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    task = payload.get("task")
    documents = task.get("documents") if isinstance(task, dict) else None
    return [item for item in documents if isinstance(item, dict)] if isinstance(documents, list) else []


def document_contents(document: dict[str, Any]) -> list[dict[str, Any]]:
    contents = document.get("contents")
    return [item for item in contents if isinstance(item, dict)] if isinstance(contents, list) else []


def walk_text_leaves(value: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_text_leaves(child, f"{path}.{key}" if path else str(key))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_text_leaves(child, f"{path}[{index}]")
        return

    text = normalize_text(value)
    if text:
        yield path, text


def strip_xml_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def printxml_text_entries(print_xml: Any) -> list[tuple[str, int, str]]:
    xml = normalize_text(print_xml)
    if not xml:
        return []
    entries: list[tuple[str, int, str]] = []
    cdata_index = 0
    text_index = 0
    text_node_matches = list(re.finditer(r"<text\b[^>]*>(.*?)</text>", xml, flags=re.DOTALL | re.IGNORECASE))
    for text_match in text_node_matches:
        inner = text_match.group(1)
        cdata_matches = re.findall(r"<!\[CDATA\[(.*?)\]\]>", inner, flags=re.DOTALL)
        if cdata_matches:
            for cdata_text in cdata_matches:
                text = normalize_text(cdata_text)
                if text:
                    entries.append(("cdata", cdata_index, text))
                    cdata_index += 1
            continue

        text = normalize_text(html.unescape(strip_xml_tags(inner)))
        if text:
            entries.append(("text", text_index, text))
            text_index += 1

    if entries:
        return entries

    for cdata_text in re.findall(r"<!\[CDATA\[(.*?)\]\]>", xml, flags=re.DOTALL):
        text = normalize_text(cdata_text)
        if text:
            entries.append(("cdata", cdata_index, text))
            cdata_index += 1
    return entries


def split_trailing_quantity_marker(value: str) -> list[str]:
    cleaned = normalize_text(value)
    if not cleaned:
        return []
    match = re.fullmatch(r"(.+?)\s*([*xX×]\s*\d+(?:\.\d+)?)", cleaned)
    if not match:
        return [cleaned]

    head = normalize_text(match.group(1))
    marker = re.sub(r"\s+", "", normalize_text(match.group(2)))
    return [part for part in (head, marker) if part]


LABELED_ATTRIBUTE_PREFIX_PATTERN = re.compile(
    r"^\s*(?:颜色分类|颜色|色号|鞋码|尺码|码数|号码|规格|款式)\s*[:：]"
)
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.I)
TAIL_SIZE_PATTERN = re.compile(r"^([1-9]\d(?:\.\d)?|[SMLX]{1,4}|均码|加大码|大码|小码)$", re.I)


def split_tail_size_candidate(value: str) -> list[str]:
    cleaned = normalize_text(value)
    if not cleaned or re.search(r"[,，、|/；;]", cleaned):
        return [cleaned] if cleaned else []
    tokens = cleaned.split()
    if len(tokens) < 2:
        return [cleaned]

    tail = tokens[-1].strip()
    if not TAIL_SIZE_PATTERN.fullmatch(tail):
        return [cleaned]

    head = normalize_text(" ".join(tokens[:-1]))
    if not head or len(head) < 2:
        return [cleaned]
    return [head, tail]


def split_selectable_segments(line: str) -> list[str]:
    cleaned = normalize_text(line)
    if not cleaned:
        return []
    normalized = re.sub(r"[；;]\s*", "\n", cleaned)
    base_parts: list[str] = []
    for line_part in re.split(r"\n+", normalized):
        line_part = normalize_text(line_part)
        if not line_part:
            continue
        # A slash inside a labelled platform field is part of that field value,
        # for example "颜色分类:黑白/TheRoger Advantage".
        delimiter_pattern = r"[,，、|]+" if LABELED_ATTRIBUTE_PREFIX_PATTERN.match(line_part) else r"[,，、|/]+"
        base_parts.extend(
            normalize_text(part)
            for part in re.split(delimiter_pattern, line_part)
            if normalize_text(part)
        )
    parts: list[str] = []
    for part in base_parts or [cleaned]:
        quantity_parts = split_trailing_quantity_marker(part)
        if len(quantity_parts) > 1:
            parts.extend(quantity_parts)
            continue
        parts.extend(split_tail_size_candidate(part))
    return parts


def is_quantity_segment(segment: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:[*xX×]\d+(?:\.\d+)?|\d+(?:\.\d+)?(?:件|双|个|份|套))",
            segment,
        )
    )


def split_reason_for_segments(line: str, segments: list[str]) -> str:
    cleaned = normalize_text(line)
    if len(segments) <= 1 and (segments[0] if segments else "") == cleaned:
        return ""
    if any(is_quantity_segment(segment) for segment in segments):
        return "safe_delimiter_and_trailing_marker"
    if len(segments) > 1 and TAIL_SIZE_PATTERN.fullmatch(segments[-1]):
        return "tail_size_candidate"
    return "safe_delimiter"


def warnings_for_line(line: str) -> list[str]:
    warnings: list[str] = []
    if URL_PATTERN.search(line):
        warnings.append("contains_url_candidate_noise")
    if "官方转运" in line or "转运" in line:
        warnings.append("contains_forwarding_note_candidate_noise")
    return warnings


def collect_raw_field_source_texts(
    value: Any,
    path_prefix: str,
    *,
    document_sequence: int | None = None,
    document_id: str | None = None,
) -> tuple[list[SourceText], list[HiddenRawField]]:
    source_texts: list[SourceText] = []
    hidden_fields: list[HiddenRawField] = []
    for path, text in walk_text_leaves(value, path_prefix):
        filter_reason = raw_field_filter_reason(path, text)
        if filter_reason is not None:
            hidden_fields.append(
                HiddenRawField(
                    text=text,
                    source_path=path,
                    filter_reason=filter_reason,
                    document_sequence=document_sequence,
                    document_id=document_id,
                )
            )
            continue
        source_texts.append(
            SourceText(
                text=text,
                source="raw_field",
                source_path=path,
                document_sequence=document_sequence,
                document_id=document_id,
            )
        )
    return source_texts, hidden_fields


def source_texts_from_record(record: RawCaptureRecord) -> tuple[list[SourceText], list[HiddenRawField]]:
    if isinstance(record.source_columns, dict):
        return collect_raw_field_source_texts(
            record.source_columns,
            "raw_capture_record.source_columns",
            document_id=record.document_id,
        )
    return [], []


def source_texts_from_document(document: dict[str, Any], document_sequence: int) -> tuple[list[SourceText], list[HiddenRawField]]:
    document_id = normalize_text(document.get("documentID")) or None
    source_texts: list[SourceText] = []
    hidden_fields: list[HiddenRawField] = []
    for content_index, content in enumerate(document_contents(document)):
        data = content.get("data")
        if isinstance(data, dict):
            raw_source_texts, raw_hidden_fields = collect_raw_field_source_texts(
                data,
                f"task.documents[{document_sequence - 1}].contents[{content_index}].data",
                document_sequence=document_sequence,
                document_id=document_id,
            )
            source_texts.extend(raw_source_texts)
            hidden_fields.extend(raw_hidden_fields)

        for entry_kind, entry_index, text in printxml_text_entries(content.get("printXML")):
            source_texts.append(
                SourceText(
                    text=text,
                    source="printed_text",
                    source_path=(
                        f"task.documents[{document_sequence - 1}].contents[{content_index}]"
                        f".printXML.{entry_kind}[{entry_index}]"
                    ),
                    document_sequence=document_sequence,
                    document_id=document_id,
                )
            )
    return source_texts, hidden_fields


def fallback_source_texts(payload: dict[str, Any] | None, raw_payload: str) -> tuple[list[SourceText], list[HiddenRawField]]:
    if payload is not None:
        return collect_raw_field_source_texts(payload, "")

    raw_text = normalize_text(raw_payload)
    return ([SourceText(text=raw_text, source="raw_payload", source_path="raw_payload")], []) if raw_text else ([], [])


def base_trace(
    record: RawCaptureRecord,
    *,
    sample_id: str,
    source_text: SourceText,
    source_text_order: int,
    source_line_index: int,
    line_index: int,
    segment_index: int | str,
    order: int,
) -> dict[str, Any]:
    return {
        "selector_key": trace_selector_key(
            source=source_text.source,
            source_path=source_text.source_path,
            source_text_order=source_text_order,
            source_line_index=source_line_index,
            segment_index=segment_index,
        ),
        "sample_id": sample_id,
        "raw_record_id": record.id,
        "task_id": record.task_id,
        "document_id": source_text.document_id,
        "document_sequence": source_text.document_sequence,
        "source_component": record.source_component,
        "source_index": record.source_index,
        "source": source_text.source,
        "source_path": source_text.source_path,
        "source_text_order": source_text_order,
        "source_line_index": source_line_index,
        "line_index": line_index,
        "segment_index": segment_index,
        "order": order,
    }


def append_text_block(
    blocks: list[dict[str, Any]],
    *,
    record: RawCaptureRecord,
    sample_id: str,
    text: str,
    source_text: SourceText,
    source_text_order: int,
    source_line_index: int,
    line_index: int,
    segment_index: int | str,
    block_kind: str,
    parent_block_id: str | None = None,
    parent_text: str | None = None,
    split_reason: str | None = None,
) -> dict[str, Any]:
    order = len(blocks)
    trace = base_trace(
        record,
        sample_id=sample_id,
        source_text=source_text,
        source_text_order=source_text_order,
        source_line_index=source_line_index,
        line_index=line_index,
        segment_index=segment_index,
        order=order,
    )
    if parent_block_id:
        trace["parent_block_id"] = parent_block_id
        trace["parent_text"] = parent_text
        trace["split_reason"] = split_reason
    block = {
        "block_id": f"{sample_id}-block-{order + 1}",
        "text": text,
        "source": source_text.source,
        "block_kind": block_kind,
        "line_index": line_index,
        "order": order,
        "raw_record_id": record.id,
        "trace": trace,
        "source_path": source_text.source_path,
        "document_id": source_text.document_id,
        "document_sequence": source_text.document_sequence,
        "parent_block_id": parent_block_id,
        "parent_text": parent_text,
        "split_reason": split_reason,
    }
    blocks.append(block)
    return block


def text_blocks_for_source_texts(
    record: RawCaptureRecord,
    source_texts: list[SourceText],
    *,
    sample_index: int,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    blocks: list[dict[str, Any]] = []
    sample_lines: list[str] = []
    warnings: list[str] = []
    line_index = 0

    sample_id = f"raw-{record.id}-sample-{sample_index}"
    for source_text_order, source_text in enumerate(source_texts):
        for source_line_index, raw_line in enumerate(normalize_text(source_text.text).splitlines() or [source_text.text]):
            line = normalize_text(raw_line)
            if not line:
                continue
            warnings.extend(warning for warning in warnings_for_line(line) if warning not in warnings)
            sample_lines.append(line)
            parent_block = append_text_block(
                blocks,
                record=record,
                sample_id=sample_id,
                text=line,
                source_text=source_text,
                source_text_order=source_text_order,
                source_line_index=source_line_index,
                line_index=line_index,
                segment_index="original",
                block_kind="original",
            )
            child_segments = split_selectable_segments(line)
            split_reason = split_reason_for_segments(line, child_segments)
            if split_reason:
                for segment_index, segment in enumerate(child_segments):
                    append_text_block(
                        blocks,
                        record=record,
                        sample_id=sample_id,
                        text=segment,
                        source_text=source_text,
                        source_text_order=source_text_order,
                        source_line_index=source_line_index,
                        line_index=line_index,
                        segment_index=segment_index,
                        block_kind="derived_child",
                        parent_block_id=parent_block["block_id"],
                        parent_text=line,
                        split_reason=split_reason,
                    )
            line_index += 1

    return "\n".join(sample_lines), blocks, warnings


def waybill_sample_from_source_texts(
    record: RawCaptureRecord,
    source_texts: list[SourceText],
    *,
    sample_index: int,
    hidden_raw_fields: list[HiddenRawField] | None = None,
    document_sequence: int | None = None,
    document_id: str | None = None,
) -> dict[str, Any]:
    sample_text, text_blocks, warnings = text_blocks_for_source_texts(
        record,
        source_texts,
        sample_index=sample_index,
    )
    return {
        "sample_id": f"raw-{record.id}-sample-{sample_index}",
        "raw_record_id": record.id,
        "task_id": record.task_id,
        "document_id": document_id or record.document_id,
        "document_sequence": document_sequence,
        "source_component": record.source_component,
        "source_index": record.source_index,
        "payload_format": record.payload_format,
        "parse_status": "readable",
        "warnings": warnings,
        "sample_text": sample_text,
        "text_blocks": text_blocks,
        "hidden_raw_fields": [
            hidden_field.as_dict()
            for hidden_field in (hidden_raw_fields or [])
        ],
    }


def read_waybill_samples(record: RawCaptureRecord) -> list[dict[str, Any]]:
    payload = load_json_payload(record.raw_payload)
    record_source_texts, record_hidden_fields = source_texts_from_record(record)
    samples: list[dict[str, Any]] = []

    if payload is not None:
        for document_sequence, document in enumerate(task_documents(payload), start=1):
            document_source_texts, document_hidden_fields = source_texts_from_document(document, document_sequence)
            source_texts = [*record_source_texts, *document_source_texts]
            if not source_texts:
                continue
            samples.append(
                waybill_sample_from_source_texts(
                    record,
                    source_texts,
                    sample_index=len(samples) + 1,
                    hidden_raw_fields=[*record_hidden_fields, *document_hidden_fields],
                    document_sequence=document_sequence,
                    document_id=normalize_text(document.get("documentID")) or record.document_id,
                )
            )

    if samples:
        return samples

    fallback_sources, fallback_hidden_fields = fallback_source_texts(payload, record.raw_payload)
    source_texts = record_source_texts or fallback_sources
    hidden_raw_fields = record_hidden_fields if record_source_texts else fallback_hidden_fields
    return [
        waybill_sample_from_source_texts(
            record,
            source_texts,
            sample_index=1,
            hidden_raw_fields=hidden_raw_fields,
            document_id=record.document_id,
        )
    ] if source_texts else []


def empty_waybill_reading_diagnostic(record: RawCaptureRecord) -> dict[str, Any]:
    payload = load_json_payload(record.raw_payload)
    raw_text = normalize_text(record.raw_payload)
    if not raw_text:
        empty_reason = "empty_raw_payload"
    elif payload is None:
        empty_reason = "raw_payload_has_no_readable_text"
    elif task_documents(payload):
        empty_reason = "task_documents_have_no_readable_text"
    else:
        empty_reason = "payload_has_no_task_documents_or_business_raw_fields"
    return {
        "raw_record_id": record.id,
        "task_id": record.task_id,
        "document_id": record.document_id,
        "source_component": record.source_component,
        "source_index": record.source_index,
        "payload_format": record.payload_format,
        "parse_status": "empty",
        "empty_reason": empty_reason,
        "warnings": [empty_reason],
    }
