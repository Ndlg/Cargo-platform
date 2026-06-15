from dataclasses import dataclass


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    display_name: str
    role_names: tuple[str, ...]
    tenant_ids: tuple[int, ...]
    workspace_ids: tuple[int, ...]

    @property
    def is_system_admin(self) -> bool:
        return "system_admin" in self.role_names

    @property
    def can_write(self) -> bool:
        return bool(self.role_names) and "readonly" not in self.role_names

    def allowed_workspace_ids(self) -> set[int]:
        return set(self.workspace_ids)

    def allowed_tenant_ids(self) -> set[int]:
        return set(self.tenant_ids)
