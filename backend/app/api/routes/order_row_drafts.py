from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id, require_write
from app.models import RawCaptureRecord
from app.services.order_row_reader import parser_raw_record_inputs, task_order_row_drafts_payload
from app.services.recognition_rule_packs import active_recognition_rule_pack
from app.services.waybill_parser_client import parse_order_row_drafts_with_service
from app.services.waybill_reading import task_documents


router = APIRouter(prefix="/order-row-drafts", tags=["order-row-drafts"])


class ManualAiParseRequest(BaseModel):
    raw_record_id: int = Field(ge=1)
    document_sequence: int = Field(default=1, ge=1)
    parent_sequence: int = Field(ge=1)


@router.get("/tasks/{task_id}")
def list_task_order_row_drafts(
    task_id: int,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _current_user: Any = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    return task_order_row_drafts_payload(
        db,
        workspace_id=workspace_id,
        task_id=task_id,
        limit=limit,
        offset=offset,
    )


@router.post("/tasks/{task_id}/manual-ai")
def start_manual_ai_parse(
    task_id: int,
    request: ManualAiParseRequest,
    db: Session = Depends(get_db),
    _current_user: Any = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    record = db.scalar(
        select(RawCaptureRecord).where(
            RawCaptureRecord.id == request.raw_record_id,
            RawCaptureRecord.task_id == task_id,
            RawCaptureRecord.workspace_id == workspace_id,
            RawCaptureRecord.is_deleted.is_(False),
            RawCaptureRecord.archived_at.is_(None),
        )
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Raw waybill not found.")

    parser_input = parser_raw_record_inputs([record])[0]
    payload = parser_input["payload"]
    documents = task_documents(payload)
    if documents:
        if request.document_sequence > len(documents):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Waybill document not found.")
        task = payload.get("task")
        parser_input["payload"] = {
            **payload,
            "task": {
                **(task if isinstance(task, dict) else {}),
                "documents": [documents[request.document_sequence - 1]],
            },
        }
    elif request.document_sequence != 1:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Waybill document not found.")
    parser_input["parent_sequence"] = request.parent_sequence

    active_pack = active_recognition_rule_pack(db, workspace_id=workspace_id)
    try:
        return parse_order_row_drafts_with_service(
            workspace_id=workspace_id,
            task_id=task_id,
            standard_details=[],
            raw_records=[parser_input],
            waybill_samples=[],
            rule_pack=active_pack.payload if active_pack is not None else None,
            allow_ai=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI 识别服务暂时不可用，未创建解析会话。",
        ) from exc
