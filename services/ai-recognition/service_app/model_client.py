from __future__ import annotations

import json
from typing import Any

import httpx


SYSTEM_PROMPT = """你只负责从给定证据片段中选择字段来源，不解析原始面单，也不编写规则。
每个商品输出一行；同一打印中的重复商品行不得去重。
只能原样返回证据中存在的 span_id，不得输出商品文字、字段路径、解析步骤、规则或解释。
product_span_ids 至少一个；quantity_span_id 必须选择正整数数量片段。
同一个 span_id 在一行内只能属于一个字段。"""


def _id_array(span_ids: list[str], *, minimum: int = 0) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": 50,
        "uniqueItems": True,
        "items": {"type": "string", "enum": span_ids},
    }


def ollama_json_schema(evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    spans = evidence.get("spans", []) if isinstance(evidence, dict) else []
    span_ids = [
        str(span["span_id"])
        for span in spans
        if isinstance(span, dict) and str(span.get("span_id") or "")
    ]
    quantity_ids = [
        str(span["span_id"])
        for span in spans
        if isinstance(span, dict)
        and str(span.get("span_id") or "")
        and str(span.get("label") or span.get("token_class") or "")
        == "positive_integer_quantity"
    ] or span_ids
    row_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["product_span_ids", "quantity_span_id"],
        "properties": {
            "product_span_ids": _id_array(span_ids, minimum=1),
            "sales_attr1_span_ids": _id_array(span_ids),
            "sales_attr2_span_ids": _id_array(span_ids),
            "quantity_span_id": {"type": "string", "enum": quantity_ids},
            "remark_span_ids": _id_array(span_ids),
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["rows"],
        "properties": {
            "rows": {
                "type": "array",
                "minItems": 1,
                "maxItems": 100,
                "items": row_schema,
            }
        },
    }


class OllamaModelClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http_client = http_client or httpx.Client(timeout=120.0)

    def recognize(self, evidence: dict[str, Any]) -> dict[str, Any]:
        response = self.http_client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    },
                ],
                "format": ollama_json_schema(evidence),
                "stream": False,
                "think": False,
                "keep_alive": "10m",
                "options": {"temperature": 0, "num_ctx": 4096},
            },
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content")
        if not isinstance(content, str):
            raise ValueError("Ollama response has no message content")
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("Ollama response content is not a JSON object")
        return result
