from __future__ import annotations

import json
from typing import Any

import httpx


SYSTEM_PROMPT = """你是面单商品订单行解析器。只处理提供的脱敏商品相关数据，不做 OCR，不猜测收件人、订单号或快递单号。
输出必须完全符合 JSON Schema，并且只输出 parents 和 candidate_rule。每个商品生成独立订单行，不去重。
同时生成 candidate_rule。结构化数据只能使用：
{"strategy":"structured_items_v1","items_path":"数组路径[]","fields":{"product":"相对字段路径","sales_attr1":"相对字段路径","sales_attr2":"相对字段路径","quantity":"相对字段路径","remark":"相对字段路径"}}
文本数据只能使用 strategy=text_pipeline_v1、text_path、可选 item_split、steps 和 defaults。
不得输出 operations、脚本、正则、文件或网络操作。"""

OLLAMA_GRAMMAR_UNSUPPORTED = {
    "exclusiveMaximum",
    "exclusiveMinimum",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
}

CANDIDATE_RULE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["strategy"],
    "properties": {
        "strategy": {
            "type": "string",
            "enum": ["structured_items_v1", "text_pipeline_v1"],
        },
        "name": {"type": "string"},
        "description": {"type": "string"},
        "items_path": {"type": "string"},
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "product": {"type": "string"},
                "sales_attr1": {"type": "string"},
                "sales_attr2": {"type": "string"},
                "quantity": {"type": "string"},
                "remark": {"type": "string"},
                "image_match_text": {"type": "string"},
            },
        },
        "defaults": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "product": {"type": "string"},
                "sales_attr1": {"type": "string"},
                "sales_attr2": {"type": "string"},
                "quantity": {"type": "integer"},
                "remark": {"type": "string"},
                "image_match_text": {"type": "string"},
            },
        },
        "text_path": {"type": "string"},
        "item_split": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["op"],
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": [
                            "split",
                            "rsplit",
                            "extract_between",
                            "trim",
                            "strip_prefix",
                            "strip_suffix",
                            "to_positive_int",
                        ],
                    },
                    "source": {"type": "string"},
                    "delimiter": {"type": "string"},
                    "targets": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "text",
                                "product",
                                "sales_attr1",
                                "sales_attr2",
                                "quantity",
                                "remark",
                                "image_match_text",
                            ],
                        },
                    },
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "target": {"type": "string"},
                    "chars": {"type": "string"},
                    "literal": {"type": "string"},
                    "consume": {"type": "boolean"},
                },
            },
        },
    },
}

ORDER_ROW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "product",
        "sales_attr1",
        "sales_attr2",
        "quantity",
        "remark",
        "image_match_text",
    ],
    "properties": {
        "product": {"type": "string"},
        "sales_attr1": {"type": "string"},
        "sales_attr2": {"type": "string"},
        "quantity": {"type": "integer"},
        "remark": {"type": "string"},
        "image_match_text": {"type": "string"},
    },
}

OLLAMA_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["parents", "candidate_rule"],
    "properties": {
        "parents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rows"],
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": ORDER_ROW_SCHEMA,
                    },
                },
            },
        },
        "candidate_rule": CANDIDATE_RULE_SCHEMA,
    },
}


def ollama_json_schema(value: Any | None = None) -> Any:
    if value is None:
        value = OLLAMA_OUTPUT_SCHEMA
    if isinstance(value, dict):
        return {
            key: ollama_json_schema(item)
            for key, item in value.items()
            if key not in OLLAMA_GRAMMAR_UNSUPPORTED
        }
    if isinstance(value, list):
        return [ollama_json_schema(item) for item in value]
    return value


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

    def recognize(
        self,
        payload: dict[str, Any],
        fingerprint: str,
        feedback: list[str] | None = None,
    ) -> dict[str, Any]:
        user_payload = {
            "fingerprint": fingerprint,
            "sanitized_payload": payload,
            "administrator_feedback": feedback or [],
        }
        response = self.http_client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                    },
                ],
                "format": ollama_json_schema(),
                "stream": False,
                "think": False,
                "keep_alive": "10m",
                "options": {"temperature": 0, "num_ctx": 4096},
            },
        )
        response.raise_for_status()
        body = response.json()
        content = body.get("message", {}).get("content")
        if not isinstance(content, str):
            raise ValueError("Ollama response has no message content")
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("Ollama response content is not a JSON object")
        return result
