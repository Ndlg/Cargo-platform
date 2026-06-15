from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_workspace_id, require_write
from app.models import FieldDefinition, Workspace
from app.repositories.base import model_to_dict


router = APIRouter()


class ExportFieldDefinitionUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64)
    export_order: int = Field(ge=1, le=1000)


@router.post("/export-field-definitions/upsert")
def upsert_export_field_definition(
    payload: ExportFieldDefinitionUpsert,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict:
    workspace = db.get(Workspace, workspace_id)
    record = db.scalars(
        select(FieldDefinition).where(
            FieldDefinition.workspace_id == workspace_id,
            FieldDefinition.code == payload.code,
        )
    ).first()

    if record is None:
        record = FieldDefinition(
            tenant_id=workspace.tenant_id if workspace else None,
            workspace_id=workspace_id,
            name=payload.name.strip(),
            code=payload.code.strip(),
            data_type="text",
            is_visible=True,
            is_filterable=False,
            use_for_matching=False,
            use_for_image_match=False,
            use_for_stall_match=False,
            use_for_grouping=False,
            use_as_quantity=False,
            use_as_remark=False,
            export_enabled=True,
            export_order=payload.export_order,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(record)
    else:
        record.name = payload.name.strip()
        record.data_type = "text"
        record.is_visible = True
        record.export_enabled = True
        record.export_order = payload.export_order
        record.is_deleted = False
        record.updated_by = current_user.id
        if hasattr(record, "tenant_id") and workspace is not None:
            record.tenant_id = workspace.tenant_id

    db.commit()
    db.refresh(record)
    return model_to_dict(record)
