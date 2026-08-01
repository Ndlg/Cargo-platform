from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id, require_write
from app.models import RawCaptureRecord, RecognitionRulePack, Role, UserWorkspace, Workspace
from app.services.order_row_reader import (
    order_rows_for_task,
    parser_raw_record_inputs,
    raw_records_for_task,
    task_order_row_drafts_payload,
)
from app.services.recognition_rule_packs import (
    ADAPTIVE_RULE_PACK_CODE,
    recognition_rule_pack_summary,
    save_learned_rule_profile,
    utc_now_iso,
)
from app.services.tenant_fingerprint_configs import selected_fields_for_fingerprint
from app.services.waybill_parser_client import (
    analyze_waybill_with_service,
    inspect_waybill_fingerprint_with_service,
    synthesize_rule_with_service,
    validate_rule_pack_with_service,
)
from app.services.waybill_reading import read_waybill_samples, task_documents


router = APIRouter(prefix="/format-learning", tags=["format-learning"])


class LearningOrderRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=512)
    sales_attr1: str = Field(default="", max_length=512)
    sales_attr2: str = Field(default="", max_length=512)
    quantity: int = Field(ge=1, le=100_000)
    remark: str = Field(default="", max_length=1000)


class FormatLearningPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_record_id: int = Field(ge=1)
    document_sequence: int = Field(ge=1)
    parent_sequence: int = Field(ge=1)


class FormatLearningRequest(FormatLearningPrepareRequest):
    expected_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rows: list[LearningOrderRow] = Field(min_length=1, max_length=100)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _positive_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _canonical_sha256(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _require_rule_admin(
    db: Session,
    *,
    current_user: CurrentUser,
    workspace_id: int,
) -> None:
    if current_user.is_system_admin:
        return
    membership = db.scalar(
        select(UserWorkspace.id)
        .join(
            Role,
            and_(
                Role.id == UserWorkspace.role_id,
                Role.tenant_id == UserWorkspace.tenant_id,
                Role.workspace_id == UserWorkspace.workspace_id,
                Role.is_deleted.is_(False),
            ),
        )
        .where(
            UserWorkspace.user_id == current_user.id,
            UserWorkspace.workspace_id == workspace_id,
            UserWorkspace.is_deleted.is_(False),
            Role.name == "workspace_admin",
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有工作空间管理员可以学习面单格式。",
        )


def _workspace_or_404(db: Session, workspace_id: int) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作空间不存在。")
    return workspace


def _record_or_404(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
    raw_record_id: int,
) -> RawCaptureRecord:
    record = db.scalar(
        select(RawCaptureRecord).where(
            RawCaptureRecord.id == raw_record_id,
            RawCaptureRecord.workspace_id == workspace_id,
            RawCaptureRecord.task_id == task_id,
            RawCaptureRecord.is_deleted.is_(False),
            RawCaptureRecord.archived_at.is_(None),
        )
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="所选面单不存在。")
    return record


def _isolated_payload(record: RawCaptureRecord, document_sequence: int) -> dict[str, Any]:
    parser_input = parser_raw_record_inputs([record])[0]
    payload = parser_input["payload"]
    documents = task_documents(payload)
    if documents:
        if document_sequence > len(documents):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="所选面单不存在。")
        task = payload.get("task")
        return {
            **payload,
            "task": {
                **(task if isinstance(task, dict) else {}),
                "documents": [documents[document_sequence - 1]],
            },
        }
    if document_sequence != 1:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="所选面单不存在。")
    return payload


def _field_values(value: Any) -> list[Any]:
    if value in (None, "", []):
        return []
    return value if isinstance(value, list) else [value]


def _inspection_payload(payload: dict[str, Any]) -> dict[str, Any]:
    fingerprint = payload.get("fingerprint")
    if isinstance(fingerprint, dict):
        return fingerprint
    return payload


def _learning_context(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
    raw_record_id: int,
    document_sequence: int,
) -> dict[str, Any]:
    record = _record_or_404(
        db,
        workspace_id=workspace_id,
        task_id=task_id,
        raw_record_id=raw_record_id,
    )
    raw_payload = _isolated_payload(record, document_sequence)
    source_component = _text(record.source_component) or "unknown"
    try:
        inspection_response = inspect_waybill_fingerprint_with_service(
            raw_payload=raw_payload,
            source_component=source_component,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="面单指纹服务暂时不可用。",
        ) from exc

    inspection = _inspection_payload(inspection_response)
    fingerprint_code = _text(inspection.get("fingerprint_code"))
    if not fingerprint_code or fingerprint_code == "UNKNOWN":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="当前面单尚不属于已支持的指纹类型。",
        )
    selected_keys = selected_fields_for_fingerprint(
        db,
        workspace_id=workspace_id,
        fingerprint_code=fingerprint_code,
    )
    if not selected_keys:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="当前租户尚未配置该面单指纹的学习字段。",
        )

    fields_by_key = {
        _text(field.get("key")): field
        for field in inspection.get("fields", [])
        if isinstance(field, dict) and _text(field.get("key"))
    }
    selected_fields = []
    for key in selected_keys:
        field = fields_by_key.get(key)
        if field is None:
            continue
        selected_fields.append(
            {
                "key": key,
                "label": _text(field.get("label")) or key,
                "path": _text(field.get("path")),
                "values": _field_values(
                    field.get("values") if "values" in field else field.get("value")
                ),
            }
        )
    if not selected_fields or not any(field["values"] for field in selected_fields):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="当前面单在租户所选字段中没有可用于学习的内容。",
        )

    try:
        analysis = analyze_waybill_with_service(
            raw_payload=raw_payload,
            source_component=source_component,
            selected_fields=[field["key"] for field in selected_fields],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="面单规则分析服务暂时不可用。",
        ) from exc

    structural_fingerprint = _text(analysis.get("structural_fingerprint"))
    grammar_signature = _text(analysis.get("grammar_signature"))
    evidence_sha256 = _text(analysis.get("evidence_sha256"))
    if (
        _text(analysis.get("fingerprint_code")) != fingerprint_code
        or not structural_fingerprint
        or not grammar_signature
        or not evidence_sha256
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="面单规则分析服务返回了不完整的证据。",
        )

    return {
        "record": record,
        "raw_payload": raw_payload,
        "source_component": source_component,
        "selected_keys": [field["key"] for field in selected_fields],
        "selected_fields": selected_fields,
        "analysis": analysis,
        "fingerprint": {
            "code": fingerprint_code,
            "name": _text(inspection.get("fingerprint_name")) or fingerprint_code,
            "structural_fingerprint": structural_fingerprint,
            "grammar_signature": grammar_signature,
        },
        "evidence_sha256": evidence_sha256,
    }


def _business_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        product = _text(item.get("product"))
        quantity = _positive_int(item.get("quantity"), 1)
        rows.append(
            {
                "product": product,
                "sales_attr1": _text(item.get("sales_attr1")),
                "sales_attr2": _text(item.get("sales_attr2")),
                "quantity": quantity,
                "remark": _text(item.get("remark")),
            }
        )
    return rows


def _location_rows(
    parents: list[dict[str, Any]],
) -> dict[tuple[int, int], dict[str, Any]]:
    mapped: dict[tuple[int, int], dict[str, Any]] = {}
    for index, parent in enumerate(parents, start=1):
        if not isinstance(parent, dict):
            continue
        raw_record_id = _positive_int(parent.get("raw_record_id"))
        parent_sequence = _positive_int(parent.get("parent_sequence"), index)
        if raw_record_id:
            mapped[(raw_record_id, parent_sequence)] = parent
    return mapped


def _location_diagnostics(
    diagnostics: list[dict[str, Any]],
) -> dict[tuple[int, int, int], dict[str, Any]]:
    mapped: dict[tuple[int, int, int], dict[str, Any]] = {}
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        raw_record_id = _positive_int(diagnostic.get("raw_record_id"))
        document_sequence = _positive_int(diagnostic.get("document_sequence"))
        parent_sequence = _positive_int(diagnostic.get("parent_sequence"))
        if raw_record_id and document_sequence and parent_sequence:
            mapped[(raw_record_id, document_sequence, parent_sequence)] = diagnostic
    return mapped


def _diagnostic_fingerprint(diagnostic: dict[str, Any] | None) -> dict[str, str] | None:
    if not diagnostic:
        return None
    nested = diagnostic.get("fingerprint")
    if isinstance(nested, dict):
        code = _text(nested.get("code") or nested.get("fingerprint_code"))
        structural = _text(
            nested.get("structural_fingerprint") or nested.get("fingerprint")
        )
        grammar = _text(nested.get("grammar_signature"))
        name = _text(nested.get("name") or nested.get("fingerprint_name")) or code
    else:
        code = _text(diagnostic.get("fingerprint_code"))
        structural = _text(diagnostic.get("structural_fingerprint") or nested)
        grammar = _text(diagnostic.get("grammar_signature"))
        name = _text(diagnostic.get("fingerprint_name")) or code
    if not code and not structural:
        return None
    return {
        "code": code,
        "name": name,
        "structural_fingerprint": structural,
        "grammar_signature": grammar,
    }


def _queue_payload(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
    include_all: bool,
) -> dict[str, Any]:
    records = raw_records_for_task(db, workspace_id=workspace_id, task_id=task_id)
    try:
        parser_payload = task_order_row_drafts_payload(
            db,
            workspace_id=workspace_id,
            task_id=task_id,
            limit=5000,
            offset=0,
        )
    except HTTPException as exc:
        if exc.status_code not in {
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        }:
            raise
        parser_payload = {"status": "parser_unavailable", "parents": [], "diagnostics": []}

    parents = [item for item in parser_payload.get("parents", []) if isinstance(item, dict)]
    diagnostics = [
        item for item in parser_payload.get("diagnostics", []) if isinstance(item, dict)
    ]
    parents_by_location = _location_rows(parents)
    diagnostics_by_location = _location_diagnostics(diagnostics)
    overall_status = _text(parser_payload.get("status"))

    items: list[dict[str, Any]] = []
    parent_sequence = 0
    for record in records:
        samples = read_waybill_samples(record) or [
            {
                "document_sequence": 1,
                "source_component": record.source_component,
                "source_index": record.source_index,
            }
        ]
        for fallback_document_sequence, sample in enumerate(samples, start=1):
            parent_sequence += 1
            document_sequence = _positive_int(
                sample.get("document_sequence"), fallback_document_sequence
            )
            key = (int(record.id), document_sequence, parent_sequence)
            diagnostic = diagnostics_by_location.get(key)
            parent = parents_by_location.get((int(record.id), parent_sequence))
            if diagnostic is not None:
                reason = _text(
                    diagnostic.get("reason")
                    or diagnostic.get("deterministic_reason")
                    or diagnostic.get("error")
                )
            elif parent is not None and parent.get("rows"):
                reason = ""
            else:
                reason = overall_status or "format_profile_missing"
            items.append(
                {
                    "raw_record_id": int(record.id),
                    "document_sequence": document_sequence,
                    "parent_sequence": parent_sequence,
                    "parent_label": _text((parent or {}).get("parent_label"))
                    or f"面单 {parent_sequence}",
                    "source_component": _text(sample.get("source_component"))
                    or _text(record.source_component),
                    "source_index": _text(sample.get("source_index"))
                    or _text(record.source_index),
                    "reason": reason,
                    "fingerprint": _diagnostic_fingerprint(diagnostic),
                    "selected_fields": [],
                    "rows": _business_rows((parent or {}).get("rows")),
                }
            )

    learning_required_count = sum(1 for item in items if item["reason"])
    visible_items = items if include_all else [item for item in items if item["reason"]]
    return {
        "contract_version": "format_learning_queue_v1",
        "task_id": task_id,
        "include_all": include_all,
        "summary": {
            "total_count": len(items),
            "learning_required_count": learning_required_count,
        },
        "items": visible_items,
    }


@router.get("/tasks/{task_id}")
def list_format_learning_queue(
    task_id: int,
    include_all: bool = Query(default=False),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    _require_rule_admin(
        db,
        current_user=_current_user,
        workspace_id=workspace_id,
    )
    return _queue_payload(
        db,
        workspace_id=workspace_id,
        task_id=task_id,
        include_all=include_all,
    )


@router.post("/tasks/{task_id}/prepare")
def prepare_format_learning(
    task_id: int,
    request: FormatLearningPrepareRequest,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    _require_rule_admin(
        db,
        current_user=_current_user,
        workspace_id=workspace_id,
    )
    context = _learning_context(
        db,
        workspace_id=workspace_id,
        task_id=task_id,
        raw_record_id=request.raw_record_id,
        document_sequence=request.document_sequence,
    )
    return {
        "contract_version": "format_learning_prepare_v1",
        "task_id": task_id,
        "raw_record_id": request.raw_record_id,
        "document_sequence": request.document_sequence,
        "parent_sequence": request.parent_sequence,
        "parent_label": f"面单 {request.parent_sequence}",
        "source_component": context["source_component"],
        "source_index": _text(context["record"].source_index),
        "reason": "",
        "fingerprint": context["fingerprint"],
        "evidence_sha256": context["evidence_sha256"],
        "selected_fields": context["selected_fields"],
        "rows": [],
    }


def _adaptive_pack(
    db: Session,
    *,
    workspace_id: int,
) -> RecognitionRulePack | None:
    return db.scalar(
        select(RecognitionRulePack).where(
            RecognitionRulePack.workspace_id == workspace_id,
            RecognitionRulePack.code == ADAPTIVE_RULE_PACK_CODE,
            RecognitionRulePack.is_deleted.is_(False),
        )
    )


def _learning_records(pack: RecognitionRulePack | None) -> list[dict[str, Any]]:
    payload = pack.payload if pack is not None and isinstance(pack.payload, dict) else {}
    records = payload.get("learning_records")
    return [item for item in records if isinstance(item, dict)] if isinstance(records, list) else []


def _learning_record_for_id(
    pack: RecognitionRulePack | None,
    learning_record_id: str,
) -> dict[str, Any] | None:
    return next(
        (
            record
            for record in _learning_records(pack)
            if _text(record.get("learning_record_id")) == learning_record_id
        ),
        None,
    )


def _valid_business_rows(rows: Any) -> bool:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        return False
    try:
        for row in rows:
            LearningOrderRow.model_validate(row)
    except Exception:
        return False
    return True


def _gold_samples_for_fingerprint(
    db: Session,
    *,
    workspace_id: int,
    fingerprint: str,
    current_learning_record_id: str,
) -> list[dict[str, Any]]:
    pack = _adaptive_pack(db, workspace_id=workspace_id)
    samples: list[dict[str, Any]] = []
    for learning_record in _learning_records(pack):
        if (
            _text(learning_record.get("fingerprint")) != fingerprint
            or _text(learning_record.get("learning_record_id"))
            == current_learning_record_id
        ):
            continue
        raw_record_id = _positive_int(learning_record.get("raw_record_id"))
        document_sequence = _positive_int(learning_record.get("document_sequence"))
        rows = learning_record.get("confirmed_rows")
        if not raw_record_id or not document_sequence or not _valid_business_rows(rows):
            raise ValueError("同类型面单的历史学习样本信息不完整，未保存新规则。")
        record = db.scalar(
            select(RawCaptureRecord).where(
                RawCaptureRecord.id == raw_record_id,
                RawCaptureRecord.workspace_id == workspace_id,
                RawCaptureRecord.is_deleted.is_(False),
            )
        )
        if record is None:
            raise ValueError("同类型面单的历史学习样本已不可用，未保存新规则。")
        samples.append(
            {
                "raw_payload": _isolated_payload(record, document_sequence),
                "source_component": _text(record.source_component) or "unknown",
                "rows": rows,
            }
        )
    return samples


def _affected_task_ids(
    pack: RecognitionRulePack,
    *,
    fingerprint: str,
    grammar_signature: str,
    current_task_id: int,
) -> list[int]:
    task_ids = {current_task_id}
    for record in _learning_records(pack):
        task_id = _positive_int(record.get("task_id"))
        if (
            _text(record.get("fingerprint")) == fingerprint
            and _text(record.get("grammar_signature")) == grammar_signature
            and task_id
        ):
            task_ids.add(task_id)
    return sorted(task_ids)


def _rerun_task_with_active_rule(
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
            summary = _rerun_task_with_active_rule(
                db,
                workspace_id=workspace_id,
                task_id=task_id,
            )
            results.append({"status": "completed", **summary})
        except Exception as exc:
            error = _text(exc)[:500] or "重算服务暂时不可用"
            results.append({"task_id": task_id, "status": "failed", "error": error})
            warnings.append(f"采集轮次 {task_id} 重算失败：{error}")
    return results, warnings


def _replay_summary(replay_report: Any) -> dict[str, int]:
    reports = [item for item in replay_report if isinstance(item, dict)] if isinstance(replay_report, list) else []
    return {
        "passed": sum(1 for item in reports if item.get("passed") is True),
        "total": len(reports),
    }


def _learned_response(
    *,
    pack: RecognitionRulePack,
    fingerprint: str,
    compiler_result: dict[str, Any],
    reruns: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    replay_report = compiler_result.get("replay_report") or []
    return {
        "contract_version": "format_learning_result_v1",
        "status": "learned",
        "message": "面单格式已学习，规则已启用并重算相关采集轮次。",
        "rule_pack": recognition_rule_pack_summary(pack),
        "format_fingerprint": fingerprint,
        "compiler_result": compiler_result,
        "replay_summary": _replay_summary(replay_report),
        "reruns": reruns,
        "warnings": warnings,
    }


@router.post("/tasks/{task_id}/learn")
def learn_format(
    task_id: int,
    request: FormatLearningRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    _require_rule_admin(
        db,
        current_user=current_user,
        workspace_id=workspace_id,
    )
    workspace = _workspace_or_404(db, workspace_id)
    context = _learning_context(
        db,
        workspace_id=workspace_id,
        task_id=task_id,
        raw_record_id=request.raw_record_id,
        document_sequence=request.document_sequence,
    )
    rows = [row.model_dump(mode="json") for row in request.rows]
    if request.expected_evidence_sha256 != context["evidence_sha256"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="面单内容或字段配置已变化，请重新打开该面单后再学习。",
        )
    fingerprint = context["fingerprint"]["structural_fingerprint"]
    grammar_signature = context["fingerprint"]["grammar_signature"]
    learning_record_id = _canonical_sha256(
        {
            "workspace_id": workspace_id,
            "raw_record_id": request.raw_record_id,
            "document_sequence": request.document_sequence,
            "fingerprint": fingerprint,
        }
    )

    existing_pack = _adaptive_pack(db, workspace_id=workspace_id)
    existing_record = _learning_record_for_id(existing_pack, learning_record_id)
    if (
        existing_pack is not None
        and existing_record is not None
        and existing_pack.is_enabled is True
        and existing_pack.status == "active"
        and existing_record.get("confirmed_rows") == rows
    ):
        compiler_result = existing_record.get("compiler_result")
        if not isinstance(compiler_result, dict):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="已有学习记录不完整，请重新选择该面单。",
            )
        task_ids = _affected_task_ids(
            existing_pack,
            fingerprint=fingerprint,
            grammar_signature=grammar_signature,
            current_task_id=task_id,
        )
        reruns, warnings = rerun_affected_tasks(
            db,
            workspace_id=workspace_id,
            task_ids=task_ids,
        )
        return _learned_response(
            pack=existing_pack,
            fingerprint=fingerprint,
            compiler_result=compiler_result,
            reruns=reruns,
            warnings=warnings,
        )

    try:
        gold_samples = _gold_samples_for_fingerprint(
            db,
            workspace_id=workspace_id,
            fingerprint=fingerprint,
            current_learning_record_id=learning_record_id,
        )
        synthesis = synthesize_rule_with_service(
            raw_payload=context["raw_payload"],
            source_component=context["source_component"],
            corrected_rows=rows,
            gold_samples=gold_samples,
            negative_samples=[],
            selected_fields=context["selected_keys"],
            expected_evidence_sha256=context["evidence_sha256"],
        )
        profile = synthesis.get("rule")
        if (
            synthesis.get("status") != "compiled"
            or not isinstance(profile, dict)
            or _text(profile.get("fingerprint")) != fingerprint
        ):
            reason = {
                "compiler_capability_missing": "当前规则引擎还不能固化这类面单。",
                "rule_replay_failed": "新规则无法完整复现当前及历史确认结果。",
                "candidate_invalid": "管理员填写的五字段结果无效。",
                "evidence_changed": "面单内容或租户字段配置已经变化，请重新打开后再学习。",
            }.get(_text(synthesis.get("status")), "识别规则合成失败。")
            raise ValueError(f"{reason}旧规则未修改。")
        replay_report = synthesis.get("replay_report") or []
        compiler_result = {
            "status": "compiled",
            "fingerprint": fingerprint,
            "grammar_signature": _text(profile.get("grammar_signature")),
            "replay_report": replay_report,
        }
        pack = save_learned_rule_profile(
            db,
            tenant_id=workspace.tenant_id,
            workspace_id=workspace_id,
            learning_record_id=learning_record_id,
            profile=profile,
            validate=lambda payload: validate_rule_pack_with_service(rule_pack=payload),
            learning_record={
                "learning_record_id": learning_record_id,
                "task_id": task_id,
                "raw_record_id": request.raw_record_id,
                "document_sequence": request.document_sequence,
                "parent_sequence": request.parent_sequence,
                "source_component": context["record"].source_component,
                "fingerprint_code": context["fingerprint"]["code"],
                "selected_fields": context["selected_keys"],
                "evidence_sha256": context["evidence_sha256"],
                "grammar_signature": grammar_signature,
                "confirmed_rows": rows,
                "compiler_result": compiler_result,
                "confirmed_at": utc_now_iso(),
                "confirmed_by": {
                    "user_id": current_user.id,
                    "username": current_user.username,
                    "display_name": current_user.display_name,
                },
                "replay_report": replay_report,
            },
        )
        db.flush()
        task_ids = _affected_task_ids(
            pack,
            fingerprint=fingerprint,
            grammar_signature=grammar_signature,
            current_task_id=task_id,
        )
        reruns, warnings = rerun_affected_tasks(
            db,
            workspace_id=workspace_id,
            task_ids=task_ids,
        )
        if warnings:
            raise ValueError(f"新规则整轮回放未通过：{warnings[0]}。旧规则未修改。")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="识别规则已被其他操作更新，请重新打开面单。",
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="识别规则服务暂时不可用，旧规则未修改。",
        ) from exc

    return _learned_response(
        pack=pack,
        fingerprint=fingerprint,
        compiler_result=compiler_result,
        reruns=reruns,
        warnings=warnings,
    )
