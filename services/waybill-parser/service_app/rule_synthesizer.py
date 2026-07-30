from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

from service_app.declarative_rules import (
    apply_projection_operations,
    parse_with_format_profile,
    projection_grammar_signature,
    projection_source_path,
    text_profile_grammar_signature,
    validate_format_profiles,
)
from service_app.evidence import build_evidence
from service_app.order_row_engine import values_at_structured_path


ALLOWED_OPERATIONS = {
    "collapse_whitespace",
    "collapse_adjacent_delimiters",
    "extract_quantity",
    "extract_between",
    "rsplit",
    "split",
    "split_part",
    "strip_field_label",
    "strip_prefix",
    "strip_suffix",
    "strip_trailing_quantity",
    "to_positive_int",
    "trim",
}
ROW_FIELDS = ("product", "sales_attr1", "sales_attr2", "quantity", "remark")
TEXT_FIELDS = ("product", "sales_attr1", "sales_attr2", "remark")
QUANTITY_SUFFIX_UNITS = {"件", "个", "双", "套", "份", "盒", "包", "组"}
QUANTITY_SUFFIX_WITH_CLOSER = re.compile(
    rf"^(?:{'|'.join(sorted(QUANTITY_SUFFIX_UNITS))})\s*[\]\)】）]+$"
)
FIELD_LABEL_PREFIX = re.compile(
    r"^\s*(?:商品(?:名称)?|产品(?:名称)?|销售属性\s*[12]|数量|备注)"
    r"\s*(?:是|为|[:：=])"
)
ARRAY_INDEX = re.compile(r"\[\d+\]")
PROJECTION_DELIMITER_RUN = re.compile(
    r"[,，;；|/、](?:\s*[,，;；|/、])*"
)
PROJECTION_XML_TEXT = re.compile(r"^(.*)\.text\[\d+\]$")
MAX_PROJECTION_TEXT_SPANS = 200
MAX_PROJECTION_TEXT_LENGTH = 4_096
MAX_PROJECTION_SPLIT_PARTS = 20
MAX_PROJECTION_CANDIDATES = 5_000
MAX_PROJECTION_OPERATION_VARIANTS = 10_000
MAX_PROJECTION_SEARCH_NODES = 10_000


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
        collapsed_fields: set[str] = set()
        for field in TEXT_FIELDS:
            expected = [row[field] for row in corrected_rows]
            if candidates[field] or not any(expected):
                continue
            matches = [
                path
                for path in sorted(common_paths)
                if [" ".join(str(item[path]).split()) for item in leaves] == expected
            ]
            if matches:
                candidates[field] = matches
                collapsed_fields.add(field)
        if len(candidates["product"]) != 1 or len(candidates["quantity"]) != 1:
            continue
        if any(len(paths) > 1 for paths in candidates.values()):
            continue
        product_path = candidates["product"][0]
        quantity_path = candidates["quantity"][0]

        fields = {"product": product_path, "quantity": quantity_path}
        defaults: dict[str, Any] = {}
        steps: list[dict[str, Any]] = [
            {"op": "collapse_whitespace", "target": field}
            for field in ROW_FIELDS
            if field in collapsed_fields
        ]
        attr1_path = next(iter(candidates["sales_attr1"]), None)
        attr2_path = next(iter(candidates["sales_attr2"]), None)
        if attr1_path:
            fields["sales_attr1"] = attr1_path
        if attr2_path:
            fields["sales_attr2"] = attr2_path

        if (
            not attr1_path
            and any(row["sales_attr1"] for row in corrected_rows)
            and any(row["sales_attr2"] for row in corrected_rows)
        ):
            combined: list[tuple[str, str, tuple[str, ...]]] = []
            for path in sorted(common_paths):
                delimiters: list[str] = []
                values: list[str] = []
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
                    values.append(value)
                else:
                    if len(set(delimiters)) == 1:
                        combined.append((path, delimiters[0], tuple(values)))
            if not combined or len(
                {(delimiter, values) for _path, delimiter, values in combined}
            ) != 1:
                continue
            attr1_path, delimiter, _values = combined[0]
            fields["sales_attr1"] = attr1_path
            fields.setdefault("sales_attr2", attr1_path)
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
) -> list[tuple[str, str, dict[str, Any] | None, str]]:
    sources: list[tuple[str, str, dict[str, Any] | None, str]] = []
    for span in evidence["spans"]:
        if span["token_class"] != "text":
            continue
        xml_text = re.search(r"\.text\[(\d+)\]$", span["source_path"])
        concrete_path = (
            span["source_path"][: xml_text.start()]
            if xml_text
            else span["source_path"]
        )
        path = concrete_path
        path = ARRAY_INDEX.sub("[]", path)
        text = span["original_text"].strip()
        text_index = int(xml_text.group(1)) if xml_text else None
        if text:
            selector = (
                {"kind": "print_xml_custom_area", "text_index": text_index}
                if text_index is not None
                else None
            )
            sources.append((path, text, selector, concrete_path))
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
            and not QUANTITY_SUFFIX_WITH_CLOSER.fullmatch(suffix.strip())
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
    text_sources: list[tuple[str, str, dict[str, Any] | None, str]],
) -> dict[str, Any] | None:
    if any(row["remark"] for row in corrected_rows):
        return None
    grouped: dict[
        tuple[str, tuple[tuple[str, Any], ...] | None],
        list[tuple[str, dict[str, Any] | None, str]],
    ] = defaultdict(list)
    for text_path, text, selector, concrete_path in text_sources:
        selector_key = tuple(sorted(selector.items())) if selector else None
        grouped[(text_path, selector_key)].append((text, selector, concrete_path))
    if len(corrected_rows) > 1:
        for (text_path, _selector_key), sources in grouped.items():
            if len(sources) != len(corrected_rows):
                continue
            rules = [
                _compile_source_order_text_rule(text_path, text, row, selector)
                for (text, selector, _concrete_path), row in zip(
                    sources, corrected_rows, strict=True
                )
            ]
            if rules[0] and all(rule == rules[0] for rule in rules[1:]):
                same_scalar = len({source[2] for source in sources}) == 1
                return {
                    **rules[0],
                    **({"item_split": "\n"} if same_scalar else {}),
                }
    for text_path, text, selector, _concrete_path in text_sources:
        if len(corrected_rows) == 1:
            rule = _compile_source_order_text_rule(
                text_path, text, corrected_rows[0], selector
            )
            if rule:
                return rule
            continue
        separators = {
            char for char in text if not char.isalnum() and not char.isspace()
        }
        separators.update(
            separator
            for separator in ("\r\n", "\n", "\r", "\t")
            if separator in text
        )
        for separator in sorted(
            separators, key=lambda value: (-len(value), value)
        ):
            parts = [part.strip() for part in text.split(separator)]
            if len(parts) != len(corrected_rows) or any(not part for part in parts):
                continue
            rules = [
                _compile_source_order_text_rule(text_path, part, row, selector)
                for part, row in zip(parts, corrected_rows)
            ]
            if rules[0] and all(rule == rules[0] for rule in rules[1:]):
                return {**rules[0], "item_split": separator}
    return None


def _projection_candidates(
    evidence: dict[str, Any],
) -> list[dict[str, Any]] | None:
    occurrences: dict[tuple[str, str], int] = defaultdict(int)
    candidates: list[dict[str, Any]] = []
    operation_variant_count = 0
    text_spans = [
        span for span in evidence["spans"] if span["token_class"] == "text"
    ]
    if len(text_spans) > MAX_PROJECTION_TEXT_SPANS:
        return None
    for order, span in enumerate(text_spans):
        source_path = projection_source_path(str(span["source_path"]))
        token_class = str(span["token_class"])
        key = (source_path, token_class)
        occurrence = occurrences[key]
        occurrences[key] += 1
        selector = {
            "source_path": source_path,
            "token_class": token_class,
            "occurrence": occurrence,
        }
        operations: list[list[dict[str, Any]]] = [
            [],
            [{"op": "strip_trailing_quantity"}],
            [{"op": "strip_field_label"}],
            [{"op": "collapse_adjacent_delimiters"}],
            [
                {"op": "collapse_adjacent_delimiters"},
                {"op": "strip_trailing_quantity"},
            ],
            [{"op": "extract_quantity"}],
        ]
        operation_variant_count += len(operations)
        if operation_variant_count > MAX_PROJECTION_OPERATION_VARIANTS:
            return None
        text = str(span["original_text"])
        if len(text) > MAX_PROJECTION_TEXT_LENGTH:
            return None
        for delimiter in dict.fromkeys(
            match.group()
            for match in PROJECTION_DELIMITER_RUN.finditer(text)
        ):
            split_count = text.count(delimiter) + 1
            if split_count > MAX_PROJECTION_SPLIT_PARTS:
                return None
            operation_variant_count += split_count * 5
            if operation_variant_count > MAX_PROJECTION_OPERATION_VARIANTS:
                return None
            for index in range(split_count):
                split_part = {
                    "op": "split_part",
                    "delimiter": delimiter,
                    "index": index,
                }
                operations.extend(
                    [
                        [split_part],
                        [split_part, {"op": "strip_field_label"}],
                        [split_part, {"op": "strip_trailing_quantity"}],
                        [split_part, {"op": "extract_quantity"}],
                        [
                            split_part,
                            {"op": "collapse_adjacent_delimiters"},
                        ],
                    ]
                )
                if index == 0:
                    continue
                first_part = apply_projection_operations(text, [split_part])
                nested_delimiters = dict.fromkeys(
                    match.group()
                    for match in PROJECTION_DELIMITER_RUN.finditer(first_part)
                )
                for nested_delimiter in nested_delimiters:
                    nested_count = first_part.count(nested_delimiter) + 1
                    if nested_count > MAX_PROJECTION_SPLIT_PARTS:
                        return None
                    operation_variant_count += nested_count * 5
                    if (
                        operation_variant_count
                        > MAX_PROJECTION_OPERATION_VARIANTS
                    ):
                        return None
                    for nested_index in range(nested_count):
                        nested_split = {
                            "op": "split_part",
                            "delimiter": nested_delimiter,
                            "index": nested_index,
                        }
                        nested_steps = [split_part, nested_split]
                        operations.extend(
                            [
                                nested_steps,
                                [
                                    *nested_steps,
                                    {"op": "strip_field_label"},
                                ],
                                [
                                    *nested_steps,
                                    {"op": "strip_trailing_quantity"},
                                ],
                                [
                                    *nested_steps,
                                    {"op": "extract_quantity"},
                                ],
                                [
                                    *nested_steps,
                                    {"op": "collapse_adjacent_delimiters"},
                                ],
                            ]
                        )
        seen_values: set[str] = set()
        for steps in operations:
            value = apply_projection_operations(text, steps)
            if not value or value in seen_values:
                continue
            seen_values.add(value)
            candidates.append(
                {
                    "order": order,
                    "value": value,
                    "part": {
                        **selector,
                        **({"operations": steps} if steps else {}),
                    },
                }
            )
            if len(candidates) > MAX_PROJECTION_CANDIDATES:
                return None
    return candidates


def _projection_alignment_axis(source_path: str) -> str | None:
    xml_text = PROJECTION_XML_TEXT.fullmatch(source_path)
    if xml_text:
        return f"xml:{xml_text.group(1)}"
    parts = source_path.split(".")
    repeated = [
        ".".join(parts[: index + 1])
        for index, part in enumerate(parts)
        if part.endswith("[]")
    ]
    return f"array:{repeated[-1]}" if repeated else None


def _find_projection_parts(
    field: str,
    expected: str,
    candidates: list[dict[str, Any]],
    minimum_order: int,
    *,
    remaining_occurrences: int,
    preferred_orders: set[int] | None = None,
    preferred_occurrences: set[int] | None = None,
    preferred_paths: set[str] | None = None,
    preferred_axes: set[str] | None = None,
) -> list[dict[str, Any]] | None:
    usable = [
        candidate
        for candidate in candidates
        if candidate["order"] > minimum_order
        and (
            field == "quantity"
            or not any(
                operation["op"] == "extract_quantity"
                for operation in candidate["part"].get("operations", [])
            )
        )
    ]

    def priority(candidate: dict[str, Any]) -> tuple[int, int]:
        return (
            len(candidate["part"].get("operations", [])),
            candidate["order"],
        )

    exact = sorted(
        (
            candidate
            for candidate in usable
            if candidate["value"] == expected
        ),
        key=priority,
    )
    if exact:
        best = [
            candidate
            for candidate in exact
            if priority(candidate)[0] == priority(exact[0])[0]
        ]
        anchored = [
            candidate
            for candidate in best
            if preferred_orders and candidate["order"] in preferred_orders
        ]
        if anchored:
            first_order = min(candidate["order"] for candidate in anchored)
            first = [
                candidate
                for candidate in anchored
                if candidate["order"] == first_order
            ]
            return [first[0]] if len(first) == 1 else None
        occurrence_anchored = [
            candidate
            for candidate in best
            if preferred_occurrences
            and candidate["part"]["occurrence"] in preferred_occurrences
            and (
                candidate["part"]["source_path"] in (preferred_paths or set())
                or _projection_alignment_axis(
                    str(candidate["part"]["source_path"])
                )
                in (preferred_axes or set())
            )
        ]
        if occurrence_anchored:
            first_occurrence = min(
                candidate["part"]["occurrence"]
                for candidate in occurrence_anchored
            )
            first = [
                candidate
                for candidate in occurrence_anchored
                if candidate["part"]["occurrence"] == first_occurrence
            ]
            return [first[0]] if len(first) == 1 else None
        if len(best) == 1:
            return [best[0]]
        roles = {
            (
                candidate["part"]["source_path"],
                tuple(
                    tuple(sorted(operation.items()))
                    for operation in candidate["part"].get("operations", [])
                ),
            )
            for candidate in best
        }
        if len(roles) == 1 and len(best) == remaining_occurrences:
            return [best[0]]
        return None

    fragments = sorted(
        (
            candidate
            for candidate in usable
            if candidate["value"] in expected
        ),
        key=lambda candidate: (candidate["order"], priority(candidate)),
    )

    solutions: list[list[dict[str, Any]]] = []
    search_nodes = 0
    search_exhausted = False

    def search(
        start: int,
        offset: int,
        selected: list[dict[str, Any]],
    ) -> None:
        nonlocal search_exhausted, search_nodes
        if len(solutions) > 1:
            return
        search_nodes += 1
        if search_nodes > MAX_PROJECTION_SEARCH_NODES:
            search_exhausted = True
            return
        if offset == len(expected):
            solutions.append(selected)
            return
        if len(selected) >= 4:
            return
        value_offset = offset + (1 if offset else 0)
        if offset and expected[offset:value_offset] != " ":
            return
        previous_order = selected[-1]["order"] if selected else minimum_order
        for index in range(start, len(fragments)):
            candidate = fragments[index]
            value = candidate["value"]
            if (
                candidate["order"] <= previous_order
                or not expected.startswith(value, value_offset)
            ):
                continue
            search(
                index + 1,
                value_offset + len(value),
                [*selected, candidate],
            )

    search(0, 0, [])
    return (
        solutions[0]
        if len(solutions) == 1 and not search_exhausted
        else None
    )


def _compile_projection_rule(
    evidence: dict[str, Any],
    corrected_rows: list[dict[str, Any]],
    selected_fields: list[str] | None,
) -> dict[str, Any] | None:
    candidates = _projection_candidates(evidence)
    if candidates is None:
        return None
    rows = [{field: [] for field in ROW_FIELDS} for _row in corrected_rows]
    product_orders: list[set[int]] = [set() for _row in corrected_rows]
    product_occurrences: list[set[int]] = [set() for _row in corrected_rows]
    product_paths: list[set[str]] = [set() for _row in corrected_rows]
    product_axes: list[set[str]] = [set() for _row in corrected_rows]
    for field in ("product",):
        minimum_order = -1
        for index, corrected_row in enumerate(corrected_rows):
            value = str(corrected_row[field]).strip()
            if not value:
                continue
            remaining_occurrences = sum(
                str(row[field]).strip() == value
                for row in corrected_rows[index:]
            )
            selected = _find_projection_parts(
                field,
                value,
                candidates,
                minimum_order,
                remaining_occurrences=remaining_occurrences,
                preferred_orders=(
                    product_orders[index] if field == "quantity" else None
                ),
                preferred_occurrences=(
                    product_occurrences[index] if field != "product" else None
                ),
            )
            if selected is None:
                return None
            rows[index][field] = [candidate["part"] for candidate in selected]
            if field == "product":
                product_orders[index] = {
                    candidate["order"] for candidate in selected
                }
                product_occurrences[index] = {
                    int(candidate["part"]["occurrence"])
                    for candidate in selected
                }
                product_paths[index] = {
                    str(candidate["part"]["source_path"])
                    for candidate in selected
                }
                product_axes[index] = {
                    axis
                    for candidate in selected
                    if (
                        axis := _projection_alignment_axis(
                            str(candidate["part"]["source_path"])
                        )
                    )
                }
            minimum_order = selected[-1]["order"]

    row_anchors: list[int | None] = []
    for index, corrected_row in enumerate(corrected_rows):
        possible = product_occurrences[index]
        anchors = set(possible) if len(possible) == 1 else set()
        if len(possible) > 1:
            for field in ("sales_attr1", "sales_attr2", "remark"):
                value = str(corrected_row[field]).strip()
                if not value:
                    continue
                exact = [
                    candidate
                    for candidate in candidates
                    if candidate["value"] == value
                    and candidate["part"]["occurrence"] in possible
                    and candidate["part"]["source_path"]
                    not in product_paths[index]
                    and _projection_alignment_axis(
                        str(candidate["part"]["source_path"])
                    )
                    in product_axes[index]
                    and not any(
                        operation["op"] == "extract_quantity"
                        for operation in candidate["part"].get("operations", [])
                    )
                ]
                if not exact:
                    continue
                operation_count = min(
                    len(candidate["part"].get("operations", []))
                    for candidate in exact
                )
                field_occurrences = {
                    int(candidate["part"]["occurrence"])
                    for candidate in exact
                    if len(candidate["part"].get("operations", []))
                    == operation_count
                }
                if len(field_occurrences) == 1:
                    anchors.update(field_occurrences)
        if len(anchors) > 1:
            return None
        row_anchors.append(next(iter(anchors)) if len(anchors) == 1 else None)

    for field in ROW_FIELDS[1:]:
        minimum_order = -1
        for index, corrected_row in enumerate(corrected_rows):
            value = str(corrected_row[field]).strip()
            if not value:
                continue
            remaining_occurrences = sum(
                str(row[field]).strip() == value
                for row in corrected_rows[index:]
            )
            selected = _find_projection_parts(
                field,
                value,
                candidates,
                minimum_order,
                remaining_occurrences=remaining_occurrences,
                preferred_orders=(
                    product_orders[index] if field == "quantity" else None
                ),
                preferred_occurrences=(
                    {row_anchors[index]}
                    if row_anchors[index] is not None
                    else None
                ),
                preferred_paths=product_paths[index],
                preferred_axes=product_axes[index],
            )
            if selected is None:
                return None
            rows[index][field] = [candidate["part"] for candidate in selected]
            minimum_order = selected[-1]["order"]
    rule: dict[str, Any] = {
        "strategy": "source_projection_v1",
        "grammar_signature": projection_grammar_signature(evidence),
        "rows": rows,
    }
    if selected_fields is not None:
        rule["selected_fields"] = list(selected_fields)
    return rule


def _rule_operations(rule: dict[str, Any]) -> set[str]:
    operations = {
        str(step.get("op"))
        for step in rule.get("steps", [])
        if isinstance(step, dict)
    }
    for row in rule.get("rows", []):
        if not isinstance(row, dict):
            continue
        for parts in row.values():
            if not isinstance(parts, list):
                continue
            operations.update(
                str(operation.get("op"))
                for part in parts
                if isinstance(part, dict)
                for operation in part.get("operations", [])
                if isinstance(operation, dict)
            )
    return operations


def _replay_rule(
    rule: dict[str, Any] | None,
    payload: dict[str, Any],
    source_component: str = "cainiao-cnprint",
) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(rule, dict):
        return 0, []
    evidence = build_evidence(
        payload,
        source_component,
        rule.get("selected_fields")
        if rule.get("strategy") in {"structured_items_v1", "source_projection_v1"}
        else None,
    )
    if rule.get("fingerprint") != evidence["structural_fingerprint"]:
        return 0, []
    if (
        rule.get("strategy") == "structured_items_v1"
        and rule.get("grammar_signature")
        and rule.get("grammar_signature") != evidence["grammar_signature"]
    ):
        return 0, []
    if (
        rule.get("strategy") == "text_pipeline_v1"
        and rule.get("grammar_signature")
        and rule["grammar_signature"] != text_profile_grammar_signature(payload, rule)
    ):
        return 0, []
    if (
        rule.get("strategy") == "source_projection_v1"
        and rule.get("grammar_signature") != projection_grammar_signature(evidence)
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
    ) or _compile_text_rule(
        expected,
        _safe_text_sources(evidence),
    ) or _compile_projection_rule(
        evidence,
        expected,
        selected_fields,
    )
    if rule is None:
        return {
            "status": "compiler_capability_missing",
            "rule": None,
            "replay_report": [],
        }
    if rule["strategy"] == "structured_items_v1":
        rule["grammar_signature"] = evidence["grammar_signature"]
        if selected_fields is not None:
            rule["selected_fields"] = list(selected_fields)
    elif rule["strategy"] == "text_pipeline_v1":
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
        sample_evidence = build_evidence(
            sample_payload,
            sample_source,
            rule.get("selected_fields")
            if rule.get("strategy") in {"structured_items_v1", "source_projection_v1"}
            else None,
        )
        grammar_neighbor = (
            rule.get("strategy")
            in {"structured_items_v1", "text_pipeline_v1", "source_projection_v1"}
            and bool(rule.get("grammar_signature"))
            and rule["fingerprint"] == sample_evidence["structural_fingerprint"]
            and rule["grammar_signature"]
            != (
                sample_evidence["grammar_signature"]
                if rule.get("strategy") == "structured_items_v1"
                else (
                    text_profile_grammar_signature(sample_payload, rule)
                    if rule.get("strategy") == "text_pipeline_v1"
                    else projection_grammar_signature(sample_evidence)
                )
            )
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
