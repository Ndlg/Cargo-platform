from __future__ import annotations

import json
from typing import Any

import httpx


SYSTEM_PROMPT = """你是面单商品订单行解析器。只处理提供的脱敏商品相关数据，不做 OCR，不猜测收件人、订单号或快递单号。
输出必须完全符合 JSON Schema，并且只输出 parents 和 candidate_rule。每个商品生成独立订单行，不去重。
订单行只能填写当前样本里的实际值，不得填写代码、JSONPath、“无”、“完整商品行原文”等占位文字。
商品、销售属性1、销售属性2、数量和备注只填写字段值，不得包含字段名称或“商品是”“销售属性1是”等说明文字。
若 administrator_feedback 包含 corrected_rows，parents 必须逐字段原样复制 corrected_rows，不得改写；只重新生成能复现这些正确订单行的 candidate_rule。
若 administrator_feedback 包含 rule_validation_error，必须修复该规则错误；defaults.quantity 只能省略或使用大于等于 1 的整数。
candidate_rule 必须能在当前脱敏数据上重放出与 parents 完全相同的订单行。text_path 必须是从根开始的完整路径，数组段写成 []，例如 task.documents[].contents[].data.ITEM_INFO。
items_path、text_path 和 fields 只能写点分隔字段路径，不能写 .split()、数组下标、表达式或代码。printXML 是文本字段，只能使用 text_pipeline_v1，路径通常为 task.documents[].contents[].printXML。
文本规则按顺序执行，初始只有 text 和 defaults；后续步骤只能读取 text、defaults 或前一步已写入的字段。extract_between 默认不保留 start/end；若确认后的字段值包含这两个边界符，必须设置 include_delimiters:true。
同时生成 candidate_rule。结构化数据只能使用：
{"strategy":"structured_items_v1","items_path":"数组路径[]","fields":{"product":"相对字段路径","sales_attr1":"相对字段路径","sales_attr2":"相对字段路径","quantity":"相对字段路径","remark":"相对字段路径"}}
结构化规则可选 steps，先映射 fields 再按顺序执行；steps 只能读写 product、sales_attr1、sales_attr2、quantity、remark，禁止使用 text。通用拆分示例：{"op":"rsplit","source":"sales_attr1","delimiter":" ","targets":["sales_attr1","sales_attr2"]}。
若 ITEM_INFO 为“商品名称 属性1;属性2 【1件】”，文本规则示例为：
{"strategy":"text_pipeline_v1","text_path":"task.documents[].contents[].data.ITEM_INFO","steps":[{"op":"extract_between","source":"text","start":"【","end":"件】","target":"quantity","consume":true},{"op":"rsplit","source":"text","delimiter":";","targets":["product","sales_attr2"]},{"op":"rsplit","source":"product","delimiter":" ","targets":["product","sales_attr1"]},{"op":"to_positive_int","target":"quantity"}],"defaults":{"remark":""}}
若 printXML 纯文本为“编号 商品，,属性，尺码*数量”，文本规则示例为：
{"strategy":"text_pipeline_v1","text_path":"task.documents[].contents[].printXML","steps":[{"op":"split","source":"text","delimiter":"，","targets":["product","sales_attr1","sales_attr2"]},{"op":"split","source":"product","delimiter":" ","targets":["text","product"]},{"op":"trim","target":"sales_attr1","chars":","},{"op":"rsplit","source":"sales_attr2","delimiter":"*","targets":["sales_attr2","quantity"]},{"op":"to_positive_int","target":"quantity"}],"defaults":{"remark":""}}
若商品文本为“【商品标题】紫色 42.5 1 件”，且商品值需要保留书名号，文本规则示例为：
{"strategy":"text_pipeline_v1","text_path":"task.documents[].contents[].data.productInfo","steps":[{"op":"extract_between","source":"text","start":"【","end":"】","target":"product","consume":true,"include_delimiters":true},{"op":"strip_suffix","target":"text","literal":" 件"},{"op":"rsplit","source":"text","delimiter":" ","targets":["sales_attr1","sales_attr2","quantity"]},{"op":"to_positive_int","target":"quantity"}],"defaults":{"remark":""}}
不得输出 operations、脚本、正则、文件或网络操作。"""

ABSOLUTE_PATH_PATTERN = r"^[A-Za-z0-9_]+(?:\[\])?(?:\.[A-Za-z0-9_]+(?:\[\])?)*$"
RELATIVE_PATH_PATTERN = r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$"

ROW_FIELDS = [
    "product",
    "sales_attr1",
    "sales_attr2",
    "quantity",
    "remark",
]

ROW_FIELD_PROPERTIES = {
    "product": {"type": "string"},
    "sales_attr1": {"type": "string"},
    "sales_attr2": {"type": "string"},
    "quantity": {"type": "string"},
    "remark": {"type": "string"},
}

RULE_FIELD_PROPERTIES = {
    field: {"type": "string", "pattern": RELATIVE_PATH_PATTERN}
    for field in ROW_FIELD_PROPERTIES
}

DEFAULT_PROPERTIES = {
    **ROW_FIELD_PROPERTIES,
    "quantity": {"type": "integer", "minimum": 1, "maximum": 100_000},
}

TEXT_STATE_FIELDS = ["text", *ROW_FIELDS]


def step_schema(state_fields: list[str]) -> dict[str, Any]:
    return {"oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["op", "source", "delimiter", "targets"],
            "properties": {
                "op": {"type": "string", "enum": ["split", "rsplit"]},
                "source": {"type": "string", "enum": state_fields},
                "delimiter": {"type": "string", "minLength": 1, "maxLength": 64},
                "targets": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 10,
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": state_fields},
                },
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["op", "source", "start", "end", "target"],
            "properties": {
                "op": {"const": "extract_between"},
                "source": {"type": "string", "enum": state_fields},
                "start": {"type": "string", "minLength": 1, "maxLength": 64},
                "end": {"type": "string", "minLength": 1, "maxLength": 64},
                "target": {"type": "string", "enum": state_fields},
                "consume": {"type": "boolean"},
                "include_delimiters": {"type": "boolean"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["op", "target"],
            "properties": {
                "op": {"const": "trim"},
                "target": {"type": "string", "enum": state_fields},
                "chars": {"type": "string", "maxLength": 64},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["op", "target", "literal"],
            "properties": {
                "op": {"type": "string", "enum": ["strip_prefix", "strip_suffix"]},
                "target": {"type": "string", "enum": state_fields},
                "literal": {"type": "string", "minLength": 1, "maxLength": 64},
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
    ]}


TEXT_STEP_SCHEMA = step_schema(TEXT_STATE_FIELDS)
STRUCTURED_STEP_SCHEMA = step_schema(ROW_FIELDS)

STRUCTURED_RULE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["strategy", "items_path", "fields"],
    "properties": {
        "strategy": {"const": "structured_items_v1"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "items_path": {"type": "string", "pattern": ABSOLUTE_PATH_PATTERN},
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "required": ["product", "quantity"],
            "properties": RULE_FIELD_PROPERTIES,
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": STRUCTURED_STEP_SCHEMA,
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
        "text_path": {"type": "string", "pattern": ABSOLUTE_PATH_PATTERN},
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
    ],
    "properties": {
        **ROW_FIELD_PROPERTIES,
        "product": {"type": "string", "description": "商品值本身，不包含“商品”等字段名称"},
        "sales_attr1": {"type": "string", "description": "销售属性1的值本身，不包含字段名称"},
        "sales_attr2": {"type": "string", "description": "销售属性2的值本身，不包含字段名称"},
        "quantity": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100_000,
            "description": "数量值本身，不包含“数量”等字段名称",
        },
        "remark": {"type": "string", "description": "备注值本身，不包含“备注”等字段名称"},
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
