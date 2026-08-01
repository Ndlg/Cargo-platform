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
    app_version: str = "0.2.0-rc.1"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    database_url: str = "mysql+pymysql://cargo_user:cargo_pass@127.0.0.1:3306/cargo_platform"
    storage_root: str = "storage/workspaces"
    default_workspace_id: int = 1
    secret_key: str = ""
    collector_token_hash_key: str = ""
    collector_token_previous_hash_key: str = ""
    initial_setup_token: str = ""
    bootstrap_admin_password: str = ""
    access_token_expire_minutes: int = 480
    auto_create_tables: bool = False
    waybill_parser_url: str = ""


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


UNSAFE_SECRET_KEYS = {
    "change-me-in-production",
    "change-this-secret-before-production",
    "docker-local-dev-secret-change-before-production",
    "replace-this-with-a-long-random-value",
}


def validate_security_settings(settings: Settings) -> None:
    for name, value in (
        ("SECRET_KEY", settings.secret_key),
        ("COLLECTOR_TOKEN_HASH_KEY", settings.collector_token_hash_key),
    ):
        if len(value.encode("utf-8")) < 32 or value in UNSAFE_SECRET_KEYS:
            raise ValueError(f"{name} must be a non-placeholder secret of at least 32 bytes.")
    if settings.initial_setup_token and len(settings.initial_setup_token.encode("utf-8")) < 32:
        raise ValueError("INITIAL_SETUP_TOKEN must be at least 32 bytes when configured.")


@lru_cache
def get_settings() -> Settings:
    cors = os.getenv("CORS_ORIGINS")
    return Settings(
        app_name=os.getenv("APP_NAME", Settings.model_fields["app_name"].default),
        app_version=os.getenv("APP_VERSION", Settings.model_fields["app_version"].default),
        api_prefix=os.getenv("API_PREFIX", Settings.model_fields["api_prefix"].default),
        cors_origins=_split_csv(cors) if cors else Settings.model_fields["cors_origins"].default,
        database_url=os.getenv("DATABASE_URL", Settings.model_fields["database_url"].default),
        storage_root=os.getenv("STORAGE_ROOT", Settings.model_fields["storage_root"].default),
        default_workspace_id=int(os.getenv("DEFAULT_WORKSPACE_ID", "1")),
        secret_key=os.getenv("SECRET_KEY", "").strip(),
        collector_token_hash_key=os.getenv("COLLECTOR_TOKEN_HASH_KEY", "").strip(),
        collector_token_previous_hash_key=os.getenv("COLLECTOR_TOKEN_PREVIOUS_HASH_KEY", "").strip(),
        initial_setup_token=os.getenv("INITIAL_SETUP_TOKEN", "").strip(),
        bootstrap_admin_password=os.getenv("BOOTSTRAP_ADMIN_PASSWORD", ""),
        access_token_expire_minutes=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480")),
        auto_create_tables=os.getenv("AUTO_CREATE_TABLES", "false").lower() in {"1", "true", "yes"},
        waybill_parser_url=os.getenv("WAYBILL_PARSER_URL", "").strip().rstrip("/"),
    )
