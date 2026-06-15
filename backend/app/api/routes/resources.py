from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id, require_write
from app.models import MatchRule, Product, ProductSku, Stall, Workspace
from app.repositories.base import Repository, WorkspaceAccessError, model_to_dict
from app.repositories.registry import RESOURCE_MODELS


RESOURCE_ROUTES = [
    ("/tenants", "tenants", "tenants"),
    ("/workspaces", "workspaces", "workspaces"),
    ("/users", "users", "users"),
    ("/roles", "roles", "roles"),
    ("/collectors", "collectors", "collectors"),
    ("/capture-tasks", "capture_tasks", "capture-tasks"),
    ("/capture-batches", "capture_batches", "capture-batches"),
    ("/raw-capture-records", "raw_capture_records", "raw-capture-records"),
    ("/waybill-modes", "waybill_modes", "waybill-modes"),
    ("/waybill-templates", "waybill_templates", "waybill-templates"),
    ("/waybill-template-fields", "waybill_template_fields", "waybill-template-fields"),
    ("/standard-detail-batches", "standard_detail_batches", "standard-detail-batches"),
    ("/standard-details", "standard_details", "standard-details"),
    ("/field-definitions", "field_definitions", "field-definitions"),
    ("/field-role-configs", "field_role_configs", "field-role-configs"),
    ("/key-field-sets", "key_field_sets", "key-field-sets"),
    ("/match-rules", "match_rules", "match-rules"),
    ("/print-template-configs", "print_template_configs", "print-template-configs"),
    ("/products", "products", "products"),
    ("/product-skus", "product_skus", "product-skus"),
    ("/stalls", "stalls", "stalls"),
    ("/image-assets", "image_assets", "image-assets"),
    ("/report-batches", "report_batches", "report-batches"),
    ("/exceptions", "exception_records", "exceptions"),
    ("/export-records", "export_records", "export-records"),
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
    "waybill_modes",
    "waybill_templates",
    "waybill_template_fields",
}

MATCH_RULE_IDENTITY_FIELDS = {"match_values", "target_type", "target_id", "target_name"}


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


def ensure_match_rule_payload(db: Session, payload: dict[str, Any], workspace_id: int) -> None:
    match_values = payload.get("match_values")
    if not isinstance(match_values, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="match_values is required.")

    product_id = match_values.get("product_id")
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

    target_type = str(payload.get("target_type") or "")
    target_id = payload.get("target_id")
    if target_type == "product":
        if target_id != product.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="target_id must equal match_values.product_id for product rules.",
            )
        payload["target_name"] = product.name
        match_values["product_name"] = product.name
        return

    if target_type == "sku":
        if not isinstance(target_id, int):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="sku target_id is required.")
        sku = db.scalars(
            select(ProductSku).where(
                ProductSku.id == target_id,
                ProductSku.workspace_id == workspace_id,
                ProductSku.product_id == product.id,
                ProductSku.is_deleted.is_(False),
            )
        ).first()
        if sku is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="sku target_id must belong to match_values.product_id.",
            )
        if match_values.get("sku_id") not in {None, sku.id}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="match_values.sku_id must equal target_id.",
            )
        match_values["product_name"] = product.name
        match_values["sku_id"] = sku.id
        match_values["sku_name"] = sku.name
        payload["target_name"] = f"{product.name} / {sku.name}"
        return

    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="target_type must be product or sku.")


def ensure_match_rule_update_payload(
    db: Session,
    payload: dict[str, Any],
    *,
    record_id: int,
    allowed_workspace_ids: set[int],
) -> None:
    if not MATCH_RULE_IDENTITY_FIELDS.intersection(payload):
        return

    rule = db.scalars(
        select(MatchRule).where(
            MatchRule.id == record_id,
            MatchRule.workspace_id.in_(allowed_workspace_ids),
            MatchRule.is_deleted.is_(False),
        )
    ).first()
    if rule is None:
        return

    merged_payload = {
        "match_values": dict(rule.match_values) if isinstance(rule.match_values, dict) else {},
        "target_type": rule.target_type,
        "target_id": rule.target_id,
        "target_name": rule.target_name,
    }
    merged_payload.update(payload)
    if isinstance(merged_payload.get("match_values"), dict):
        merged_payload["match_values"] = dict(merged_payload["match_values"])

    ensure_match_rule_payload(db, merged_payload, int(rule.workspace_id))
    payload["match_values"] = merged_payload["match_values"]
    payload["target_name"] = merged_payload["target_name"]


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
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=2000),
    ) -> list[dict[str, Any]]:
        ensure_server_admin_access(resource_name, current_user, write=False)
        repo = Repository(db, model)
        try:
            records = repo.list(
                workspace_id=workspace_id,
                allowed_workspace_ids=allowed_workspace_ids_for(db, current_user),
                offset=offset,
                limit=limit,
            )
            if resource_name == "workspaces" and not current_user.is_system_admin:
                allowed = current_user.allowed_workspace_ids()
                return [record for record in records if record["id"] in allowed]
            return records
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
        if resource_name == "match_rules":
            ensure_match_rule_payload(db, payload, workspace_id)
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
        return record

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
        if resource_name == "match_rules":
            ensure_match_rule_update_payload(
                db,
                payload,
                record_id=record_id,
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
