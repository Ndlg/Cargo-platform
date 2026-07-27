from collections import Counter, defaultdict
import re
from typing import Any


CHILD_LABEL_SUFFIX = re.compile(r"-子\d+$")
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


def analyze_waybill_coverage(
    *,
    expected_parent_counts: dict[int, int],
    rows: list[dict[str, Any]],
    normal_export_count: int,
    exception_export_count: int,
) -> dict[str, Any]:
    expected_raw_ids = set(expected_parent_counts)
    parent_rows: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    seen_raw_ids: set[int] = set()
    missing_source_count = 0

    for row in rows:
        raw_record_id = _int_value(row.get("raw_record_id"))
        source_label = str(row.get("source_label") or "").strip()
        if raw_record_id not in expected_raw_ids or not source_label:
            missing_source_count += 1
            continue
        seen_raw_ids.add(raw_record_id)
        parent_rows[(raw_record_id, CHILD_LABEL_SUFFIX.sub("", source_label))].append(row)

    expected_parent_count = sum(expected_parent_counts.values())
    recognized_parent_count = len(parent_rows)
    failures: list[dict[str, Any]] = []
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
    for grouped_rows in parent_rows.values():
        fingerprints = Counter(
            tuple(str(row.get(field) or "").strip() for field in BUSINESS_FIELDS)
            for row in grouped_rows
        )
        duplicate_child_count += sum(count - 1 for count in fingerprints.values() if count > 1)
        high_child_count += len(grouped_rows) > 10

    warnings: list[dict[str, Any]] = []
    if duplicate_child_count:
        warnings.append({"code": "duplicate_child_rows", "count": duplicate_child_count})
    if high_child_count:
        warnings.append({"code": "high_child_count", "count": high_child_count})

    return {
        "ok": not failures,
        "expected_parent_count": expected_parent_count,
        "recognized_parent_count": recognized_parent_count,
        "result_row_count": len(rows),
        "normal_export_count": normal_export_count,
        "exception_export_count": exception_export_count,
        "failures": failures,
        "warnings": warnings,
    }
