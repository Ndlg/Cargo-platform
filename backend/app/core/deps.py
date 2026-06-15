from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import Role, User, UserWorkspace, Workspace


bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token.")
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token.")
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token.") from None

    user = db.get(User, user_id)
    if user is None or user.is_deleted or not user.is_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is disabled or missing.")

    memberships = db.execute(
        select(UserWorkspace, Role)
        .join(
            Workspace,
            (Workspace.id == UserWorkspace.workspace_id) & (Workspace.is_deleted.is_(False)),
        )
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
        .where(UserWorkspace.user_id == user.id, UserWorkspace.is_deleted.is_(False))
    ).all()
    active_memberships = tuple(
        (user_workspace, role) for user_workspace, role in memberships if role is not None
    )
    tenant_ids = tuple(
        sorted(
            {
                user_workspace.tenant_id
                for user_workspace, _role in active_memberships
                if user_workspace.tenant_id is not None
            }
        )
    )
    workspace_ids = tuple(
        sorted({user_workspace.workspace_id for user_workspace, _role in active_memberships})
    )
    role_names = tuple(sorted({role.name for _user_workspace, role in active_memberships}))
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role_names=role_names,
        tenant_ids=tenant_ids,
        workspace_ids=workspace_ids,
    )


def get_workspace_id(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    x_workspace_id: Annotated[int | None, Header(alias="X-Workspace-Id")] = None,
) -> int:
    if current_user.is_system_admin:
        if x_workspace_id is None:
            return min(current_user.allowed_workspace_ids(), default=get_settings().default_workspace_id)
        workspace = db.get(Workspace, x_workspace_id)
        if workspace is None or workspace.is_deleted:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace access denied.")
        return x_workspace_id

    allowed = current_user.allowed_workspace_ids()
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No workspace access.")
    workspace_id = x_workspace_id or min(allowed)
    if workspace_id not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace access denied.")
    return workspace_id


def require_write(current_user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    if not current_user.can_write:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Read-only users cannot write.")
    return current_user
