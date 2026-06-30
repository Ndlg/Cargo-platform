from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RawCaptureRecord, StandardDetail
from app.services.order_row_contract import OrderRowDraft, ParentWaybillDraft
from app.services.recognition_rule_packs import (
    active_recognition_rule_pack,
    recognition_rule_pack_summary,
    rule_pack_missing_order_rows_response,
)
from app.services.waybill_parser_client import (
    parse_order_row_drafts_with_service,
    waybill_parser_service_enabled,
)
from app.services.waybill_reading import empty_waybill_reading_diagnostic, read_waybill_samples


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def int_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_order_row_five_fields(values: dict[str, Any] | None) -> dict[str, str]:
    values = values or {}
    return {
        "product": text_value(values.get("product")),
        "sales_attr1": text_value(values.get("sales_attr1")),
        "sales_attr2": text_value(values.get("sales_attr2")),
        "quantity": text_value(values.get("quantity")),
        "remark": text_value(values.get("remark")),
    }


def parser_service_required(context: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"面单解析服务未启用，{context}没有使用旧解析兜底。请配置 WAYBILL_PARSER_URL 并启动独立解析服务。",
    )


def parser_standard_detail_input(detail: StandardDetail, parent_sequence: int) -> dict[str, Any]:
    return {
        "standard_detail_id": int(detail.id),
        "parent_sequence": parent_sequence,
        "field_values": detail.field_values if isinstance(detail.field_values, dict) else {},
    }


def parser_waybill_sample_input(sample: dict[str, Any], parent_sequence: int) -> dict[str, Any]:
    text_blocks = sample.get("text_blocks")
    warnings = sample.get("warnings")
    return {
        "raw_record_id": sample.get("raw_record_id"),
        "task_id": sample.get("task_id"),
        "parent_sequence": parent_sequence,
        "document_id": sample.get("document_id"),
        "document_sequence": sample.get("document_sequence"),
        "source_component": sample.get("source_component"),
        "source_index": sample.get("source_index"),
        "sample_text": sample.get("sample_text") or "",
        "text_blocks": text_blocks if isinstance(text_blocks, list) else [],
        "warnings": warnings if isinstance(warnings, list) else [],
    }


def order_row_sample_inputs_from_records(records: list[RawCaptureRecord]) -> list[dict[str, Any]]:
    sample_inputs: list[dict[str, Any]] = []
    for record in records:
        samples = read_waybill_samples(record)
        if not samples:
            diagnostic = empty_waybill_reading_diagnostic(record)
            diagnostic["sample_text"] = ""
            samples = [diagnostic]
        for sample in samples:
            sample_inputs.append(parser_waybill_sample_input(sample, len(sample_inputs) + 1))
    return sample_inputs


def has_readable_waybill_samples(sample_inputs: list[dict[str, Any]]) -> bool:
    """Ignore diagnostics/raw JSON placeholders when deciding the source of truth."""
    for sample in sample_inputs:
        sample_text = text_value(sample.get("sample_text"))
        if sample_text and not sample_text.lstrip().startswith(("{", "[")):
            return True

        text_blocks = sample.get("text_blocks")
        if not isinstance(text_blocks, list):
            continue
        for block in text_blocks:
            if not isinstance(block, dict):
                continue
            block_text = text_value(block.get("text"))
            if not block_text or block_text.lstrip().startswith(("{", "[")):
                continue
            source_path = text_value(block.get("source_path") or block.get("path"))
            source = text_value(block.get("source"))
            if source_path == "raw_payload" or source == "raw_payload":
                continue
            return True
    return False


def order_row_drafts_from_parser_payload(payload: dict[str, Any]) -> list[ParentWaybillDraft]:
    parents: list[ParentWaybillDraft] = []
    for parent_payload in payload.get("parents") or []:
        if not isinstance(parent_payload, dict):
            continue
        parent_rows = parent_payload.get("rows")
        if not isinstance(parent_rows, list) or not parent_rows:
            parent_rows = [
                row
                for row in payload.get("rows") or []
                if isinstance(row, dict)
                and text_value(row.get("parent_label")) == text_value(parent_payload.get("parent_label"))
            ]
        rows: list[OrderRowDraft] = []
        for row_payload in parent_rows:
            if not isinstance(row_payload, dict):
                continue
            rows.append(
                OrderRowDraft(
                    raw_record_id=int_value(row_payload.get("raw_record_id")) or 0,
                    task_id=int_value(row_payload.get("task_id")),
                    parent_label=text_value(row_payload.get("parent_label")),
                    child_label=text_value(row_payload.get("child_label")),
                    child_index=int_value(row_payload.get("child_index")) or 1,
                    child_count=int_value(row_payload.get("child_count")) or 1,
                    source_component=text_value(row_payload.get("source_component")),
                    source_index=text_value(row_payload.get("source_index")),
                    product=text_value(row_payload.get("product")),
                    sales_attr1=text_value(row_payload.get("sales_attr1")),
                    sales_attr2=text_value(row_payload.get("sales_attr2")),
                    quantity=int_value(row_payload.get("quantity")),
                    remark=text_value(row_payload.get("remark")),
                    image_match_text=text_value(row_payload.get("image_match_text")),
                    original_text=text_value(row_payload.get("original_text")),
                    status=text_value(row_payload.get("status")) or "draft",
                    review_reason=text_value(row_payload.get("review_reason")),
                )
            )
        parents.append(
            ParentWaybillDraft(
                raw_record_id=int_value(parent_payload.get("raw_record_id")) or (rows[0].raw_record_id if rows else 0),
                task_id=int_value(parent_payload.get("task_id")),
                parent_label=text_value(parent_payload.get("parent_label")),
                source_component=text_value(parent_payload.get("source_component")),
                source_index=text_value(parent_payload.get("source_index")),
                child_count=int_value(parent_payload.get("child_count")) or len(rows),
                rows=rows,
            )
        )
    return parents


def row_fields_from_order_draft(row: OrderRowDraft) -> dict[str, str]:
    fields = normalize_order_row_five_fields(
        {
            "product": row.product,
            "sales_attr1": row.sales_attr1,
            "sales_attr2": row.sales_attr2,
            "quantity": "" if row.quantity is None else str(row.quantity),
            "remark": row.remark,
        }
    )
    fields["_order_row_status"] = text_value(row.status)
    fields["_order_row_review_reason"] = text_value(row.review_reason)
    return fields


def sources_from_order_parents(
    parents: list[ParentWaybillDraft],
    *,
    standard_details_by_raw_id: dict[int, StandardDetail] | None = None,
    raw_records_by_id: dict[int, RawCaptureRecord] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    rows: list[dict[str, str]] = []
    sources: list[dict[str, Any]] = []
    standard_details_by_raw_id = standard_details_by_raw_id or {}
    raw_records_by_id = raw_records_by_id or {}
    for parent in parents:
        for row in parent.rows:
            rows.append(row_fields_from_order_draft(row))
            detail = standard_details_by_raw_id.get(int(row.raw_record_id))
            raw_record = raw_records_by_id.get(int(row.raw_record_id))
            sources.append(
                {
                    "source_type": "order_row_draft",
                    "row": row,
                    "parent": parent,
                    "standard_detail": detail,
                    "raw_record": raw_record,
                    "raw_record_id": row.raw_record_id,
                    "task_id": row.task_id,
                    "parent_label": row.parent_label,
                    "child_label": row.child_label,
                    "child_index": row.child_index,
                    "child_count": row.child_count,
                    "item_index": row.child_index,
                    "item_count": row.child_count,
                    "source_component": row.source_component,
                    "source_index": row.source_index,
                }
            )
    return rows, sources


def standard_details_for_task(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
) -> list[StandardDetail]:
    details = db.scalars(
        select(StandardDetail)
        .where(
            StandardDetail.workspace_id == workspace_id,
            StandardDetail.is_deleted.is_(False),
            StandardDetail.archived_at.is_(None),
        )
        .order_by(StandardDetail.id.asc())
        .limit(10000)
    ).all()
    matching: list[StandardDetail] = []
    for detail in details:
        values = detail.field_values if isinstance(detail.field_values, dict) else {}
        if (int_value(values.get("capture_task_id")) or 0) == task_id:
            matching.append(detail)
    return matching


def raw_records_for_task(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
) -> list[RawCaptureRecord]:
    return db.scalars(
        select(RawCaptureRecord)
        .where(
            RawCaptureRecord.workspace_id == workspace_id,
            RawCaptureRecord.task_id == task_id,
            RawCaptureRecord.is_deleted.is_(False),
            RawCaptureRecord.archived_at.is_(None),
        )
        .order_by(RawCaptureRecord.id.asc())
        .limit(10000)
    ).all()


def task_waybill_counts(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
) -> tuple[int, int]:
    raw_records = raw_records_for_task(db, workspace_id=workspace_id, task_id=task_id)
    return len(raw_records), sum(len(read_waybill_samples(raw_record)) for raw_record in raw_records)


def standard_detail_raw_id_map(details: list[StandardDetail]) -> dict[int, StandardDetail]:
    mapped: dict[int, StandardDetail] = {}
    for detail in details:
        values = detail.field_values if isinstance(detail.field_values, dict) else {}
        raw_record_id = int_value(values.get("raw_record_id"))
        if raw_record_id is not None:
            mapped[raw_record_id] = detail
    return mapped


def parse_standard_details_to_order_rows(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
    details: list[StandardDetail],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    active_pack = active_recognition_rule_pack(db, workspace_id=workspace_id)
    if active_pack is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="当前工作区没有启用识别规则包，请先导入并启用规则包。",
        )
    if not waybill_parser_service_enabled():
        raise parser_service_required("订单行")

    parser_inputs = [
        parser_standard_detail_input(detail, index)
        for index, detail in enumerate(details, start=1)
    ]
    try:
        payload = parse_order_row_drafts_with_service(
            task_id=task_id,
            standard_details=parser_inputs,
            raw_records=[],
            rule_pack=active_pack.payload,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="面单解析服务暂时不可用，订单行没有使用旧解析兜底。请稍后重试或检查解析服务。",
        ) from exc
    return sources_from_order_parents(
        order_row_drafts_from_parser_payload(payload),
        standard_details_by_raw_id=standard_detail_raw_id_map(details),
    )


def parse_waybill_sample_inputs_to_order_rows(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
    sample_inputs: list[dict[str, Any]],
    records: list[RawCaptureRecord] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    active_pack = active_recognition_rule_pack(db, workspace_id=workspace_id)
    if active_pack is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="当前工作区没有启用识别规则包，请先导入并启用规则包。",
        )
    if not waybill_parser_service_enabled():
        raise parser_service_required("订单行")

    try:
        payload = parse_order_row_drafts_with_service(
            task_id=task_id,
            standard_details=[],
            raw_records=[],
            waybill_samples=sample_inputs,
            rule_pack=active_pack.payload,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="面单解析服务暂时不可用，订单行没有使用旧解析兜底。请稍后重试或检查解析服务。",
        ) from exc
    details = standard_details_for_task(db, workspace_id=workspace_id, task_id=task_id)
    return sources_from_order_parents(
        order_row_drafts_from_parser_payload(payload),
        standard_details_by_raw_id=standard_detail_raw_id_map(details),
        raw_records_by_id={int(record.id): record for record in (records or [])},
    )


def parse_raw_records_to_order_rows(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
    records: list[RawCaptureRecord],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    return parse_waybill_sample_inputs_to_order_rows(
        db,
        workspace_id=workspace_id,
        task_id=task_id,
        sample_inputs=order_row_sample_inputs_from_records(records),
        records=records,
    )


def order_rows_for_task(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    records = raw_records_for_task(db, workspace_id=workspace_id, task_id=task_id)
    sample_inputs = order_row_sample_inputs_from_records(records)
    if has_readable_waybill_samples(sample_inputs):
        return parse_waybill_sample_inputs_to_order_rows(
            db,
            workspace_id=workspace_id,
            task_id=task_id,
            sample_inputs=sample_inputs,
            records=records,
        )

    details = standard_details_for_task(db, workspace_id=workspace_id, task_id=task_id)
    if details:
        return parse_standard_details_to_order_rows(
            db,
            workspace_id=workspace_id,
            task_id=task_id,
            details=details,
        )
    return [], []


def order_rows_for_selected_ids(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
    selected_ids: list[int],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    if not selected_ids:
        return [], []

    details = db.scalars(
        select(StandardDetail)
        .where(
            StandardDetail.workspace_id == workspace_id,
            StandardDetail.id.in_(selected_ids),
            StandardDetail.is_deleted.is_(False),
            StandardDetail.archived_at.is_(None),
        )
        .order_by(StandardDetail.id.asc())
    ).all()
    if details:
        direct_rows: list[dict[str, str]] = []
        direct_sources: list[dict[str, Any]] = []
        parser_details: list[StandardDetail] = []
        for detail in details:
            values = detail.field_values if isinstance(detail.field_values, dict) else {}
            has_parser_source = any(
                text_value(values.get(key))
                for key in (
                    "product_short_text",
                    "product_full_text",
                    "raw_product_text",
                    "print_text",
                    "standard_text",
                )
            )
            direct = five_fields_from_standard_detail(detail)
            if any(direct.values()) and not has_parser_source:
                direct_rows.append(direct)
                direct_sources.append({"source_type": "standard_detail", "detail": detail})
            else:
                parser_details.append(detail)

        parsed_rows: list[dict[str, str]] = []
        parsed_sources: list[dict[str, Any]] = []
        if parser_details:
            parsed_rows, parsed_sources = parse_standard_details_to_order_rows(
                db,
                workspace_id=workspace_id,
                task_id=task_id,
                details=parser_details,
            )
        return [*direct_rows, *parsed_rows], [*direct_sources, *parsed_sources]

    raw_records = db.scalars(
        select(RawCaptureRecord)
        .where(
            RawCaptureRecord.workspace_id == workspace_id,
            RawCaptureRecord.id.in_(selected_ids),
            RawCaptureRecord.is_deleted.is_(False),
            RawCaptureRecord.archived_at.is_(None),
        )
        .order_by(RawCaptureRecord.id.asc())
    ).all()
    return parse_raw_records_to_order_rows(
        db,
        workspace_id=workspace_id,
        task_id=task_id,
        records=raw_records,
    )


def five_fields_from_standard_detail(detail: StandardDetail) -> dict[str, str]:
    values = detail.field_values if isinstance(detail.field_values, dict) else {}
    direct = normalize_order_row_five_fields(values)
    if any(direct.values()):
        return direct
    return normalize_order_row_five_fields(
        {
            "product": values.get("product")
            or values.get("custom_product_text")
            or values.get("product_short_text")
            or values.get("product_full_text"),
            "sales_attr1": values.get("sales_attr1")
            or values.get("custom_sales_attr1_text")
            or values.get("custom_spec_text")
            or values.get("spec_text"),
            "sales_attr2": values.get("sales_attr2")
            or values.get("custom_sales_attr2_text")
            or values.get("custom_size_text"),
            "quantity": values.get("quantity") or values.get("custom_quantity_text") or values.get("product_count_text"),
            "remark": values.get("remark")
            or values.get("custom_item_remark_text")
            or values.get("buyer_remark")
            or values.get("seller_remark"),
        }
    )


def standard_details_for_scope(
    db: Session,
    *,
    workspace_id: int,
    scope_type: str,
    task_id: int | None = None,
    selected_ids: list[int] | None = None,
) -> list[StandardDetail]:
    statement = select(StandardDetail).where(
        StandardDetail.workspace_id == workspace_id,
        StandardDetail.is_deleted.is_(False),
        StandardDetail.archived_at.is_(None),
    )
    selected_ids = selected_ids or []
    if scope_type == "selected_records":
        if not selected_ids:
            return []
        statement = statement.where(StandardDetail.id.in_(selected_ids))
    statement = statement.order_by(StandardDetail.id.asc()).limit(2000)
    details = db.scalars(statement).all()
    if scope_type == "current_batch" and task_id:
        return [
            detail
            for detail in details
            if (int_value((detail.field_values or {}).get("capture_task_id")) or 0) == task_id
        ]
    return details


def task_order_row_drafts_payload(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    records = raw_records_for_task(db, workspace_id=workspace_id, task_id=task_id)
    sample_inputs = order_row_sample_inputs_from_records(records)
    if has_readable_waybill_samples(sample_inputs):
        active_pack = active_recognition_rule_pack(db, workspace_id=workspace_id)
        if active_pack is None:
            return rule_pack_missing_order_rows_response(
                task_id=task_id,
                parent_waybill_count=len(sample_inputs),
                source_type="waybill_samples",
            )
        if not waybill_parser_service_enabled():
            raise parser_service_required("订单行")

        try:
            payload = parse_order_row_drafts_with_service(
                task_id=task_id,
                standard_details=[],
                raw_records=[],
                waybill_samples=sample_inputs[offset : offset + limit],
                rule_pack=active_pack.payload,
            )
            payload.setdefault("recognition_rule_pack", recognition_rule_pack_summary(active_pack))
            return payload
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="面单解析服务暂时不可用，订单行没有使用旧解析兜底。",
            ) from exc

    details = standard_details_for_task(db, workspace_id=workspace_id, task_id=task_id)
    if details:
        active_pack = active_recognition_rule_pack(db, workspace_id=workspace_id)
        if active_pack is None:
            return rule_pack_missing_order_rows_response(
                task_id=task_id,
                parent_waybill_count=len(details),
                source_type="standard_details",
            )
        if not waybill_parser_service_enabled():
            raise parser_service_required("订单行")

        sliced_details = details[offset : offset + limit]
        parser_inputs = [
            parser_standard_detail_input(detail, offset + index)
            for index, detail in enumerate(sliced_details, start=1)
        ]
        try:
            payload = parse_order_row_drafts_with_service(
                task_id=task_id,
                standard_details=parser_inputs,
                raw_records=[],
                rule_pack=active_pack.payload,
            )
            payload.setdefault("recognition_rule_pack", recognition_rule_pack_summary(active_pack))
            return payload
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="面单解析服务暂时不可用，订单行没有使用旧解析兜底。",
            ) from exc

    if offset == 0 and not records:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Capture task records not found.")
    return {
        "contract_version": "order_row_drafts_v1",
        "task_id": task_id,
        "summary": {
            "parent_waybill_count": 0,
            "child_waybill_count": 0,
            "draft_count": 0,
            "needs_review_count": 0,
            "special_count": 0,
        },
        "parents": [],
        "rows": [],
    }
