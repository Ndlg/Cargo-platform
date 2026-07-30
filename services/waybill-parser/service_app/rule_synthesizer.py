from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

from service_app.declarative_rules import (
    parse_with_format_profile,
    text_profile_grammar_signature,
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
    r"^\s*(?:商品(?:名称)?|产品(?:名称)?|销售属性\s*[12]|数量|备注)"
    r"\s*(?:是|为|[:：=])"
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


def _common_item_prefix(source_paths: list[str]) -> str | None:
    if not source_paths:
        return None
    common = source_paths[0].split(".")
    for source_path in source_paths[1:]:
        parts = source_path.split(".")
        common_length = 0
        while (
            common_length < len(common)
            and common_length < len(parts)
            and common[common_length] == parts[common_length]
        ):
            common_length += 1
        common = common[:common_length]
    indexed = [index for index, part in enumerate(common) if re.search(r"\[\d+\]$", part)]
    return ".".join(common[: indexed[-1] + 1]) if indexed else None


def _business_item_fields(evidence: dict[str, Any]) -> list[tuple[str, set[str]]]:
    spans = {span["span_id"]: span for span in evidence["spans"]}
    paths: dict[str, list[set[str]]] = defaultdict(list)
    for group in evidence["candidate_groups"]["structured_list_item"]:
        source_paths = [
            spans[span_id]["source_path"]
            for span_id in group
            if span_id in spans
        ]
        item_path = _common_item_prefix(source_paths)
        if item_path is None:
            continue
        relative_paths = {
            ARRAY_INDEX.sub("[]", source_path[len(item_path) + 1 :])
            for source_path in source_paths
            if source_path.startswith(f"{item_path}.")
        }
        if relative_paths:
            paths[ARRAY_INDEX.sub("[]", item_path)].append(relative_paths)
    return [
        (item_path, set.intersection(*item_fields))
        for item_path, item_fields in sorted(paths.items())
        if item_fields and set.intersection(*item_fields)
    ]


def _allowlisted_scalars(
    items: list[dict[str, Any]],
    field_paths: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        values: dict[str, Any] = {}
        for field_path in sorted(field_paths):
            matches = values_at_structured_path(item, field_path)
            if len(matches) != 1:
                continue
            value = matches[0][0]
            if value is not None and not isinstance(value, (dict, list)):
                values[field_path] = value
        result.append(values)
    return result


def _structural_delimiter(value: str) -> bool:
    return bool(value) and len(value) <= 64 and not any(
        char.isalnum() for char in value
    )


def _compile_structured_rule(
    payload: dict[str, Any],
    corrected_rows: list[dict[str, Any]],
    item_fields: list[tuple[str, set[str]]],
) -> dict[str, Any] | None:
    for items_path, allowed_fields in item_fields:
        items = [
            item
            for item, _path in values_at_structured_path(payload, items_path)
            if isinstance(item, dict)
        ]
        if len(items) != len(corrected_rows):
            continue
        leaves = _allowlisted_scalars(items, allowed_fields)
        if not leaves or any(not item for item in leaves):
            continue
        common_paths = set.intersection(*(set(item) for item in leaves))

        def exact_paths(field: str) -> list[str]:
            expected = [row[field] for row in corrected_rows]
            if field != "quantity" and not any(expected):
                return []
            matches: list[str] = []
            for path in sorted(common_paths):
                actual = [item[path] for item in leaves]
                if field == "quantity":
                    if all(
                        not isinstance(value, bool)
                        and isinstance(value, (int, float, str))
                        and str(value).strip().isdigit()
                        for value in actual
                    ) and [int(str(value).strip()) for value in actual] == expected:
                        matches.append(path)
                elif [str(value).strip() for value in actual] == expected:
                    matches.append(path)
            return matches

        candidates = {field: exact_paths(field) for field in ROW_FIELDS}
        if len(candidates["product"]) != 1 or len(candidates["quantity"]) != 1:
            continue
        if any(len(paths) > 1 for paths in candidates.values()):
            continue
        product_path = candidates["product"][0]
        quantity_path = candidates["quantity"][0]

        fields = {"product": product_path, "quantity": quantity_path}
        defaults: dict[str, Any] = {}
        steps: list[dict[str, Any]] = []
        attr1_path = next(iter(candidates["sales_attr1"]), None)
        attr2_path = next(iter(candidates["sales_attr2"]), None)
        if attr1_path:
            fields["sales_attr1"] = attr1_path
        if attr2_path:
            fields["sales_attr2"] = attr2_path

        if (
            not attr1_path
            and not attr2_path
            and any(row["sales_attr1"] for row in corrected_rows)
            and any(row["sales_attr2"] for row in corrected_rows)
        ):
            combined: list[tuple[str, str]] = []
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
                    if not _structural_delimiter(delimiter):
                        break
                    delimiters.append(delimiter)
                else:
                    if len(set(delimiters)) == 1:
                        combined.append((path, delimiters[0]))
            if len(combined) != 1:
                continue
            attr1_path, delimiter = combined[0]
            attr2_path = attr1_path
            fields["sales_attr1"] = attr1_path
            fields["sales_attr2"] = attr1_path
            steps.append(
                {
                    "op": "rsplit",
                    "source": "sales_attr1",
                    "delimiter": delimiter,
                    "targets": ["sales_attr1", "sales_attr2"],
                }
            )

        for field, path in (
            ("sales_attr1", attr1_path),
            ("sales_attr2", attr2_path),
            ("remark", next(iter(candidates["remark"]), None)),
        ):
            if path:
                fields.setdefault(field, path)
            elif all(not row[field] for row in corrected_rows):
                defaults[field] = ""
            else:
                break
        else:
            reused_paths = list(fields.values())
            if len(set(reused_paths)) != len(reused_paths) - int(
                bool(steps) and attr1_path is not None and attr1_path == attr2_path
            ):
                continue
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


def _safe_text_sources(
    evidence: dict[str, Any],
) -> list[tuple[str, str, dict[str, Any] | None]]:
    sources: list[tuple[str, str, dict[str, Any] | None]] = []
    seen: set[tuple[str, str, int | None]] = set()
    for span in evidence["spans"]:
        if span["token_class"] != "text":
            continue
        xml_text = re.search(r"\.text\[(\d+)\]$", span["source_path"])
        path = (
            span["source_path"][: xml_text.start()]
            if xml_text
            else span["source_path"]
        )
        path = ARRAY_INDEX.sub("[]", path)
        text = span["original_text"].strip()
        text_index = int(xml_text.group(1)) if xml_text else None
        key = (path, text, text_index)
        if text and key not in seen:
            seen.add(key)
            selector = (
                {"kind": "print_xml_custom_area", "text_index": text_index}
                if text_index is not None
                else None
            )
            sources.append((path, text, selector))
    return sources


def _compile_source_order_text_rule(
    text_path: str,
    text: str,
    row: dict[str, Any],
    text_selector: dict[str, Any] | None = None,
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
    rule = {
        "strategy": "text_pipeline_v1",
        "text_path": text_path,
        "steps": steps,
        "defaults": {
            field: ""
            for field in ("sales_attr1", "sales_attr2", "remark")
            if not str(row[field]).strip()
        },
    }
    if text_selector is not None:
        rule["text_selector"] = text_selector
    return rule


def _compile_text_rule(
    corrected_rows: list[dict[str, Any]],
    text_sources: list[tuple[str, str, dict[str, Any] | None]],
) -> dict[str, Any] | None:
    # ponytail: one corrected text row is enough for delimiter programs;
    # repeated text needs a real failed sample before adding another primitive.
    if len(corrected_rows) != 1 or corrected_rows[0]["remark"]:
        return None
    row = corrected_rows[0]
    for text_path, text, selector in text_sources:
        rule = _compile_source_order_text_rule(text_path, text, row, selector)
        if rule:
            return rule
    return None


def _rule_operations(rule: dict[str, Any]) -> set[str]:
    return {
        str(step.get("op"))
        for step in rule.get("steps", [])
        if isinstance(step, dict)
    }


def _replay_rule(
    rule: dict[str, Any] | None,
    payload: dict[str, Any],
    source_component: str = "cainiao-cnprint",
) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(rule, dict):
        return 0, []
    evidence = build_evidence(payload, source_component)
    if rule.get("fingerprint") != evidence["structural_fingerprint"]:
        return 0, []
    if (
        rule.get("strategy") == "text_pipeline_v1"
        and rule.get("grammar_signature")
        and rule["grammar_signature"] != text_profile_grammar_signature(payload, rule)
    ):
        return 0, []
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
    return len(parent.rows), _normalized_rows(rows) or []


def replay_rule(
    rule: dict[str, Any] | None,
    payload: dict[str, Any],
    source_component: str = "cainiao-cnprint",
) -> list[dict[str, Any]]:
    return _replay_rule(rule, payload, source_component)[1]


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
    selected_fields: list[str] | None = None,
) -> dict[str, Any]:
    expected = _normalized_rows(corrected_rows)
    if expected is None:
        return {"status": "candidate_invalid", "rule": None, "replay_report": []}

    evidence = build_evidence(payload, source_component, selected_fields)
    rule = _compile_structured_rule(
        payload,
        expected,
        _business_item_fields(evidence),
    ) or _compile_text_rule(expected, _safe_text_sources(evidence))
    if rule is None:
        return {
            "status": "compiler_capability_missing",
            "rule": None,
            "replay_report": [],
        }
    if rule["strategy"] == "text_pipeline_v1":
        rule["grammar_signature"] = text_profile_grammar_signature(payload, rule)
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
        *,
        require_no_rows: bool = False,
    ) -> bool:
        emitted_row_count, actual = _replay_rule(rule, sample_payload, sample_source)
        passed = (
            emitted_row_count == 0
            if require_no_rows
            else actual == sample_expected
        )
        report.append(
            {
                "kind": kind,
                "passed": passed,
                "expected": sample_expected,
                "actual": actual,
                "emitted_row_count": emitted_row_count,
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
        sample_source = str(sample.get("source_component") or source_component)
        sample_evidence = build_evidence(sample_payload, sample_source)
        grammar_neighbor = (
            rule.get("strategy") == "text_pipeline_v1"
            and bool(rule.get("grammar_signature"))
            and rule["fingerprint"] == sample_evidence["structural_fingerprint"]
            and rule["grammar_signature"]
            != text_profile_grammar_signature(sample_payload, rule)
        )
        passed = check(
            "gold_neighbor" if grammar_neighbor else "gold",
            sample_payload,
            sample_source,
            [] if grammar_neighbor else sample_expected,
            require_no_rows=grammar_neighbor,
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
            require_no_rows=True,
        ) and passed

    return {
        "status": "compiled" if passed else "rule_replay_failed",
        "rule": rule if passed else None,
        "replay_report": report,
    }
