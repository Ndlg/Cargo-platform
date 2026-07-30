from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

from service_app.declarative_rules import (
    parse_with_format_profile,
    validate_format_profiles,
)
from service_app.evidence import build_evidence
from service_app.order_row_engine import values_at_structured_path


ALLOWED_OPERATIONS = {
    "extract_between",
    "rsplit",
    "split",
    "strip_prefix",
    "strip_suffix",
    "to_positive_int",
    "trim",
}
ROW_FIELDS = ("product", "sales_attr1", "sales_attr2", "quantity", "remark")
TEXT_FIELDS = ("product", "sales_attr1", "sales_attr2", "remark")
QUANTITY_SUFFIX_UNITS = {"件", "个", "双", "套", "份", "盒", "包", "组"}
FIELD_LABEL_PREFIX = re.compile(
    r"^\s*(?:商品|销售属性\s*1|销售属性\s*2|数量|备注)\s*(?:是|[:：=])"
)
ARRAY_INDEX = re.compile(r"\[\d+\]")


def _normalized_rows(rows: object) -> list[dict[str, Any]] | None:
    if not isinstance(rows, list) or not rows:
        return None
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        values: dict[str, Any] = {}
        for field in TEXT_FIELDS:
            value = row.get(field, "")
            if not isinstance(value, str):
                return None
            value = value.strip()
            if FIELD_LABEL_PREFIX.match(value):
                return None
            values[field] = value
        quantity = row.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            return None
        values["quantity"] = quantity
        if not values["product"]:
            return None
        normalized.append({field: values[field] for field in ROW_FIELDS})
    return normalized


def _relative_scalars(value: Any, path: tuple[str, ...] = ()) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            field_path: scalar
            for key, item in value.items()
            for field_path, scalar in _relative_scalars(item, (*path, str(key))).items()
        }
    if isinstance(value, list) or value is None or not path:
        return {}
    return {".".join(path): value}


def _business_item_paths(evidence: dict[str, Any]) -> list[str]:
    spans = {span["span_id"]: span for span in evidence["spans"]}
    paths: set[str] = set()
    for group in evidence["candidate_groups"]["structured_list_item"]:
        source_paths = [
            spans[span_id]["source_path"]
            for span_id in group
            if span_id in spans
        ]
        for source_path in source_paths[:1]:
            indexes = list(re.finditer(r"\[\d+\]", source_path))
            if not indexes:
                continue
            item_path = source_path[: indexes[-1].end()]
            paths.add(ARRAY_INDEX.sub("[]", item_path))
    return sorted(paths)


def _compile_structured_rule(
    payload: dict[str, Any],
    corrected_rows: list[dict[str, Any]],
    item_paths: list[str],
) -> dict[str, Any] | None:
    for items_path in item_paths:
        items = [
            item
            for item, _path in values_at_structured_path(payload, items_path)
            if isinstance(item, dict)
        ]
        if len(items) != len(corrected_rows):
            continue
        leaves = [_relative_scalars(item) for item in items]
        if not leaves:
            continue
        common_paths = set.intersection(*(set(item) for item in leaves))

        def exact_path(field: str) -> str | None:
            expected = [row[field] for row in corrected_rows]
            for path in sorted(common_paths):
                actual = [item[path] for item in leaves]
                if field == "quantity":
                    if all(
                        not isinstance(value, bool)
                        and isinstance(value, (int, float, str))
                        and str(value).strip().isdigit()
                        for value in actual
                    ) and [int(str(value).strip()) for value in actual] == expected:
                        return path
                elif [str(value).strip() for value in actual] == expected:
                    return path
            return None

        product_path = exact_path("product")
        quantity_path = exact_path("quantity")
        if not product_path or not quantity_path:
            continue

        fields = {"product": product_path, "quantity": quantity_path}
        defaults: dict[str, Any] = {}
        steps: list[dict[str, Any]] = []
        attr1_path = exact_path("sales_attr1")
        attr2_path = exact_path("sales_attr2")
        if attr1_path:
            fields["sales_attr1"] = attr1_path
        if attr2_path:
            fields["sales_attr2"] = attr2_path

        if not attr1_path and any(row["sales_attr1"] for row in corrected_rows):
            for path in sorted(common_paths):
                delimiters: list[str] = []
                for item, row in zip(leaves, corrected_rows, strict=True):
                    value = str(item[path]).strip()
                    attr1 = row["sales_attr1"]
                    attr2 = row["sales_attr2"]
                    if (
                        not attr1
                        or not attr2
                        or not value.startswith(attr1)
                        or not value.endswith(attr2)
                    ):
                        break
                    delimiter = value[len(attr1) : len(value) - len(attr2)]
                    if not delimiter or len(delimiter) > 64:
                        break
                    delimiters.append(delimiter)
                else:
                    if len(set(delimiters)) == 1:
                        fields["sales_attr1"] = path
                        fields["sales_attr2"] = path
                        steps.append(
                            {
                                "op": "rsplit",
                                "source": "sales_attr1",
                                "delimiter": delimiters[0],
                                "targets": ["sales_attr1", "sales_attr2"],
                            }
                        )
                        attr1_path = path
                        attr2_path = path
                        break

        for field, path in (
            ("sales_attr1", attr1_path),
            ("sales_attr2", attr2_path),
            ("remark", exact_path("remark")),
        ):
            if path:
                fields.setdefault(field, path)
            elif all(not row[field] for row in corrected_rows):
                defaults[field] = ""
            else:
                break
        else:
            rule: dict[str, Any] = {
                "strategy": "structured_items_v1",
                "items_path": items_path,
                "fields": fields,
            }
            if steps:
                rule["steps"] = steps
            if defaults:
                rule["defaults"] = defaults
            return rule
    return None


def _safe_text_sources(evidence: dict[str, Any]) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for span in evidence["spans"]:
        if span["token_class"] != "text":
            continue
        path = re.sub(r"\.text\[\d+\]$", "", span["source_path"])
        path = ARRAY_INDEX.sub("[]", path)
        item = (path, span["original_text"].strip())
        if item[1] and item not in seen:
            seen.add(item)
            sources.append(item)
    return sources


def _compile_source_order_text_rule(
    text_path: str,
    text: str,
    row: dict[str, Any],
) -> dict[str, Any] | None:
    segments: list[tuple[int, int, str, str]] = []
    for field in ("product", "sales_attr1", "sales_attr2", "quantity"):
        field_value = str(row[field]).strip()
        if not field_value:
            continue
        positions = []
        for match in re.finditer(re.escape(field_value), text):
            if field == "quantity" and (
                (match.start() and text[match.start() - 1].isdigit())
                or (match.end() < len(text) and text[match.end()].isdigit())
            ):
                continue
            positions.append((match.start(), match.end()))
        if len(positions) != 1:
            return None
        start, end = positions[0]
        segments.append((start, end, field, field_value))

    segments.sort()
    if len(segments) < 3 or any(
        left[1] > right[0] for left, right in zip(segments, segments[1:])
    ):
        return None

    prefix = text[: segments[0][0]]
    suffix = text[segments[-1][1] :]
    if (
        len(prefix) > 8
        or any(char.isalnum() for char in prefix)
        or len(suffix) > 8
        or (
            suffix.strip()
            and any(char.isalnum() for char in suffix.strip())
            and suffix.strip() not in QUANTITY_SUFFIX_UNITS
        )
    ):
        return None

    gaps = [
        text[left[1] : right[0]]
        for left, right in zip(segments, segments[1:])
    ]
    if any(
        not gap
        or len(gap) > 64
        or any(char.isalnum() for char in gap)
        or gap in left[3]
        for left, gap in zip(segments, gaps)
    ):
        return None

    steps: list[dict[str, Any]] = []
    if prefix:
        steps.append({"op": "strip_prefix", "target": "text", "literal": prefix})
    if suffix:
        steps.append({"op": "strip_suffix", "target": "text", "literal": suffix})
    for index, gap in enumerate(gaps):
        steps.append(
            {
                "op": "split",
                "source": "text",
                "delimiter": gap,
                "targets": [
                    segments[index][2],
                    segments[index + 1][2] if index == len(gaps) - 1 else "text",
                ],
            }
        )
    steps.append({"op": "to_positive_int", "target": "quantity"})
    return {
        "strategy": "text_pipeline_v1",
        "text_path": text_path,
        "steps": steps,
        "defaults": {
            field: ""
            for field in ("sales_attr1", "sales_attr2", "remark")
            if not str(row[field]).strip()
        },
    }


def _compile_text_rule(
    corrected_rows: list[dict[str, Any]],
    text_sources: list[tuple[str, str]],
) -> dict[str, Any] | None:
    # ponytail: one corrected text row is enough for delimiter programs;
    # repeated text needs a real failed sample before adding another primitive.
    if len(corrected_rows) != 1 or corrected_rows[0]["remark"]:
        return None
    row = corrected_rows[0]
    for text_path, text in text_sources:
        rule = _compile_source_order_text_rule(text_path, text, row)
        if rule:
            return rule
    return None


def _rule_operations(rule: dict[str, Any]) -> set[str]:
    return {
        str(step.get("op"))
        for step in rule.get("steps", [])
        if isinstance(step, dict)
    }


def replay_rule(
    rule: dict[str, Any] | None,
    payload: dict[str, Any],
    source_component: str = "cainiao-cnprint",
) -> list[dict[str, Any]]:
    if not isinstance(rule, dict):
        return []
    evidence = build_evidence(payload, source_component)
    if rule.get("fingerprint") != evidence["structural_fingerprint"]:
        return []
    parent = parse_with_format_profile(
        payload,
        rule,
        raw_record_id=1,
        task_id=None,
        source_component=source_component,
        source_index="replay",
        parent_sequence=1,
    )
    rows = [
        {
            "product": row.product,
            "sales_attr1": row.sales_attr1,
            "sales_attr2": row.sales_attr2,
            "quantity": row.quantity,
            "remark": row.remark,
        }
        for row in parent.rows
    ]
    return _normalized_rows(rows) or []


def _sample_payload(sample: dict[str, Any]) -> dict[str, Any] | None:
    payload = sample.get("raw_payload", sample.get("payload"))
    return payload if isinstance(payload, dict) else None


def synthesize_rule(
    *,
    payload: dict[str, Any],
    source_component: str,
    corrected_rows: list[dict[str, Any]],
    gold_samples: list[dict[str, Any]],
    negative_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = _normalized_rows(corrected_rows)
    if expected is None:
        return {"status": "candidate_invalid", "rule": None, "replay_report": []}

    evidence = build_evidence(payload, source_component)
    rule = _compile_structured_rule(
        payload,
        expected,
        _business_item_paths(evidence),
    ) or _compile_text_rule(expected, _safe_text_sources(evidence))
    if rule is None:
        return {
            "status": "compiler_capability_missing",
            "rule": None,
            "replay_report": [],
        }
    rule = {**rule, "fingerprint": evidence["structural_fingerprint"]}
    if (
        _rule_operations(rule) - ALLOWED_OPERATIONS
        or validate_format_profiles([rule])
    ):
        return {
            "status": "compiler_capability_missing",
            "rule": None,
            "replay_report": [],
        }

    report: list[dict[str, Any]] = []

    def check(
        kind: str,
        sample_payload: dict[str, Any],
        sample_source: str,
        sample_expected: list[dict[str, Any]],
    ) -> bool:
        actual = replay_rule(rule, sample_payload, sample_source)
        passed = actual == sample_expected
        report.append(
            {
                "kind": kind,
                "passed": passed,
                "expected": sample_expected,
                "actual": actual,
            }
        )
        return passed

    passed = check("current", payload, source_component, expected)
    for sample in gold_samples:
        sample_payload = _sample_payload(sample)
        sample_expected = _normalized_rows(sample.get("rows"))
        if sample_payload is None or sample_expected is None:
            return {
                "status": "candidate_invalid",
                "rule": None,
                "replay_report": report,
            }
        passed = check(
            "gold",
            sample_payload,
            str(sample.get("source_component") or source_component),
            sample_expected,
        ) and passed
    for sample in negative_samples:
        sample_payload = _sample_payload(sample)
        if sample_payload is None:
            return {
                "status": "candidate_invalid",
                "rule": None,
                "replay_report": report,
            }
        passed = check(
            "negative",
            sample_payload,
            str(sample.get("source_component") or source_component),
            [],
        ) and passed

    return {
        "status": "compiled" if passed else "rule_replay_failed",
        "rule": rule if passed else None,
        "replay_report": report,
    }
