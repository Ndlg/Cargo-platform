from __future__ import annotations

import json
from typing import Any

import httpx

from .contracts import AiCandidate


SYSTEM_PROMPT = """你是面单商品订单行解析器。只处理提供的脱敏商品相关数据，不做 OCR，不猜测收件人、订单号或快递单号。
输出必须完全符合 JSON Schema。每个父面单保留来源，每个商品生成独立订单行，不去重。
同时生成一个只使用允许的声明式操作的 candidate_rule；不得输出脚本、正则、文件或网络操作。"""


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
        self.http_client = http_client or httpx.Client(timeout=30.0)

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
                "format": AiCandidate.model_json_schema(),
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
