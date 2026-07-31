from __future__ import annotations

import secrets
from hashlib import sha256
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id, require_write
from app.models import RawCaptureRecord, RecognitionRulePack, Workspace
from app.services.ai_recognition_client import (
    approve_ai_recognition_session_with_service,
    feedback_ai_recognition_session_with_service,
    get_ai_recognition_session_with_service,
    reject_ai_recognition_session_with_service,
)
from app.services.order_row_reader import order_rows_for_task, parser_raw_record_inputs
from app.services.recognition_rule_packs import (
    AI_RULE_PACK_CODE,
    recognition_rule_pack_summary,
    save_ai_rule_profile,
    utc_now_iso,
)
from app.services.tenant_fingerprint_configs import selected_fields_for_fingerprint
from app.services.waybill_parser_client import (
    synthesize_rule_with_service,
    validate_rule_pack_with_service,
)
from app.services.waybill_reading import task_documents


router = APIRouter(tags=["ai-recognition"])
AiSessionId = Annotated[
    str,
    Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]


class AiProxyOrderRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=512)
    sales_attr1: str = Field(default="", max_length=512)
    sales_attr2: str = Field(default="", max_length=512)
    quantity: int = Field(ge=1, le=100_000)
    remark: str = Field(default="", max_length=1000)


class AiProxyFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="", max_length=2000)
    corrected_rows: list[AiProxyOrderRow] | None = Field(default=None, min_length=1, max_length=100)
    note: str = Field(default="", max_length=2000)


class AiApprovalActor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1)
    username: str = Field(min_length=1, max_length=255)
    display_name: str = Field(default="", max_length=255)


def session_in_workspace(session_id: str, workspace_id: int) -> dict[str, Any]:
    session = get_ai_recognition_session_with_service(session_id)
    if session.get("workspace_id") != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace access denied.",
        )
    return session


@router.get("/ai-recognition/sessions/{session_id}")
def get_ai_recognition_session(
    session_id: AiSessionId,
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    return session_in_workspace(session_id, workspace_id)


@router.post("/ai-recognition/sessions/{session_id}/feedback")
def feedback_ai_recognition_session(
    session_id: AiSessionId,
    request: AiProxyFeedbackRequest,
    _current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    session_in_workspace(session_id, workspace_id)
    return feedback_ai_recognition_session_with_service(
        session_id,
        request.model_dump(mode="json"),
    )


@router.post("/ai-recognition/sessions/{session_id}/approve")
def approve_ai_recognition_session(
    session_id: AiSessionId,
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    session_in_workspace(session_id, workspace_id)
    return approve_ai_recognition_session_with_service(
        session_id,
        actor={
            "id": current_user.id,
            "username": current_user.username,
            "display_name": current_user.display_name,
        },
    )


@router.post("/ai-recognition/sessions/{session_id}/reject")
def reject_ai_recognition_session(
    session_id: AiSessionId,
    _current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    session_in_workspace(session_id, workspace_id)
    return reject_ai_recognition_session_with_service(session_id)


class AiRuleApprovalRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    workspace_id: int = Field(ge=1)
    task_id: int = Field(ge=1)
    raw_record_id: int = Field(ge=1)
    document_sequence: int = Field(default=1, ge=1)
    format_fingerprint: str = Field(min_length=1, max_length=128)
    fingerprint_code: str = Field(min_length=1, max_length=128)
    candidate_output: dict[str, Any] = Field(default_factory=dict)
    model_candidate: dict[str, Any]
    administrator_rows: list[AiProxyOrderRow] = Field(min_length=1, max_length=100)
    model_candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    administrator_rows_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor: AiApprovalActor
    validate_only: bool = False


BUSINESS_ROW_FIELDS = (
    "product",
    "sales_attr1",
    "sales_attr2",
    "quantity",
    "remark",
)


def comparable_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        rows = [
            row
            for parent in payload.get("parents") or []
            if isinstance(parent, dict)
            for row in parent.get("rows") or []
            if isinstance(row, dict)
        ]
    return [
        {field: row.get(field, "") for field in BUSINESS_ROW_FIELDS}
        for row in rows
        if isinstance(row, dict)
    ]


def canonical_sha256(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def valid_business_rows(rows: list[dict[str, Any]]) -> bool:
    return 1 <= len(rows) <= 100 and all(
        isinstance(row["product"], str)
        and bool(row["product"].strip())
        and len(row["product"]) <= 512
        and isinstance(row["sales_attr1"], str)
        and len(row["sales_attr1"]) <= 512
        and isinstance(row["sales_attr2"], str)
        and len(row["sales_attr2"]) <= 512
        and isinstance(row["remark"], str)
        and len(row["remark"]) <= 1000
        and isinstance(row["quantity"], int)
        and not isinstance(row["quantity"], bool)
        and 1 <= row["quantity"] <= 100_000
        for row in rows
    )


def parser_input_for_document(
    record: RawCaptureRecord,
    document_sequence: int,
) -> dict[str, Any]:
    parser_input = parser_raw_record_inputs([record])[0]
    payload = parser_input["payload"]
    documents = task_documents(payload)
    if documents:
        if document_sequence > len(documents):
            raise ValueError("所选面单已经不存在，未同步规则。")
        task = payload.get("task")
        parser_input["payload"] = {
            **payload,
            "task": {
                **(task if isinstance(task, dict) else {}),
                "documents": [documents[document_sequence - 1]],
            },
        }
    elif document_sequence != 1:
        raise ValueError("所选面单已经不存在，未同步规则。")
    parser_input["document_sequence"] = document_sequence
    return parser_input


def gold_samples_for_fingerprint(
    db: Session,
    *,
    workspace_id: int,
    fingerprint: str,
    current_session_id: str,
) -> list[dict[str, Any]]:
    pack = db.scalar(
        select(RecognitionRulePack).where(
            RecognitionRulePack.workspace_id == workspace_id,
            RecognitionRulePack.code == AI_RULE_PACK_CODE,
            RecognitionRulePack.is_deleted.is_(False),
        )
    )
    payload = pack.payload if pack is not None and isinstance(pack.payload, dict) else {}
    records = payload.get("ai_learning_records")
    samples: list[dict[str, Any]] = []
    for learning_record in records if isinstance(records, list) else []:
        if (
            not isinstance(learning_record, dict)
            or learning_record.get("fingerprint") != fingerprint
            or learning_record.get("session_id") == current_session_id
        ):
            continue
        raw_record_id = learning_record.get("raw_record_id")
        document_sequence = learning_record.get("document_sequence")
        confirmed_rows = comparable_rows(
            {
                "rows": (
                    learning_record.get("administrator_rows")
                    or learning_record.get("confirmed_rows")
                )
            }
        )
        if (
            not isinstance(raw_record_id, int)
            or isinstance(raw_record_id, bool)
            or raw_record_id < 1
            or not isinstance(document_sequence, int)
            or isinstance(document_sequence, bool)
            or document_sequence < 1
            or not valid_business_rows(confirmed_rows)
        ):
            raise ValueError("同类型面单的历史确认样本信息不完整，未保存新规则。")
        record = db.scalar(
            select(RawCaptureRecord).where(
                RawCaptureRecord.id == raw_record_id,
                RawCaptureRecord.workspace_id == workspace_id,
                RawCaptureRecord.is_deleted.is_(False),
            )
        )
        if record is None:
            raise ValueError("同类型面单的历史确认样本已不可用，未保存新规则。")
        parser_input = parser_input_for_document(record, document_sequence)
        samples.append(
            {
                "raw_payload": parser_input["payload"],
                "source_component": record.source_component or "unknown",
                "rows": confirmed_rows,
            }
        )
    return samples


def ai_rule_pack_for_workspace(
    db: Session,
    *,
    workspace_id: int,
) -> RecognitionRulePack | None:
    return db.scalar(
        select(RecognitionRulePack).where(
            RecognitionRulePack.workspace_id == workspace_id,
            RecognitionRulePack.code == AI_RULE_PACK_CODE,
            RecognitionRulePack.is_deleted.is_(False),
        )
    )


def learning_record_for_session(
    pack: RecognitionRulePack | None,
    *,
    session_id: str,
) -> dict[str, Any] | None:
    payload = pack.payload if pack is not None and isinstance(pack.payload, dict) else {}
    records = payload.get("ai_learning_records")
    return next(
        (
            record
            for record in (records if isinstance(records, list) else [])
            if isinstance(record, dict) and record.get("session_id") == session_id
        ),
        None,
    )


def ensure_idempotent_approval_matches(
    learning_record: dict[str, Any],
    *,
    request: AiRuleApprovalRequest,
    expected_rows: list[dict[str, Any]],
) -> None:
    matches = (
        learning_record.get("task_id") == request.task_id
        and learning_record.get("raw_record_id") == request.raw_record_id
        and learning_record.get("document_sequence") == request.document_sequence
        and learning_record.get("fingerprint") == request.format_fingerprint
        and learning_record.get("fingerprint_code") == request.fingerprint_code
        and comparable_rows(
            {
                "rows": (
                    learning_record.get("administrator_rows")
                    or learning_record.get("confirmed_rows")
                )
            }
        )
        == expected_rows
        and (
            not learning_record.get("model_candidate_sha256")
            or learning_record.get("model_candidate_sha256")
            == request.model_candidate_sha256
        )
        and (
            not learning_record.get("administrator_rows_sha256")
            or learning_record.get("administrator_rows_sha256")
            == request.administrator_rows_sha256
        )
    )
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该 AI 会话已经确认过其他结果，请重新打开当前面单。",
        )


def affected_task_ids(
    pack: RecognitionRulePack,
    *,
    fingerprint: str,
    grammar_signature: str,
    current_task_id: int,
) -> list[int]:
    payload = pack.payload if isinstance(pack.payload, dict) else {}
    records = payload.get("ai_learning_records")
    task_ids = {current_task_id}
    for record in records if isinstance(records, list) else []:
        task_id = record.get("task_id") if isinstance(record, dict) else None
        if (
            isinstance(record, dict)
            and record.get("fingerprint") == fingerprint
            and str(record.get("grammar_signature") or "") == grammar_signature
            and isinstance(task_id, int)
            and not isinstance(task_id, bool)
            and task_id > 0
        ):
            task_ids.add(task_id)
    return sorted(task_ids)


def rerun_task_with_active_rule(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
) -> dict[str, Any]:
    from app.api.routes.product_sku_linking import preview_with_rules, saved_rule_payloads

    rows, _sources = order_rows_for_task(
        db,
        workspace_id=workspace_id,
        task_id=task_id,
    )
    preview = preview_with_rules(
        db,
        workspace_id=workspace_id,
        rows=rows,
        rules=saved_rule_payloads(db, workspace_id=workspace_id),
    )
    return {
        "task_id": task_id,
        "parsed_row_count": len(rows),
        "match_summary": preview.get("summary") or {},
    }


def rerun_affected_tasks(
    db: Session,
    *,
    workspace_id: int,
    task_ids: list[int],
) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for task_id in task_ids:
        try:
            summary = rerun_task_with_active_rule(
                db,
                workspace_id=workspace_id,
                task_id=task_id,
            )
            results.append(
                {
                    "task_id": task_id,
                    "status": "completed",
                    "parsed_row_count": int(summary.get("parsed_row_count") or 0),
                    "match_summary": summary.get("match_summary") or {},
                }
            )
        except Exception as exc:
            error = str(exc).strip()[:500] or "重算服务暂时不可用"
            results.append(
                {
                    "task_id": task_id,
                    "status": "failed",
                    "error": error,
                }
            )
            warnings.append(f"采集轮次 {task_id} 重算失败：{error}")
    return results, warnings


def approved_response(
    *,
    pack_summary: dict[str, Any] | None,
    format_fingerprint: str,
    reruns: list[dict[str, Any]],
    warnings: list[str],
    compiler_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "approved",
        "rule_pack": pack_summary,
        "format_fingerprint": format_fingerprint,
        "negative_replay": "not_available",
        "reruns": reruns,
        "warnings": warnings,
        "compiler_result": compiler_result,
    }


@router.post("/internal/ai-recognition/approve")
def approve_ai_rule(
    request: AiRuleApprovalRequest,
    db: Session = Depends(get_db),
    token: str = Header(default="", alias="X-AI-Recognition-Token"),
) -> dict[str, Any]:
    settings = get_settings()
    if (
        not settings.ai_recognition_enabled
        or not settings.ai_recognition_internal_token
        or not secrets.compare_digest(token, settings.ai_recognition_internal_token)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid AI recognition token.")

    administrator_rows = [
        row.model_dump(mode="json")
        for row in request.administrator_rows
    ]
    actor = request.actor.model_dump(mode="json")
    if (
        canonical_sha256(request.model_candidate) != request.model_candidate_sha256
        or canonical_sha256(administrator_rows) != request.administrator_rows_sha256
        or comparable_rows(request.candidate_output) != administrator_rows
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="AI 审批来源审计信息无效，未同步规则。",
        )

    workspace = db.get(Workspace, request.workspace_id)
    if workspace is None or workspace.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")

    record = db.scalar(
        select(RawCaptureRecord).where(
            RawCaptureRecord.id == request.raw_record_id,
            RawCaptureRecord.workspace_id == request.workspace_id,
            RawCaptureRecord.task_id == request.task_id,
            RawCaptureRecord.is_deleted.is_(False),
            RawCaptureRecord.archived_at.is_(None),
        )
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Raw waybill not found.")

    expected_rows = administrator_rows
    if not valid_business_rows(expected_rows):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="AI 候选缺少可确认的订单行，未同步规则。",
        )

    existing_pack = ai_rule_pack_for_workspace(
        db,
        workspace_id=request.workspace_id,
    )
    existing_learning_record = learning_record_for_session(
        existing_pack,
        session_id=request.session_id,
    )
    if existing_pack is not None and existing_learning_record is not None:
        ensure_idempotent_approval_matches(
            existing_learning_record,
            request=request,
            expected_rows=expected_rows,
        )
        if request.validate_only:
            return {
                "status": "valid",
                "format_fingerprint": request.format_fingerprint,
            }
        task_ids = affected_task_ids(
            existing_pack,
            fingerprint=request.format_fingerprint,
            grammar_signature=str(
                existing_learning_record.get("grammar_signature") or ""
            ),
            current_task_id=request.task_id,
        )
        reruns, warnings = rerun_affected_tasks(
            db,
            workspace_id=request.workspace_id,
            task_ids=task_ids,
        )
        return approved_response(
            pack_summary=recognition_rule_pack_summary(existing_pack),
            format_fingerprint=request.format_fingerprint,
            reruns=reruns,
            warnings=warnings,
            compiler_result=(
                existing_learning_record.get("compiler_result")
                if isinstance(existing_learning_record.get("compiler_result"), dict)
                else None
            ),
        )

    try:
        parser_input = parser_input_for_document(record, request.document_sequence)
        gold_samples = gold_samples_for_fingerprint(
            db,
            workspace_id=request.workspace_id,
            fingerprint=request.format_fingerprint,
            current_session_id=request.session_id,
        )
        selected_fields = selected_fields_for_fingerprint(
            db,
            workspace_id=request.workspace_id,
            fingerprint_code=request.fingerprint_code,
        )
        synthesis = synthesize_rule_with_service(
            raw_payload=parser_input["payload"],
            source_component=record.source_component or "unknown",
            corrected_rows=expected_rows,
            gold_samples=gold_samples,
            negative_samples=[],
            selected_fields=selected_fields,
        )
        profile = synthesis.get("rule")
        if (
            synthesis.get("status") != "compiled"
            or not isinstance(profile, dict)
            or profile.get("fingerprint") != request.format_fingerprint
        ):
            reason = {
                "compiler_capability_missing": "当前识别引擎还不能把这类面单固化成稳定规则。",
                "rule_replay_failed": "新规则无法完整复现当前及历史确认结果。",
                "candidate_invalid": "管理员确认的五字段结果无效。",
            }.get(str(synthesis.get("status") or ""), "识别规则合成失败。")
            raise ValueError(f"{reason}旧规则未修改。")
        compiler_result = {
            "status": "compiled",
            "fingerprint": request.format_fingerprint,
            "grammar_signature": profile.get("grammar_signature"),
            "replay_report": synthesis.get("replay_report") or [],
        }
        pack = save_ai_rule_profile(
            db,
            tenant_id=workspace.tenant_id,
            workspace_id=workspace.id,
            session_id=request.session_id,
            profile=profile,
            validate=lambda payload: validate_rule_pack_with_service(rule_pack=payload),
            learning_record={
                "session_id": request.session_id,
                "task_id": request.task_id,
                "raw_record_id": request.raw_record_id,
                "document_sequence": request.document_sequence,
                "source_component": record.source_component,
                "fingerprint_code": request.fingerprint_code,
                "grammar_signature": profile.get("grammar_signature"),
                "confirmed_rows": expected_rows,
                "model_candidate": request.model_candidate,
                "administrator_rows": expected_rows,
                "compiler_result": compiler_result,
                "model_candidate_sha256": request.model_candidate_sha256,
                "administrator_rows_sha256": request.administrator_rows_sha256,
                "approved_at": utc_now_iso(),
                "approved_by": actor,
                "replay_report": synthesis.get("replay_report") or [],
                "negative_replay": "not_available",
            },
        )
        if request.validate_only:
            db.rollback()
            return {
                "status": "valid",
                "format_fingerprint": request.format_fingerprint,
            }
        db.flush()
        pack_summary = recognition_rule_pack_summary(pack)
        task_ids = affected_task_ids(
            pack,
            fingerprint=request.format_fingerprint,
            grammar_signature=str(profile.get("grammar_signature") or ""),
            current_task_id=request.task_id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="识别规则已被另一个审批更新，请重新打开面单后再确认。",
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="识别规则校验服务暂时不可用，未保存候选规则。",
        ) from exc

    reruns, warnings = rerun_affected_tasks(
        db,
        workspace_id=request.workspace_id,
        task_ids=task_ids,
    )
    return approved_response(
        pack_summary=pack_summary,
        format_fingerprint=request.format_fingerprint,
        reruns=reruns,
        warnings=warnings,
        compiler_result=compiler_result,
    )
