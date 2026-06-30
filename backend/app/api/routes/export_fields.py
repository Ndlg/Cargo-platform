from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_workspace_id, require_write
from app.models import ExportHeaderDefinition, Workspace
from app.repositories.base import model_to_dict


router = APIRouter()


class ExportHeaderUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64)
    export_order: int = Field(ge=1, le=1000)


@router.post("/export-headers/upsert")
def upsert_export_header(
    payload: ExportHeaderUpsert,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict:
    workspace = db.get(Workspace, workspace_id)
    record = db.scalars(
        select(ExportHeaderDefinition).where(
            ExportHeaderDefinition.workspace_id == workspace_id,
            ExportHeaderDefinition.code == payload.code,
        )
    ).first()

    if record is None:
        record = ExportHeaderDefinition(
            tenant_id=workspace.tenant_id if workspace else None,
            workspace_id=workspace_id,
            name=payload.name.strip(),
            code=payload.code.strip(),
            data_type="text",
            export_enabled=True,
            export_order=payload.export_order,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(record)
    else:
        record.name = payload.name.strip()
        record.data_type = "text"
        record.export_enabled = True
        record.export_order = payload.export_order
        record.is_deleted = False
        record.updated_by = current_user.id
        if hasattr(record, "tenant_id") and workspace is not None:
            record.tenant_id = workspace.tenant_id

    db.commit()
    db.refresh(record)
    return model_to_dict(record)
