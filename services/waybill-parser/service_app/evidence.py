from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import re
from typing import Any, Iterator
import unicodedata
from xml.etree import ElementTree

from services.shared.waybill_fingerprint import (
    business_shape_fingerprint,
    grammar_signature_for_texts,
    inspect_fingerprint,
)


_DELIMITER_RE = re.compile(r"[,;|/、]")
_FULL_POSITIVE_INT_RE = re.compile(r"[1-9]\d*")
_QUANTITY_RE = re.compile(
    r"(?:[*xX×]\s*[1-9]\d*|[1-9]\d*\s*(?:件|双|雙|个|個|条|條|套|份|只|支|瓶|包|组|組))"
)
_SHOE_SIZE_RE = re.compile(r"(?<!\d)(?:2[5-9]|[3-5]\d)(?:\.\d)?(?!\d)")
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


@dataclass(frozen=True)
class SourceSpan:
    span_id: str
    source_path: str
    original_text: str
    normalized_text: str
    start: int
    end: int
    token_class: str


@dataclass(frozen=True)
class _Leaf:
    source_path: str
    source_key: str
    value: Any
    array_items: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _SpanRecord:
    span: SourceSpan
    array_items: tuple[tuple[str, int], ...]


def _walk_leaves(
    value: Any,
    path: str = "",
    array_items: tuple[tuple[str, int], ...] = (),
) -> Iterator[_Leaf]:
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if isinstance(item, (dict, list)):
                yield from _walk_leaves(item, child_path, array_items)
            else:
                yield _Leaf(child_path, str(key), item, array_items)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]"
            yield from _walk_leaves(
                item,
                item_path,
                (*array_items, (item_path, len(value))),
            )


def _normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _span_id(source_path: str, start: int, end: int) -> str:
    digest = sha256(f"{source_path}\0{start}\0{end}".encode("utf-8")).hexdigest()[:20]
    return f"span-{digest}"


def _is_quantity(text: str) -> bool:
    return bool(_FULL_POSITIVE_INT_RE.fullmatch(text) or _QUANTITY_RE.search(text))


def _token_class(text: str) -> str:
    if _DELIMITER_RE.search(text):
        return "delimited_text"
    if _is_quantity(text):
        return "positive_integer"
    if _SHOE_SIZE_RE.fullmatch(text):
        return "shoe_size_like_numeric"
    return "text"


def _text_records(
    source_path: str,
    text: str,
    array_items: tuple[tuple[str, int], ...],
) -> list[_SpanRecord]:
    records: list[_SpanRecord] = []
    for match in re.finditer(r"[^\r\n]+", text):
        normalized = _normalize_text(match.group())
        if not normalized:
            continue
        span = SourceSpan(
            span_id=_span_id(source_path, match.start(), match.end()),
            source_path=source_path,
            original_text=match.group(),
            normalized_text=normalized,
            start=match.start(),
            end=match.end(),
            token_class=_token_class(normalized),
        )
        records.append(_SpanRecord(span, array_items))
    return records


def _print_xml_records(leaf: _Leaf) -> list[_SpanRecord]:
    if not isinstance(leaf.value, str) or not leaf.value.strip():
        return []
    try:
        root = ElementTree.fromstring(leaf.value)
        texts = [
            "".join(element.itertext())
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "text"
        ]
        suffix = "text"
    except ElementTree.ParseError:
        texts = _CDATA_RE.findall(leaf.value)
        suffix = "cdata"
    return [
        record
        for index, text in enumerate(texts)
        for record in _text_records(
            f"{leaf.source_path}.{suffix}[{index}]",
            text,
            leaf.array_items,
        )
    ]


def _dedupe_groups(groups: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for group in groups:
        key = tuple(group)
        if key and key not in seen:
            seen.add(key)
            result.append(group)
    return result


def _candidate_groups(records: list[_SpanRecord]) -> dict[str, list[list[str]]]:
    structured: dict[str, list[str]] = {}
    arrays: dict[str, list[str]] = {}
    repeated: dict[str, list[str]] = {}
    for record in records:
        span = record.span
        if record.array_items:
            structured.setdefault(record.array_items[-1][0], []).append(span.span_id)
        for item_path, item_count in record.array_items:
            if item_count > 1:
                arrays.setdefault(item_path.rsplit("[", 1)[0], []).append(span.span_id)
        repeated.setdefault(span.normalized_text, []).append(span.span_id)

    return {
        "structured_list_item": _dedupe_groups(list(structured.values())),
        "line": [[record.span.span_id] for record in records],
        "delimiter_separated_segment": [
            [record.span.span_id]
            for record in records
            if _DELIMITER_RE.search(record.span.normalized_text)
        ],
        "positive_integer_quantity": [
            [record.span.span_id]
            for record in records
            if _is_quantity(record.span.normalized_text)
        ],
        "shoe_size_like_numeric_segment": [
            [record.span.span_id]
            for record in records
            if _SHOE_SIZE_RE.search(record.span.normalized_text)
        ],
        "repeated_line_or_array_group": _dedupe_groups(
            [
                *arrays.values(),
                *(span_ids for span_ids in repeated.values() if len(span_ids) > 1),
            ]
        ),
    }


def build_evidence(
    payload: dict[str, Any],
    source_component: str,
    selected_fields: list[str] | None = None,
) -> dict[str, Any]:
    inspection = inspect_fingerprint(payload, source_component)
    fields = inspection["fields"] if inspection else []
    selected = (
        {str(field) for field in selected_fields}
        if selected_fields is not None
        else {field["key"] for field in fields if field["default_selected"]}
    )
    field_source_keys = {
        field["key"]: field["path"].rsplit(".", 1)[-1].split("//", 1)[0].replace("[]", "")
        for field in fields
    }
    selected_source_keys = {
        source_key
        for field_key, source_key in field_source_keys.items()
        if field_key in selected
    }
    all_source_keys = set(field_source_keys.values())

    excluded = {"non_business": 0, "unselected_business": 0}
    records: list[_SpanRecord] = []
    for leaf in _walk_leaves(payload):
        if leaf.source_key in selected_source_keys:
            if leaf.source_key == "printXML":
                records.extend(_print_xml_records(leaf))
            elif leaf.value is not None and not isinstance(leaf.value, (dict, list)):
                records.extend(
                    _text_records(
                        leaf.source_path,
                        str(leaf.value),
                        leaf.array_items,
                    )
                )
        elif leaf.source_key in all_source_keys:
            excluded["unselected_business"] += 1
        else:
            excluded["non_business"] += 1

    return {
        "contract_version": "waybill_evidence_v1",
        "source_component": source_component,
        "fingerprint_code": inspection["fingerprint_code"] if inspection else "UNKNOWN",
        "structural_fingerprint": business_shape_fingerprint(payload, source_component),
        "grammar_signature": grammar_signature_for_texts(
            record.span.original_text for record in records
        ),
        "spans": [asdict(record.span) for record in records],
        "candidate_groups": _candidate_groups(records),
        "excluded_field_counts": excluded,
    }
