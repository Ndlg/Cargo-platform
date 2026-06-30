from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id, require_write
from app.models import RecognitionRulePack
from app.services.recognition_rule_packs import (
    RECOGNITION_RULE_PACK_CONTRACT_VERSION,
    activate_recognition_rule_pack,
    normalize_rule_pack_payload,
    recognition_rule_pack_summary,
    upsert_recognition_rule_pack,
)


router = APIRouter(prefix="/recognition-rule-packs", tags=["recognition-rule-packs"])


class RecognitionRulePackImportRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    activate: bool = False
    description: str | None = None


def current_tenant_id(current_user: CurrentUser) -> int | None:
    return min(current_user.tenant_ids, default=None)


def rule_pack_record(pack: RecognitionRulePack, *, include_payload: bool = False) -> dict[str, Any]:
    record = {
        **(recognition_rule_pack_summary(pack) or {}),
        "tenant_id": pack.tenant_id,
        "workspace_id": pack.workspace_id,
        "description": pack.description,
        "created_at": pack.created_at.isoformat() if pack.created_at else None,
        "updated_at": pack.updated_at.isoformat() if pack.updated_at else None,
    }
    if include_payload:
        record["payload"] = pack.payload
    return record


def get_rule_pack_or_404(db: Session, *, pack_id: int, workspace_id: int) -> RecognitionRulePack:
    pack = db.get(RecognitionRulePack, pack_id)
    if pack is None or pack.workspace_id != workspace_id or pack.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recognition rule pack not found.")
    return pack


@router.get("")
def list_recognition_rule_packs(
    db: Session = Depends(get_db),
    _current_user: Any = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    packs = db.scalars(
        select(RecognitionRulePack)
        .where(
            RecognitionRulePack.workspace_id == workspace_id,
            RecognitionRulePack.is_deleted.is_(False),
        )
        .order_by(RecognitionRulePack.is_enabled.desc(), RecognitionRulePack.updated_at.desc())
    ).all()
    return {
        "contract_version": RECOGNITION_RULE_PACK_CONTRACT_VERSION,
        "active_pack": next((rule_pack_record(pack) for pack in packs if pack.is_enabled and pack.status == "active"), None),
        "packs": [rule_pack_record(pack) for pack in packs],
    }


@router.post("/import", status_code=status.HTTP_201_CREATED)
def import_recognition_rule_pack(
    request: RecognitionRulePackImportRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    try:
        normalized = normalize_rule_pack_payload(request.payload)
        pack = upsert_recognition_rule_pack(
            db,
            tenant_id=current_tenant_id(current_user),
            workspace_id=workspace_id,
            payload=normalized,
            activate=request.activate,
            description=request.description,
        )
        db.commit()
        db.refresh(pack)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {
        "contract_version": RECOGNITION_RULE_PACK_CONTRACT_VERSION,
        "pack": rule_pack_record(pack, include_payload=True),
    }


@router.post("/{pack_id}/activate")
def activate_recognition_rule_pack_endpoint(
    pack_id: int,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    pack = get_rule_pack_or_404(db, pack_id=pack_id, workspace_id=workspace_id)
    activate_recognition_rule_pack(db, workspace_id=workspace_id, pack=pack)
    db.commit()
    db.refresh(pack)
    return {
        "contract_version": RECOGNITION_RULE_PACK_CONTRACT_VERSION,
        "pack": rule_pack_record(pack, include_payload=True),
    }


@router.post("/{pack_id}/deactivate")
def deactivate_recognition_rule_pack_endpoint(
    pack_id: int,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    pack = get_rule_pack_or_404(db, pack_id=pack_id, workspace_id=workspace_id)
    pack.status = "inactive"
    pack.is_enabled = False
    db.commit()
    db.refresh(pack)
    return {
        "contract_version": RECOGNITION_RULE_PACK_CONTRACT_VERSION,
        "pack": rule_pack_record(pack, include_payload=True),
    }


@router.delete("/{pack_id}")
def delete_recognition_rule_pack_endpoint(
    pack_id: int,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    pack = get_rule_pack_or_404(db, pack_id=pack_id, workspace_id=workspace_id)
    pack.status = "inactive"
    pack.is_enabled = False
    pack.is_deleted = True
    db.commit()
    return {
        "contract_version": RECOGNITION_RULE_PACK_CONTRACT_VERSION,
        "pack_id": pack_id,
        "deleted": True,
    }


@router.get("/{pack_id}/export")
def export_recognition_rule_pack(
    pack_id: int,
    db: Session = Depends(get_db),
    _current_user: Any = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    pack = get_rule_pack_or_404(db, pack_id=pack_id, workspace_id=workspace_id)
    return {
        "contract_version": RECOGNITION_RULE_PACK_CONTRACT_VERSION,
        "pack": rule_pack_record(pack),
        "payload": pack.payload,
    }
