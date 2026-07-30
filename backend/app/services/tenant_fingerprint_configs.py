from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TenantFingerprintConfig, Workspace


def tenant_fingerprint_field_selections(
    db: Session,
    *,
    workspace_id: int,
) -> dict[str, list[str]]:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.tenant_id is None:
        return {}
    configs = db.scalars(
        select(TenantFingerprintConfig).where(
            TenantFingerprintConfig.tenant_id == workspace.tenant_id,
            TenantFingerprintConfig.is_enabled.is_(True),
            TenantFingerprintConfig.is_deleted.is_(False),
        )
    ).all()
    return {
        str(config.fingerprint_code): [
            str(field)
            for field in config.selected_fields
            if isinstance(field, str) and field
        ]
        for config in configs
        if isinstance(config.selected_fields, list)
    }


def selected_fields_for_fingerprint(
    db: Session,
    *,
    workspace_id: int,
    fingerprint_code: str,
) -> list[str] | None:
    return tenant_fingerprint_field_selections(
        db,
        workspace_id=workspace_id,
    ).get(fingerprint_code)
