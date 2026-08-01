from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
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
    settings = get_settings()
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

    user = db.scalars(select(User).where(User.username == "admin", User.is_deleted.is_(False))).first()
    if user is None and settings.bootstrap_admin_password:
        user = User(
            username="admin",
            display_name="Administrator",
            password_hash=hash_password(settings.bootstrap_admin_password),
            password_initialized=True,
            is_enabled=True,
        )
        db.add(user)
        db.flush()

    if user is not None:
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


def system_setup_required(db: Session) -> bool:
    initialized_admin = db.execute(
        select(User.id)
        .join(UserWorkspace, UserWorkspace.user_id == User.id)
        .join(Role, Role.id == UserWorkspace.role_id)
        .where(
            User.is_deleted.is_(False),
            User.is_enabled.is_(True),
            User.password_initialized.is_(True),
            UserWorkspace.is_deleted.is_(False),
            Role.is_deleted.is_(False),
            Role.name == "system_admin",
        )
    ).first()
    return initialized_admin is None


def initialize_system_admin(db: Session, *, display_name: str, password: str) -> User:
    if not system_setup_required(db):
        raise ValueError("System setup has already completed.")

    workspace = db.scalars(select(Workspace).where(Workspace.code == "default")).first()
    if workspace is None:
        raise RuntimeError("Default workspace is missing.")
    role = db.scalars(
        select(Role).where(Role.workspace_id == workspace.id, Role.name == "system_admin")
    ).first()
    if role is None:
        raise RuntimeError("System administrator role is missing.")

    user = db.scalars(select(User).where(User.username == "admin", User.is_deleted.is_(False))).first()
    if user is None:
        user = User(
            username="admin",
            display_name=display_name,
            password_hash=hash_password(password),
            password_initialized=True,
            is_enabled=True,
        )
        db.add(user)
        db.flush()
    else:
        user.display_name = display_name
        user.password_hash = hash_password(password)
        user.password_initialized = True
        user.is_enabled = True

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
    else:
        membership.tenant_id = workspace.tenant_id
        membership.role_id = role.id
        membership.is_deleted = False

    db.commit()
    db.refresh(user)
    return user
