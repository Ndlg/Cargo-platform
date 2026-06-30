from __future__ import annotations

from typing import Any

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
    return errors


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
        "warnings": [],
        "pack": rule_pack_summary(payload.rule_pack),
    }


@app.post("/api/v1/rule-packs/explain")
def explain_rule_pack(payload: RulePackRequest) -> dict[str, Any]:
    errors = rule_pack_validation_errors(payload.rule_pack)
    parser_policy = payload.rule_pack.get("parser_policy") if isinstance(payload.rule_pack, dict) else {}
    order_row_parser = (
        str(parser_policy.get("order_row_parser") or "").strip() if isinstance(parser_policy, dict) else ""
    )
    capabilities = [
        "requires active rule pack"
        if isinstance(parser_policy, dict) and parser_policy.get("requires_active_rule_pack")
        else "active rule pack optional",
        "shoe waybill order-row parser"
        if order_row_parser == "shoe_waybill_v1"
        else "no order-row parser configured",
        "special waybill policy"
        if isinstance(parser_policy, dict) and parser_policy.get("special_text_keywords")
        else "no special waybill policy",
        "quantity normalization"
        if isinstance(parser_policy, dict) and parser_policy.get("quantity")
        else "default quantity behavior",
    ]
    return {
        "contract_version": RECOGNITION_RULE_PACK_CONTRACT_VERSION,
        "status": "invalid" if errors else "valid",
        "errors": errors,
        "pack": rule_pack_summary(payload.rule_pack),
        "capabilities": capabilities,
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
    raw_parent_offset = len(parents)
    for index, record in enumerate(payload.raw_records, start=1):
        parents.append(
            draft_rows_from_payload(
                record.payload,
                raw_record_id=record.raw_record_id,
                task_id=record.task_id,
                source_component=record.source_component,
                source_index=record.source_index,
                parent_sequence=record.parent_sequence or raw_parent_offset + index,
            )
        )

    return {
        "contract_version": ORDER_ROW_DRAFTS_CONTRACT_VERSION,
        "task_id": payload.task_id,
        "summary": order_row_draft_summary(parents),
        "parents": [parent.as_dict() for parent in parents],
        "rows": [row.as_dict() for parent in parents for row in parent.rows],
    }
