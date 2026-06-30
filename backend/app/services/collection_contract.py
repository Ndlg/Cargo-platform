from typing import Any, Protocol

from app.models import CaptureTask, Collector, RawCaptureRecord


COLLECTION_MODULE_OUTPUT_CONTRACT = "raw_capture_record"
COLLECTION_MODULE_SIMILARITY_POLICY = "no_similarity_or_fingerprint_decisions"
COLLECTION_MODULE_RULE_POLICY = "no_field_product_or_similarity_rules"

RAW_CAPTURE_RECORD_CONTRACT_FIELDS = (
    "id",
    "tenant_id",
    "workspace_id",
    "task_id",
    "collector_id",
    "document_id",
    "source_machine",
    "source_component",
    "source_index",
    "dedupe_key",
    "captured_at",
    "payload_format",
    "raw_payload",
    "source_columns",
    "status",
)

RAW_CAPTURE_RECORD_SOURCE_METADATA_FIELDS = (
    "tenant_id",
    "workspace_id",
    "task_id",
    "collector_id",
    "document_id",
    "source_machine",
    "source_component",
    "source_index",
    "dedupe_key",
    "captured_at",
    "payload_format",
    "source_columns",
)


class RawCaptureRecordInput(Protocol):
    document_id: str | None
    source_machine: str | None
    source_component: str | None
    source_index: str | None
    dedupe_key: str | None
    payload_format: str
    raw_payload: str
    source_columns: dict[str, Any] | None
    captured_at: str | None


def build_raw_capture_record(
    *,
    collector: Collector,
    task: CaptureTask,
    payload: RawCaptureRecordInput,
    captured_at: str,
) -> RawCaptureRecord:
    """Create raw_capture_record with audit metadata and no learned-rule decisions."""
    return RawCaptureRecord(
        tenant_id=collector.tenant_id,
        workspace_id=collector.workspace_id,
        task_id=task.id,
        collector_id=collector.id,
        document_id=payload.document_id,
        source_machine=payload.source_machine or collector.source_machine,
        source_component=payload.source_component,
        source_index=payload.source_index,
        dedupe_key=payload.dedupe_key,
        waybill_mode=None,
        payload_format=payload.payload_format,
        raw_payload=payload.raw_payload,
        source_columns=payload.source_columns,
        parsed_payload=None,
        captured_at=payload.captured_at or captured_at,
        status="pending",
    )
