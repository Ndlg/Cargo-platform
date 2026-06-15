from fastapi import APIRouter, HTTPException, status
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import create_access_token, verify_password
from app.models import User, Workspace


router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalars(
        select(User).where(User.username == payload.username, User.is_deleted.is_(False))
    ).first()
    if user is None or not user.is_enabled or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    token = create_access_token(str(user.id), {"username": user.username})
    return TokenResponse(access_token=token)


@router.get("/me")
def me(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    workspaces = db.scalars(
        select(Workspace).where(
            Workspace.id.in_(current_user.workspace_ids),
            Workspace.is_deleted.is_(False),
        )
    ).all()
    return {
        "id": current_user.id,
        "username": current_user.username,
        "display_name": current_user.display_name,
        "roles": list(current_user.role_names),
        "tenant_ids": list(current_user.tenant_ids),
        "workspaces": [
            {
                "id": workspace.id,
                "tenant_id": workspace.tenant_id,
                "name": workspace.name,
                "code": workspace.code,
            }
            for workspace in workspaces
        ],
    }
