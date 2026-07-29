from __future__ import annotations

import json
from typing import Any

import httpx


SYSTEM_PROMPT = """你是面单商品订单行解析器。只处理提供的脱敏商品相关数据，不做 OCR，不猜测收件人、订单号或快递单号。
输出必须完全符合 JSON Schema，并且只输出 parents 和 candidate_rule。每个商品生成独立订单行，不去重。
订单行只能填写当前样本里的实际值，不得填写代码、JSONPath、“无”、“完整商品行原文”等占位文字。
candidate_rule 必须能在当前脱敏数据上重放出与 parents 完全相同的订单行。text_path 必须是从根开始的完整路径，数组段写成 []，例如 task.documents[].contents[].data.ITEM_INFO。
文本规则按顺序执行，初始只有 text 和 defaults；后续步骤只能读取 text、defaults 或前一步已写入的字段。
同时生成 candidate_rule。结构化数据只能使用：
{"strategy":"structured_items_v1","items_path":"数组路径[]","fields":{"product":"相对字段路径","sales_attr1":"相对字段路径","sales_attr2":"相对字段路径","quantity":"相对字段路径","remark":"相对字段路径"}}
文本数据示例：
{"strategy":"text_pipeline_v1","text_path":"task.documents[].contents[].data.ITEM_INFO","steps":[{"op":"split","source":"text","delimiter":"|","targets":["product","sales_attr1","sales_attr2","quantity"]},{"op":"to_positive_int","target":"quantity"}]}
若 ITEM_INFO 为“商品名称 属性1;属性2 【1件】”，规则顺序应为：先从 text 的“【”到“件】”提取 quantity 并 consume；再 rsplit text 的“;”到 product、sales_attr2；再 rsplit product 的最后一个空格到 product、sales_attr1；最后把 quantity 转为正整数。
不得输出 operations、脚本、正则、文件或网络操作。"""

ROW_FIELD_PROPERTIES = {
    "product": {"type": "string"},
    "sales_attr1": {"type": "string"},
    "sales_attr2": {"type": "string"},
    "quantity": {"type": "string"},
    "remark": {"type": "string"},
    "image_match_text": {"type": "string"},
}

DEFAULT_PROPERTIES = {
    **ROW_FIELD_PROPERTIES,
    "quantity": {"type": "integer"},
}

TEXT_STATE_FIELDS = [
    "text",
    "product",
    "sales_attr1",
    "sales_attr2",
    "quantity",
    "remark",
    "image_match_text",
]

TEXT_STEP_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["op", "source", "delimiter", "targets"],
            "properties": {
                "op": {"type": "string", "enum": ["split", "rsplit"]},
                "source": {"type": "string", "enum": TEXT_STATE_FIELDS},
                "delimiter": {"type": "string"},
                "targets": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 10,
                    "items": {"type": "string", "enum": TEXT_STATE_FIELDS},
                },
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["op", "source", "start", "end", "target"],
            "properties": {
                "op": {"const": "extract_between"},
                "source": {"type": "string", "enum": TEXT_STATE_FIELDS},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "target": {"type": "string", "enum": TEXT_STATE_FIELDS},
                "consume": {"type": "boolean"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["op", "target"],
            "properties": {
                "op": {"const": "trim"},
                "target": {"type": "string", "enum": TEXT_STATE_FIELDS},
                "chars": {"type": "string"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["op", "target", "literal"],
            "properties": {
                "op": {"type": "string", "enum": ["strip_prefix", "strip_suffix"]},
                "target": {"type": "string", "enum": TEXT_STATE_FIELDS},
                "literal": {"type": "string"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["op", "target"],
            "properties": {
                "op": {"const": "to_positive_int"},
                "target": {"const": "quantity"},
            },
        },
    ],
}

STRUCTURED_RULE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["strategy", "items_path", "fields"],
    "properties": {
        "strategy": {"const": "structured_items_v1"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "items_path": {"type": "string"},
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "required": ["product", "quantity"],
            "properties": ROW_FIELD_PROPERTIES,
        },
        "defaults": {
            "type": "object",
            "additionalProperties": False,
            "properties": DEFAULT_PROPERTIES,
        },
    },
}

TEXT_RULE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["strategy", "text_path", "steps"],
    "properties": {
        "strategy": {"const": "text_pipeline_v1"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "text_path": {"type": "string"},
        "item_split": {"type": "string"},
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": TEXT_STEP_SCHEMA,
        },
        "defaults": {
            "type": "object",
            "additionalProperties": False,
            "properties": DEFAULT_PROPERTIES,
        },
    },
}

CANDIDATE_RULE_SCHEMA = {
    "oneOf": [
        STRUCTURED_RULE_SCHEMA,
        TEXT_RULE_SCHEMA,
    ]
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
        **ROW_FIELD_PROPERTIES,
        "quantity": {"type": "integer"},
    },
}

OLLAMA_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["parents", "candidate_rule"],
    "properties": {
        "parents": {
            "type": "array",
            "minItems": 1,
            "maxItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rows"],
                "properties": {
                    "rows": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": ORDER_ROW_SCHEMA,
                    },
                },
            },
        },
        "candidate_rule": CANDIDATE_RULE_SCHEMA,
    },
}


def ollama_json_schema() -> dict[str, Any]:
    return OLLAMA_OUTPUT_SCHEMA


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
