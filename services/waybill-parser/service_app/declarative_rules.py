from __future__ import annotations

import re
from typing import Any

from services.shared.waybill_fingerprint import fingerprint_for_payload
from service_app.order_row_engine import (
    OrderRowDraft,
    ParentWaybillDraft,
    business_parent_label,
    print_xml_text,
    text_value,
    values_at_structured_path,
)


PATH_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\[\])?(?:\.[A-Za-z0-9_]+(?:\[\])?)*$")
FIELD_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$")
FINGERPRINT_PATTERN = re.compile(r"^(?:sha256|v2:[A-Za-z0-9_-]+:sha256):[0-9a-f]{64}$")
MAX_QUANTITY = 100_000
ROW_FIELDS = {"product", "sales_attr1", "sales_attr2", "quantity", "remark", "image_match_text"}
STATE_FIELDS = ROW_FIELDS | {"text"}
STRUCTURED_PROFILE_KEYS = {
    "fingerprint",
    "strategy",
    "name",
    "description",
    "items_path",
    "fields",
    "steps",
    "defaults",
}
TEXT_PROFILE_KEYS = {
    "fingerprint",
    "strategy",
    "name",
    "description",
    "text_path",
    "item_split",
    "steps",
    "defaults",
}


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


def validate_structured_profile(profile: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    if set(profile) - STRUCTURED_PROFILE_KEYS:
        errors.append(prefix)
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
        "to_positive_int": {"op", "target"},
    }
    if op not in allowed or set(step) - allowed.get(str(op), set()):
        return [prefix]
    errors: list[str] = []
    source = step.get("source", "text")
    target = step.get("target")
    if op in {"split", "rsplit", "extract_between"} and source not in state_fields:
        errors.append(f"{prefix}.source")
    if op in {"extract_between", "trim", "strip_prefix", "strip_suffix", "to_positive_int"}:
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


def validate_format_profiles(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        return ["parser_policy.format_profiles"]
    errors: list[str] = []
    fingerprints: set[str] = set()
    for index, profile in enumerate(value):
        prefix = f"parser_policy.format_profiles[{index}]"
        if not isinstance(profile, dict):
            errors.append(prefix)
            continue
        fingerprint = text_value(profile.get("fingerprint"))
        if not FINGERPRINT_PATTERN.fullmatch(fingerprint) or fingerprint in fingerprints:
            errors.append(f"{prefix}.fingerprint")
        fingerprints.add(fingerprint)
        strategy = profile.get("strategy")
        if strategy == "structured_items_v1":
            errors.extend(validate_structured_profile(profile, prefix))
        elif strategy == "text_pipeline_v1":
            errors.extend(validate_text_profile(profile, prefix))
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


def pipeline_text_value(value: Any) -> str:
    text = text_value(value)
    return print_xml_text(text) or text


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
    text_values = [
        (text, path)
        for value, path in values_at_structured_path(payload, profile["text_path"])
        if (text := pipeline_text_value(value))
    ]
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


def check_parent_completeness(parent: ParentWaybillDraft) -> tuple[bool, list[str]]:
    if not parent.rows:
        return False, ["missing_order_rows"]
    reasons: list[str] = []
    for row in parent.rows:
        if not row.product:
            reasons.append("missing_product")
        if row.quantity is None or row.quantity <= 0:
            reasons.append("missing_quantity")
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
    return text_parent(payload, profile, **kwargs)


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
) -> tuple[ParentWaybillDraft, dict[str, Any]]:
    fingerprint = fingerprint_for_payload(payload, text_value(source_component), fingerprint_strategy)
    profile = next(
        (profile for profile in profiles if profile.get("fingerprint") == fingerprint),
        None,
    )
    if profile is None:
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
        return parent, {
            "raw_record_id": raw_record_id,
            "parent_label": parent_label,
            "fingerprint": fingerprint,
            "reason": "format_profile_missing",
        }

    parent = parse_with_format_profile(
        payload,
        profile,
        raw_record_id=raw_record_id,
        task_id=task_id,
        source_component=source_component,
        source_index=source_index,
        parent_sequence=parent_sequence,
    )
    complete, reasons = check_parent_completeness(parent)
    return parent, {
        "raw_record_id": raw_record_id,
        "parent_label": parent.parent_label,
        "fingerprint": fingerprint,
        "reason": "" if complete else reasons[0],
        "reasons": reasons,
    }
