from __future__ import annotations

import json
import os
import re
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from service_app.declarative_rules import (
    parse_declarative_payload,
    validate_format_profiles,
)
from service_app.evidence import build_evidence
from service_app.order_row_engine import (
    ORDER_ROW_DRAFTS_CONTRACT_VERSION,
    ParentWaybillDraft,
    business_parent_label,
    draft_rows_from_payload,
    draft_rows_from_standard_detail_values,
    draft_rows_from_waybill_sample,
    order_row_draft_summary,
)
from service_app.rule_synthesizer import synthesize_rule as synthesize_waybill_rule
from services.shared.waybill_fingerprint import fingerprint_catalog, inspect_fingerprint


app = FastAPI(
    title="Cargo Platform Waybill Parser",
    version=os.getenv("APP_VERSION", "1.0.0-rc.1"),
)

RECOGNITION_RULE_PACK_CONTRACT_VERSION = "recognition_rule_pack_v1"
SUPPORTED_ORDER_ROW_PARSERS = {"declarative_v1", "shoe_waybill_v1"}
SUPPORTED_FINGERPRINT_STRATEGIES = {"legacy_structure_v1", "business_shape_v2"}
STRUCTURED_ITEM_FIELD_LISTS = ("product_fields", "spec_fields", "quantity_fields", "remark_fields")
STRUCTURED_ITEM_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\[\])?(?:\.[A-Za-z0-9_]+(?:\[\])?)*$")
SELECTED_PARSER_POLICY_FIELDS = {"order_row_parser"}
APPLIED_PARSER_POLICY_FIELDS = {
    "label_cleanup",
    "manual_label_only",
    "multi_item",
    "non_shoe",
    "quantity",
    "requires_active_rule_pack",
    "size_normalization",
    "special_text_keywords",
    "format_profiles",
    "fingerprint_strategy",
    "structured_item_sources",
}
REQUIRED_MULTI_ITEM_FIELDS = (
    "split_parent_waybill",
    "preserve_parent_text",
    "pair_product_lines_with_labeled_attr_lines",
    "output_item_rows",
)
class StandardDetailParseInput(BaseModel):
    standard_detail_id: int
    parent_sequence: int
    field_values: dict[str, Any] = Field(default_factory=dict)


class RawRecordParseInput(BaseModel):
    raw_record_id: int
    task_id: int | None = None
    parent_sequence: int | None = None
    document_sequence: int = Field(default=1, ge=1)
    source_component: str | None = None
    source_index: str | None = None
    assignment_warning: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WaybillSampleParseInput(BaseModel):
    raw_record_id: int
    task_id: int | None = None
    parent_sequence: int
    document_id: str | None = None
    document_sequence: int | None = None
    source_component: str | None = None
    source_index: str | None = None
    sample_text: str = ""
    text_blocks: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BatchParseRequest(BaseModel):
    task_id: int | None = None
    standard_details: list[StandardDetailParseInput] = Field(default_factory=list)
    raw_records: list[RawRecordParseInput] = Field(default_factory=list)
    waybill_samples: list[WaybillSampleParseInput] = Field(default_factory=list)
    rule_pack: dict[str, Any] | None = None


class RulePackRequest(BaseModel):
    rule_pack: dict[str, Any] | None = None


class AnalyzeRequest(BaseModel):
    raw_payload: dict[str, Any]
    source_component: str
    selected_fields: list[str] = Field(default_factory=list)


class RuleSynthesisRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=512)
    sales_attr1: str = Field(default="", max_length=512)
    sales_attr2: str = Field(default="", max_length=512)
    quantity: int = Field(ge=1, le=100_000)
    remark: str = Field(default="", max_length=1000)


class RuleSynthesisGoldSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_payload: dict[str, Any]
    source_component: str | None = Field(default=None, min_length=1, max_length=128)
    rows: list[RuleSynthesisRow] = Field(min_length=1, max_length=100)


class RuleSynthesisNegativeSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_payload: dict[str, Any]
    source_component: str | None = Field(default=None, min_length=1, max_length=128)


def _json_depth_within_limit(value: Any, limit: int = 64) -> bool:
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > limit:
            return False
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return True


class RuleSynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_payload: dict[str, Any]
    source_component: str = Field(min_length=1, max_length=128)
    corrected_rows: list[RuleSynthesisRow] = Field(min_length=1, max_length=100)
    gold_samples: list[RuleSynthesisGoldSample] = Field(default_factory=list, max_length=100)
    negative_samples: list[RuleSynthesisNegativeSample] = Field(
        default_factory=list,
        max_length=100,
    )
    selected_fields: list[str] | None = None
    expected_evidence_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_input_size(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and (
                not _json_depth_within_limit(value)
                or len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 2_000_000
            )
        ):
            raise HTTPException(
                status_code=422,
                detail="Rule synthesis input exceeds size or depth limit.",
            )
        return value


def rule_pack_validation_errors(rule_pack: dict[str, Any] | None) -> list[str]:
    if not isinstance(rule_pack, dict):
        return ["rule_pack"]

    errors: list[str] = []
    if rule_pack.get("contract_version") != RECOGNITION_RULE_PACK_CONTRACT_VERSION:
        errors.append("contract_version")

    pack = rule_pack.get("pack")
    if not isinstance(pack, dict):
        return [*errors, "pack"]

    for field in ("code", "name", "version"):
        if not str(pack.get(field) or "").strip():
            errors.append(f"pack.{field}")

    parser_policy = rule_pack.get("parser_policy")
    if not isinstance(parser_policy, dict):
        errors.append("parser_policy")
    else:
        order_row_parser = str(parser_policy.get("order_row_parser") or "").strip()
        if not order_row_parser or order_row_parser not in SUPPORTED_ORDER_ROW_PARSERS:
            errors.append("parser_policy.order_row_parser")
        if order_row_parser == "declarative_v1":
            errors.extend(validate_format_profiles(parser_policy.get("format_profiles")))
        fingerprint_strategy = parser_policy.get("fingerprint_strategy", "legacy_structure_v1")
        if not isinstance(fingerprint_strategy, str) or fingerprint_strategy not in SUPPORTED_FINGERPRINT_STRATEGIES:
            errors.append("parser_policy.fingerprint_strategy")
        if (
            "requires_active_rule_pack" in parser_policy
            and parser_policy.get("requires_active_rule_pack") is not True
        ):
            errors.append("parser_policy.requires_active_rule_pack")
        multi_item = parser_policy.get("multi_item")
        if multi_item is not None:
            if not isinstance(multi_item, dict):
                errors.append("parser_policy.multi_item")
            else:
                for field in REQUIRED_MULTI_ITEM_FIELDS:
                    if multi_item.get(field) is not True:
                        errors.append(f"parser_policy.multi_item.{field}")
        structured_sources = parser_policy.get("structured_item_sources")
        if structured_sources is not None:
            if not isinstance(structured_sources, list):
                errors.append("parser_policy.structured_item_sources")
            else:
                for index, source in enumerate(structured_sources):
                    prefix = f"parser_policy.structured_item_sources[{index}]"
                    if not isinstance(source, dict):
                        errors.append(prefix)
                        continue
                    if not str(source.get("name") or "").strip():
                        errors.append(f"{prefix}.name")
                    items_path = str(source.get("items_path") or "").strip()
                    if not items_path or not STRUCTURED_ITEM_PATH_PATTERN.fullmatch(items_path) or not items_path.endswith("[]"):
                        errors.append(f"{prefix}.items_path")
                    for field_name in STRUCTURED_ITEM_FIELD_LISTS:
                        field_names = source.get(field_name)
                        if not isinstance(field_names, list) or not all(
                            isinstance(field, str) and field.strip() for field in field_names
                        ):
                            errors.append(f"{prefix}.{field_name}")
        special_rules = parser_policy.get("special_text_keywords")
        if special_rules is not None:
            if not isinstance(special_rules, list):
                errors.append("parser_policy.special_text_keywords")
            else:
                for index, rule in enumerate(special_rules):
                    prefix = f"parser_policy.special_text_keywords[{index}]"
                    if not isinstance(rule, dict):
                        errors.append(prefix)
                        continue
                    if not str(rule.get("keyword") or "").strip():
                        errors.append(f"{prefix}.keyword")
                    if rule.get("status") != "special":
                        errors.append(f"{prefix}.status")
                    if not str(rule.get("reason") or "").strip():
                        errors.append(f"{prefix}.reason")
        quantity = parser_policy.get("quantity")
        if quantity is not None:
            if not isinstance(quantity, dict):
                errors.append("parser_policy.quantity")
            else:
                default_quantity = quantity.get("default_if_missing")
                if (
                    not isinstance(default_quantity, int)
                    or isinstance(default_quantity, bool)
                    or default_quantity <= 0
                ):
                    errors.append("parser_policy.quantity.default_if_missing")
        label_cleanup = parser_policy.get("label_cleanup")
        if label_cleanup is not None:
            if not isinstance(label_cleanup, dict):
                errors.append("parser_policy.label_cleanup")
            else:
                for field in ("strip_prefixes", "separator_chars"):
                    values = label_cleanup.get(field)
                    if not isinstance(values, list) or not all(
                        isinstance(value, str) and value.strip() for value in values
                    ):
                        errors.append(f"parser_policy.label_cleanup.{field}")
        size_normalization = parser_policy.get("size_normalization")
        if size_normalization is not None:
            if not isinstance(size_normalization, dict):
                errors.append("parser_policy.size_normalization")
            else:
                for field in ("enabled", "strip_purchase_hint"):
                    if not isinstance(size_normalization.get(field), bool):
                        errors.append(f"parser_policy.size_normalization.{field}")
        manual_label_only = parser_policy.get("manual_label_only")
        if manual_label_only is not None:
            if not isinstance(manual_label_only, dict):
                errors.append("parser_policy.manual_label_only")
            else:
                if not isinstance(manual_label_only.get("allow_empty_product"), bool):
                    errors.append("parser_policy.manual_label_only.allow_empty_product")
                default_quantity = manual_label_only.get("default_quantity_if_missing")
                if (
                    not isinstance(default_quantity, int)
                    or isinstance(default_quantity, bool)
                    or default_quantity <= 0
                ):
                    errors.append("parser_policy.manual_label_only.default_quantity_if_missing")
        non_shoe = parser_policy.get("non_shoe")
        if non_shoe is not None:
            if not isinstance(non_shoe, dict):
                errors.append("parser_policy.non_shoe")
            elif not isinstance(non_shoe.get("allow_non_numeric_sales_attr2"), bool):
                errors.append("parser_policy.non_shoe.allow_non_numeric_sales_attr2")
    return errors


def parser_policy_usage(rule_pack: dict[str, Any] | None) -> dict[str, list[str]]:
    parser_policy = rule_pack.get("parser_policy") if isinstance(rule_pack, dict) else None
    configured = set(parser_policy) if isinstance(parser_policy, dict) else set()
    return {
        "selected": sorted(configured & SELECTED_PARSER_POLICY_FIELDS),
        "applied": sorted(configured & APPLIED_PARSER_POLICY_FIELDS),
        "configured_but_not_applied": sorted(
            configured - SELECTED_PARSER_POLICY_FIELDS - APPLIED_PARSER_POLICY_FIELDS
        ),
    }


def rule_pack_validation_warnings(rule_pack: dict[str, Any] | None) -> list[dict[str, str]]:
    return [
        {"code": "policy_field_not_applied", "field": f"parser_policy.{field}"}
        for field in parser_policy_usage(rule_pack)["configured_but_not_applied"]
    ]


def rule_pack_summary(rule_pack: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(rule_pack, dict):
        return None
    pack = rule_pack.get("pack")
    if not isinstance(pack, dict):
        return None
    return {
        "code": str(pack.get("code") or "").strip(),
        "name": str(pack.get("name") or "").strip(),
        "version": str(pack.get("version") or "").strip(),
    }


def empty_parse_summary(input_count: int) -> dict[str, int]:
    return {
        "parent_waybill_count": input_count,
        "child_waybill_count": 0,
        "draft_count": 0,
        "needs_review_count": 0,
        "special_count": 0,
        "pending_rule_pack_count": input_count,
    }


def parse_status_from_diagnostics(diagnostics: list[dict[str, Any]]) -> str:
    reasons = {diagnostic["reason"] for diagnostic in diagnostics if diagnostic["reason"]}
    if "format_profile_missing" in reasons:
        return "format_profile_missing"
    if reasons and reasons <= {"timestamp_invalid_fallback", "source_history_ambiguous"}:
        return "capture_source_exception"
    return "format_profile_incomplete" if reasons else "parsed"


def raw_record_document_payloads(record: RawRecordParseInput) -> list[dict[str, Any]]:
    task = record.payload.get("task") if isinstance(record.payload, dict) else None
    documents = task.get("documents") if isinstance(task, dict) else None
    document_payloads: list[dict[str, Any]] = []
    if isinstance(documents, list) and documents:
        for document in documents:
            if isinstance(document, dict):
                document_payloads.append(
                    {
                        **record.payload,
                        "task": {**task, "documents": [document]},
                    }
                )
    return document_payloads or [record.payload]


def empty_parent(
    *,
    raw_record_id: int,
    task_id: int | None,
    source_component: str | None,
    source_index: str | None,
    parent_sequence: int,
) -> ParentWaybillDraft:
    parent_label = business_parent_label(
        source_index,
        raw_record_id,
        parent_sequence=parent_sequence,
    )
    return ParentWaybillDraft(
        raw_record_id=raw_record_id,
        task_id=task_id,
        parent_label=parent_label,
        source_component=str(source_component or ""),
        source_index=str(source_index or ""),
        child_count=0,
        rows=[],
    )


def quarantined_parent(
    record: RawRecordParseInput,
    parent_sequence: int,
) -> tuple[ParentWaybillDraft, dict[str, Any]]:
    parent = empty_parent(
        raw_record_id=record.raw_record_id,
        task_id=record.task_id,
        source_component=record.source_component,
        source_index=record.source_index,
        parent_sequence=parent_sequence,
    )
    return parent, {
        "raw_record_id": record.raw_record_id,
        "parent_label": parent.parent_label,
        "fingerprint": "",
        "reason": record.assignment_warning,
        "document_sequence": record.document_sequence,
        "parent_sequence": parent_sequence,
    }


def quarantined_parents(
    record: RawRecordParseInput,
    first_parent_sequence: int,
) -> list[tuple[ParentWaybillDraft, dict[str, Any]]]:
    quarantined = []
    for document_offset, _ in enumerate(raw_record_document_payloads(record)):
        parent, diagnostic = quarantined_parent(
            record,
            first_parent_sequence + document_offset,
        )
        diagnostic["document_sequence"] = record.document_sequence + document_offset
        quarantined.append((parent, diagnostic))
    return quarantined


@app.get("/health")
@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": "Cargo Platform Waybill Parser",
        "version": app.version,
        "contract_version": ORDER_ROW_DRAFTS_CONTRACT_VERSION,
    }


@app.get("/api/v1/fingerprints")
def list_fingerprints() -> dict[str, Any]:
    return {
        "contract_version": "waybill_fingerprints_v1",
        "fingerprints": fingerprint_catalog(),
    }


@app.post("/api/v1/fingerprints/inspect")
def inspect_waybill_fingerprint(payload: AnalyzeRequest) -> dict[str, Any]:
    return {
        "contract_version": "waybill_fingerprints_v1",
        "fingerprint": inspect_fingerprint(
            payload.raw_payload,
            payload.source_component,
        ),
    }


@app.post("/api/v1/analyze")
def analyze(payload: AnalyzeRequest) -> dict[str, Any]:
    return build_evidence(
        payload.raw_payload,
        payload.source_component,
        payload.selected_fields or None,
    )


@app.post("/api/v1/rules/synthesize")
def synthesize_rule(payload: RuleSynthesisRequest) -> dict[str, Any]:
    return synthesize_waybill_rule(
        payload=payload.raw_payload,
        source_component=payload.source_component,
        corrected_rows=[row.model_dump() for row in payload.corrected_rows],
        gold_samples=[sample.model_dump() for sample in payload.gold_samples],
        negative_samples=[sample.model_dump() for sample in payload.negative_samples],
        selected_fields=payload.selected_fields,
        expected_evidence_sha256=payload.expected_evidence_sha256,
    )


@app.post("/api/v1/rule-packs/validate")
def validate_rule_pack(payload: RulePackRequest) -> dict[str, Any]:
    errors = rule_pack_validation_errors(payload.rule_pack)
    return {
        "contract_version": RECOGNITION_RULE_PACK_CONTRACT_VERSION,
        "status": "invalid" if errors else "valid",
        "errors": errors,
        "warnings": rule_pack_validation_warnings(payload.rule_pack),
        "pack": rule_pack_summary(payload.rule_pack),
    }


@app.post("/api/v1/rule-packs/explain")
def explain_rule_pack(payload: RulePackRequest) -> dict[str, Any]:
    errors = rule_pack_validation_errors(payload.rule_pack)
    parser_policy = payload.rule_pack.get("parser_policy") if isinstance(payload.rule_pack, dict) else {}
    usage = parser_policy_usage(payload.rule_pack)
    order_row_parser = (
        str(parser_policy.get("order_row_parser") or "").strip() if isinstance(parser_policy, dict) else ""
    )
    parser_capability = {
        "shoe_waybill_v1": "shoe waybill order-row parser",
        "declarative_v1": "declarative format profile parser",
    }.get(order_row_parser, "no order-row parser configured")
    capabilities = [
        "requires active rule pack",
        parser_capability,
        "structured item source rules"
        if "structured_item_sources" in usage["applied"]
        else "no structured item source rules",
        "special waybill keyword rules"
        if "special_text_keywords" in usage["applied"]
        else "no special waybill keyword rules",
        "quantity defaults"
        if "quantity" in usage["applied"]
        else "no quantity defaults",
        "field label cleanup"
        if "label_cleanup" in usage["applied"]
        else "no field label cleanup",
        "size normalization"
        if "size_normalization" in usage["applied"]
        else "no size normalization",
        "manual label-only policy"
        if "manual_label_only" in usage["applied"]
        else "no manual label-only policy",
        "non-shoe attribute policy"
        if "non_shoe" in usage["applied"]
        else "no non-shoe attribute policy",
    ]
    return {
        "contract_version": RECOGNITION_RULE_PACK_CONTRACT_VERSION,
        "status": "invalid" if errors else "valid",
        "errors": errors,
        "warnings": rule_pack_validation_warnings(payload.rule_pack),
        "pack": rule_pack_summary(payload.rule_pack),
        "capabilities": capabilities,
        "policy_usage": usage,
        "business_db_access": False,
        "mutates_platform_data": False,
    }


@app.post("/api/v1/parse/preview")
def parse_preview(payload: BatchParseRequest) -> dict[str, Any]:
    result = parse_batch(payload)
    result["preview"] = True
    result["mutates_platform_data"] = False
    return result


@app.post("/api/v1/parse/batch")
def parse_batch(payload: BatchParseRequest) -> dict[str, Any]:
    input_count = len(payload.standard_details) + len(payload.waybill_samples) + len(payload.raw_records)
    if not payload.rule_pack:
        return {
            "contract_version": ORDER_ROW_DRAFTS_CONTRACT_VERSION,
            "task_id": payload.task_id,
            "status": "rule_pack_missing",
            "rule_pack_required": True,
            "message": "Waybill parser requires an explicit recognition rule pack.",
            "summary": empty_parse_summary(input_count),
            "recognition_rule_pack": None,
            "parents": [],
            "rows": [],
        }

    errors = rule_pack_validation_errors(payload.rule_pack)
    if errors:
        return {
            "contract_version": ORDER_ROW_DRAFTS_CONTRACT_VERSION,
            "task_id": payload.task_id,
            "status": "rule_pack_invalid",
            "rule_pack_required": True,
            "message": "Recognition rule pack cannot be used for parsing until its parser policy is configured.",
            "errors": errors,
            "summary": empty_parse_summary(input_count),
            "recognition_rule_pack": rule_pack_summary(payload.rule_pack),
            "parents": [],
            "rows": [],
        }

    parser_policy = payload.rule_pack.get("parser_policy")
    if parser_policy.get("order_row_parser") == "declarative_v1":
        parents = []
        diagnostics: list[dict[str, Any]] = []
        for detail in payload.standard_details:
            parent_label = business_parent_label(
                None,
                detail.standard_detail_id,
                parent_sequence=detail.parent_sequence,
            )
            parents.append(
                ParentWaybillDraft(
                    raw_record_id=detail.standard_detail_id,
                    task_id=payload.task_id,
                    parent_label=parent_label,
                    source_component="standard_detail",
                    source_index=str(detail.standard_detail_id),
                    child_count=0,
                    rows=[],
                )
            )
            diagnostics.append(
                {
                    "raw_record_id": detail.standard_detail_id,
                    "parent_label": parent_label,
                    "fingerprint": "",
                    "reason": "declarative_raw_payload_required",
                }
            )
        for sample in payload.waybill_samples:
            parent_label = business_parent_label(
                sample.source_index,
                sample.raw_record_id,
                parent_sequence=sample.parent_sequence,
            )
            parents.append(
                ParentWaybillDraft(
                    raw_record_id=sample.raw_record_id,
                    task_id=sample.task_id,
                    parent_label=parent_label,
                    source_component=str(sample.source_component or ""),
                    source_index=str(sample.source_index or ""),
                    child_count=0,
                    rows=[],
                )
            )
            diagnostics.append(
                {
                    "raw_record_id": sample.raw_record_id,
                    "parent_label": parent_label,
                    "fingerprint": "",
                    "reason": "declarative_raw_payload_required",
                }
            )
        next_parent_sequence = len(parents) + 1
        profiles = parser_policy["format_profiles"]
        pack_meta = payload.rule_pack["pack"]
        unresolved_parent_count = 0
        for record in payload.raw_records:
            if record.assignment_warning:
                first_parent_sequence = record.parent_sequence or next_parent_sequence
                quarantined = quarantined_parents(record, first_parent_sequence)
                parents.extend(parent for parent, _ in quarantined)
                diagnostics.extend(diagnostic for _, diagnostic in quarantined)
                unresolved_parent_count += len(quarantined)
                next_parent_sequence = first_parent_sequence + len(quarantined)
                continue
            document_payloads = raw_record_document_payloads(record)

            first_parent_sequence = record.parent_sequence or next_parent_sequence
            for document_offset, document_payload in enumerate(document_payloads):
                parent, diagnostic = parse_declarative_payload(
                    document_payload,
                    profiles,
                    raw_record_id=record.raw_record_id,
                    task_id=record.task_id,
                    source_component=record.source_component,
                    source_index=record.source_index,
                    parent_sequence=first_parent_sequence + document_offset,
                    fingerprint_strategy=parser_policy.get("fingerprint_strategy", "legacy_structure_v1"),
                    rule_pack_code=str(pack_meta.get("code") or ""),
                    rule_pack_version=str(pack_meta.get("version") or ""),
                )
                diagnostic["document_sequence"] = record.document_sequence + document_offset
                diagnostic["parent_sequence"] = first_parent_sequence + document_offset
                if diagnostic["reason"]:
                    parent = empty_parent(
                        raw_record_id=record.raw_record_id,
                        task_id=record.task_id,
                        source_component=record.source_component,
                        source_index=record.source_index,
                        parent_sequence=first_parent_sequence + document_offset,
                    )
                    unresolved_parent_count += 1
                parents.append(parent)
                diagnostics.append(diagnostic)
            next_parent_sequence = first_parent_sequence + len(document_payloads)

        summary = order_row_draft_summary(parents)
        summary["needs_review_count"] += unresolved_parent_count
        return {
            "contract_version": ORDER_ROW_DRAFTS_CONTRACT_VERSION,
            "task_id": payload.task_id,
            "status": parse_status_from_diagnostics(diagnostics),
            "summary": summary,
            "diagnostics": diagnostics,
            "parents": [parent.as_dict() for parent in parents],
            "rows": [row.as_dict() for parent in parents for row in parent.rows],
        }

    parents = [
        draft_rows_from_standard_detail_values(
            detail.field_values,
            standard_detail_id=detail.standard_detail_id,
            parent_sequence=detail.parent_sequence,
            parser_policy=parser_policy,
        )
        for detail in payload.standard_details
    ]
    parents.extend(
        draft_rows_from_waybill_sample(
            sample.model_dump(),
            parent_sequence=sample.parent_sequence,
            parser_policy=parser_policy,
        )
        for sample in payload.waybill_samples
    )
    next_parent_sequence = len(parents) + 1
    diagnostics: list[dict[str, Any]] = []
    unresolved_parent_count = 0
    for record in payload.raw_records:
        if record.assignment_warning:
            first_parent_sequence = record.parent_sequence or next_parent_sequence
            quarantined = quarantined_parents(record, first_parent_sequence)
            parents.extend(parent for parent, _ in quarantined)
            diagnostics.extend(diagnostic for _, diagnostic in quarantined)
            unresolved_parent_count += len(quarantined)
            next_parent_sequence = first_parent_sequence + len(quarantined)
            continue
        document_payloads = raw_record_document_payloads(record)

        first_parent_sequence = record.parent_sequence or next_parent_sequence
        for document_offset, document_payload in enumerate(document_payloads):
            parents.append(
                draft_rows_from_payload(
                    document_payload,
                    raw_record_id=record.raw_record_id,
                    task_id=record.task_id,
                    source_component=record.source_component,
                    source_index=record.source_index,
                    parent_sequence=first_parent_sequence + document_offset,
                    parser_policy=parser_policy,
                )
            )
        next_parent_sequence = first_parent_sequence + len(document_payloads)

    summary = order_row_draft_summary(parents)
    summary["needs_review_count"] += unresolved_parent_count
    return {
        "contract_version": ORDER_ROW_DRAFTS_CONTRACT_VERSION,
        "task_id": payload.task_id,
        "status": parse_status_from_diagnostics(diagnostics),
        "summary": summary,
        "diagnostics": diagnostics,
        "parents": [parent.as_dict() for parent in parents],
        "rows": [row.as_dict() for parent in parents for row in parent.rows],
    }
