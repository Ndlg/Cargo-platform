from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RecognitionRulePack, RecognitionRulePackRevision
from app.services.order_row_contract import ORDER_ROW_DRAFTS_CONTRACT_VERSION


RECOGNITION_RULE_PACK_CONTRACT_VERSION = "recognition_rule_pack_v1"
RULE_PACK_MISSING_STATUS = "rule_pack_missing"
SUPPORTED_ORDER_ROW_PARSERS = {"declarative_v1", "shoe_waybill_v1"}
AI_RULE_PACK_CODE = "ai-recognition-main"
AI_RULE_PACK_NAME = "AI识别规则包"


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
        was_deleted = pack.is_deleted
        pack.is_deleted = False
        pack.name = text_value(pack_meta.get("name"))
        pack.version = text_value(pack_meta.get("version")) or pack.version
        pack.description = description or text_value(pack_meta.get("description")) or (
            None if was_deleted else pack.description
        )
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


def save_ai_rule_profile(
    db: Session,
    *,
    tenant_id: int | None,
    workspace_id: int,
    session_id: str,
    profile: dict[str, Any],
    validate: Any,
    learning_record: dict[str, Any] | None = None,
) -> RecognitionRulePack:
    fingerprint = text_value(profile.get("fingerprint"))
    if not fingerprint:
        raise ValueError("AI candidate rule requires a format fingerprint.")
    grammar_signature = text_value(profile.get("grammar_signature"))
    strategy = text_value(profile.get("strategy"))
    selected_fields = tuple(profile.get("selected_fields") or ())

    def same_profile_slot(item: dict[str, Any]) -> bool:
        if (
            text_value(item.get("fingerprint")) != fingerprint
            or text_value(item.get("strategy")) != strategy
            or tuple(item.get("selected_fields") or ()) != selected_fields
        ):
            return False
        return (
            strategy == "structured_items_v1"
            or text_value(item.get("grammar_signature")) == grammar_signature
        )

    pack = db.scalar(
        select(RecognitionRulePack).where(
            RecognitionRulePack.workspace_id == workspace_id,
            RecognitionRulePack.code == AI_RULE_PACK_CODE,
        )
    )
    current_payload = (
        deepcopy(pack.payload)
        if pack is not None and not pack.is_deleted and isinstance(pack.payload, dict)
        else {}
    )
    parser_policy = object_value(current_payload.get("parser_policy"))
    profiles = [
        deepcopy(item)
        for item in list_value(parser_policy.get("format_profiles"))
        if isinstance(item, dict)
        and not same_profile_slot(item)
    ]
    profiles.append(
        {
            **deepcopy(profile),
            "provenance": {
                "source": "confirmed_ai_rule",
                "learning_session_id": session_id,
            },
        }
    )
    learning_records = [
        deepcopy(item)
        for item in list_value(current_payload.get("ai_learning_records"))
        if isinstance(item, dict) and text_value(item.get("session_id")) != session_id
    ]
    if learning_record is not None:
        learning_records.append(
            {
                **deepcopy(learning_record),
                "fingerprint": fingerprint,
                "grammar_signature": grammar_signature,
            }
        )
    latest_revision = int(
        db.scalar(
            select(func.max(RecognitionRulePackRevision.revision)).where(
                RecognitionRulePackRevision.workspace_id == workspace_id,
                RecognitionRulePackRevision.code == AI_RULE_PACK_CODE,
            )
        )
        or 0
    )
    if latest_revision == 0 and current_payload:
        db.add(
            RecognitionRulePackRevision(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                code=AI_RULE_PACK_CODE,
                revision=1,
                version=text_value(object_value(current_payload.get("pack")).get("version"))
                or (pack.version if pack is not None else "1.0.0"),
                payload=deepcopy(current_payload),
            )
        )
        latest_revision = 1
    revision = latest_revision + 1
    version = f"1.0.{revision}"
    payload = normalize_rule_pack_payload(
        {
            **current_payload,
            "contract_version": RECOGNITION_RULE_PACK_CONTRACT_VERSION,
            "pack": {
                **object_value(current_payload.get("pack")),
                "code": AI_RULE_PACK_CODE,
                "name": AI_RULE_PACK_NAME,
                "version": version,
            },
            "parser_policy": {
                **parser_policy,
                "order_row_parser": "declarative_v1",
                "fingerprint_strategy": "business_shape_v2",
                "format_profiles": profiles,
            },
            "ai_learning_records": learning_records,
        }
    )
    validation = validate(payload)
    if validation.get("status") != "valid":
        errors = validation.get("errors")
        raise ValueError(f"AI candidate rule is invalid: {errors or 'unknown validation error'}")

    if pack is None:
        pack = RecognitionRulePack(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=AI_RULE_PACK_NAME,
            code=AI_RULE_PACK_CODE,
            version=version,
            payload=payload,
            status="draft",
            is_enabled=False,
        )
        db.add(pack)
    else:
        pack.is_deleted = False
        pack.name = AI_RULE_PACK_NAME
        pack.version = version
        pack.payload = payload
    db.add(
        RecognitionRulePackRevision(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            code=AI_RULE_PACK_CODE,
            revision=revision,
            version=version,
            payload=deepcopy(payload),
        )
    )
    pack.description = f"管理员最后确认 AI 会话 {session_id} 后更新。"
    activate_recognition_rule_pack(db, workspace_id=workspace_id, pack=pack)
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
            "needs_review_count": parent_waybill_count,
            "special_count": 0,
            "pending_rule_pack_count": parent_waybill_count,
        },
        "source_type": source_type,
        "recognition_rule_pack": None,
        "parents": [],
        "rows": [],
    }
