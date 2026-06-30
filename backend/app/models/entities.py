from sqlalchemy import Boolean, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel, WorkspaceModel


class Tenant(BaseModel):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class Workspace(BaseModel):
    __tablename__ = "workspaces"

    tenant_id: Mapped[int | None] = mapped_column(index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class User(BaseModel):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Role(WorkspaceModel):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserWorkspace(BaseModel):
    __tablename__ = "user_workspaces"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uk_user_workspace"),)

    tenant_id: Mapped[int | None] = mapped_column(index=True, nullable=True)
    workspace_id: Mapped[int] = mapped_column(index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(index=True, nullable=False)
    role_id: Mapped[int | None] = mapped_column(nullable=True)


class Collector(WorkspaceModel):
    __tablename__ = "collectors"

    collector_id: Mapped[str] = mapped_column(String(128), nullable=False)
    collector_name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_machine: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    online_status: Mapped[str] = mapped_column(String(32), default="offline", nullable=False)
    last_heartbeat_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class CaptureTask(WorkspaceModel):
    __tablename__ = "capture_tasks"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    collector_id: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ended_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archived_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archived_by: Mapped[int | None] = mapped_column(nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class CaptureBatch(WorkspaceModel):
    __tablename__ = "capture_batches"

    task_id: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    record_count: Mapped[int] = mapped_column(default=0, nullable=False)


class RawCaptureRecord(WorkspaceModel):
    __tablename__ = "raw_capture_records"

    capture_batch_id: Mapped[int | None] = mapped_column(nullable=True)
    task_id: Mapped[int | None] = mapped_column(nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    collector_id: Mapped[int | None] = mapped_column(nullable=True)
    source_machine: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_component: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_index: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    captured_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    waybill_mode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_format: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    raw_payload: Mapped[str] = mapped_column(Text, nullable=False)
    source_columns: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parsed_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    standard_detail_id: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    archived_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archived_by: Mapped[int | None] = mapped_column(nullable=True)


class StandardDetailBatch(WorkspaceModel):
    __tablename__ = "standard_detail_batches"

    waybill_mode_id: Mapped[int | None] = mapped_column(nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)


class StandardDetail(WorkspaceModel):
    __tablename__ = "standard_details"

    standard_detail_batch_id: Mapped[int] = mapped_column(nullable=False)
    waybill_mode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    image_match_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    stall_match_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archived_by: Mapped[int | None] = mapped_column(nullable=True)


class ExportHeaderDefinition(WorkspaceModel):
    __tablename__ = "export_header_definitions"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uk_export_header_workspace_code"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    data_type: Mapped[str] = mapped_column(String(32), default="text", nullable=False)
    export_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    export_order: Mapped[int] = mapped_column(default=0, nullable=False)


class ProductMatchingRule(WorkspaceModel):
    __tablename__ = "product_matching_rules"

    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scope_type: Mapped[str] = mapped_column(String(64), default="current_batch", nullable=False)
    scope_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    product_id: Mapped[int] = mapped_column(nullable=False)
    product_match_fields: Mapped[list] = mapped_column(JSON, nullable=False)
    product_keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    product_match_type: Mapped[str] = mapped_column(String(32), default="contains", nullable=False)
    sku_match_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sku_id: Mapped[int | None] = mapped_column(nullable=True)
    image_asset_id: Mapped[int | None] = mapped_column(nullable=True)
    source_samples: Mapped[list | None] = mapped_column(JSON, nullable=True)
    field_sources: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    preview_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    revision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(default=100, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RecognitionRulePack(WorkspaceModel):
    __tablename__ = "recognition_rule_packs"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uk_recognition_rule_packs_workspace_code"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), default="1.0.0", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    activated_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Product(WorkspaceModel):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uk_products_workspace_name"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    stall_id: Mapped[int | None] = mapped_column(nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ProductSku(WorkspaceModel):
    __tablename__ = "product_skus"
    __table_args__ = (UniqueConstraint("workspace_id", "product_id", "name", name="uk_product_skus_product_name"),)

    product_id: Mapped[int] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    stall_id: Mapped[int | None] = mapped_column(nullable=True)
    image_asset_id: Mapped[int | None] = mapped_column(nullable=True)
    sort_order: Mapped[int] = mapped_column(default=100, nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Stall(WorkspaceModel):
    __tablename__ = "stalls"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ImageAsset(WorkspaceModel):
    __tablename__ = "image_assets"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size: Mapped[int | None] = mapped_column(nullable=True)
