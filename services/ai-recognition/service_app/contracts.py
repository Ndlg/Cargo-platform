from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator


MAX_INPUT_BYTES = 2_000_000
MAX_RULE_BYTES = 65_536
SUPPORTED_STRATEGIES = {"structured_items_v1", "text_pipeline_v1"}
FORBIDDEN_RULE_KEYS = {
    "code",
    "command",
    "eval",
    "exec",
    "file",
    "file_path",
    "network",
    "python",
    "regex",
    "script",
    "shell",
    "url",
}


def validate_declarative_value(value: Any, *, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("candidate_rule exceeds maximum nesting depth")
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("candidate_rule object is too large")
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_RULE_KEYS:
                raise ValueError(f"candidate_rule contains forbidden key: {key}")
            validate_declarative_value(item, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 100:
            raise ValueError("candidate_rule list is too large")
        for item in value:
            validate_declarative_value(item, depth=depth + 1)
    elif isinstance(value, str) and len(value) > 4096:
        raise ValueError("candidate_rule string is too long")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("candidate_rule contains unsupported value")


class RecognizeRequest(BaseModel):
    workspace_id: int = Field(ge=1)
    task_id: int = Field(ge=1)
    raw_record_id: int = Field(ge=1)
    document_sequence: int = Field(default=1, ge=1)
    source_component: str = Field(min_length=1, max_length=128)
    deterministic_failure_reason: str = Field(default="", max_length=512)
    payload: dict[str, Any]
    field_selections: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def validate_payload_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("payload exceeds 2 MB")
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
        label = labels[info.field_name]
        if re.match(rf"^\s*{label}\s*(?:是|为|[:：])", value):
            raise ValueError("field value contains its field name")
        return value


class AiParent(BaseModel):
    source: dict[str, Any] = Field(default_factory=dict)
    rows: list[AiOrderRow] = Field(min_length=1, max_length=100)


class AiCandidate(BaseModel):
    contract_version: Literal["ai_waybill_candidate_v1"]
    fingerprint: str = Field(min_length=1, max_length=128)
    parents: list[AiParent] = Field(min_length=1, max_length=100)
    rule_evidence: list[str] = Field(default_factory=list, max_length=50)
    candidate_rule: dict[str, Any]
    warnings: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("candidate_rule")
    @classmethod
    def validate_candidate_rule(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("strategy") not in SUPPORTED_STRATEGIES:
            raise ValueError("candidate_rule strategy is unsupported")
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_RULE_BYTES:
            raise ValueError("candidate_rule exceeds 64 KB")
        validate_declarative_value(value)
        return value


class FeedbackRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
