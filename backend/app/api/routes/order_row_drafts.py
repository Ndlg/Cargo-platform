from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id
from app.services.order_row_reader import task_order_row_drafts_payload


router = APIRouter(prefix="/order-row-drafts", tags=["order-row-drafts"])


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
