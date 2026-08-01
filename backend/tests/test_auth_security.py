import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select


os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("SECRET_KEY", "backend-test-secret-key-at-least-32-bytes")
os.environ.setdefault("COLLECTOR_TOKEN_HASH_KEY", "collector-test-hash-key-at-least-32-bytes")
os.environ.setdefault("INITIAL_SETUP_TOKEN", "initial-setup-test-token-at-least-32-bytes")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "Test-Admin-Password-2026!")

from app.core.config import Settings, validate_security_settings
from app.core import database
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import User, UserWorkspace


TEST_ADMIN_PASSWORD = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
SETUP_PASSWORD = "Release-Admin-Password-2026!"
RESET_PASSWORD = "Release-Admin-Password-2027!"


def test_sqlite_upgrade_only_requires_legacy_admin_reset(tmp_path, monkeypatch) -> None:
    migration_engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE workspaces (id INTEGER PRIMARY KEY, tenant_id INTEGER)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64) NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO users (id, username) VALUES (1, 'admin'), (2, 'clerk')"
        )

    monkeypatch.setattr(database, "engine", migration_engine)
    database._run_sqlite_compat_migrations()

    with migration_engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT username, password_initialized FROM users ORDER BY id"
        ).all()
        schema_version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
    assert rows == [("admin", 0), ("clerk", 1)]
    assert schema_version == database.SQLITE_SCHEMA_VERSION


def test_sqlite_upgrade_rejects_database_from_newer_release(tmp_path, monkeypatch) -> None:
    migration_engine = create_engine(f"sqlite:///{tmp_path / 'newer.db'}")
    with migration_engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE workspaces (id INTEGER PRIMARY KEY, tenant_id INTEGER)")
        connection.exec_driver_sql("PRAGMA user_version = 999")

    monkeypatch.setattr(database, "engine", migration_engine)
    with pytest.raises(RuntimeError, match="newer schema version"):
        database._run_sqlite_compat_migrations()


def test_runtime_security_settings_reject_missing_or_placeholder_keys() -> None:
    with pytest.raises(ValueError):
        validate_security_settings(Settings(secret_key="", collector_token_hash_key="x" * 32))
    with pytest.raises(ValueError):
        validate_security_settings(
            Settings(
                secret_key="change-this-secret-before-production",
                collector_token_hash_key="x" * 32,
            )
        )
    with pytest.raises(ValueError):
        validate_security_settings(Settings(secret_key="x" * 32, collector_token_hash_key="short"))

    validate_security_settings(
        Settings(
            secret_key="jwt-release-key-with-at-least-32-bytes",
            collector_token_hash_key="collector-release-key-at-least-32-bytes",
        )
    )


def test_first_run_setup_replaces_legacy_admin_and_invalidates_old_tokens() -> None:
    with TestClient(app) as client:
        with SessionLocal() as db:
            admin = db.scalars(select(User).where(User.username == "admin")).first()
            if admin is not None:
                db.execute(delete(UserWorkspace).where(UserWorkspace.user_id == admin.id))
                db.delete(admin)
                db.commit()

        setup_status = client.get("/api/v1/auth/setup-status")
        assert setup_status.status_code == 200
        assert setup_status.json() == {"required": True, "available": True}

        before_setup = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert before_setup.status_code == 409

        wrong_token = client.post(
            "/api/v1/auth/setup",
            json={
                "setup_token": "wrong-setup-token",
                "display_name": "Administrator",
                "password": SETUP_PASSWORD,
            },
        )
        assert wrong_token.status_code == 401

        initialized = client.post(
            "/api/v1/auth/setup",
            json={
                "setup_token": os.environ["INITIAL_SETUP_TOKEN"],
                "display_name": "Administrator",
                "password": SETUP_PASSWORD,
            },
        )
        assert initialized.status_code == 200
        initialized_token = initialized.json()["access_token"]

        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {initialized_token}"},
        )
        assert me.status_code == 200
        admin_id = int(me.json()["id"])

        assert client.get("/api/v1/auth/setup-status").json() == {
            "required": False,
            "available": False,
        }
        repeated_setup = client.post(
            "/api/v1/auth/setup",
            json={
                "setup_token": os.environ["INITIAL_SETUP_TOKEN"],
                "display_name": "Administrator",
                "password": SETUP_PASSWORD,
            },
        )
        assert repeated_setup.status_code == 409

        old_default = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert old_default.status_code == 401

        reset = client.post(
            f"/api/v1/platform/customer-accounts/users/{admin_id}/reset-password",
            headers={"Authorization": f"Bearer {initialized_token}"},
            json={"password": RESET_PASSWORD},
        )
        assert reset.status_code == 200
        assert client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {initialized_token}"},
        ).status_code == 401

        new_login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": RESET_PASSWORD},
        )
        assert new_login.status_code == 200

        with SessionLocal() as db:
            admin = db.get(User, admin_id)
            assert admin is not None
            admin.password_hash = hash_password(TEST_ADMIN_PASSWORD)
            admin.password_initialized = True
            db.commit()


def test_login_failures_are_rate_limited() -> None:
    with TestClient(app) as client:
        payload = {"username": "rate-limit-probe", "password": "wrong-password"}
        for _attempt in range(5):
            response = client.post("/api/v1/auth/login", json=payload)
            assert response.status_code == 401

        blocked = client.post("/api/v1/auth/login", json=payload)
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0


def test_security_headers_are_present() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
