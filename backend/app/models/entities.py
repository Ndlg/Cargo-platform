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


class WaybillMode(BaseModel):
    __tablename__ = "waybill_modes"
    __table_args__ = (UniqueConstraint("code", name="uk_waybill_mode_code"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    input_format: Mapped[str] = mapped_column(String(32), nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WaybillTemplate(BaseModel):
    __tablename__ = "waybill_templates"

    waybill_mode_id: Mapped[int] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    input_format: Mapped[str] = mapped_column(String(32), nullable=False)
    recognition_rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extraction_rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1", nullable=False)


class WaybillTemplateField(BaseModel):
    __tablename__ = "waybill_template_fields"

    waybill_template_id: Mapped[int] = mapped_column(nullable=False)
    target_field_code: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    extraction_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    sort_order: Mapped[int] = mapped_column(default=0, nullable=False)


class PrintTemplateConfig(WorkspaceModel):
    __tablename__ = "print_template_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "waybill_mode", "template_key", name="uk_print_template_workspace_key"),
    )

    waybill_mode: Mapped[str] = mapped_column(String(128), nullable=False)
    template_key: Mapped[str] = mapped_column(String(255), nullable=False)
    template_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    template_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_status: Mapped[str] = mapped_column(String(32), default="platform_structured", nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


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


class FieldDefinition(WorkspaceModel):
    __tablename__ = "field_definitions"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uk_field_definition_workspace_code"),)

    waybill_mode_id: Mapped[int | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    data_type: Mapped[str] = mapped_column(String(32), default="text", nullable=False)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_filterable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    use_for_matching: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_for_image_match: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_for_stall_match: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_for_grouping: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_as_quantity: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_as_remark: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    export_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    export_order: Mapped[int] = mapped_column(default=0, nullable=False)


class FieldRoleConfig(WorkspaceModel):
    __tablename__ = "field_role_configs"

    waybill_mode_id: Mapped[int | None] = mapped_column(nullable=True)
    field_definition_id: Mapped[int] = mapped_column(nullable=False)
    role_code: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class KeyFieldSet(WorkspaceModel):
    __tablename__ = "key_field_sets"

    waybill_mode_id: Mapped[int | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    field_codes: Mapped[list] = mapped_column(JSON, nullable=False)
    priority: Mapped[int] = mapped_column(default=100, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MatchRule(WorkspaceModel):
    __tablename__ = "match_rules"

    waybill_mode_id: Mapped[int | None] = mapped_column(nullable=True)
    key_field_set_id: Mapped[int] = mapped_column(nullable=False)
    match_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[int | None] = mapped_column(nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    priority: Mapped[int] = mapped_column(default=100, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


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


class ReportBatch(WorkspaceModel):
    __tablename__ = "report_batches"

    standard_detail_batch_id: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)


class ReportLine(WorkspaceModel):
    __tablename__ = "report_lines"

    report_batch_id: Mapped[int] = mapped_column(nullable=False)
    field_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    quantity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_asset_id: Mapped[int | None] = mapped_column(nullable=True)
    stall_id: Mapped[int | None] = mapped_column(nullable=True)


class ExceptionRecord(WorkspaceModel):
    __tablename__ = "exception_records"

    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[int | None] = mapped_column(nullable=True)
    exception_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)


class ExportRecord(WorkspaceModel):
    __tablename__ = "export_records"

    report_batch_id: Mapped[int | None] = mapped_column(nullable=True)
    export_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)


class OperationLog(WorkspaceModel):
    __tablename__ = "operation_logs"

    user_id: Mapped[int | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[int | None] = mapped_column(nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
