from collections import Counter, defaultdict
import re
from typing import Any


PARENT_SEQUENCE = re.compile(r"(?:第1批-第|面单\s*)(\d+)(?:单)?")
BUSINESS_FIELDS = (
    "product_text",
    "sales_attr1_text",
    "sales_attr2_text",
    "quantity_text",
    "remark_text",
)


def _int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parent_key(values: dict[str, Any]) -> tuple[int, int] | None:
    raw_record_id = _int_value(values.get("raw_record_id"))
    parent_sequence = _int_value(values.get("parent_sequence"))
    if parent_sequence is None:
        match = PARENT_SEQUENCE.search(str(values.get("source_label") or "").strip())
        parent_sequence = int(match.group(1)) if match else None
    if raw_record_id is None or parent_sequence is None:
        return None
    return raw_record_id, parent_sequence


def _actionable_reason(row: dict[str, Any]) -> str:
    return str(
        row.get("reason")
        or row.get("exception_reason")
        or row.get("review_reason")
        or ""
    ).strip()


def analyze_waybill_coverage(
    *,
    expected_parent_documents: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    normal_export_count: int,
    exception_export_count: int,
) -> dict[str, Any]:
    expected_parent_keys: set[tuple[int, int]] = set()
    expected_source_missing_count = 0
    expected_identity_collision_count = 0
    for document in expected_parent_documents:
        key = _parent_key(document)
        if key is None:
            expected_source_missing_count += 1
        elif key in expected_parent_keys:
            expected_identity_collision_count += 1
        else:
            expected_parent_keys.add(key)
    expected_raw_ids = {raw_record_id for raw_record_id, _ in expected_parent_keys}
    parent_rows: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    seen_raw_ids: set[int] = set()
    missing_source_count = 0

    for row in rows:
        key = _parent_key(row)
        if key is None:
            missing_source_count += 1
            continue
        raw_record_id, _ = key
        seen_raw_ids.add(raw_record_id)
        parent_rows[key].append(row)

    expected_parent_count = len(expected_parent_documents)
    recognized_parent_count = len(parent_rows)
    failures: list[dict[str, Any]] = []
    if expected_source_missing_count:
        failures.append(
            {
                "code": "expected_parent_trace_missing",
                "count": expected_source_missing_count,
            }
        )
    if expected_identity_collision_count:
        failures.append(
            {
                "code": "expected_parent_identity_collision",
                "count": expected_identity_collision_count,
            }
        )
    if expected_parent_count != recognized_parent_count:
        failures.append(
            {
                "code": "parent_count_mismatch",
                "count": abs(expected_parent_count - recognized_parent_count),
            }
        )
    uncovered_raw_count = len(expected_raw_ids - seen_raw_ids)
    if uncovered_raw_count:
        failures.append({"code": "raw_record_uncovered", "count": uncovered_raw_count})
    if missing_source_count:
        failures.append({"code": "source_trace_missing", "count": missing_source_count})
    export_gap = abs(len(rows) - normal_export_count - exception_export_count)
    if export_gap:
        failures.append({"code": "export_coverage_mismatch", "count": export_gap})

    duplicate_child_count = 0
    high_child_count = 0
    normal_parent_keys: set[tuple[int, int]] = set()
    exception_parent_keys: set[tuple[int, int]] = set()
    mixed_parent_count = 0
    exception_not_actionable_count = 0
    for key, grouped_rows in parent_rows.items():
        normal_rows = [row for row in grouped_rows if row.get("status") == "matched"]
        exception_rows = [row for row in grouped_rows if row.get("status") != "matched"]
        if exception_rows:
            mixed_parent_count += bool(normal_rows)
            if all(_actionable_reason(row) for row in exception_rows):
                exception_parent_keys.add(key)
            else:
                exception_not_actionable_count += 1
        else:
            normal_parent_keys.add(key)

        fingerprints = Counter(
            tuple(str(row.get(field) or "").strip() for field in BUSINESS_FIELDS)
            for row in grouped_rows
        )
        duplicate_child_count += sum(count - 1 for count in fingerprints.values() if count > 1)
        high_child_count += len(grouped_rows) > 10

    if exception_not_actionable_count:
        failures.append(
            {
                "code": "exception_not_actionable",
                "count": exception_not_actionable_count,
            }
        )
    covered_parent_keys = normal_parent_keys | exception_parent_keys
    overlap = normal_parent_keys & exception_parent_keys
    if overlap:
        failures.append({"code": "parent_partition_overlap", "count": len(overlap)})
    partition_gap = expected_parent_keys ^ covered_parent_keys
    if partition_gap or overlap:
        failures.append(
            {
                "code": "parent_partition_incomplete",
                "count": len(partition_gap) + len(overlap),
            }
        )

    warnings: list[dict[str, Any]] = []
    if duplicate_child_count:
        warnings.append({"code": "duplicate_child_rows", "count": duplicate_child_count})
    if high_child_count:
        warnings.append({"code": "high_child_count", "count": high_child_count})
    if mixed_parent_count:
        warnings.append({"code": "mixed_parent_outcomes", "count": mixed_parent_count})

    return {
        "ok": not failures,
        "expected_parent_count": expected_parent_count,
        "recognized_parent_count": recognized_parent_count,
        "covered_parent_count": len(covered_parent_keys),
        "normal_parent_count": len(normal_parent_keys),
        "exception_parent_count": len(exception_parent_keys),
        "parent_partition_disjoint": not overlap,
        "parent_partition_complete": (
            not partition_gap
            and not overlap
            and not expected_source_missing_count
            and not expected_identity_collision_count
        ),
        "result_row_count": len(rows),
        "normal_export_count": normal_export_count,
        "exception_export_count": exception_export_count,
        "failures": failures,
        "warnings": warnings,
    }
