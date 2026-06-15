from copy import deepcopy
from datetime import datetime, timezone
from secrets import compare_digest
from typing import Any

from app.core.config import get_settings


GLOBAL_RESOURCES = {
    "tenants",
    "users",
    "waybill_modes",
    "waybill_templates",
    "waybill_template_fields",
    "workspaces",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, list[dict[str, Any]]] = {}
        self._next_ids: dict[str, int] = {}
        self._tokens: dict[str, dict[str, Any]] = {}
        self._seed()

    def _seed(self) -> None:
        workspace = self.create_record(
            "workspaces",
            {
                "name": "Default workspace",
                "code": "default",
                "remark": "Initial workspace for local development.",
            },
        )
        self.create_record(
            "users",
            {
                "username": "admin",
                "display_name": "Administrator",
                "role": "system_admin",
                "is_enabled": True,
            },
        )
        self.create_record(
            "roles",
            {
                "workspace_id": workspace["id"],
                "name": "system_admin",
                "remark": "Can administer all workspaces.",
            },
        )
        self.create_record(
            "waybill_modes",
            {
                "name": "Manual upload",
                "code": "manual_upload",
                "input_format": "excel_row",
                "is_enabled": True,
            },
        )

    def _next_id(self, resource: str) -> int:
        value = self._next_ids.get(resource, 1)
        self._next_ids[resource] = value + 1
        return value

    def create_record(self, resource: str, payload: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        record = dict(payload)
        if resource not in GLOBAL_RESOURCES:
            record.setdefault("workspace_id", settings.default_workspace_id)
        record.setdefault("is_deleted", False)
        record["id"] = self._next_id(resource)
        record["created_at"] = utc_now()
        record["updated_at"] = record["created_at"]
        self._data.setdefault(resource, []).append(record)
        return deepcopy(record)

    def list_records(self, resource: str, workspace_id: int | None = None) -> list[dict[str, Any]]:
        records = self._data.get(resource, [])
        filtered = [
            record
            for record in records
            if not record.get("is_deleted")
            and (workspace_id is None or record.get("workspace_id") == workspace_id)
        ]
        return deepcopy(filtered)

    def get_record(self, resource: str, record_id: int) -> dict[str, Any] | None:
        for record in self._data.get(resource, []):
            if record["id"] == record_id and not record.get("is_deleted"):
                return deepcopy(record)
        return None

    def authenticate(self, username: str, password: str) -> str | None:
        if compare_digest(username, "admin") and compare_digest(password, "admin123"):
            token = "dev-admin-token"
            self._tokens[token] = self.current_user()
            return token
        return None

    def current_user(self) -> dict[str, Any]:
        return {
            "id": 1,
            "username": "admin",
            "display_name": "Administrator",
            "workspaces": self.list_records("workspaces"),
            "roles": ["system_admin"],
        }


store = MemoryStore()
