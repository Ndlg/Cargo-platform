from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id, require_write
from app.models import ImageAsset, Product, ProductSku, RawCaptureRecord, Stall, Workspace
from app.repositories.base import Repository, WorkspaceAccessError, model_to_dict
from app.repositories.registry import RESOURCE_MODELS
from app.services.waybill_reading import read_waybill_samples


RESOURCE_ROUTES = [
    ("/tenants", "tenants", "tenants"),
    ("/workspaces", "workspaces", "workspaces"),
    ("/users", "users", "users"),
    ("/roles", "roles", "roles"),
    ("/collectors", "collectors", "collectors"),
    ("/capture-tasks", "capture_tasks", "capture-tasks"),
    ("/capture-batches", "capture_batches", "capture-batches"),
    ("/raw-capture-records", "raw_capture_records", "raw-capture-records"),
    ("/standard-detail-batches", "standard_detail_batches", "standard-detail-batches"),
    ("/standard-details", "standard_details", "standard-details"),
    ("/export-header-definitions", "export_header_definitions", "export-header-definitions"),
    ("/products", "products", "products"),
    ("/product-skus", "product_skus", "product-skus"),
    ("/stalls", "stalls", "stalls"),
    ("/image-assets", "image_assets", "image-assets"),
]

SERVER_ADMIN_READ_RESOURCES = {
    "roles",
    "tenants",
    "users",
}
SERVER_ADMIN_WRITE_RESOURCES = {
    "tenants",
    "roles",
    "users",
    "workspaces",
}

STANDARD_DETAIL_LIST_RECORD_FIELDS = {
    "id",
    "tenant_id",
    "workspace_id",
    "standard_detail_batch_id",
    "waybill_mode",
    "image_match_status",
    "stall_match_status",
    "archived_at",
    "archived_by",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "is_deleted",
}
STANDARD_DETAIL_TOP_LEVEL_HEAVY_FIELDS = {
    "full_text",
    "raw_payload",
}
STANDARD_DETAIL_BUSINESS_FIELD_KEYS = {
    "source_platform",
    "logistics_no",
    "order_no",
    "shop_name",
    "product_short_text",
    "product_full_text",
    "product_count_text",
    "spec_text",
    "quantity",
    "buyer_remark",
    "seller_remark",
    "buyer_nick",
    "print_time",
    "pay_order_time",
    "create_order_time",
    "item_total_price",
    "item_total_count",
    "encrypted_waybill",
    "custom_area_kind",
    "custom_area_raw_text",
    "custom_area_lines",
    "sender_masked",
    "recipient_masked",
    "template_urls",
    "custom_product_text",
    "custom_sales_attr1_text",
    "custom_sales_attr2_text",
    "custom_quantity_text",
    "custom_item_remark_text",
    "custom_spec_text",
    "custom_size_text",
    "custom_item_raw_text",
    "custom_items",
    "standard_rows",
    "product_sku_linking_result",
    "product_sku_linking_results",
}


def clean_standard_detail_field_values(values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    return {
        key: value
        for key, value in values.items()
        if str(key) in STANDARD_DETAIL_BUSINESS_FIELD_KEYS
    }


def project_list_records(resource_name: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if resource_name != "standard_details":
        return records
    projected: list[dict[str, Any]] = []
    for record in records:
        slim = {key: value for key, value in record.items() if key in STANDARD_DETAIL_LIST_RECORD_FIELDS}
        if record.get("full_text"):
            full_text = str(record["full_text"])
            slim["full_text_preview"] = full_text if len(full_text) <= 160 else f"{full_text[:160].rstrip()}..."
        slim["field_values"] = clean_standard_detail_field_values(record.get("field_values"))
        for key in STANDARD_DETAIL_TOP_LEVEL_HEAVY_FIELDS:
            slim.pop(key, None)
        projected.append(slim)
    return projected


def project_get_record(resource_name: str, record: dict[str, Any]) -> dict[str, Any]:
    if resource_name != "standard_details":
        return record
    return {
        **record,
        "field_values": clean_standard_detail_field_values(record.get("field_values")),
    }


def attach_capture_task_waybill_counts(db: Session, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    task_ids = [int(record["id"]) for record in records if record.get("id") is not None]
    if not task_ids:
        return records

    raw_records = db.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.task_id.in_(task_ids),
            RawCaptureRecord.is_deleted.is_(False),
        )
    ).all()
    records_by_task_id: dict[int, list[RawCaptureRecord]] = {}
    for raw_record in raw_records:
        if raw_record.task_id is None:
            continue
        records_by_task_id.setdefault(int(raw_record.task_id), []).append(raw_record)

    enriched: list[dict[str, Any]] = []
    for record in records:
        task_id = int(record["id"])
        task_raw_records = records_by_task_id.get(task_id, [])
        waybill_count = sum(len(read_waybill_samples(raw_record)) for raw_record in task_raw_records)
        enriched.append(
            {
                **record,
                "record_count": waybill_count,
                "raw_record_count": len(task_raw_records),
                "waybill_count": waybill_count,
                "parent_waybill_count": waybill_count,
            }
        )
    return enriched


def ensure_server_admin_access(resource_name: str, current_user: CurrentUser, *, write: bool) -> None:
    restricted = SERVER_ADMIN_WRITE_RESOURCES if write else SERVER_ADMIN_READ_RESOURCES
    if resource_name in restricted and not current_user.is_system_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Server administrator access required.",
        )


def allowed_workspace_ids_for(db: Session, current_user: CurrentUser) -> set[int]:
    if not current_user.is_system_admin:
        return current_user.allowed_workspace_ids()
    return set(db.scalars(select(Workspace.id).where(Workspace.is_deleted.is_(False))).all())


def ensure_optional_stall_payload(db: Session, payload: dict[str, Any], workspace_id: int) -> None:
    if "stall_id" not in payload or payload.get("stall_id") in (None, ""):
        if payload.get("stall_id") == "":
            payload["stall_id"] = None
        return
    stall_id = payload.get("stall_id")
    if not isinstance(stall_id, int):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="stall_id must be an integer.")
    stall = db.scalars(
        select(Stall).where(
            Stall.id == stall_id,
            Stall.workspace_id == workspace_id,
            Stall.is_deleted.is_(False),
        )
    ).first()
    if stall is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="stall_id must belong to the current workspace.",
        )


def ensure_product_sku_create_payload(db: Session, payload: dict[str, Any], workspace_id: int) -> None:
    product_id = payload.get("product_id")
    if not isinstance(product_id, int):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="product_id is required.")
    product = db.scalars(
        select(Product).where(
            Product.id == product_id,
            Product.workspace_id == workspace_id,
            Product.is_deleted.is_(False),
        )
    ).first()
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="product_id must belong to the current workspace.",
        )


def ensure_stall_update_payload(
    db: Session,
    payload: dict[str, Any],
    *,
    record_id: int,
    model: type[Product] | type[ProductSku],
    allowed_workspace_ids: set[int],
) -> None:
    if "stall_id" not in payload:
        return
    record = db.scalars(
        select(model).where(
            model.id == record_id,
            model.workspace_id.in_(allowed_workspace_ids),
            model.is_deleted.is_(False),
        )
    ).first()
    if record is None:
        return
    ensure_optional_stall_payload(db, payload, int(record.workspace_id))


def restore_deleted_product(
    db: Session,
    payload: dict[str, Any],
    *,
    workspace_id: int,
    user_id: int | None,
) -> dict[str, Any] | None:
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    product = db.scalars(
        select(Product).where(
            Product.workspace_id == workspace_id,
            Product.name == name,
            Product.is_deleted.is_(True),
        )
    ).first()
    if product is None:
        return None

    product.is_deleted = False
    product.is_enabled = bool(payload.get("is_enabled", product.is_enabled))
    if "remark" in payload:
        product.remark = payload.get("remark")
    if "code" in payload:
        product.code = payload.get("code")
    if "stall_id" in payload:
        ensure_optional_stall_payload(db, payload, workspace_id)
        product.stall_id = payload.get("stall_id")
    product.updated_by = user_id
    db.commit()
    db.refresh(product)
    return model_to_dict(product)


def build_resource_router(resource_name: str, tag: str) -> APIRouter:
    router = APIRouter()
    model = RESOURCE_MODELS[resource_name]

    @router.get("")
    def list_items(
        db: Session = Depends(get_db),
        current_user: CurrentUser = Depends(get_current_user),
        workspace_id: int | None = Query(default=None, ge=1),
        product_id: int | None = Query(default=None, ge=1),
        task_id: int | None = Query(default=None, ge=1),
        q: str | None = Query(default=None, max_length=100),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=2000),
        include_archived: bool = Query(default=False),
    ) -> list[dict[str, Any]]:
        ensure_server_admin_access(resource_name, current_user, write=False)
        allowed_workspace_ids = allowed_workspace_ids_for(db, current_user)
        if resource_name == "product_skus" and product_id is not None:
            try:
                if workspace_id is not None and workspace_id not in allowed_workspace_ids:
                    raise WorkspaceAccessError("Workspace access denied.")
                statement = select(ProductSku).where(
                    ProductSku.product_id == product_id,
                    ProductSku.is_deleted.is_(False),
                )
                if workspace_id is None:
                    statement = statement.where(ProductSku.workspace_id.in_(allowed_workspace_ids))
                else:
                    statement = statement.where(ProductSku.workspace_id == workspace_id)
                keyword = (q or "").strip()
                if keyword:
                    statement = statement.where(ProductSku.name.ilike(f"%{keyword}%"))
                statement = statement.order_by(ProductSku.id.asc()).offset(offset).limit(limit)
                records = [model_to_dict(item) for item in db.scalars(statement).all()]
                return project_list_records(resource_name, records)
            except WorkspaceAccessError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if resource_name == "products" and (q or "").strip():
            try:
                if workspace_id is not None and workspace_id not in allowed_workspace_ids:
                    raise WorkspaceAccessError("Workspace access denied.")
                keyword = q.strip()
                statement = select(Product).where(
                    Product.name.ilike(f"%{keyword}%"),
                    Product.is_deleted.is_(False),
                )
                if workspace_id is None:
                    statement = statement.where(Product.workspace_id.in_(allowed_workspace_ids))
                else:
                    statement = statement.where(Product.workspace_id == workspace_id)
                statement = statement.order_by(Product.id.asc()).offset(offset).limit(limit)
                records = [model_to_dict(item) for item in db.scalars(statement).all()]
                return project_list_records(resource_name, records)
            except WorkspaceAccessError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if resource_name == "image_assets" and (q or "").strip():
            try:
                if workspace_id is not None and workspace_id not in allowed_workspace_ids:
                    raise WorkspaceAccessError("Workspace access denied.")
                statement = select(ImageAsset).where(
                    ImageAsset.name.ilike(f"%{q.strip()}%"),
                    ImageAsset.is_deleted.is_(False),
                )
                if workspace_id is None:
                    statement = statement.where(ImageAsset.workspace_id.in_(allowed_workspace_ids))
                else:
                    statement = statement.where(ImageAsset.workspace_id == workspace_id)
                statement = statement.order_by(ImageAsset.id.asc()).offset(offset).limit(limit)
                records = [model_to_dict(item) for item in db.scalars(statement).all()]
                return project_list_records(resource_name, records)
            except WorkspaceAccessError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if resource_name == "raw_capture_records" and task_id is not None:
            try:
                if workspace_id is not None and workspace_id not in allowed_workspace_ids:
                    raise WorkspaceAccessError("Workspace access denied.")
                statement = select(RawCaptureRecord).where(
                    RawCaptureRecord.task_id == task_id,
                    RawCaptureRecord.is_deleted.is_(False),
                )
                if not include_archived:
                    statement = statement.where(RawCaptureRecord.archived_at.is_(None))
                if workspace_id is None:
                    statement = statement.where(RawCaptureRecord.workspace_id.in_(allowed_workspace_ids))
                else:
                    statement = statement.where(RawCaptureRecord.workspace_id == workspace_id)
                statement = statement.order_by(RawCaptureRecord.id.asc()).offset(offset).limit(limit)
                records = [model_to_dict(item) for item in db.scalars(statement).all()]
                return project_list_records(resource_name, records)
            except WorkspaceAccessError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        repo = Repository(db, model)
        try:
            records = repo.list(
                workspace_id=workspace_id,
                allowed_workspace_ids=allowed_workspace_ids,
                offset=offset,
                limit=limit,
                include_archived=include_archived,
                order_by_id_desc=resource_name == "capture_tasks",
            )
            if resource_name == "workspaces" and not current_user.is_system_admin:
                allowed = current_user.allowed_workspace_ids()
                return [record for record in records if record["id"] in allowed]
            projected = project_list_records(resource_name, records)
            if resource_name == "capture_tasks":
                return attach_capture_task_waybill_counts(db, projected)
            return projected
        except WorkspaceAccessError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.post("", status_code=status.HTTP_201_CREATED)
    def create_item(
        payload: dict[str, Any] = Body(default_factory=dict),
        db: Session = Depends(get_db),
        current_user: CurrentUser = Depends(require_write),
        workspace_id: int = Depends(get_workspace_id),
    ) -> dict[str, Any]:
        ensure_server_admin_access(resource_name, current_user, write=True)
        if resource_name in {"products", "product_skus"}:
            ensure_optional_stall_payload(db, payload, workspace_id)
        if resource_name == "product_skus":
            ensure_product_sku_create_payload(db, payload, workspace_id)
        if resource_name == "products" and (restored := restore_deleted_product(
            db,
            payload,
            workspace_id=workspace_id,
            user_id=current_user.id,
        )):
            return restored
        repo = Repository(db, model)
        try:
            return repo.create(payload, workspace_id=workspace_id, user_id=current_user.id)
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{tag} record already exists.") from exc
        except WorkspaceAccessError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.get("/{record_id}")
    def get_item(
        record_id: int,
        db: Session = Depends(get_db),
        current_user: CurrentUser = Depends(get_current_user),
    ) -> dict[str, Any]:
        ensure_server_admin_access(resource_name, current_user, write=False)
        repo = Repository(db, model)
        record = repo.get(
            record_id,
            allowed_workspace_ids=allowed_workspace_ids_for(db, current_user),
        )
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{tag} record not found.")
        if resource_name == "workspaces" and not current_user.is_system_admin and record["id"] not in current_user.allowed_workspace_ids():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace access denied.")
        return project_get_record(resource_name, record)

    @router.patch("/{record_id}")
    def update_item(
        record_id: int,
        payload: dict[str, Any] = Body(default_factory=dict),
        db: Session = Depends(get_db),
        current_user: CurrentUser = Depends(require_write),
    ) -> dict[str, Any]:
        ensure_server_admin_access(resource_name, current_user, write=True)
        allowed_workspace_ids = allowed_workspace_ids_for(db, current_user)
        if resource_name == "products":
            ensure_stall_update_payload(
                db,
                payload,
                record_id=record_id,
                model=Product,
                allowed_workspace_ids=allowed_workspace_ids,
            )
        if resource_name == "product_skus":
            ensure_stall_update_payload(
                db,
                payload,
                record_id=record_id,
                model=ProductSku,
                allowed_workspace_ids=allowed_workspace_ids,
            )
        repo = Repository(db, model)
        record = repo.update(
            record_id,
            payload,
            allowed_workspace_ids=allowed_workspace_ids,
            user_id=current_user.id,
        )
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{tag} record not found.")
        return record

    @router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_item(
        record_id: int,
        db: Session = Depends(get_db),
        current_user: CurrentUser = Depends(require_write),
    ) -> None:
        ensure_server_admin_access(resource_name, current_user, write=True)
        repo = Repository(db, model)
        deleted = repo.soft_delete(
            record_id,
            allowed_workspace_ids=allowed_workspace_ids_for(db, current_user),
            user_id=current_user.id,
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{tag} record not found.")

    list_items.__name__ = f"list_{resource_name}"
    create_item.__name__ = f"create_{resource_name}"
    get_item.__name__ = f"get_{resource_name}"

    return router
