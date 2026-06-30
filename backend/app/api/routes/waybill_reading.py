from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id
from app.models import RawCaptureRecord, StandardDetail
from app.services.waybill_reading import (
    TEXT_BLOCK_CONTRACT_FIELDS,
    WAYBILL_READING_CONTRACT_VERSION,
    empty_waybill_reading_diagnostic,
    read_waybill_samples,
)


router = APIRouter(prefix="/waybill-reading", tags=["waybill-reading"])


class WaybillTextBlockResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    block_id: str
    text: str
    source: str
    block_kind: Literal["original", "derived_child"]
    line_index: int
    order: int
    raw_record_id: int
    trace: dict[str, Any]
    source_path: str | None = None
    document_id: str | None = None
    document_sequence: int | None = None
    parent_block_id: str | None = None
    parent_text: str | None = None
    split_reason: str | None = None


class HiddenRawFieldResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str
    source: Literal["raw_field"]
    source_path: str
    filter_reason: str
    document_sequence: int | None = None
    document_id: str | None = None


class WaybillReadingSampleResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    sample_id: str
    raw_record_id: int
    task_id: int | None = None
    document_id: str | None = None
    document_sequence: int | None = None
    record_order: int | None = None
    sample_order: int | None = None
    source_component: str | None = None
    source_index: str | None = None
    payload_format: str | None = None
    parse_status: Literal["readable"]
    warnings: list[str]
    sample_text: str
    text_blocks: list[WaybillTextBlockResponse]
    hidden_raw_fields: list[HiddenRawFieldResponse]


class WaybillReadingDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    raw_record_id: int
    task_id: int | None = None
    document_id: str | None = None
    source_component: str | None = None
    source_index: str | None = None
    payload_format: str | None = None
    parse_status: Literal["empty"]
    empty_reason: str
    warnings: list[str]
    record_order: int | None = None


class WaybillReadingBatchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    bulk_supported: bool
    record_count: int
    loaded_record_count: int
    total_record_count: int
    sample_count: int
    loaded_sample_count: int
    total_sample_count: int
    total_sample_count_exact: bool
    total_sample_count_note: str | None = None
    limit: int
    offset: int
    has_more_records: bool
    ordered_by: list[str]
    scope: Literal["single_record", "task"]
    suggestion_groups: list[dict[str, Any]]
    suggestion_policy: str


class WaybillReadingSamplesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str
    batch: WaybillReadingBatchResponse
    input_contract: dict[str, Any]
    output_contract: dict[str, Any]
    diagnostics: list[WaybillReadingDiagnosticResponse]
    samples: list[WaybillReadingSampleResponse]


def raw_record_query(
    *,
    workspace_id: int,
    raw_record_id: int | None = None,
    task_id: int | None = None,
) -> Any:
    statement = select(RawCaptureRecord).where(
        RawCaptureRecord.workspace_id == workspace_id,
        RawCaptureRecord.is_deleted.is_(False),
        RawCaptureRecord.archived_at.is_(None),
    )
    if raw_record_id is not None:
        statement = statement.where(RawCaptureRecord.id == raw_record_id)
    if task_id is not None:
        statement = statement.where(RawCaptureRecord.task_id == task_id)
    return statement.order_by(RawCaptureRecord.id.asc())


def raw_record_id_from_standard_detail(
    db: Session,
    *,
    workspace_id: int,
    standard_detail_id: int,
) -> int:
    detail = db.scalars(
        select(StandardDetail).where(
            StandardDetail.id == standard_detail_id,
            StandardDetail.workspace_id == workspace_id,
            StandardDetail.is_deleted.is_(False),
            StandardDetail.archived_at.is_(None),
        )
    ).first()
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Standard detail not found.")

    values = detail.field_values if isinstance(detail.field_values, dict) else {}
    raw_record_id = values.get("raw_record_id")
    if not isinstance(raw_record_id, int):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Standard detail does not reference a raw capture record.",
        )
    return raw_record_id


@router.get("/samples", response_model=WaybillReadingSamplesResponse)
def list_waybill_reading_samples(
    raw_record_id: int | None = Query(default=None, ge=1),
    task_id: int | None = Query(default=None, ge=1),
    standard_detail_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _current_user: Any = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    if sum(value is not None for value in (raw_record_id, task_id, standard_detail_id)) != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provide exactly one of raw_record_id, task_id, or standard_detail_id.",
        )
    if offset and task_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="offset is only supported when querying by task_id.",
        )

    if standard_detail_id is not None:
        raw_record_id = raw_record_id_from_standard_detail(
            db,
            workspace_id=workspace_id,
            standard_detail_id=standard_detail_id,
        )

    record_statement = raw_record_query(
        workspace_id=workspace_id,
        raw_record_id=raw_record_id,
        task_id=task_id,
    )
    total_record_count = db.scalar(select(func.count()).select_from(record_statement.subquery())) or 0
    records = db.scalars(record_statement.offset(offset).limit(limit)).all()
    if raw_record_id is not None and not records:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Raw capture record not found.")

    samples: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for record_index, record in enumerate(records):
        record_order = offset + record_index
        record_samples = read_waybill_samples(record)
        if not record_samples:
            diagnostics.append(
                {
                    **empty_waybill_reading_diagnostic(record),
                    "record_order": record_order,
                }
            )
        for sample in record_samples:
            samples.append(
                {
                    **sample,
                    "record_order": record_order,
                    "sample_order": len(samples),
                }
            )
    has_more_records = offset + len(records) < total_record_count
    total_sample_count_exact = not has_more_records and offset == 0
    total_sample_count = len(samples)
    total_sample_count_note = None
    if not total_sample_count_exact:
        total_sample_count_note = "paginated_response_reports_loaded_sample_count_only_to_avoid_full_task_parse"

    return {
        "contract_version": WAYBILL_READING_CONTRACT_VERSION,
        "batch": {
            "bulk_supported": True,
            "record_count": len(records),
            "loaded_record_count": len(records),
            "total_record_count": total_record_count,
            "sample_count": len(samples),
            "loaded_sample_count": len(samples),
            "total_sample_count": total_sample_count,
            "total_sample_count_exact": total_sample_count_exact,
            "total_sample_count_note": total_sample_count_note,
            "limit": limit,
            "offset": offset,
            "has_more_records": has_more_records,
            "ordered_by": ["raw_capture_records.id", "document_sequence", "text_blocks.order"],
            "scope": "task" if task_id is not None else "single_record",
            "suggestion_groups": [],
            "suggestion_policy": "suggestion_only_empty_by_default",
        },
        "input_contract": {
            "module": "collection",
            "resource": "raw_capture_record",
            "query": ["raw_record_id", "task_id", "standard_detail_id", "limit", "offset"],
        },
        "output_contract": {
            "module": "waybill_reading",
            "sample": "waybill_sample",
            "text_block_fields": list(TEXT_BLOCK_CONTRACT_FIELDS),
            "text_block_role": "selectable_text_only",
            "sample_status_fields": ["parse_status", "warnings"],
            "diagnostics": "records_without_readable_text_are_reported_without_creating_field_rules",
            "original_blocks_preserved": True,
            "derived_child_blocks": "auxiliary_selectable_candidates_only",
            "child_blocks_replace_parent": False,
            "hidden_raw_fields": "filtered_metadata_not_selectable_text_blocks",
            "consumer": "order_row_mapping",
            "bulk_consumer_rule": "bulk_mapping_must_use_user_confirmed_type_or_scope_before_apply",
            "automatic_grouping": False,
            "automatic_template_detection": False,
            "automatic_field_mapping": False,
            "similarity_policy": "suggestion_only_not_used_by_this_endpoint",
        },
        "diagnostics": diagnostics,
        "samples": samples,
    }
