from collections import defaultdict
from math import ceil
from secrets import compare_digest
from threading import Lock
from time import monotonic

from fastapi import APIRouter, HTTPException, Request, status
from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, password_version, verify_password
from app.models import User, Workspace
from app.services.bootstrap import initialize_system_admin, system_setup_required


router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SetupRequest(BaseModel):
    setup_token: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=128)


class LoginAttemptLimiter:
    def __init__(self, *, limit: int = 5, window_seconds: int = 300) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def retry_after(self, key: str, now: float | None = None) -> int:
        current = monotonic() if now is None else now
        with self._lock:
            recent = [value for value in self._attempts[key] if current - value < self.window_seconds]
            self._attempts[key] = recent
            if len(recent) < self.limit:
                return 0
            return max(1, ceil(self.window_seconds - (current - recent[0])))

    def record_failure(self, key: str, now: float | None = None) -> None:
        current = monotonic() if now is None else now
        with self._lock:
            self._attempts[key].append(current)

    def clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


# ponytail: process-local limiter fits the single Uvicorn worker; use shared storage before multi-instance deploys.
login_attempts = LoginAttemptLimiter()
dummy_password_hash = hash_password("cargo-platform-invalid-login-probe")


def _login_key(request: Request, username: str) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    client_ip = forwarded or (request.client.host if request.client else "unknown")
    return f"{client_ip}:{username.strip().casefold()}"


def _token_for_user(user: User) -> TokenResponse:
    token = create_access_token(
        str(user.id),
        {"username": user.username, "pwdv": password_version(user.password_hash)},
    )
    return TokenResponse(access_token=token)


@router.get("/setup-status")
def setup_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    required = system_setup_required(db)
    return {"required": required, "available": required and bool(get_settings().initial_setup_token)}


@router.post("/setup", response_model=TokenResponse)
def setup(payload: SetupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    if not system_setup_required(db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="系统初始化已经完成。")
    setup_token = get_settings().initial_setup_token
    if not setup_token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="系统初始化令牌未配置。")
    if not compare_digest(payload.setup_token, setup_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="初始化令牌无效。")
    try:
        user = initialize_system_admin(
            db,
            display_name=payload.display_name.strip(),
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _token_for_user(user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    if system_setup_required(db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="系统尚未初始化，请先设置管理员密码。")
    key = _login_key(request, payload.username)
    retry_after = login_attempts.retry_after(key)
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录失败次数过多，请稍后再试。",
            headers={"Retry-After": str(retry_after)},
        )
    user = db.scalars(
        select(User).where(User.username == payload.username, User.is_deleted.is_(False))
    ).first()
    password_hash = user.password_hash if user is not None else dummy_password_hash
    password_matches = verify_password(payload.password, password_hash)
    if (
        user is None
        or not user.is_enabled
        or not user.password_initialized
        or not password_matches
    ):
        login_attempts.record_failure(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    login_attempts.clear(key)
    return _token_for_user(user)


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
