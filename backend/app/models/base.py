from datetime import datetime

from sqlalchemy import Boolean, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AuditMixin:
    created_by: Mapped[int | None] = mapped_column(nullable=True)
    updated_by: Mapped[int | None] = mapped_column(nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class WorkspaceMixin:
    @declared_attr
    def tenant_id(cls) -> Mapped[int | None]:
        return mapped_column(index=True, nullable=True)

    @declared_attr
    def workspace_id(cls) -> Mapped[int]:
        return mapped_column(index=True, nullable=False)


class BaseModel(IdMixin, TimestampMixin, AuditMixin, Base):
    __abstract__ = True


class WorkspaceModel(WorkspaceMixin, BaseModel):
    __abstract__ = True
