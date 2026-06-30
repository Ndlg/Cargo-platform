from app.models import (
    CaptureBatch,
    CaptureTask,
    Collector,
    ExportHeaderDefinition,
    ImageAsset,
    Product,
    ProductMatchingRule,
    ProductSku,
    RawCaptureRecord,
    Role,
    Stall,
    StandardDetail,
    StandardDetailBatch,
    Tenant,
    User,
    Workspace,
)
from app.models.base import Base


RESOURCE_MODELS: dict[str, type[Base]] = {
    "tenants": Tenant,
    "workspaces": Workspace,
    "users": User,
    "roles": Role,
    "collectors": Collector,
    "capture_tasks": CaptureTask,
    "capture_batches": CaptureBatch,
    "raw_capture_records": RawCaptureRecord,
    "standard_detail_batches": StandardDetailBatch,
    "standard_details": StandardDetail,
    "export_header_definitions": ExportHeaderDefinition,
    "products": Product,
    "product_matching_rules": ProductMatchingRule,
    "product_skus": ProductSku,
    "stalls": Stall,
    "image_assets": ImageAsset,
}
