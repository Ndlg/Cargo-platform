from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id, require_write
from app.models import TenantFingerprintConfig, Workspace
from app.services.ai_recognition_client import fingerprint_catalog_with_service


router = APIRouter(
    prefix="/tenant-fingerprint-configs",
    tags=["tenant-fingerprint-configs"],
)


class TenantFingerprintConfigUpdate(BaseModel):
    selected_fields: list[str] = Field(min_length=1)


def _tenant_id(db: Session, workspace_id: int) -> int:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.is_deleted or workspace.tenant_id is None:
        raise HTTPException(status_code=422, detail="当前工作区没有关联租户。")
    return workspace.tenant_id


def _catalog() -> dict[str, dict[str, Any]]:
    try:
        payload = fingerprint_catalog_with_service()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AI 指纹服务暂时不可用。") from exc
    return {
        item["code"]: item
        for item in payload.get("fingerprints", [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }


def _record(config: TenantFingerprintConfig, capability: dict[str, Any]) -> dict[str, Any]:
    return {
        **capability,
        "selected_fields": config.selected_fields,
    }


@router.get("")
def list_tenant_fingerprint_configs(
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    tenant_id = _tenant_id(db, workspace_id)
    catalog = _catalog()
    configs = db.scalars(
        select(TenantFingerprintConfig)
        .where(
            TenantFingerprintConfig.tenant_id == tenant_id,
            TenantFingerprintConfig.is_enabled.is_(True),
            TenantFingerprintConfig.is_deleted.is_(False),
        )
        .order_by(TenantFingerprintConfig.id)
    ).all()
    return {
        "contract_version": "tenant_fingerprint_configs_v1",
        "tenant_id": tenant_id,
        "fingerprints": [
            _record(config, catalog[config.fingerprint_code])
            for config in configs
            if config.fingerprint_code in catalog
        ],
    }


@router.put("/{fingerprint_code}")
def update_tenant_fingerprint_config(
    fingerprint_code: str,
    request: TenantFingerprintConfigUpdate,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    tenant_id = _tenant_id(db, workspace_id)
    config = db.scalar(
        select(TenantFingerprintConfig).where(
            TenantFingerprintConfig.tenant_id == tenant_id,
            TenantFingerprintConfig.fingerprint_code == fingerprint_code,
            TenantFingerprintConfig.is_enabled.is_(True),
            TenantFingerprintConfig.is_deleted.is_(False),
        )
    )
    if config is None:
        raise HTTPException(status_code=404, detail="当前租户未获授权使用该面单指纹。")

    capability = _catalog().get(fingerprint_code)
    if capability is None:
        raise HTTPException(status_code=404, detail="该面单指纹不存在。")
    allowed_fields = {
        field["key"]
        for field in capability.get("candidate_fields", [])
        if isinstance(field, dict) and isinstance(field.get("key"), str)
    }
    selected_fields = list(dict.fromkeys(request.selected_fields))
    invalid_fields = [field for field in selected_fields if field not in allowed_fields]
    if invalid_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"该指纹不支持字段：{', '.join(invalid_fields)}",
        )

    config.selected_fields = selected_fields
    config.updated_by = _current_user.id
    db.commit()
    db.refresh(config)
    return {
        "contract_version": "tenant_fingerprint_configs_v1",
        "fingerprint": _record(config, capability),
    }
