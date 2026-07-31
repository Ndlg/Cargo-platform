from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import re
from typing import Any

from services.shared.waybill_fingerprint import (
    fingerprint_for_payload,
    grammar_signature_for_texts,
)
from service_app.douyin_product_info import (
    compact_spaces,
    quantity_from_text,
    strip_trailing_quantity_text,
)
from service_app.evidence import build_evidence
from service_app.order_row_engine import (
    OrderRowDraft,
    ParentWaybillDraft,
    business_parent_label,
    print_xml_text,
    remove_field_label,
    text_value,
    values_at_structured_path,
)


PATH_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\[\])?(?:\.[A-Za-z0-9_]+(?:\[\])?)*$")
PROJECTION_PATH_PATTERN = re.compile(
    r"^[A-Za-z0-9_]+(?:\[\])?"
    r"(?:\.[A-Za-z0-9_]+(?:\[\]|\[\d+\])?)*$"
)
FIELD_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$")
FINGERPRINT_PATTERN = re.compile(r"^(?:sha256|v2:[A-Za-z0-9_-]+:sha256):[0-9a-f]{64}$")
GRAMMAR_SIGNATURE_PATTERN = re.compile(r"^grammar-v1:sha256:[0-9a-f]{64}$")
MAX_QUANTITY = 100_000
QUANTITY_MARKER_PATTERN = re.compile(r"[【\[(（]\s*\d+\s*(?:件|个|個|双|雙|条|條|套|只|瓶|包|箱)\s*[】\])）]")
ROW_FIELDS = {"product", "sales_attr1", "sales_attr2", "quantity", "remark", "image_match_text"}
STATE_FIELDS = ROW_FIELDS | {"text"}
STRUCTURED_PROFILE_KEYS = {
    "fingerprint",
    "strategy",
    "name",
    "description",
    "grammar_signature",
    "selected_fields",
    "items_path",
    "fields",
    "steps",
    "defaults",
    "provenance",
}
TEXT_PROFILE_KEYS = {
    "fingerprint",
    "strategy",
    "name",
    "description",
    "text_path",
    "text_selector",
    "grammar_signature",
    "item_split",
    "steps",
    "defaults",
    "provenance",
}
SOURCE_PROJECTION_PROFILE_KEYS = {
    "fingerprint",
    "strategy",
    "name",
    "description",
    "grammar_signature",
    "selected_fields",
    "rows",
    "provenance",
}
PROJECTION_TOKEN_CLASSES = {"text"}
PROJECTION_OPERATIONS = {
    "collapse_adjacent_delimiters",
    "extract_quantity",
    "split_part",
    "strip_field_label",
    "strip_trailing_quantity",
}
PROJECTION_DELIMITER_PATTERN = re.compile(r"^[^A-Za-z0-9\u4e00-\u9fff]{1,64}$")
ARRAY_INDEX_PATTERN = re.compile(r"\[\d+\]")
ADJACENT_DELIMITERS_PATTERN = re.compile(
    r"([,，;；|/、])(?:\s*[,，;；|/、])+"
)


def structural_fingerprint(payload: dict[str, Any], source_component: str | None) -> str:
    return fingerprint_for_payload(payload, text_value(source_component), "legacy_structure_v1")


def valid_path(value: object, *, allow_lists: bool = True) -> bool:
    text = text_value(value)
    if not text or len(text) > 512 or len(text.split(".")) > 12:
        return False
    if not PATH_PATTERN.fullmatch(text):
        return False
    return allow_lists or "[]" not in text


def validate_defaults(defaults: object, prefix: str) -> list[str]:
    if defaults is None:
        return []
    if not isinstance(defaults, dict):
        return [f"{prefix}.defaults"]
    errors: list[str] = []
    if set(defaults) - ROW_FIELDS:
        errors.append(f"{prefix}.defaults")
    quantity = defaults.get("quantity")
    if quantity is not None and (
        not isinstance(quantity, int)
        or isinstance(quantity, bool)
        or not 1 <= quantity <= MAX_QUANTITY
    ):
        errors.append(f"{prefix}.defaults.quantity")
    for field, value in defaults.items():
        if field != "quantity" and (not isinstance(value, str) or len(value) > 2000):
            errors.append(f"{prefix}.defaults.{field}")
    return errors


def validate_selected_fields(value: object, prefix: str) -> list[str]:
    if value is None:
        return []
    if (
        not isinstance(value, list)
        or len(value) > 100
        or len(value) != len(set(value))
        or any(
            not isinstance(field, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", field)
            for field in value
        )
    ):
        return [f"{prefix}.selected_fields"]
    return []


def validate_profile_provenance(value: object, prefix: str) -> list[str]:
    if value is None:
        return []
    if (
        not isinstance(value, dict)
        or set(value) != {"source", "learning_session_id"}
        or value.get("source") != "confirmed_ai_rule"
        or not isinstance(value.get("learning_session_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["learning_session_id"])
    ):
        return [f"{prefix}.provenance"]
    return []


def validate_structured_profile(profile: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    if set(profile) - STRUCTURED_PROFILE_KEYS:
        errors.append(prefix)
    grammar_signature = profile.get("grammar_signature")
    if grammar_signature is not None and (
        not isinstance(grammar_signature, str)
        or not GRAMMAR_SIGNATURE_PATTERN.fullmatch(grammar_signature)
    ):
        errors.append(f"{prefix}.grammar_signature")
    errors.extend(validate_selected_fields(profile.get("selected_fields"), prefix))
    if not valid_path(profile.get("items_path")):
        errors.append(f"{prefix}.items_path")
    fields = profile.get("fields")
    if not isinstance(fields, dict):
        errors.append(f"{prefix}.fields")
    else:
        defaults = profile.get("defaults")
        has_quantity_default = (
            isinstance(defaults, dict)
            and isinstance(defaults.get("quantity"), int)
            and not isinstance(defaults.get("quantity"), bool)
            and 1 <= defaults["quantity"] <= MAX_QUANTITY
        )
        if set(fields) - ROW_FIELDS or "product" not in fields or (
            "quantity" not in fields and not has_quantity_default
        ):
            errors.append(f"{prefix}.fields")
        for field, path in fields.items():
            if field not in ROW_FIELDS or not valid_path(path, allow_lists=False):
                errors.append(f"{prefix}.fields.{field}")
    steps = profile.get("steps")
    if steps is not None:
        if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
            errors.append(f"{prefix}.steps")
        else:
            for index, step in enumerate(steps):
                errors.extend(validate_text_step(step, f"{prefix}.steps[{index}]", state_fields=ROW_FIELDS))
    errors.extend(validate_defaults(profile.get("defaults"), prefix))
    return errors


def validate_text_step(
    step: object,
    prefix: str,
    *,
    state_fields: set[str] = STATE_FIELDS,
) -> list[str]:
    if not isinstance(step, dict):
        return [prefix]
    op = step.get("op")
    allowed: dict[str, set[str]] = {
        "split": {"op", "source", "delimiter", "targets"},
        "rsplit": {"op", "source", "delimiter", "targets"},
        "extract_between": {
            "op",
            "source",
            "start",
            "end",
            "target",
            "consume",
            "include_delimiters",
        },
        "trim": {"op", "target", "chars"},
        "strip_prefix": {"op", "target", "literal"},
        "strip_suffix": {"op", "target", "literal"},
        "collapse_whitespace": {"op", "target"},
        "to_positive_int": {"op", "target"},
    }
    if op not in allowed or set(step) - allowed.get(str(op), set()):
        return [prefix]
    errors: list[str] = []
    source = step.get("source", "text")
    target = step.get("target")
    if op in {"split", "rsplit", "extract_between"} and source not in state_fields:
        errors.append(f"{prefix}.source")
    if op in {
        "extract_between",
        "trim",
        "strip_prefix",
        "strip_suffix",
        "collapse_whitespace",
        "to_positive_int",
    }:
        if target not in state_fields:
            errors.append(f"{prefix}.target")
    if op in {"split", "rsplit"}:
        delimiter = step.get("delimiter")
        targets = step.get("targets")
        if not isinstance(delimiter, str) or not delimiter or len(delimiter) > 64:
            errors.append(f"{prefix}.delimiter")
        if (
            not isinstance(targets, list)
            or not 2 <= len(targets) <= 10
            or len(set(targets)) != len(targets)
            or any(target not in state_fields for target in targets)
        ):
            errors.append(f"{prefix}.targets")
    if op == "extract_between":
        for field in ("start", "end"):
            value = step.get(field)
            if not isinstance(value, str) or not value or len(value) > 64:
                errors.append(f"{prefix}.{field}")
        if "consume" in step and not isinstance(step.get("consume"), bool):
            errors.append(f"{prefix}.consume")
        if "include_delimiters" in step and not isinstance(step.get("include_delimiters"), bool):
            errors.append(f"{prefix}.include_delimiters")
    if op == "trim" and "chars" in step:
        chars = step.get("chars")
        if not isinstance(chars, str) or len(chars) > 64:
            errors.append(f"{prefix}.chars")
    if op in {"strip_prefix", "strip_suffix"}:
        literal = step.get("literal")
        if not isinstance(literal, str) or not literal or len(literal) > 64:
            errors.append(f"{prefix}.literal")
    return errors


def validate_text_profile(profile: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    if set(profile) - TEXT_PROFILE_KEYS:
        errors.append(prefix)
    if not valid_path(profile.get("text_path")):
        errors.append(f"{prefix}.text_path")
    selector = profile.get("text_selector")
    if selector is not None and (
        not isinstance(selector, dict)
        or set(selector) != {"kind", "text_index"}
        or selector.get("kind") != "print_xml_custom_area"
        or isinstance(selector.get("text_index"), bool)
        or not isinstance(selector.get("text_index"), int)
        or not 0 <= selector["text_index"] <= 10_000
        or not str(profile.get("text_path") or "").endswith("printXML")
    ):
        errors.append(f"{prefix}.text_selector")
    grammar_signature = profile.get("grammar_signature")
    if grammar_signature is not None and (
        not isinstance(grammar_signature, str)
        or not GRAMMAR_SIGNATURE_PATTERN.fullmatch(grammar_signature)
    ):
        errors.append(f"{prefix}.grammar_signature")
    item_split = profile.get("item_split")
    if item_split is not None and (
        not isinstance(item_split, str) or not item_split or len(item_split) > 64
    ):
        errors.append(f"{prefix}.item_split")
    steps = profile.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        errors.append(f"{prefix}.steps")
    else:
        for index, step in enumerate(steps):
            errors.extend(validate_text_step(step, f"{prefix}.steps[{index}]"))
    errors.extend(validate_defaults(profile.get("defaults"), prefix))
    return errors


def validate_projection_part(part: object, prefix: str) -> list[str]:
    if not isinstance(part, dict):
        return [prefix]
    if set(part) - {"source_path", "token_class", "occurrence", "operations"}:
        return [prefix]
    errors: list[str] = []
    source_path = part.get("source_path")
    if (
        not isinstance(source_path, str)
        or len(source_path) > 512
        or not PROJECTION_PATH_PATTERN.fullmatch(source_path)
    ):
        errors.append(f"{prefix}.source_path")
    if part.get("token_class") not in PROJECTION_TOKEN_CLASSES:
        errors.append(f"{prefix}.token_class")
    occurrence = part.get("occurrence")
    if (
        isinstance(occurrence, bool)
        or not isinstance(occurrence, int)
        or not 0 <= occurrence <= 10_000
    ):
        errors.append(f"{prefix}.occurrence")
    operations = part.get("operations", [])
    if not isinstance(operations, list) or len(operations) > 4:
        errors.append(f"{prefix}.operations")
    else:
        for index, operation in enumerate(operations):
            operation_prefix = f"{prefix}.operations[{index}]"
            if not isinstance(operation, dict):
                errors.append(operation_prefix)
                continue
            op = operation.get("op")
            allowed = (
                {"op", "delimiter", "index"}
                if op == "split_part"
                else {"op"}
            )
            if op not in PROJECTION_OPERATIONS or set(operation) != allowed:
                errors.append(operation_prefix)
                continue
            if op == "split_part":
                if (
                    not isinstance(operation.get("delimiter"), str)
                    or not PROJECTION_DELIMITER_PATTERN.fullmatch(
                        operation["delimiter"]
                    )
                ):
                    errors.append(f"{operation_prefix}.delimiter")
                index_value = operation.get("index")
                if (
                    isinstance(index_value, bool)
                    or not isinstance(index_value, int)
                    or not 0 <= index_value <= 20
                ):
                    errors.append(f"{operation_prefix}.index")
    return errors


def validate_source_projection_profile(
    profile: dict[str, Any],
    prefix: str,
) -> list[str]:
    errors: list[str] = []
    if set(profile) - SOURCE_PROJECTION_PROFILE_KEYS:
        errors.append(prefix)
    grammar_signature = profile.get("grammar_signature")
    if (
        not isinstance(grammar_signature, str)
        or not GRAMMAR_SIGNATURE_PATTERN.fullmatch(grammar_signature)
    ):
        errors.append(f"{prefix}.grammar_signature")
    errors.extend(validate_selected_fields(profile.get("selected_fields"), prefix))
    rows = profile.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        return [*errors, f"{prefix}.rows"]
    for row_index, row in enumerate(rows):
        row_prefix = f"{prefix}.rows[{row_index}]"
        if not isinstance(row, dict) or set(row) != ROW_FIELDS - {"image_match_text"}:
            errors.append(row_prefix)
            continue
        for field, parts in row.items():
            field_prefix = f"{row_prefix}.{field}"
            if (
                not isinstance(parts, list)
                or len(parts) > 4
                or (field in {"product", "quantity"} and not parts)
            ):
                errors.append(field_prefix)
                continue
            for part_index, part in enumerate(parts):
                errors.extend(
                    validate_projection_part(
                        part,
                        f"{field_prefix}[{part_index}]",
                    )
                )
    return errors


def validate_format_profiles(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        return ["parser_policy.format_profiles"]
    errors: list[str] = []
    identities: set[tuple[str, str, tuple[str, ...], str | None]] = set()
    for index, profile in enumerate(value):
        prefix = f"parser_policy.format_profiles[{index}]"
        if not isinstance(profile, dict):
            errors.append(prefix)
            continue
        fingerprint = text_value(profile.get("fingerprint"))
        strategy = text_value(profile.get("strategy"))
        grammar_signature = text_value(profile.get("grammar_signature")) or None
        selected_fields = tuple(profile.get("selected_fields") or ())
        identity = (
            fingerprint,
            strategy,
            selected_fields,
            grammar_signature,
        )
        if not FINGERPRINT_PATTERN.fullmatch(fingerprint) or identity in identities:
            errors.append(f"{prefix}.fingerprint")
        identities.add(identity)
        errors.extend(validate_profile_provenance(profile.get("provenance"), prefix))
        if strategy == "structured_items_v1":
            errors.extend(validate_structured_profile(profile, prefix))
        elif strategy == "text_pipeline_v1":
            errors.extend(validate_text_profile(profile, prefix))
        elif strategy == "source_projection_v1":
            errors.extend(validate_source_projection_profile(profile, prefix))
        else:
            errors.append(f"{prefix}.strategy")
    return list(dict.fromkeys(errors))


def first_path_value(payload: dict[str, Any], path: str) -> tuple[Any, str]:
    values = values_at_structured_path(payload, path)
    return values[0] if values else (None, path)


def relative_field_value(item: dict[str, Any], path: str) -> tuple[Any, str]:
    return first_path_value(item, path)


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= MAX_QUANTITY else None
    text = text_value(value)
    return int(text) if text.isdigit() and 1 <= int(text) <= MAX_QUANTITY else None


def pipeline_text_value(value: Any, selector: object = None) -> str:
    text = text_value(value)
    if isinstance(selector, dict) and selector.get("kind") == "print_xml_custom_area":
        return print_xml_text(
            text,
            text_index=selector.get("text_index"),
            require_custom_area=True,
        )
    return print_xml_text(text) or text


def text_profile_input_values(
    payload: dict[str, Any],
    profile: dict[str, Any],
) -> list[tuple[str, str]]:
    return [
        (text, path)
        for value, path in values_at_structured_path(payload, profile["text_path"])
        if (text := pipeline_text_value(value, profile.get("text_selector")))
    ]


def text_profile_grammar_signature(
    payload: dict[str, Any],
    profile: dict[str, Any],
) -> str:
    return grammar_signature_for_texts(
        text for text, _path in text_profile_input_values(payload, profile)
    )


def row_from_values(
    values: dict[str, Any],
    traces: dict[str, str],
    *,
    raw_record_id: int,
    task_id: int | None,
    parent_label: str,
    source_component: str,
    source_index: str,
    child_index: int,
    child_count: int,
) -> OrderRowDraft:
    product = text_value(values.get("product"))
    sales_attr1 = text_value(values.get("sales_attr1"))
    sales_attr2 = text_value(values.get("sales_attr2"))
    quantity = positive_int(values.get("quantity"))
    remark = text_value(values.get("remark"))
    image_match_text = text_value(values.get("image_match_text")) or " ".join(
        part for part in (product, sales_attr1, sales_attr2, remark) if part
    )
    missing = [field for field, value in (("product", product), ("quantity", quantity)) if not value]
    original_text = " / ".join(
        part
        for part in (
            product,
            sales_attr1,
            sales_attr2,
            text_value(values.get("quantity")),
            remark,
        )
        if part
    )
    return OrderRowDraft(
        raw_record_id=raw_record_id,
        task_id=task_id,
        parent_label=parent_label,
        child_label=f"{parent_label}-子{child_index}",
        child_index=child_index,
        child_count=child_count,
        source_component=source_component,
        source_index=source_index,
        product=product,
        sales_attr1=sales_attr1,
        sales_attr2=sales_attr2,
        quantity=quantity,
        remark=remark,
        image_match_text=image_match_text,
        original_text=original_text,
        status="needs_review" if missing else "draft",
        review_reason=f"missing_{'_'.join(missing)}" if missing else "",
        source_trace=traces,
    )


def structured_parent(
    payload: dict[str, Any],
    profile: dict[str, Any],
    *,
    raw_record_id: int,
    task_id: int | None,
    source_component: str,
    source_index: str,
    parent_sequence: int,
) -> ParentWaybillDraft:
    parent_label = business_parent_label(source_index, raw_record_id, parent_sequence=parent_sequence)
    items = [
        (item, path)
        for item, path in values_at_structured_path(payload, profile["items_path"])
        if isinstance(item, dict)
    ]
    rows: list[OrderRowDraft] = []
    defaults = profile.get("defaults") if isinstance(profile.get("defaults"), dict) else {}
    fields = profile["fields"]
    for index, (item, item_path) in enumerate(items, start=1):
        values = dict(defaults)
        traces: dict[str, str] = {"item": item_path}
        for field, path in fields.items():
            value, resolved = relative_field_value(item, path)
            values[field] = value
            traces[field] = f"{item_path}.{resolved}" if resolved else item_path
        for step_index, step in enumerate(profile.get("steps", [])):
            apply_text_step(values, step)
            for target in step.get("targets") or [step.get("target")]:
                if target in ROW_FIELDS:
                    traces[target] = f"{item_path}#step[{step_index}]"
        rows.append(
            row_from_values(
                values,
                traces,
                raw_record_id=raw_record_id,
                task_id=task_id,
                parent_label=parent_label,
                source_component=source_component,
                source_index=source_index,
                child_index=index,
                child_count=len(items),
            )
        )
    return ParentWaybillDraft(
        raw_record_id=raw_record_id,
        task_id=task_id,
        parent_label=parent_label,
        source_component=source_component,
        source_index=source_index,
        child_count=len(rows),
        rows=rows,
    )


def apply_text_step(state: dict[str, Any], step: dict[str, Any]) -> None:
    op = step["op"]
    if op in {"split", "rsplit"}:
        source = text_value(state.get(step.get("source", "text")))
        delimiter = step["delimiter"]
        targets = step["targets"]
        parts = (
            source.rsplit(delimiter, len(targets) - 1)
            if op == "rsplit"
            else source.split(delimiter, len(targets) - 1)
        )
        for index, target in enumerate(targets):
            state[target] = parts[index].strip() if index < len(parts) else ""
        return
    target = step["target"]
    source_field = step.get("source", target)
    value = text_value(state.get(source_field))
    if op == "extract_between":
        start = step["start"]
        end = step["end"]
        start_index = value.find(start)
        end_index = value.find(end, start_index + len(start)) if start_index >= 0 else -1
        if start_index >= 0 and end_index >= 0:
            extracted_start = start_index if step.get("include_delimiters") else start_index + len(start)
            extracted_end = end_index + len(end) if step.get("include_delimiters") else end_index
            extracted = value[extracted_start:extracted_end].strip()
        else:
            extracted = ""
        state[target] = extracted
        if extracted and step.get("consume"):
            state[source_field] = (value[:start_index] + value[end_index + len(end) :]).strip()
    elif op == "trim":
        chars = step.get("chars")
        state[target] = value.strip(chars) if isinstance(chars, str) else value.strip()
    elif op == "strip_prefix":
        literal = step["literal"]
        state[target] = value[len(literal) :].strip() if value.startswith(literal) else value
    elif op == "strip_suffix":
        literal = step["literal"]
        state[target] = value[: -len(literal)].strip() if value.endswith(literal) else value
    elif op == "collapse_whitespace":
        state[target] = " ".join(value.split())
    elif op == "to_positive_int":
        state[target] = positive_int(value)


def text_parent(
    payload: dict[str, Any],
    profile: dict[str, Any],
    *,
    raw_record_id: int,
    task_id: int | None,
    source_component: str,
    source_index: str,
    parent_sequence: int,
) -> ParentWaybillDraft:
    parent_label = business_parent_label(source_index, raw_record_id, parent_sequence=parent_sequence)
    text_values = text_profile_input_values(payload, profile)
    items: list[tuple[str, str]] = []
    item_split = profile.get("item_split")
    for value, path in text_values:
        parts = value.split(item_split) if isinstance(item_split, str) else [value]
        items.extend((part.strip(), path) for part in parts if part.strip())
    rows: list[OrderRowDraft] = []
    defaults = profile.get("defaults") if isinstance(profile.get("defaults"), dict) else {}
    for index, (item, path) in enumerate(items, start=1):
        state: dict[str, Any] = {**defaults, "text": item}
        traces: dict[str, str] = {}
        for step_index, step in enumerate(profile["steps"]):
            apply_text_step(state, step)
            targets = step.get("targets") or [step.get("target")]
            for target in targets:
                if target in ROW_FIELDS:
                    traces[target] = f"{path}#step[{step_index}]"
        rows.append(
            row_from_values(
                state,
                traces,
                raw_record_id=raw_record_id,
                task_id=task_id,
                parent_label=parent_label,
                source_component=source_component,
                source_index=source_index,
                child_index=index,
                child_count=len(items),
            )
        )
    return ParentWaybillDraft(
        raw_record_id=raw_record_id,
        task_id=task_id,
        parent_label=parent_label,
        source_component=source_component,
        source_index=source_index,
        child_count=len(rows),
        rows=rows,
    )


def projection_source_path(source_path: str) -> str:
    xml_text = re.search(r"(\.text\[\d+\])$", source_path)
    suffix = xml_text.group(1) if xml_text else ""
    base = source_path[: xml_text.start()] if xml_text else source_path
    return f"{ARRAY_INDEX_PATTERN.sub('[]', base)}{suffix}"


def projection_grammar_signature(evidence: dict[str, Any]) -> str:
    occurrences: dict[tuple[str, str], int] = {}
    structure: list[tuple[str, str, int, str]] = []
    for span in evidence["spans"]:
        if span["token_class"] != "text":
            continue
        source_path = projection_source_path(str(span["source_path"]))
        token_class = str(span["token_class"])
        key = (source_path, token_class)
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        structure.append(
            (
                source_path,
                token_class,
                occurrence,
                grammar_signature_for_texts([str(span["original_text"])]),
            )
        )
    encoded = json.dumps(
        structure,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"grammar-v1:sha256:{sha256(encoded).hexdigest()}"


def apply_projection_operations(
    value: str,
    operations: list[dict[str, Any]],
) -> str:
    result = compact_spaces(value)
    for operation in operations:
        op = operation["op"]
        if op == "collapse_adjacent_delimiters":
            result = ADJACENT_DELIMITERS_PATTERN.sub(r"\1", result)
        elif op == "extract_quantity":
            quantity = quantity_from_text(result)
            result = str(quantity) if quantity is not None else ""
        elif op == "split_part":
            parts = result.split(operation["delimiter"])
            index = operation["index"]
            result = parts[index].strip() if index < len(parts) else ""
        elif op == "strip_field_label":
            result = remove_field_label(result)
        elif op == "strip_trailing_quantity":
            result = strip_trailing_quantity_text(result)[0]
    return compact_spaces(result)


def source_projection_parent(
    payload: dict[str, Any],
    profile: dict[str, Any],
    *,
    raw_record_id: int,
    task_id: int | None,
    source_component: str,
    source_index: str,
    parent_sequence: int,
) -> ParentWaybillDraft:
    parent_label = business_parent_label(
        source_index,
        raw_record_id,
        parent_sequence=parent_sequence,
    )
    evidence = build_evidence(
        payload,
        source_component,
        profile.get("selected_fields"),
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for span in evidence["spans"]:
        key = (
            projection_source_path(str(span["source_path"])),
            str(span["token_class"]),
        )
        grouped.setdefault(key, []).append(span)

    rows: list[OrderRowDraft] = []
    row_specs = profile["rows"]
    for row_index, field_specs in enumerate(row_specs, start=1):
        values: dict[str, Any] = {}
        traces: dict[str, str] = {}
        for field, parts in field_specs.items():
            resolved_values: list[str] = []
            resolved_paths: list[str] = []
            for part in parts:
                candidates = grouped.get(
                    (part["source_path"], part["token_class"]),
                    [],
                )
                occurrence = part["occurrence"]
                if occurrence >= len(candidates):
                    resolved_values = []
                    resolved_paths = []
                    break
                span = candidates[occurrence]
                resolved_values.append(
                    apply_projection_operations(
                        str(span["original_text"]),
                        part.get("operations", []),
                    )
                )
                resolved_paths.append(
                    f"{span['source_path']}#{span['token_class']}[{occurrence}]"
                    + (
                        "#"
                        + ">".join(
                            str(operation["op"])
                            for operation in part.get("operations", [])
                        )
                        if part.get("operations")
                        else ""
                    )
                )
            values[field] = " ".join(
                value for value in resolved_values if value
            )
            if resolved_paths:
                traces[field] = " + ".join(resolved_paths)
        rows.append(
            row_from_values(
                values,
                traces,
                raw_record_id=raw_record_id,
                task_id=task_id,
                parent_label=parent_label,
                source_component=source_component,
                source_index=source_index,
                child_index=row_index,
                child_count=len(row_specs),
            )
        )
    return ParentWaybillDraft(
        raw_record_id=raw_record_id,
        task_id=task_id,
        parent_label=parent_label,
        source_component=source_component,
        source_index=source_index,
        child_count=len(rows),
        rows=rows,
    )


def check_parent_completeness(parent: ParentWaybillDraft) -> tuple[bool, list[str]]:
    if not parent.rows:
        return False, ["missing_order_rows"]
    reasons: list[str] = []
    for row in parent.rows:
        if not row.product:
            reasons.append("missing_product")
        if row.quantity is None or row.quantity <= 0:
            reasons.append("missing_quantity")
        if "\n" in row.product or "\r" in row.product:
            reasons.append("multiple_products_collapsed")
        if QUANTITY_MARKER_PATTERN.search(row.sales_attr1) or QUANTITY_MARKER_PATTERN.search(row.sales_attr2):
            reasons.append("quantity_marker_in_sales_attribute")
    return not reasons, list(dict.fromkeys(reasons))


def parse_with_format_profile(
    payload: dict[str, Any],
    profile: dict[str, Any],
    *,
    raw_record_id: int,
    task_id: int | None,
    source_component: str | None,
    source_index: str | None,
    parent_sequence: int,
) -> ParentWaybillDraft:
    kwargs = {
        "raw_record_id": raw_record_id,
        "task_id": task_id,
        "source_component": text_value(source_component),
        "source_index": text_value(source_index),
        "parent_sequence": parent_sequence,
    }
    if profile["strategy"] == "structured_items_v1":
        return structured_parent(payload, profile, **kwargs)
    if profile["strategy"] == "text_pipeline_v1":
        return text_parent(payload, profile, **kwargs)
    return source_projection_parent(payload, profile, **kwargs)


def parse_declarative_payload(
    payload: dict[str, Any],
    profiles: list[dict[str, Any]],
    *,
    raw_record_id: int,
    task_id: int | None,
    source_component: str | None,
    source_index: str | None,
    parent_sequence: int,
    fingerprint_strategy: str = "legacy_structure_v1",
    rule_pack_code: str = "",
    rule_pack_version: str = "",
) -> tuple[ParentWaybillDraft, dict[str, Any]]:
    fingerprint = fingerprint_for_payload(payload, text_value(source_component), fingerprint_strategy)
    candidates = [
        profile
        for profile in profiles
        if profile.get("fingerprint") == fingerprint
    ]
    projection_signatures = {
        tuple(profile.get("selected_fields") or ()): projection_grammar_signature(
            build_evidence(
                payload,
                text_value(source_component),
                profile.get("selected_fields"),
            )
        )
        for profile in candidates
        if profile.get("strategy") == "source_projection_v1"
    }
    compatible = [
        candidate
        for candidate in candidates
        if (
            candidate.get("strategy") == "structured_items_v1"
            or not candidate.get("grammar_signature")
            or (
                candidate.get("strategy") == "text_pipeline_v1"
                and candidate.get("grammar_signature")
                == text_profile_grammar_signature(payload, candidate)
            )
            or (
                candidate.get("strategy") == "source_projection_v1"
                and candidate.get("grammar_signature")
                == projection_signatures.get(
                    tuple(candidate.get("selected_fields") or ())
                )
            )
        )
    ]

    def empty_result(reason: str, reasons: list[str] | None = None):
        parent_label = business_parent_label(source_index, raw_record_id, parent_sequence=parent_sequence)
        parent = ParentWaybillDraft(
            raw_record_id=raw_record_id,
            task_id=task_id,
            parent_label=parent_label,
            source_component=text_value(source_component),
            source_index=text_value(source_index),
            child_count=0,
            rows=[],
        )
        diagnostic = {
            "raw_record_id": raw_record_id,
            "parent_label": parent_label,
            "fingerprint": fingerprint,
            "reason": reason,
        }
        if reasons:
            diagnostic["reasons"] = reasons
        return parent, diagnostic

    if not compatible:
        return empty_result("format_profile_missing")

    matches: dict[tuple[tuple[Any, ...], ...], tuple[dict[str, Any], ParentWaybillDraft]] = {}
    incomplete_reasons: list[str] = []
    for candidate in compatible:
        candidate_parent = parse_with_format_profile(
            payload,
            candidate,
            raw_record_id=raw_record_id,
            task_id=task_id,
            source_component=source_component,
            source_index=source_index,
            parent_sequence=parent_sequence,
        )
        complete, reasons = check_parent_completeness(candidate_parent)
        if not complete:
            incomplete_reasons.extend(reasons)
            continue
        business_rows = tuple(
            (
                row.product,
                row.sales_attr1,
                row.sales_attr2,
                row.quantity,
                row.remark,
                row.image_match_text,
            )
            for row in candidate_parent.rows
        )
        matches[business_rows] = (candidate, candidate_parent)

    if not matches:
        reasons = list(dict.fromkeys(incomplete_reasons)) or ["missing_order_rows"]
        return empty_result(
            reasons[0],
            reasons,
        )
    if len(matches) > 1:
        return empty_result("profile_ambiguous")

    profile, parent = next(iter(matches.values()))
    provenance = (
        profile.get("provenance")
        if isinstance(profile.get("provenance"), dict)
        else {}
    )
    compiled_rule = {
        "source": text_value(provenance.get("source")) or "declarative_rule",
        **(
            {"learning_session_id": text_value(provenance.get("learning_session_id"))}
            if text_value(provenance.get("learning_session_id"))
            else {}
        ),
        "rule_pack_code": text_value(rule_pack_code),
        "rule_pack_version": text_value(rule_pack_version),
        "fingerprint": fingerprint,
        "grammar_signature": text_value(profile.get("grammar_signature")),
        "strategy": text_value(profile.get("strategy")),
        "ai_call_count": 0,
    }
    parent = replace(
        parent,
        rows=[
            replace(
                row,
                source_trace={
                    **(row.source_trace or {}),
                    "compiled_rule": compiled_rule,
                },
            )
            for row in parent.rows
        ],
    )
    complete, reasons = check_parent_completeness(parent)
    return parent, {
        "raw_record_id": raw_record_id,
        "parent_label": parent.parent_label,
        "fingerprint": fingerprint,
        "compiled_rule": compiled_rule,
        "reason": "" if complete else reasons[0],
        "reasons": reasons,
    }
