from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import (
    CaptureBatch,
    CaptureTask,
    Collector,
    ExportHeaderDefinition,
    ImageAsset,
    Product,
    ProductSku,
    RawCaptureRecord,
    Role,
    Stall,
    StandardDetail,
    StandardDetailBatch,
    Tenant,
    User,
    UserWorkspace,
    Workspace,
)


WORKSPACE_TENANT_MODELS = (
    Role,
    UserWorkspace,
    Collector,
    CaptureTask,
    CaptureBatch,
    RawCaptureRecord,
    StandardDetailBatch,
    StandardDetail,
    ExportHeaderDefinition,
    Product,
    ProductSku,
    Stall,
    ImageAsset,
)


def _backfill_workspace_tenant_ids(db: Session) -> None:
    workspace_tenants = dict(
        db.execute(
            select(Workspace.id, Workspace.tenant_id).where(Workspace.tenant_id.is_not(None))
        ).all()
    )
    for model in WORKSPACE_TENANT_MODELS:
        records = db.scalars(select(model).where(model.tenant_id.is_(None))).all()
        for record in records:
            record.tenant_id = workspace_tenants.get(record.workspace_id)


def seed_initial_data(db: Session) -> None:
    tenant = db.scalars(select(Tenant).where(Tenant.code == "default")).first()
    if tenant is None:
        tenant = Tenant(
            name="Default tenant",
            code="default",
            status="active",
            remark="Initial tenant for local development.",
        )
        db.add(tenant)
        db.flush()

    workspace = db.scalars(select(Workspace).where(Workspace.code == "default")).first()
    if workspace is None:
        workspace = Workspace(
            tenant_id=tenant.id,
            name="Default workspace",
            code="default",
            remark="Initial workspace.",
        )
        db.add(workspace)
        db.flush()
    elif workspace.tenant_id is None:
        workspace.tenant_id = tenant.id

    orphan_workspaces = db.scalars(select(Workspace).where(Workspace.tenant_id.is_(None))).all()
    for orphan_workspace in orphan_workspaces:
        orphan_workspace.tenant_id = tenant.id
    db.flush()
    _backfill_workspace_tenant_ids(db)

    role = db.scalars(
        select(Role).where(Role.workspace_id == workspace.id, Role.name == "system_admin")
    ).first()
    if role is None:
        role = Role(
            tenant_id=workspace.tenant_id,
            workspace_id=workspace.id,
            name="system_admin",
            remark="System administrator.",
        )
        db.add(role)
        db.flush()
    elif role.tenant_id is None:
        role.tenant_id = workspace.tenant_id

    user = db.scalars(select(User).where(User.username == "admin")).first()
    if user is None:
        user = User(
            username="admin",
            display_name="Administrator",
            password_hash=hash_password("admin123"),
            is_enabled=True,
        )
        db.add(user)
        db.flush()

    membership = db.scalars(
        select(UserWorkspace).where(
            UserWorkspace.workspace_id == workspace.id,
            UserWorkspace.user_id == user.id,
        )
    ).first()
    if membership is None:
        db.add(
            UserWorkspace(
                tenant_id=workspace.tenant_id,
                workspace_id=workspace.id,
                user_id=user.id,
                role_id=role.id,
            )
        )
    elif membership.tenant_id is None:
        membership.tenant_id = workspace.tenant_id

    db.commit()
