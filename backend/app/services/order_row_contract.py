from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


ORDER_ROW_DRAFTS_CONTRACT_VERSION = "order_row_drafts_v1"


@dataclass(frozen=True)
class OrderRowDraft:
    raw_record_id: int
    task_id: int | None
    parent_label: str
    child_label: str
    child_index: int
    child_count: int
    source_component: str
    source_index: str
    product: str
    sales_attr1: str
    sales_attr2: str
    quantity: int | None
    remark: str
    image_match_text: str
    original_text: str
    status: str
    review_reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParentWaybillDraft:
    raw_record_id: int
    task_id: int | None
    parent_label: str
    source_component: str
    source_index: str
    child_count: int
    rows: list[OrderRowDraft]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["rows"] = [row.as_dict() for row in self.rows]
        return payload
