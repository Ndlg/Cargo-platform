from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RecognitionRulePack
from app.services.order_row_contract import ORDER_ROW_DRAFTS_CONTRACT_VERSION


RECOGNITION_RULE_PACK_CONTRACT_VERSION = "recognition_rule_pack_v1"
RULE_PACK_MISSING_STATUS = "rule_pack_missing"
SUPPORTED_ORDER_ROW_PARSERS = {"shoe_waybill_v1"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def object_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def text_value(value: Any) -> str:
    return str(value or "").strip()


def normalize_rule_pack_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = object_value(payload).copy()
    data["contract_version"] = text_value(data.get("contract_version")) or RECOGNITION_RULE_PACK_CONTRACT_VERSION
    if data["contract_version"] != RECOGNITION_RULE_PACK_CONTRACT_VERSION:
        raise ValueError(
            f"recognition rule pack contract mismatch: {data['contract_version']} "
            f"!= {RECOGNITION_RULE_PACK_CONTRACT_VERSION}"
        )

    pack = object_value(data.get("pack")).copy()
    code = text_value(pack.get("code"))
    name = text_value(pack.get("name"))
    if not code:
        raise ValueError("recognition rule pack requires pack.code.")
    if not name:
        raise ValueError("recognition rule pack requires pack.name.")

    parser_policy = object_value(data.get("parser_policy"))
    if not parser_policy:
        raise ValueError("recognition rule pack requires parser_policy.")
    parser_policy = parser_policy.copy()
    order_row_parser = text_value(parser_policy.get("order_row_parser"))
    if not order_row_parser:
        raise ValueError("recognition rule pack requires parser_policy.order_row_parser.")
    if order_row_parser not in SUPPORTED_ORDER_ROW_PARSERS:
        raise ValueError(f"unsupported parser_policy.order_row_parser: {order_row_parser}.")
    parser_policy["order_row_parser"] = order_row_parser

    data["pack"] = {
        **pack,
        "code": code,
        "name": name,
        "version": text_value(pack.get("version")) or "1.0.0",
    }
    data["parser_policy"] = parser_policy
    data.setdefault("product_matching_policy", {})
    data.setdefault("export_policy", {})
    return data


def recognition_rule_pack_summary(pack: RecognitionRulePack | None) -> dict[str, Any] | None:
    if pack is None:
        return None
    return {
        "id": pack.id,
        "name": pack.name,
        "code": pack.code,
        "version": pack.version,
        "status": pack.status,
        "is_enabled": pack.is_enabled,
        "activated_at": pack.activated_at,
    }


def active_recognition_rule_pack(db: Session, *, workspace_id: int) -> RecognitionRulePack | None:
    return db.scalar(
        select(RecognitionRulePack)
        .where(
            RecognitionRulePack.workspace_id == workspace_id,
            RecognitionRulePack.is_deleted.is_(False),
            RecognitionRulePack.is_enabled.is_(True),
            RecognitionRulePack.status == "active",
        )
        .order_by(RecognitionRulePack.updated_at.desc(), RecognitionRulePack.id.desc())
    )


def upsert_recognition_rule_pack(
    db: Session,
    *,
    tenant_id: int | None,
    workspace_id: int,
    payload: dict[str, Any],
    activate: bool = False,
    description: str | None = None,
) -> RecognitionRulePack:
    normalized = normalize_rule_pack_payload(payload)
    pack_meta = object_value(normalized.get("pack"))
    code = text_value(pack_meta.get("code"))

    pack = db.scalar(
        select(RecognitionRulePack).where(
            RecognitionRulePack.workspace_id == workspace_id,
            RecognitionRulePack.code == code,
            RecognitionRulePack.is_deleted.is_(False),
        )
    )
    if pack is None:
        pack = RecognitionRulePack(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=text_value(pack_meta.get("name")),
            code=code,
            version=text_value(pack_meta.get("version")) or "1.0.0",
            description=description or text_value(pack_meta.get("description")) or None,
            payload=normalized,
            status="draft",
            is_enabled=False,
        )
        db.add(pack)
    else:
        pack.name = text_value(pack_meta.get("name"))
        pack.version = text_value(pack_meta.get("version")) or pack.version
        pack.description = description or text_value(pack_meta.get("description")) or pack.description
        pack.payload = normalized

    if activate:
        activate_recognition_rule_pack(db, workspace_id=workspace_id, pack=pack)
    return pack


def activate_recognition_rule_pack(
    db: Session,
    *,
    workspace_id: int,
    pack: RecognitionRulePack,
) -> RecognitionRulePack:
    for existing in db.scalars(
        select(RecognitionRulePack).where(
            RecognitionRulePack.workspace_id == workspace_id,
            RecognitionRulePack.is_deleted.is_(False),
            RecognitionRulePack.is_enabled.is_(True),
        )
    ):
        existing.is_enabled = False
        if existing.status == "active":
            existing.status = "inactive"

    pack.status = "active"
    pack.is_enabled = True
    pack.activated_at = utc_now_iso()
    return pack


def rule_pack_missing_order_rows_response(
    *,
    task_id: int,
    parent_waybill_count: int,
    source_type: str,
) -> dict[str, Any]:
    return {
        "contract_version": ORDER_ROW_DRAFTS_CONTRACT_VERSION,
        "task_id": task_id,
        "status": RULE_PACK_MISSING_STATUS,
        "rule_pack_required": True,
        "message": "当前工作空间未启用识别规则包。请先导入并启用规则包，再进行面单识别。",
        "summary": {
            "parent_waybill_count": parent_waybill_count,
            "child_waybill_count": 0,
            "draft_count": 0,
            "needs_review_count": 0,
            "special_count": 0,
            "pending_rule_pack_count": parent_waybill_count,
        },
        "source_type": source_type,
        "recognition_rule_pack": None,
        "parents": [],
        "rows": [],
    }
