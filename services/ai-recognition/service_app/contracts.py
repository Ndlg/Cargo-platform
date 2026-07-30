from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


MAX_INPUT_BYTES = 2_000_000


class RecognizeRequest(BaseModel):
    workspace_id: int = Field(ge=1)
    task_id: int = Field(ge=1)
    raw_record_id: int = Field(ge=1)
    document_sequence: int = Field(default=1, ge=1)
    source_component: str = Field(min_length=1, max_length=128)
    deterministic_failure_reason: str = Field(default="", max_length=512)
    evidence: dict[str, Any]

    @field_validator("evidence")
    @classmethod
    def validate_evidence_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("evidence exceeds 2 MB")
        return value


class FingerprintInspectRequest(BaseModel):
    source_component: str = Field(default="", max_length=128)
    payload: dict[str, Any]

    @field_validator("payload")
    @classmethod
    def validate_payload_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("payload exceeds 2 MB")
        return value


class AiOrderRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=512)
    sales_attr1: str = Field(default="", max_length=512)
    sales_attr2: str = Field(default="", max_length=512)
    quantity: int = Field(ge=1, le=100_000)
    remark: str = Field(default="", max_length=1000)

    @field_validator("product", "sales_attr1", "sales_attr2", "remark")
    @classmethod
    def reject_field_label_prefix(cls, value: str, info: ValidationInfo) -> str:
        labels = {
            "product": r"(?:商品(?:名称)?|产品(?:名称)?)",
            "sales_attr1": r"销售属性\s*1",
            "sales_attr2": r"销售属性\s*2",
            "remark": r"备注",
        }
        if re.match(rf"^\s*{labels[info.field_name]}\s*(?:是|为|[:：])", value):
            raise ValueError("field value contains its field name")
        return value


class SpanSelectionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_span_ids: list[str] = Field(min_length=1, max_length=50)
    sales_attr1_span_ids: list[str] = Field(default_factory=list, max_length=50)
    sales_attr2_span_ids: list[str] = Field(default_factory=list, max_length=50)
    quantity_span_id: str = Field(min_length=1, max_length=128)
    remark_span_ids: list[str] = Field(default_factory=list, max_length=50)


class SpanSelectionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[SpanSelectionRow] = Field(min_length=1, max_length=100)


class FeedbackRequest(BaseModel):
    message: str = Field(default="", max_length=2000)
    corrected_rows: list[AiOrderRow] | None = Field(default=None, min_length=1, max_length=100)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_feedback(self) -> FeedbackRequest:
        if not self.message.strip() and not self.note.strip() and not self.corrected_rows:
            raise ValueError("feedback is empty")
        return self
