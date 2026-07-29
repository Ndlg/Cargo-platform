from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models import RawCaptureRecord, Workspace
from app.services.order_row_reader import parser_raw_record_inputs
from app.services.recognition_rule_packs import (
    recognition_rule_pack_summary,
    save_ai_rule_profile,
)
from app.services.waybill_parser_client import (
    preview_order_row_drafts_with_service,
    validate_rule_pack_with_service,
)
from app.services.waybill_reading import task_documents


router = APIRouter(prefix="/internal/ai-recognition", tags=["ai-recognition-internal"])


class AiRuleApprovalRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    workspace_id: int = Field(ge=1)
    task_id: int = Field(ge=1)
    raw_record_id: int = Field(ge=1)
    document_sequence: int = Field(default=1, ge=1)
    format_fingerprint: str = Field(min_length=1, max_length=128)
    candidate_rule: dict[str, Any]
    rule_evidence: list[str] = Field(default_factory=list)
    candidate_output: dict[str, Any] = Field(default_factory=dict)
    validate_only: bool = False


BUSINESS_ROW_FIELDS = (
    "product",
    "sales_attr1",
    "sales_attr2",
    "quantity",
    "remark",
)


def comparable_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        rows = [
            row
            for parent in payload.get("parents") or []
            if isinstance(parent, dict)
            for row in parent.get("rows") or []
            if isinstance(row, dict)
        ]
    return [
        {field: row.get(field, "") for field in BUSINESS_ROW_FIELDS}
        for row in rows
        if isinstance(row, dict)
    ]


def rows_cover_expected(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> bool:
    if len(actual) != len(expected):
        return False
    remaining = actual.copy()
    for row in expected:
        if row not in remaining:
            return False
        remaining.remove(row)
    return True


def parser_input_for_document(
    record: RawCaptureRecord,
    document_sequence: int,
) -> dict[str, Any]:
    parser_input = parser_raw_record_inputs([record])[0]
    payload = parser_input["payload"]
    documents = task_documents(payload)
    if documents:
        if document_sequence > len(documents):
            raise ValueError("所选面单已经不存在，未同步规则。")
        task = payload.get("task")
        parser_input["payload"] = {
            **payload,
            "task": {
                **(task if isinstance(task, dict) else {}),
                "documents": [documents[document_sequence - 1]],
            },
        }
    elif document_sequence != 1:
        raise ValueError("所选面单已经不存在，未同步规则。")
    parser_input["document_sequence"] = document_sequence
    return parser_input


@router.post("/approve")
def approve_ai_rule(
    request: AiRuleApprovalRequest,
    db: Session = Depends(get_db),
    token: str = Header(default="", alias="X-AI-Recognition-Token"),
) -> dict[str, Any]:
    settings = get_settings()
    if (
        not settings.ai_recognition_enabled
        or not settings.ai_recognition_internal_token
        or not secrets.compare_digest(token, settings.ai_recognition_internal_token)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid AI recognition token.")

    workspace = db.get(Workspace, request.workspace_id)
    if workspace is None or workspace.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")

    record = db.scalar(
        select(RawCaptureRecord).where(
            RawCaptureRecord.id == request.raw_record_id,
            RawCaptureRecord.workspace_id == request.workspace_id,
            RawCaptureRecord.task_id == request.task_id,
            RawCaptureRecord.is_deleted.is_(False),
            RawCaptureRecord.archived_at.is_(None),
        )
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Raw waybill not found.")

    parents = request.candidate_output.get("parents")
    first_parent = parents[0] if isinstance(parents, list) and parents else None
    source = first_parent.get("source") if isinstance(first_parent, dict) else None
    evidence_payload = source.get("sanitized_payload") if isinstance(source, dict) else None
    expected_rows = comparable_rows(request.candidate_output)
    if not isinstance(evidence_payload, dict) or not expected_rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="AI 候选缺少可重放的面单证据或订单行，未同步规则。",
        )

    profile = {**request.candidate_rule, "fingerprint": request.format_fingerprint}
    try:
        pack = save_ai_rule_profile(
            db,
            tenant_id=workspace.tenant_id,
            workspace_id=workspace.id,
            session_id=request.session_id,
            profile=profile,
            validate=lambda payload: validate_rule_pack_with_service(rule_pack=payload),
            learning_record={
                "session_id": request.session_id,
                "task_id": request.task_id,
                "raw_record_id": request.raw_record_id,
                "document_sequence": request.document_sequence,
                "source_component": record.source_component,
                "sample_payload": evidence_payload,
                "confirmed_rows": expected_rows,
                "rule_evidence": request.rule_evidence,
            },
        )
        learning_records = pack.payload.get("ai_learning_records")
        for learning_record in learning_records if isinstance(learning_records, list) else []:
            if (
                not isinstance(learning_record, dict)
                or learning_record.get("fingerprint") != request.format_fingerprint
            ):
                continue
            raw_record_id = learning_record.get("raw_record_id")
            document_sequence = learning_record.get("document_sequence")
            confirmed_rows = comparable_rows({"rows": learning_record.get("confirmed_rows")})
            if (
                not isinstance(raw_record_id, int)
                or isinstance(raw_record_id, bool)
                or raw_record_id < 1
                or not isinstance(document_sequence, int)
                or isinstance(document_sequence, bool)
                or document_sequence < 1
                or not confirmed_rows
            ):
                raise ValueError("同类型面单的历史确认样本信息不完整，未保存新规则。")
            replay_record = db.scalar(
                select(RawCaptureRecord).where(
                    RawCaptureRecord.id == raw_record_id,
                    RawCaptureRecord.workspace_id == request.workspace_id,
                    RawCaptureRecord.is_deleted.is_(False),
                )
            )
            if replay_record is None:
                raise ValueError("同类型面单的历史确认样本已不可用，未保存新规则。")
            replay = preview_order_row_drafts_with_service(
                task_id=int(learning_record.get("task_id") or replay_record.task_id or request.task_id),
                raw_records=[parser_input_for_document(replay_record, document_sequence)],
                rule_pack=pack.payload,
            )
            if not rows_cover_expected(comparable_rows(replay), confirmed_rows):
                if learning_record.get("session_id") == request.session_id:
                    raise ValueError("AI 生成的规则无法复现你确认的订单行，尚未保存。请按修改结果重新生成规则。")
                raise ValueError("AI 生成的规则破坏了同类型面单的已确认结果，尚未保存。")
        if request.validate_only:
            db.rollback()
            return {
                "status": "valid",
                "format_fingerprint": request.format_fingerprint,
            }
        db.commit()
        db.refresh(pack)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="识别规则校验服务暂时不可用，未保存候选规则。",
        ) from exc

    return {
        "status": "activated",
        "rule_pack": recognition_rule_pack_summary(pack),
        "format_fingerprint": request.format_fingerprint,
        "rerun_task_id": request.task_id,
    }
