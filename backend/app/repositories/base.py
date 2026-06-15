from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.base import Base


ModelT = TypeVar("ModelT", bound=Base)


class WorkspaceAccessError(PermissionError):
    pass


CREATE_LOCKED_FIELDS = {
    "id",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "is_deleted",
    "token_hash",
}

UPDATE_LOCKED_FIELDS = CREATE_LOCKED_FIELDS | {"created_at"}
SENSITIVE_FIELDS = {"password_hash", "token_hash"}
RESOURCE_LOCKED_FIELDS = {
    "products": {"keywords"},
    "product_skus": {"keywords"},
}


def model_to_dict(model: Base) -> dict[str, Any]:
    return {
        column.name: getattr(model, column.name)
        for column in model.__table__.columns
        if column.name not in SENSITIVE_FIELDS
    }


class Repository:
    def __init__(self, db: Session, model: type[ModelT]):
        self.db = db
        self.model = model

    @property
    def is_workspace_scoped(self) -> bool:
        return hasattr(self.model, "workspace_id")

    @property
    def is_tenant_scoped(self) -> bool:
        return hasattr(self.model, "tenant_id")

    def _tenant_id_for_workspace(self, workspace_id: int) -> int | None:
        from app.models import Workspace

        workspace = self.db.get(Workspace, workspace_id)
        if workspace is None or workspace.is_deleted:
            raise WorkspaceAccessError("Workspace access denied.")
        return workspace.tenant_id

    def _create_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        columns = {column.name for column in self.model.__table__.columns}
        locked_fields = set(CREATE_LOCKED_FIELDS)
        locked_fields.update(RESOURCE_LOCKED_FIELDS.get(self.model.__tablename__, set()))
        if self.is_workspace_scoped:
            locked_fields.update({"tenant_id", "workspace_id"})
        return {
            key: value
            for key, value in payload.items()
            if key in columns and key not in locked_fields
        }

    def _workspace_statement(self, workspace_id: int | None, allowed_workspace_ids: set[int]) -> Select[tuple[ModelT]]:
        statement = select(self.model).where(self.model.is_deleted.is_(False))
        if self.is_workspace_scoped:
            if workspace_id is None:
                statement = statement.where(self.model.workspace_id.in_(allowed_workspace_ids))
            else:
                if workspace_id not in allowed_workspace_ids:
                    raise WorkspaceAccessError("Workspace access denied.")
                statement = statement.where(self.model.workspace_id == workspace_id)
        return statement

    def list(
        self,
        *,
        workspace_id: int | None,
        allowed_workspace_ids: set[int],
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        statement = self._workspace_statement(workspace_id, allowed_workspace_ids).offset(offset).limit(limit)
        return [model_to_dict(item) for item in self.db.scalars(statement).all()]

    def get(self, record_id: int, *, allowed_workspace_ids: set[int]) -> dict[str, Any] | None:
        statement = select(self.model).where(self.model.id == record_id, self.model.is_deleted.is_(False))
        if self.is_workspace_scoped:
            statement = statement.where(self.model.workspace_id.in_(allowed_workspace_ids))
        record = self.db.scalars(statement).first()
        return model_to_dict(record) if record else None

    def create(self, payload: dict[str, Any], *, workspace_id: int | None, user_id: int | None) -> dict[str, Any]:
        record_payload = self._create_payload(payload)
        if self.is_workspace_scoped:
            if workspace_id is None:
                raise WorkspaceAccessError("workspace_id is required.")
            record_payload["workspace_id"] = workspace_id
            if self.is_tenant_scoped:
                record_payload["tenant_id"] = self._tenant_id_for_workspace(workspace_id)
        record_payload["created_by"] = user_id
        record_payload["updated_by"] = user_id
        record = self.model(**record_payload)
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return model_to_dict(record)

    def update(
        self,
        record_id: int,
        payload: dict[str, Any],
        *,
        allowed_workspace_ids: set[int],
        user_id: int | None,
    ) -> dict[str, Any] | None:
        statement = select(self.model).where(self.model.id == record_id, self.model.is_deleted.is_(False))
        if self.is_workspace_scoped:
            statement = statement.where(self.model.workspace_id.in_(allowed_workspace_ids))
        record = self.db.scalars(statement).first()
        if record is None:
            return None
        locked_fields = set(UPDATE_LOCKED_FIELDS)
        if self.is_workspace_scoped:
            locked_fields.update({"tenant_id", "workspace_id"})
        locked_fields.update(RESOURCE_LOCKED_FIELDS.get(self.model.__tablename__, set()))
        if self.model.__tablename__ == "workspaces":
            locked_fields.add("tenant_id")
        if self.model.__tablename__ == "product_skus":
            locked_fields.add("product_id")

        for key, value in payload.items():
            if key in locked_fields:
                continue
            if hasattr(record, key):
                setattr(record, key, value)
        if hasattr(record, "updated_by"):
            record.updated_by = user_id
        self.db.commit()
        self.db.refresh(record)
        return model_to_dict(record)

    def soft_delete(self, record_id: int, *, allowed_workspace_ids: set[int], user_id: int | None) -> bool:
        statement = select(self.model).where(self.model.id == record_id, self.model.is_deleted.is_(False))
        if self.is_workspace_scoped:
            statement = statement.where(self.model.workspace_id.in_(allowed_workspace_ids))
        record = self.db.scalars(statement).first()
        if record is None:
            return False
        record.is_deleted = True
        if self.model.__tablename__ == "collectors":
            if hasattr(record, "is_enabled"):
                record.is_enabled = False
            if hasattr(record, "online_status"):
                record.online_status = "offline"
            if hasattr(record, "token_hash"):
                record.token_hash = None
        if hasattr(record, "updated_by"):
            record.updated_by = user_id
        self.db.commit()
        return True
