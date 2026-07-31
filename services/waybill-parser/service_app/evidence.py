from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import re
from typing import Any, Iterator
import unicodedata

from service_app.order_row_engine import print_xml_text_blocks
from services.shared.waybill_fingerprint import (
    business_shape_fingerprint,
    fingerprint_catalog,
    grammar_signature_for_texts,
    inspect_fingerprint,
)


_DELIMITER_RE = re.compile(r"[,，;；|/、]")
_DELIMITER_SEGMENT_RE = re.compile(r"[^,，;；|/、]+")
_FULL_POSITIVE_INT_RE = re.compile(r"[1-9]\d*")
_POSITIVE_DIGITS = r"[1-9１-９][0-9０-９]*"
_QUANTITY_UNITS = r"件|双|雙|个|個|条|條|套|份|只|支|瓶|包|组|組"
_QUANTITY_CAPTURE_RE = re.compile(
    rf"(?:[*xX×＊]\s*(?P<multiplier>{_POSITIVE_DIGITS})|"
    rf"(?P<unit>{_POSITIVE_DIGITS})(?=\s*(?:{_QUANTITY_UNITS})))"
)
_NUMBER_CAPTURE_RE = re.compile(
    r"(?<![0-9０-９])([0-9０-９]{2}(?:[.．][0-9０-９])?)(?![0-9０-９])"
)
_WRAPPER_ARRAY_KEYS = {"documents", "contents"}
FIELD_ROLE_TOKEN_CLASSES = {"shoe_size_like_numeric_segment"}


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


def _span_id(source_path: str, start: int, end: int, token_class: str) -> str:
    digest = sha256(
        f"{source_path}\0{start}\0{end}\0{token_class}".encode("utf-8")
    ).hexdigest()[:20]
    return f"span-{digest}"


def _trimmed_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _quantity_ranges(text: str) -> list[tuple[int, int]]:
    ranges = [
        match.span("multiplier") if match.group("multiplier") else match.span("unit")
        for match in _QUANTITY_CAPTURE_RE.finditer(text)
    ]
    start, end = _trimmed_bounds(text, 0, len(text))
    if _FULL_POSITIVE_INT_RE.fullmatch(_normalize_text(text[start:end])):
        ranges.append((start, end))
    return list(dict.fromkeys(ranges))


def _shoe_size_ranges(text: str) -> list[tuple[int, int]]:
    return [
        match.span(1)
        for match in _NUMBER_CAPTURE_RE.finditer(text)
        if 25 <= float(unicodedata.normalize("NFKC", match.group(1))) <= 59
    ]


def value_matches_token_class(value: Any, token_class: str) -> bool:
    text = str(value).strip()
    if token_class == "shoe_size_like_numeric_segment":
        return (0, len(text)) in _shoe_size_ranges(text)
    return False


def _quantity_syntax_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for start, end in _quantity_ranges(text):
        syntax_start = start
        while syntax_start and text[syntax_start - 1].isspace():
            syntax_start -= 1
        if syntax_start and text[syntax_start - 1] in "*xX×＊":
            syntax_start -= 1
        syntax_end = end
        while syntax_end < len(text) and text[syntax_end].isspace():
            syntax_end += 1
        unit = re.match(rf"(?:{_QUANTITY_UNITS})", text[syntax_end:])
        if unit:
            syntax_end += unit.end()
        ranges.append((syntax_start, syntax_end))
    return ranges


def _delimiter_segment_ranges(text: str) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for segment in _DELIMITER_SEGMENT_RE.finditer(text):
        protected = sorted(
            [
                *(
                    (segment.start() + start, segment.start() + end)
                    for start, end in _quantity_syntax_ranges(segment.group())
                ),
                *(
                    (segment.start() + start, segment.start() + end)
                    for start, end in _shoe_size_ranges(segment.group())
                ),
            ]
        )
        if not protected:
            bounds = _trimmed_bounds(text, *segment.span())
            if bounds[0] < bounds[1]:
                result.append(bounds)
            continue
        cursor = segment.start()
        for start, end in protected:
            bounds = _trimmed_bounds(text, cursor, start)
            if bounds[0] < bounds[1]:
                result.append(bounds)
            cursor = max(cursor, end)
        bounds = _trimmed_bounds(text, cursor, segment.end())
        if bounds[0] < bounds[1]:
            result.append(bounds)
    return result


def _span_record(
    source_path: str,
    text: str,
    start: int,
    end: int,
    token_class: str,
    array_items: tuple[tuple[str, int], ...],
) -> _SpanRecord | None:
    normalized = _normalize_text(text[start:end])
    if not normalized:
        return None
    return _SpanRecord(
        SourceSpan(
            span_id=_span_id(source_path, start, end, token_class),
            source_path=source_path,
            original_text=text[start:end],
            normalized_text=normalized,
            start=start,
            end=end,
            token_class=token_class,
        ),
        array_items,
    )


def _text_records(
    source_path: str,
    text: str,
    array_items: tuple[tuple[str, int], ...],
) -> list[_SpanRecord]:
    records: list[_SpanRecord] = []
    for line in re.finditer(r"[^\r\n]+", text):
        line_record = _span_record(
            source_path,
            text,
            line.start(),
            line.end(),
            "text",
            array_items,
        )
        if line_record is None:
            continue
        records.append(line_record)
        line_text = line.group()
        candidates: list[tuple[int, int, str]] = []
        if _DELIMITER_RE.search(line_text):
            candidates.extend(
                (*bounds, "delimiter_segment")
                for bounds in _delimiter_segment_ranges(line_text)
            )
        candidates.extend(
            (*bounds, "positive_integer_quantity")
            for bounds in _quantity_ranges(line_text)
        )
        candidates.extend(
            (*bounds, "shoe_size_like_numeric_segment")
            for bounds in _shoe_size_ranges(line_text)
        )
        for start, end, token_class in candidates:
            record = _span_record(
                source_path,
                text,
                line.start() + start,
                line.start() + end,
                token_class,
                array_items,
            )
            if record is not None:
                records.append(record)
    return records


def _print_xml_records(leaf: _Leaf) -> tuple[list[_SpanRecord], int]:
    if not isinstance(leaf.value, str) or not leaf.value.strip():
        return [], 0
    texts = print_xml_text_blocks(leaf.value)
    if texts is None:
        return [], 1
    records = [
        record
        for index, (text, marked) in enumerate(texts)
        if marked
        for record in _text_records(
            f"{leaf.source_path}.text[{index}]",
            text,
            leaf.array_items,
        )
    ]
    return records, sum(not marked for _, marked in texts)


def _dedupe_groups(groups: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for group in groups:
        key = tuple(group)
        if key and key not in seen:
            seen.add(key)
            result.append(group)
    return result


def _array_key(item_path: str) -> str:
    return item_path.rsplit("[", 1)[0].rsplit(".", 1)[-1]


def _candidate_groups(
    records: list[_SpanRecord],
    business_array_keys: set[str],
) -> dict[str, list[list[str]]]:
    structured: dict[str, list[str]] = {}
    arrays: dict[str, list[str]] = {}
    repeated: dict[tuple[str, str], list[str]] = {}
    quantities: dict[str, list[str]] = {}
    for record in records:
        span = record.span
        if span.token_class == "positive_integer_quantity":
            quantities.setdefault(span.source_path, []).append(span.span_id)
        if span.token_class != "text":
            continue
        item_arrays = [
            (item_path, item_count)
            for item_path, item_count in record.array_items
            if _array_key(item_path) in business_array_keys
        ]
        if item_arrays:
            item_path, _ = item_arrays[-1]
            structured.setdefault(item_path, []).append(span.span_id)
        for item_path, item_count in item_arrays:
            if item_count > 1:
                arrays.setdefault(item_path.rsplit("[", 1)[0], []).append(span.span_id)
        repeated.setdefault(
            (span.source_path, span.normalized_text),
            [],
        ).append(span.span_id)

    return {
        "structured_list_item": _dedupe_groups(list(structured.values())),
        "line": [
            [record.span.span_id]
            for record in records
            if record.span.token_class == "text"
        ],
        "delimiter_separated_segment": [
            [record.span.span_id]
            for record in records
            if record.span.token_class == "delimiter_segment"
        ],
        "positive_integer_quantity": [
            [record.span.span_id]
            for record in records
            if record.span.token_class == "positive_integer_quantity"
        ],
        "shoe_size_like_numeric_segment": [
            [record.span.span_id]
            for record in records
            if record.span.token_class == "shoe_size_like_numeric_segment"
        ],
        "repeated_line_or_array_group": _dedupe_groups(
            [
                *arrays.values(),
                *(span_ids for span_ids in repeated.values() if len(span_ids) > 1),
                *(span_ids for span_ids in quantities.values() if len(span_ids) > 1),
            ]
        ),
    }


def _catalog_path_pattern(path: str, detect_path: str) -> re.Pattern[str]:
    source_path = path.split("//", 1)[0]
    if not source_path.startswith("contents[]"):
        source_root = source_path.split(".", 1)[0]
        root_index = detect_path.find(source_root)
        if root_index >= 0:
            source_path = f"{detect_path[:root_index]}{source_path}"
    pattern = re.escape(source_path).replace(r"\[\]", r"\[\d+\]")
    task_prefix = (
        r"(?:task\.documents\[\d+\]\.)?"
        if source_path.startswith("contents[]")
        else ""
    )
    return re.compile(rf"^{task_prefix}{pattern}$")


def _business_array_keys(fields: list[dict[str, Any]], selected: set[str]) -> set[str]:
    return {
        array_key
        for field in fields
        if field["key"] in selected
        for array_key in re.findall(r"([^.]+)\[\]", field["path"])
        if array_key not in _WRAPPER_ARRAY_KEYS
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
    catalogue_entry = next(
        (
            entry
            for entry in fingerprint_catalog()
            if inspection and entry["code"] == inspection["fingerprint_code"]
        ),
        {"detect_path": ""},
    )
    field_sources = [
        (
            field["key"],
            _catalog_path_pattern(field["path"], catalogue_entry["detect_path"]),
        )
        for field in fields
    ]

    excluded = {"non_business": 0, "unselected_business": 0}
    records: list[_SpanRecord] = []
    for leaf in _walk_leaves(payload):
        matched_fields = {
            field_key
            for field_key, pattern in field_sources
            if pattern.search(leaf.source_path)
        }
        if matched_fields & selected:
            if leaf.source_key == "printXML":
                print_records, excluded_text_count = _print_xml_records(leaf)
                records.extend(print_records)
                excluded["non_business"] += excluded_text_count
            elif leaf.value is not None and not isinstance(leaf.value, (dict, list)):
                records.extend(
                    _text_records(
                        leaf.source_path,
                        str(leaf.value),
                        leaf.array_items,
                    )
                )
        elif matched_fields:
            excluded["unselected_business"] += 1
        else:
            excluded["non_business"] += 1

    return {
        "contract_version": "waybill_evidence_v1",
        "source_component": source_component,
        "fingerprint_code": inspection["fingerprint_code"] if inspection else "UNKNOWN",
        "structural_fingerprint": business_shape_fingerprint(payload, source_component),
        "grammar_signature": grammar_signature_for_texts(
            record.span.original_text
            for record in records
            if record.span.token_class == "text"
        ),
        "spans": [asdict(record.span) for record in records],
        "candidate_groups": _candidate_groups(
            records,
            _business_array_keys(fields, selected),
        ),
        "excluded_field_counts": excluded,
    }
