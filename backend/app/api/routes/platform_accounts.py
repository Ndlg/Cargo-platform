from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import require_write
from app.core.security import hash_password
from app.models import Role, Tenant, User, UserWorkspace, Workspace


router = APIRouter(prefix="/platform/customer-accounts", tags=["platform-customer-accounts"])


class CustomerAccountCreate(BaseModel):
    tenant_name: str = Field(min_length=1, max_length=128)
    tenant_code: str = Field(min_length=1, max_length=64)
    workspace_name: str = Field(min_length=1, max_length=128)
    workspace_code: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=6, max_length=128)


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=6, max_length=128)


def _require_system_admin(current_user: CurrentUser) -> None:
    if not current_user.is_system_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Server administrator access required.",
        )


def _clean_text(value: str) -> str:
    return value.strip()


def _tenant_to_dict(tenant: Tenant) -> dict[str, object]:
    return {
        "id": tenant.id,
        "name": tenant.name,
        "code": tenant.code,
        "status": tenant.status,
        "remark": tenant.remark,
    }


def _workspace_to_dict(
    workspace: Workspace,
    tenant: Tenant | None,
    admin_users: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "id": workspace.id,
        "tenant_id": workspace.tenant_id,
        "tenant_name": tenant.name if tenant else "-",
        "tenant_code": tenant.code if tenant else "-",
        "name": workspace.name,
        "code": workspace.code,
        "remark": workspace.remark,
        "admin_users": admin_users,
    }


def _user_to_dict(
    user: User,
    memberships: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "is_enabled": user.is_enabled,
        "memberships": memberships,
    }


def _load_customer_accounts(db: Session) -> dict[str, object]:
    tenants = db.scalars(
        select(Tenant).where(Tenant.is_deleted.is_(False)).order_by(Tenant.id)
    ).all()
    workspaces = db.scalars(
        select(Workspace).where(Workspace.is_deleted.is_(False)).order_by(Workspace.id)
    ).all()
    users = db.scalars(
        select(User).where(User.is_deleted.is_(False)).order_by(User.id)
    ).all()
    membership_rows = db.execute(
        select(UserWorkspace, Role)
        .join(
            Role,
            and_(
                Role.id == UserWorkspace.role_id,
                Role.tenant_id == UserWorkspace.tenant_id,
                Role.workspace_id == UserWorkspace.workspace_id,
                Role.is_deleted.is_(False),
            ),
            isouter=True,
        )
        .where(UserWorkspace.is_deleted.is_(False))
        .order_by(UserWorkspace.workspace_id, UserWorkspace.user_id)
    ).all()

    tenant_by_id = {tenant.id: tenant for tenant in tenants}
    workspace_by_id = {workspace.id: workspace for workspace in workspaces}
    user_by_id = {user.id: user for user in users}
    workspace_users: dict[int, list[dict[str, object]]] = {}
    user_memberships: dict[int, list[dict[str, object]]] = {}

    for membership, role in membership_rows:
        workspace = workspace_by_id.get(membership.workspace_id)
        user = user_by_id.get(membership.user_id)
        if workspace is None or user is None:
            continue
        role_name = role.name if role else "-"
        admin_user = {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role_name": role_name,
            "is_enabled": user.is_enabled,
        }
        workspace_users.setdefault(workspace.id, []).append(admin_user)
        user_memberships.setdefault(user.id, []).append(
            {
                "tenant_id": membership.tenant_id,
                "workspace_id": workspace.id,
                "workspace_name": workspace.name,
                "workspace_code": workspace.code,
                "role_name": role_name,
            }
        )

    return {
        "tenants": [_tenant_to_dict(tenant) for tenant in tenants],
        "workspaces": [
            _workspace_to_dict(
                workspace,
                tenant_by_id.get(workspace.tenant_id),
                workspace_users.get(workspace.id, []),
            )
            for workspace in workspaces
        ],
        "users": [
            _user_to_dict(user, user_memberships.get(user.id, []))
            for user in users
        ],
    }


@router.get("")
def list_customer_accounts(
    current_user: CurrentUser = Depends(require_write),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_system_admin(current_user)
    return _load_customer_accounts(db)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_customer_account(
    payload: CustomerAccountCreate,
    current_user: CurrentUser = Depends(require_write),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_system_admin(current_user)
    tenant_code = _clean_text(payload.tenant_code)
    workspace_code = _clean_text(payload.workspace_code)
    username = _clean_text(payload.username)

    if db.scalars(select(Tenant).where(Tenant.code == tenant_code, Tenant.is_deleted.is_(False))).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="客户编码已经存在。")
    if db.scalars(select(Workspace).where(Workspace.code == workspace_code, Workspace.is_deleted.is_(False))).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="工作空间编码已经存在。")
    if db.scalars(select(User).where(User.username == username, User.is_deleted.is_(False))).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="登录账号已经存在。")

    tenant = Tenant(
        name=_clean_text(payload.tenant_name),
        code=tenant_code,
        status="active",
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(tenant)
    db.flush()

    workspace = Workspace(
        tenant_id=tenant.id,
        name=_clean_text(payload.workspace_name),
        code=workspace_code,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(workspace)
    db.flush()

    role = Role(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        name="workspace_admin",
        remark="Customer workspace administrator.",
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(role)
    db.flush()

    user = User(
        username=username,
        display_name=_clean_text(payload.display_name),
        password_hash=hash_password(payload.password),
        is_enabled=True,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(user)
    db.flush()

    db.add(
        UserWorkspace(
            tenant_id=tenant.id,
            workspace_id=workspace.id,
            user_id=user.id,
            role_id=role.id,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="客户账号信息已存在。") from exc

    db.refresh(tenant)
    db.refresh(workspace)
    db.refresh(user)
    return {
        "tenant": _tenant_to_dict(tenant),
        "workspace": _workspace_to_dict(
            workspace,
            tenant,
            [
                {
                    "id": user.id,
                    "username": user.username,
                    "display_name": user.display_name,
                    "role_name": role.name,
                    "is_enabled": user.is_enabled,
                }
            ],
        ),
        "user": _user_to_dict(
            user,
            [
                {
                    "tenant_id": tenant.id,
                    "workspace_id": workspace.id,
                    "workspace_name": workspace.name,
                    "workspace_code": workspace.code,
                    "role_name": role.name,
                }
            ],
        ),
    }


@router.post("/users/{user_id}/reset-password")
def reset_customer_user_password(
    user_id: int,
    payload: PasswordResetRequest,
    current_user: CurrentUser = Depends(require_write),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_system_admin(current_user)
    user = db.get(User, user_id)
    if user is None or user.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在。")
    user.password_hash = hash_password(payload.password)
    user.updated_by = current_user.id
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username, "display_name": user.display_name}
