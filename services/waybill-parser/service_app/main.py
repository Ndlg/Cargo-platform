from __future__ import annotations

from typing import Any
import re

from fastapi import FastAPI
from pydantic import BaseModel, Field

from service_app.order_row_engine import (
    ORDER_ROW_DRAFTS_CONTRACT_VERSION,
    draft_rows_from_payload,
    draft_rows_from_standard_detail_values,
    draft_rows_from_waybill_sample,
    order_row_draft_summary,
)


app = FastAPI(title="Cargo Platform Waybill Parser", version="0.1.0")

RECOGNITION_RULE_PACK_CONTRACT_VERSION = "recognition_rule_pack_v1"
SUPPORTED_ORDER_ROW_PARSERS = {"shoe_waybill_v1"}
STRUCTURED_ITEM_FIELD_LISTS = ("product_fields", "spec_fields", "quantity_fields", "remark_fields")
STRUCTURED_ITEM_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\[\])?(?:\.[A-Za-z0-9_]+(?:\[\])?)*$")
SELECTED_PARSER_POLICY_FIELDS = {"order_row_parser"}
APPLIED_PARSER_POLICY_FIELDS = {"structured_item_sources"}


class StandardDetailParseInput(BaseModel):
    standard_detail_id: int
    parent_sequence: int
    field_values: dict[str, Any] = Field(default_factory=dict)


class RawRecordParseInput(BaseModel):
    raw_record_id: int
    task_id: int | None = None
    parent_sequence: int | None = None
    source_component: str | None = None
    source_index: str | None = None
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


@app.get("/health")
@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": "Cargo Platform Waybill Parser",
        "contract_version": ORDER_ROW_DRAFTS_CONTRACT_VERSION,
    }


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
    capabilities = [
        "requires active rule pack",
        "shoe waybill order-row parser"
        if order_row_parser == "shoe_waybill_v1"
        else "no order-row parser configured",
        "structured item source rules"
        if "structured_item_sources" in usage["applied"]
        else "no structured item source rules",
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

    parents = [
        draft_rows_from_standard_detail_values(
            detail.field_values,
            standard_detail_id=detail.standard_detail_id,
            parent_sequence=detail.parent_sequence,
        )
        for detail in payload.standard_details
    ]
    parents.extend(
        draft_rows_from_waybill_sample(
            sample.model_dump(),
            parent_sequence=sample.parent_sequence,
        )
        for sample in payload.waybill_samples
    )
    next_parent_sequence = len(parents) + 1
    for record in payload.raw_records:
        task = record.payload.get("task") if isinstance(record.payload, dict) else None
        documents = task.get("documents") if isinstance(task, dict) else None
        document_payloads = []
        if isinstance(documents, list) and documents:
            for document in documents:
                if not isinstance(document, dict):
                    continue
                document_payloads.append(
                    {
                        **record.payload,
                        "task": {**task, "documents": [document]},
                    }
                )
        if not document_payloads:
            document_payloads = [record.payload]

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
                    parser_policy=payload.rule_pack.get("parser_policy"),
                )
            )
        next_parent_sequence = first_parent_sequence + len(document_payloads)

    return {
        "contract_version": ORDER_ROW_DRAFTS_CONTRACT_VERSION,
        "task_id": payload.task_id,
        "summary": order_row_draft_summary(parents),
        "parents": [parent.as_dict() for parent in parents],
        "rows": [row.as_dict() for parent in parents for row in parent.rows],
    }
