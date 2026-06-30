import os
from pathlib import Path


TEST_DB = Path(__file__).resolve().parent / "workspace_isolation_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["SECRET_KEY"] = "workspace-isolation-secret"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, Tenant, User, UserWorkspace, Workspace  # noqa: E402


def _create_user_workspace_pair(username: str, workspace_code: str) -> tuple[str, str, int, int]:
    password = f"{username}123"
    with SessionLocal() as db:
        tenant = Tenant(name=f"{username.title()} tenant", code=f"{workspace_code}_tenant")
        db.add(tenant)
        db.flush()

        workspace = Workspace(
            tenant_id=tenant.id,
            name=f"{username.title()} workspace",
            code=workspace_code,
        )
        db.add(workspace)
        db.flush()

        role = Role(tenant_id=tenant.id, workspace_id=workspace.id, name="operator")
        user = User(
            username=username,
            display_name=username.title(),
            password_hash=hash_password(password),
        )
        db.add_all([role, user])
        db.flush()

        db.add(
            UserWorkspace(
                tenant_id=tenant.id,
                workspace_id=workspace.id,
                user_id=user.id,
                role_id=role.id,
            )
        )
        db.commit()
        return username, password, workspace.id, role.id


def _login(client: TestClient, username: str, password: str, workspace_id: int) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(workspace_id)}


def test_users_only_read_data_from_their_own_workspace() -> None:
    with TestClient(app) as client:
        alice_username, alice_password, workspace_a_id, alice_role_id = _create_user_workspace_pair(
            "alice",
            "workspace_a",
        )
        bob_username, bob_password, workspace_b_id, _bob_role_id = _create_user_workspace_pair(
            "bob",
            "workspace_b",
        )

        alice_headers = _login(client, alice_username, alice_password, workspace_a_id)
        bob_headers = _login(client, bob_username, bob_password, workspace_b_id)

        alice_created = client.post(
            "/api/v1/export-header-definitions",
            headers=alice_headers,
            json={"name": "Alice field", "code": "alice_field", "data_type": "text"},
        )
        bob_created = client.post(
            "/api/v1/export-header-definitions",
            headers=bob_headers,
            json={"name": "Bob field", "code": "bob_field", "data_type": "text"},
        )
        assert alice_created.status_code == 201
        assert bob_created.status_code == 201
        assert alice_created.json()["tenant_id"] != bob_created.json()["tenant_id"]

        bob_record_id = bob_created.json()["id"]

        alice_list = client.get("/api/v1/export-header-definitions", headers=alice_headers)
        bob_list = client.get("/api/v1/export-header-definitions", headers=bob_headers)

        assert alice_list.status_code == 200
        assert bob_list.status_code == 200
        assert [item["code"] for item in alice_list.json()] == ["alice_field"]
        assert [item["code"] for item in bob_list.json()] == ["bob_field"]

        alice_cross_workspace_query = client.get(
            f"/api/v1/export-header-definitions?workspace_id={workspace_b_id}",
            headers=alice_headers,
        )
        alice_cross_workspace_create = client.post(
            "/api/v1/export-header-definitions",
            headers={
                "Authorization": alice_headers["Authorization"],
                "X-Workspace-Id": str(workspace_b_id),
            },
            json={"name": "Forbidden field", "code": "forbidden_field", "data_type": "text"},
        )
        alice_cross_record_get = client.get(
            f"/api/v1/export-header-definitions/{bob_record_id}",
            headers=alice_headers,
        )
        alice_workspaces = client.get("/api/v1/workspaces", headers=alice_headers)
        alice_tenants = client.get("/api/v1/tenants", headers=alice_headers)
        alice_users = client.get("/api/v1/users", headers=alice_headers)
        alice_role_escalation = client.patch(
            f"/api/v1/roles/{alice_role_id}",
            headers=alice_headers,
            json={"name": "system_admin"},
        )
        deprecated_waybill_modes = client.get("/api/v1/waybill-modes", headers=alice_headers)
        deprecated_waybill_templates = client.get("/api/v1/waybill-templates", headers=alice_headers)
        deprecated_waybill_template_fields = client.get("/api/v1/waybill-template-fields", headers=alice_headers)

        assert alice_cross_workspace_query.status_code == 403
        assert alice_cross_workspace_create.status_code == 403
        assert alice_cross_record_get.status_code == 404
        assert alice_workspaces.status_code == 200
        assert [workspace["id"] for workspace in alice_workspaces.json()] == [workspace_a_id]
        assert alice_tenants.status_code == 403
        assert alice_users.status_code == 403
        assert alice_role_escalation.status_code == 403
        assert deprecated_waybill_modes.status_code == 404
        assert deprecated_waybill_templates.status_code == 404
        assert deprecated_waybill_template_fields.status_code == 404


def test_soft_deleted_roles_do_not_grant_permissions() -> None:
    with TestClient(app) as client:
        username, password, workspace_id, role_id = _create_user_workspace_pair(
            "charlie",
            "workspace_c",
        )
        with SessionLocal() as db:
            role = db.get(Role, role_id)
            assert role is not None
            role.name = "system_admin"
            role.is_deleted = True
            db.commit()

        headers = _login(client, username, password, workspace_id)
        me = client.get("/api/v1/auth/me", headers=headers)
        tenants = client.get("/api/v1/tenants", headers=headers)

        assert me.status_code == 200
        assert "system_admin" not in me.json()["roles"]
        assert me.json()["workspaces"] == []
        assert tenants.status_code == 403

        denied_create = client.post(
            "/api/v1/export-header-definitions",
            headers=headers,
            json={"name": "Denied", "code": "denied", "data_type": "text"},
        )
        assert denied_create.status_code == 403


def test_mismatched_role_membership_does_not_grant_permissions() -> None:
    with TestClient(app) as client:
        username, password, workspace_id, role_id = _create_user_workspace_pair(
            "dana",
            "workspace_d",
        )
        _other_username, _other_password, _other_workspace_id, other_role_id = _create_user_workspace_pair(
            "erin",
            "workspace_e",
        )
        with SessionLocal() as db:
            other_role = db.get(Role, other_role_id)
            assert other_role is not None
            other_role.name = "system_admin"
            membership = db.scalars(
                select(UserWorkspace).where(UserWorkspace.role_id == role_id)
            ).first()
            assert membership is not None
            membership.role_id = other_role_id
            db.commit()

        headers = _login(client, username, password, workspace_id)
        me = client.get("/api/v1/auth/me", headers=headers)
        tenants = client.get("/api/v1/tenants", headers=headers)
        denied_create = client.post(
            "/api/v1/export-header-definitions",
            headers=headers,
            json={"name": "Denied", "code": "denied_mismatch", "data_type": "text"},
        )

        assert me.status_code == 200
        assert me.json()["roles"] == []
        assert me.json()["workspaces"] == []
        assert tenants.status_code == 403
        assert denied_create.status_code == 403


def teardown_module() -> None:
    from app.core.database import engine

    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()
