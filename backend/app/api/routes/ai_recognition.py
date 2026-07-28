from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models import Workspace
from app.services.recognition_rule_packs import (
    create_ai_rule_pack_revision,
    recognition_rule_pack_summary,
)
from app.services.waybill_parser_client import validate_rule_pack_with_service


router = APIRouter(prefix="/internal/ai-recognition", tags=["ai-recognition-internal"])


class AiRuleApprovalRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    workspace_id: int = Field(ge=1)
    task_id: int = Field(ge=1)
    raw_record_id: int = Field(ge=1)
    format_fingerprint: str = Field(min_length=1, max_length=128)
    candidate_rule: dict[str, Any]
    rule_evidence: list[str] = Field(default_factory=list)
    candidate_output: dict[str, Any] = Field(default_factory=dict)


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

    profile = {**request.candidate_rule, "fingerprint": request.format_fingerprint}
    try:
        pack = create_ai_rule_pack_revision(
            db,
            tenant_id=workspace.tenant_id,
            workspace_id=workspace.id,
            session_id=request.session_id,
            profile=profile,
            validate=lambda payload: validate_rule_pack_with_service(rule_pack=payload),
        )
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
