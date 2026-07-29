from __future__ import annotations

import os
from typing import Any

import httpx


AI_STATUSES = {"model_running", "ai_rule_pending", "ai_result_invalid", "ai_parse_failed"}


class AiRecognitionUnavailable(RuntimeError):
    pass


def ai_recognition_url() -> str:
    return os.getenv("AI_RECOGNITION_URL", "").strip().rstrip("/")


def ai_recognition_enabled() -> bool:
    return bool(ai_recognition_url())


def recognize_with_ai(
    *,
    workspace_id: int,
    task_id: int,
    raw_record_id: int,
    source_component: str,
    deterministic_failure_reason: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    base_url = ai_recognition_url()
    if not base_url:
        raise AiRecognitionUnavailable("AI_RECOGNITION_URL is not configured")
    try:
        response = httpx.post(
            f"{base_url}/api/v1/recognize",
            json={
                "workspace_id": workspace_id,
                "task_id": task_id,
                "raw_record_id": raw_record_id,
                "source_component": source_component or "unknown",
                "deterministic_failure_reason": deterministic_failure_reason,
                "payload": payload,
            },
            timeout=35,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AiRecognitionUnavailable(str(exc)) from exc
    if not isinstance(result, dict) or result.get("status") not in AI_STATUSES:
        raise ValueError("AI recognition response has an unsupported status")
    if result["status"] == "ai_rule_pending" and not str(result.get("session_id") or "").strip():
        raise ValueError("AI recognition response has no session id")
    return result
