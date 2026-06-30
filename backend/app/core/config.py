from functools import lru_cache
import os
from pathlib import Path

from pydantic import BaseModel

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is provided by requirements.
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")


class Settings(BaseModel):
    app_name: str = "Cargo Platform"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    database_url: str = "mysql+pymysql://cargo_user:cargo_pass@127.0.0.1:3306/cargo_platform"
    redis_url: str = "redis://127.0.0.1:6379/0"
    storage_root: str = "storage/workspaces"
    default_workspace_id: int = 1
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 480
    auto_create_tables: bool = False
    waybill_parser_url: str = ""


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    cors = os.getenv("CORS_ORIGINS")
    return Settings(
        app_name=os.getenv("APP_NAME", Settings.model_fields["app_name"].default),
        app_version=os.getenv("APP_VERSION", Settings.model_fields["app_version"].default),
        api_prefix=os.getenv("API_PREFIX", Settings.model_fields["api_prefix"].default),
        cors_origins=_split_csv(cors) if cors else Settings.model_fields["cors_origins"].default,
        database_url=os.getenv("DATABASE_URL", Settings.model_fields["database_url"].default),
        redis_url=os.getenv("REDIS_URL", Settings.model_fields["redis_url"].default),
        storage_root=os.getenv("STORAGE_ROOT", Settings.model_fields["storage_root"].default),
        default_workspace_id=int(os.getenv("DEFAULT_WORKSPACE_ID", "1")),
        secret_key=os.getenv("SECRET_KEY", Settings.model_fields["secret_key"].default),
        access_token_expire_minutes=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480")),
        auto_create_tables=os.getenv("AUTO_CREATE_TABLES", "false").lower() in {"1", "true", "yes"},
        waybill_parser_url=os.getenv("WAYBILL_PARSER_URL", "").strip().rstrip("/"),
    )
