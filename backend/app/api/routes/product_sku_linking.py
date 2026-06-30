from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id, require_write
from app.models import (
    ImageAsset,
    Product,
    ProductMatchingRule,
    ProductSku,
    Stall,
    StandardDetail,
    Workspace,
)
from app.services.order_row_reader import (
    five_fields_from_standard_detail,
    order_rows_for_selected_ids,
    order_rows_for_task,
    standard_details_for_scope as order_row_standard_details_for_scope,
    task_waybill_counts,
)
from app.services.product_sku_linking import (
    PRODUCT_SKU_LINKING_FIELDS,
    PRODUCT_SKU_LINKING_RESULTS_KEY,
    exportable_product_sku_linking_result,
    int_value,
    normalize_five_fields,
    product_sku_linking_contract,
    preview_product_sku_linking,
    text_value,
)
from app.services.recognition_rule_packs import RULE_PACK_MISSING_STATUS, active_recognition_rule_pack


router = APIRouter(prefix="/product-sku-linking", tags=["product-sku-linking"])


class ProductSkuLinkingRow(BaseModel):
    product: str = ""
    sales_attr1: str = ""
    sales_attr2: str = ""
    quantity: str = ""
    remark: str = ""


class ProductMatchingScope(BaseModel):
    scope_type: str = "global"
    task_id: int | None = None
    standard_detail_ids: list[int] = Field(default_factory=list)
    selected_record_ids: list[int] = Field(default_factory=list)
    confirmed_by_user: bool = True
    preview_impact_count: int | None = None


class ProductSkuLinkingRule(BaseModel):
    id: int | None = None
    name: str | None = None
    scope_type: str = "global"
    scope_payload: dict[str, Any] = Field(default_factory=dict)
    product_match_fields: list[str] = Field(default_factory=list)
    product_keyword: str = ""
    product_match_type: str = "contains"
    sku_match_fields: list[str] = Field(default_factory=list)
    product_id: int | None = None
    sku_id: int | None = None
    image_asset_id: int | None = None
    source_samples: list[dict[str, Any]] = Field(default_factory=list)
    field_sources: dict[str, str] = Field(default_factory=dict)
    preview_summary: dict[str, Any] = Field(default_factory=dict)
    revision: int = 1
    revision_note: str | None = None
    priority: int = 100
    is_enabled: bool = True

    # Stage81 draft payload compatibility. New callers should use product_* fields.
    match_fields: list[str] = Field(default_factory=list)
    field_values: dict[str, str] = Field(default_factory=dict)
    match_type: str = "contains"


class ProductSkuLinkingPreviewRequest(BaseModel):
    rows: list[ProductSkuLinkingRow] = Field(default_factory=list)
    scope: ProductMatchingScope | None = None
    rule: ProductSkuLinkingRule | None = None
    linking_rules: list[ProductSkuLinkingRule] = Field(default_factory=list)
    rule_ids: list[int] = Field(default_factory=list)
    include_saved_rules: bool = False


class ProductMatchingRuleSaveRequest(BaseModel):
    name: str | None = None
    scope: ProductMatchingScope = Field(default_factory=ProductMatchingScope)
    product_id: int
    product_match_fields: list[str]
    product_keyword: str
    product_match_type: str = "contains"
    sku_match_fields: list[str] = Field(default_factory=list)
    sku_id: int | None = None
    image_asset_id: int | None = None
    source_samples: list[dict[str, Any]] = Field(default_factory=list)
    field_sources: dict[str, str] = Field(default_factory=dict)
    preview_summary: dict[str, Any] = Field(default_factory=dict)
    revision_note: str | None = None
    priority: int = 100
    is_enabled: bool = True


class ProductMatchingRuleUpdateRequest(BaseModel):
    name: str | None = None
    scope: ProductMatchingScope | None = None
    product_id: int | None = None
    product_match_fields: list[str] | None = None
    product_keyword: str | None = None
    product_match_type: str | None = None
    sku_match_fields: list[str] | None = None
    sku_id: int | None = None
    image_asset_id: int | None = None
    source_samples: list[dict[str, Any]] | None = None
    field_sources: dict[str, str] | None = None
    preview_summary: dict[str, Any] | None = None
    revision_note: str | None = None
    priority: int | None = None
    is_enabled: bool | None = None


class ProductSkuLinkingApplyRequest(BaseModel):
    scope: ProductMatchingScope
    rule_ids: list[int] = Field(default_factory=list)
    include_enabled_rules: bool = True


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def tenant_id_for_workspace(db: Session, workspace_id: int) -> int | None:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.is_deleted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace access denied.")
    return workspace.tenant_id


def active_assets(db: Session, workspace_id: int) -> tuple[list[Product], list[ProductSku], list[ImageAsset]]:
    products = db.scalars(
        select(Product).where(
            Product.workspace_id == workspace_id,
            Product.is_enabled.is_(True),
            Product.is_deleted.is_(False),
        )
    ).all()
    skus = db.scalars(
        select(ProductSku).where(
            ProductSku.workspace_id == workspace_id,
            ProductSku.is_enabled.is_(True),
            ProductSku.is_deleted.is_(False),
        )
    ).all()
    images = db.scalars(
        select(ImageAsset).where(
            ImageAsset.workspace_id == workspace_id,
            ImageAsset.is_deleted.is_(False),
        )
    ).all()
    stall_ids = {
        stall_id
        for item in [*products, *skus]
        if (stall_id := int_value(getattr(item, "stall_id", None))) is not None
    }
    stalls_by_id = {}
    if stall_ids:
        stalls_by_id = {
            stall.id: stall.name
            for stall in db.scalars(
                select(Stall).where(
                    Stall.workspace_id == workspace_id,
                    Stall.id.in_(stall_ids),
                    Stall.is_deleted.is_(False),
                )
            ).all()
        }
    for item in [*products, *skus]:
        stall_id = int_value(getattr(item, "stall_id", None))
        setattr(item, "stall_name", stalls_by_id.get(stall_id or 0, ""))
    return products, skus, images


def scope_payload(scope: ProductMatchingScope) -> dict[str, Any]:
    return scope.model_dump(exclude={"scope_type"})


def selected_scope_ids(scope: ProductMatchingScope) -> list[int]:
    selected_ids: list[int] = []
    for item in [*scope.selected_record_ids, *scope.standard_detail_ids]:
        try:
            parsed_id = int(item)
        except (TypeError, ValueError):
            continue
        if parsed_id > 0 and parsed_id not in selected_ids:
            selected_ids.append(parsed_id)
    return selected_ids


def normalize_source_samples(samples: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for sample in samples or []:
        row = normalize_five_fields(sample)
        if any(row.values()):
            cleaned.append(row)
    return cleaned[:5]


def rows_for_preview(
    db: Session,
    *,
    workspace_id: int,
    payload: ProductSkuLinkingPreviewRequest,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    if payload.rows:
        rows = [row.model_dump() for row in payload.rows]
        sources = [{"source_type": "inline"} for _row in rows]
        return rows, sources
    if payload.scope is None:
        return [], []

    if payload.scope.scope_type == "current_batch" and payload.scope.task_id:
        return order_rows_for_task(db, workspace_id=workspace_id, task_id=payload.scope.task_id)

    if payload.scope.scope_type == "selected_records":
        return order_rows_for_selected_ids(
            db,
            workspace_id=workspace_id,
            task_id=payload.scope.task_id or 0,
            selected_ids=selected_scope_ids(payload.scope),
        )

    details = order_row_standard_details_for_scope(
        db,
        workspace_id=workspace_id,
        scope_type=payload.scope.scope_type,
        task_id=payload.scope.task_id,
        selected_ids=selected_scope_ids(payload.scope),
    )
    rows = [five_fields_from_standard_detail(detail) for detail in details]
    sources = [{"source_type": "standard_detail", "detail": detail} for detail in details]
    return rows, sources


def product_matching_rule_pack_missing_response(
    *,
    scope: ProductMatchingScope | None,
) -> dict[str, Any]:
    response = preview_product_sku_linking([], [], products=[], skus=[], images=[])
    response.update(
        {
            "status": RULE_PACK_MISSING_STATUS,
            "rule_pack_required": True,
            "message": "当前工作空间未启用识别规则包。请先导入并启用规则包，再检查或写回商品匹配结果。",
            "coverage": {
                "scope_type": scope.scope_type if scope else "none",
                "task_id": scope.task_id if scope else None,
                "standard_row_count": 0,
                "order_row_waybill_count": 0,
                "standard_detail_count": 0,
                "source_type_counts": {},
            },
        }
    )
    return response


def product_matching_scope_required_response(
    *,
    scope: ProductMatchingScope | None,
) -> dict[str, Any]:
    response = preview_product_sku_linking([], [], products=[], skus=[], images=[])
    response.update(
        {
            "status": "scope_required",
            "rule_pack_required": False,
            "message": "请选择采集批次或单行订单后再检查商品匹配；全局只表示学习记录可复用，不作为直接扫库的数据范围。",
            "coverage": {
                "scope_type": scope.scope_type if scope else "none",
                "task_id": scope.task_id if scope else None,
                "standard_row_count": 0,
                "order_row_waybill_count": 0,
                "standard_detail_count": 0,
                "source_type_counts": {},
            },
        }
    )
    return response


def scope_coverage_payload(
    db: Session,
    *,
    workspace_id: int,
    scope: ProductMatchingScope | None,
    rows: list[dict[str, str]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    source_type_counts: dict[str, int] = {}
    standard_detail_ids: set[int] = set()
    order_row_parent_keys: set[str] = set()
    for source in sources:
        source_type = str(source.get("source_type") or "unknown")
        source_type_counts[source_type] = source_type_counts.get(source_type, 0) + 1
        if source_type == "order_row_draft":
            parent_key = text_value(source.get("parent_label")) or text_value(source.get("raw_record_id"))
            if parent_key:
                order_row_parent_keys.add(parent_key)
            detail = source.get("standard_detail")
            if isinstance(detail, StandardDetail):
                standard_detail_ids.add(int(detail.id))
            continue
        detail = source.get("detail")
        if isinstance(detail, StandardDetail):
            standard_detail_ids.add(int(detail.id))

    order_row_waybill_count = len(order_row_parent_keys) or len(standard_detail_ids)
    coverage: dict[str, Any] = {
        "scope_type": scope.scope_type if scope else "inline",
        "standard_row_count": len(rows),
        "order_row_waybill_count": order_row_waybill_count,
        "standard_detail_count": len(standard_detail_ids),
        "source_type_counts": source_type_counts,
    }

    if scope and scope.scope_type == "current_batch" and scope.task_id:
        total_raw_record_count, total_waybill_count = task_waybill_counts(
            db,
            workspace_id=workspace_id,
            task_id=scope.task_id,
        )
        coverage.update(
            {
                "task_id": scope.task_id,
                "total_raw_record_count": total_raw_record_count,
                "total_waybill_count": total_waybill_count,
                "missing_order_row_count": max(total_waybill_count - order_row_waybill_count, 0),
            }
        )
    return coverage


def rule_to_payload(rule: ProductMatchingRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "scope_type": rule.scope_type,
        "scope_payload": rule.scope_payload or {},
        "product_id": rule.product_id,
        "product_match_fields": rule.product_match_fields or [],
        "product_keyword": rule.product_keyword,
        "product_match_type": rule.product_match_type,
        "sku_match_fields": rule.sku_match_fields or [],
        "sku_id": rule.sku_id,
        "image_asset_id": rule.image_asset_id,
        "source_samples": rule.source_samples or [],
        "field_sources": rule.field_sources or {},
        "preview_summary": rule.preview_summary or {},
        "revision": rule.revision,
        "revision_note": rule.revision_note,
        "priority": rule.priority,
        "is_enabled": rule.is_enabled,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def saved_rule_payloads(
    db: Session,
    *,
    workspace_id: int,
    rule_ids: list[int] | None = None,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    statement = select(ProductMatchingRule).where(
        ProductMatchingRule.workspace_id == workspace_id,
        ProductMatchingRule.is_deleted.is_(False),
    )
    if not include_disabled:
        statement = statement.where(ProductMatchingRule.is_enabled.is_(True))
    if rule_ids:
        statement = statement.where(ProductMatchingRule.id.in_(rule_ids))
    statement = statement.order_by(ProductMatchingRule.priority.asc(), ProductMatchingRule.id.asc())
    return [rule_to_payload(rule) for rule in db.scalars(statement).all()]


def validate_rule_assets(
    db: Session,
    *,
    workspace_id: int,
    product_id: int,
    sku_id: int | None,
    image_asset_id: int | None,
) -> None:
    product = db.get(Product, product_id)
    if product is None or product.workspace_id != workspace_id or product.is_deleted or not product.is_enabled:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="目标商品不存在或不可用。")
    if sku_id is not None:
        sku = db.get(ProductSku, sku_id)
        if (
            sku is None
            or sku.workspace_id != workspace_id
            or sku.product_id != product_id
            or sku.is_deleted
            or not sku.is_enabled
        ):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="目标 SKU 不属于当前商品或不可用。")
    if image_asset_id is not None:
        image = db.get(ImageAsset, image_asset_id)
        if image is None or image.workspace_id != workspace_id or image.is_deleted:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="目标图片不存在或不可用。")


def validate_five_field_codes(fields: list[str], *, label: str) -> None:
    invalid_fields = [field for field in fields if field not in PRODUCT_SKU_LINKING_FIELDS]
    if invalid_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label}只能选择面单解析后的五字段。",
        )


def preview_with_rules(
    db: Session,
    *,
    workspace_id: int,
    rows: list[dict[str, str]],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    products, skus, images = active_assets(db, workspace_id)
    return preview_product_sku_linking(rows, rules, products=products, skus=skus, images=images)


@router.get("/contract")
def get_product_sku_linking_contract(
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    return product_sku_linking_contract()


@router.get("/rules")
def list_product_matching_rules(
    include_disabled: bool = Query(default=False),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    rules = saved_rule_payloads(
        db,
        workspace_id=workspace_id,
        include_disabled=include_disabled,
    )
    return {"contract": product_sku_linking_contract(), "rules": rules}


@router.post("/preview")
def preview_product_sku_linking_matches(
    payload: ProductSkuLinkingPreviewRequest = Body(default_factory=ProductSkuLinkingPreviewRequest),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    if not payload.rows and active_recognition_rule_pack(db, workspace_id=workspace_id) is None:
        return product_matching_rule_pack_missing_response(scope=payload.scope)
    if not payload.rows and (payload.scope is None or payload.scope.scope_type == "global"):
        return product_matching_scope_required_response(scope=payload.scope)

    rows, sources = rows_for_preview(db, workspace_id=workspace_id, payload=payload)
    rules: list[dict[str, Any]] = []
    if payload.include_saved_rules or payload.rule_ids:
        rules.extend(saved_rule_payloads(db, workspace_id=workspace_id, rule_ids=payload.rule_ids))
    rules.extend([rule.model_dump() for rule in payload.linking_rules])
    if payload.rule is not None:
        rules.append(payload.rule.model_dump())
    preview = preview_with_rules(db, workspace_id=workspace_id, rows=rows, rules=rules)
    preview["coverage"] = scope_coverage_payload(
        db,
        workspace_id=workspace_id,
        scope=payload.scope,
        rows=rows,
        sources=sources,
    )
    return preview


@router.post("/rules", status_code=status.HTTP_201_CREATED)
def save_product_matching_rule(
    payload: ProductMatchingRuleSaveRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    if not payload.product_match_fields:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请选择用于识别商品的五字段。")
    validate_five_field_codes(payload.product_match_fields, label="商品匹配字段")
    validate_five_field_codes(payload.sku_match_fields, label="SKU 匹配字段")
    if payload.product_match_type not in {"contains", "exact"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="商品关键词匹配方式无效。")
    if not payload.product_keyword.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="商品关键词不能为空。")
    validate_rule_assets(
        db,
        workspace_id=workspace_id,
        product_id=payload.product_id,
        sku_id=payload.sku_id,
        image_asset_id=payload.image_asset_id,
    )

    rule_payload = {
        "name": payload.name,
        "scope_type": "global",
        "scope_payload": {"scope_type": "global"},
        "product_id": payload.product_id,
        "product_match_fields": payload.product_match_fields,
        "product_keyword": payload.product_keyword.strip(),
        "product_match_type": payload.product_match_type,
        "sku_match_fields": payload.sku_match_fields,
        "sku_id": payload.sku_id,
        "image_asset_id": payload.image_asset_id,
        "source_samples": normalize_source_samples(payload.source_samples),
        "field_sources": payload.field_sources,
        "preview_summary": payload.preview_summary,
        "revision_note": payload.revision_note,
        "priority": payload.priority,
        "is_enabled": payload.is_enabled,
    }
    preview_rows, _details = rows_for_preview(
        db,
        workspace_id=workspace_id,
        payload=ProductSkuLinkingPreviewRequest(scope=payload.scope),
    )
    if preview_rows:
        preview = preview_with_rules(db, workspace_id=workspace_id, rows=preview_rows, rules=[rule_payload])
        rule_payload["preview_summary"] = preview["summary"]
        if not rule_payload["source_samples"]:
            rule_payload["source_samples"] = [row for row in preview_rows[:5]]

    rule = ProductMatchingRule(
        tenant_id=tenant_id_for_workspace(db, workspace_id),
        workspace_id=workspace_id,
        created_by=current_user.id,
        updated_by=current_user.id,
        **rule_payload,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {"contract": product_sku_linking_contract(), "rule": rule_to_payload(rule)}


@router.patch("/rules/{rule_id}")
def update_product_matching_rule(
    rule_id: int,
    payload: ProductMatchingRuleUpdateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    rule = db.scalars(
        select(ProductMatchingRule).where(
            ProductMatchingRule.id == rule_id,
            ProductMatchingRule.workspace_id == workspace_id,
            ProductMatchingRule.is_deleted.is_(False),
        )
    ).first()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品匹配学习记录不存在。")

    update_payload = payload.model_dump(exclude_unset=True)
    if "source_samples" in update_payload and update_payload["source_samples"] is not None:
        update_payload["source_samples"] = normalize_source_samples(update_payload["source_samples"])
    if "product_match_fields" in update_payload and update_payload["product_match_fields"] is not None:
        validate_five_field_codes(update_payload["product_match_fields"], label="商品匹配字段")
    if "sku_match_fields" in update_payload and update_payload["sku_match_fields"] is not None:
        validate_five_field_codes(update_payload["sku_match_fields"], label="SKU 匹配字段")
    if any(key in update_payload for key in {"product_id", "sku_id", "image_asset_id"}):
        validate_rule_assets(
            db,
            workspace_id=workspace_id,
            product_id=int(update_payload.get("product_id") or rule.product_id),
            sku_id=update_payload.get("sku_id", rule.sku_id),
            image_asset_id=update_payload.get("image_asset_id", rule.image_asset_id),
        )

    revision_fields = {
        "product_id",
        "product_match_fields",
        "product_keyword",
        "product_match_type",
        "sku_match_fields",
        "sku_id",
        "image_asset_id",
    }
    should_revise = any(key in update_payload for key in revision_fields)
    for key, value in update_payload.items():
        if key == "scope":
            rule.scope_type = "global"
            rule.scope_payload = {"scope_type": "global"}
            continue
        if hasattr(rule, key):
            setattr(rule, key, value)
    if should_revise:
        rule.revision += 1
    rule.updated_by = current_user.id
    db.commit()
    db.refresh(rule)
    return {"contract": product_sku_linking_contract(), "rule": rule_to_payload(rule)}


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product_matching_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> None:
    rule = db.scalars(
        select(ProductMatchingRule).where(
            ProductMatchingRule.id == rule_id,
            ProductMatchingRule.workspace_id == workspace_id,
            ProductMatchingRule.is_deleted.is_(False),
        )
    ).first()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品匹配学习记录不存在。")
    rule.is_deleted = True
    rule.is_enabled = False
    rule.updated_by = current_user.id
    db.commit()


@router.post("/apply")
def apply_product_sku_linking_results(
    payload: ProductSkuLinkingApplyRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    if active_recognition_rule_pack(db, workspace_id=workspace_id) is None:
        response = product_matching_rule_pack_missing_response(scope=payload.scope)
        response.update(
            {
                "applied_detail_count": 0,
                "applied_standard_detail_count": 0,
                "applied_item_count": 0,
            }
        )
        return response
    if payload.scope is None or payload.scope.scope_type == "global":
        response = product_matching_scope_required_response(scope=payload.scope)
        response.update(
            {
                "applied_detail_count": 0,
                "applied_standard_detail_count": 0,
                "applied_item_count": 0,
            }
        )
        return response

    rows, sources = rows_for_preview(
        db,
        workspace_id=workspace_id,
        payload=ProductSkuLinkingPreviewRequest(scope=payload.scope),
    )
    rules = saved_rule_payloads(
        db,
        workspace_id=workspace_id,
        rule_ids=payload.rule_ids,
        include_disabled=not payload.include_enabled_rules,
    )
    if payload.include_enabled_rules and not payload.rule_ids:
        rules = saved_rule_payloads(db, workspace_id=workspace_id)
    preview = preview_with_rules(db, workspace_id=workspace_id, rows=rows, rules=rules)
    rule_ids = [int(rule["id"]) for rule in rules if isinstance(rule.get("id"), int)]
    applied_detail_count = 0
    applied_item_count = 0
    grouped_order_detail_results: dict[int, dict[str, Any]] = {}

    for source, row in zip(sources, preview["rows"], strict=False):
        payload_row = exportable_product_sku_linking_result(row)
        payload_row["item_index"] = int(source.get("item_index") or 1)
        payload_row["item_count"] = int(source.get("item_count") or 1)
        payload_row["product_matching_rule_ids"] = rule_ids
        payload_row["product_matching_applied_at"] = utc_now_text()

        if source.get("source_type") == "standard_detail":
            detail = source.get("detail")
            if not isinstance(detail, StandardDetail):
                continue
            values = dict(detail.field_values or {})
            values[PRODUCT_SKU_LINKING_RESULTS_KEY] = [payload_row]
            values["product_matching_applied_at"] = payload_row["product_matching_applied_at"]
            values["product_matching_rule_ids"] = rule_ids
            detail.field_values = values
            detail.updated_by = current_user.id
            applied_detail_count += 1
            applied_item_count += 1
            continue

        if source.get("source_type") == "order_row_draft":
            detail = source.get("standard_detail")
            if not isinstance(detail, StandardDetail):
                applied_item_count += 1
                continue
            payload_row["standard_detail_id"] = detail.id
            payload_row["raw_record_id"] = source.get("raw_record_id")
            payload_row["source_label"] = source.get("child_label")
            grouped = grouped_order_detail_results.setdefault(
                detail.id,
                {
                    "detail": detail,
                    "rows": [],
                    "applied_at": payload_row["product_matching_applied_at"],
                },
            )
            grouped["rows"].append(payload_row)
            applied_item_count += 1
            continue

    for grouped in grouped_order_detail_results.values():
        detail = grouped["detail"]
        values = dict(detail.field_values or {})
        values[PRODUCT_SKU_LINKING_RESULTS_KEY] = grouped["rows"]
        values["product_matching_applied_at"] = grouped["applied_at"]
        values["product_matching_rule_ids"] = rule_ids
        detail.field_values = values
        detail.updated_by = current_user.id
        applied_detail_count += 1

    db.commit()
    return {
        "contract": product_sku_linking_contract(),
        "applied_detail_count": applied_detail_count,
        "applied_standard_detail_count": applied_detail_count,
        "applied_item_count": applied_item_count,
        "summary": preview["summary"],
        "samples": preview["samples"],
    }
