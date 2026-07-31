from typing import Any

import httpx

from app.core.config import get_settings


def request_ai_recognition_service(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.ai_recognition_url or not settings.ai_recognition_internal_token:
        raise RuntimeError("AI recognition service is not configured.")
    response = httpx.request(
        method,
        f"{settings.ai_recognition_url}{path}",
        json=payload,
        headers={
            "X-AI-Recognition-Token": settings.ai_recognition_internal_token,
        },
        timeout=90.0,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("AI recognition response is not an object.")
    return result


def fingerprint_catalog_with_service() -> dict[str, Any]:
    return request_ai_recognition_service("GET", "/api/v1/fingerprints")


def get_ai_recognition_session_with_service(session_id: str) -> dict[str, Any]:
    return request_ai_recognition_service(
        "GET",
        f"/api/v1/sessions/{session_id}",
    )


def feedback_ai_recognition_session_with_service(
    session_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return request_ai_recognition_service(
        "POST",
        f"/api/v1/sessions/{session_id}/feedback",
        payload=payload,
    )


def approve_ai_recognition_session_with_service(
    session_id: str,
    *,
    actor: dict[str, Any],
) -> dict[str, Any]:
    return request_ai_recognition_service(
        "POST",
        f"/api/v1/sessions/{session_id}/approve",
        payload={"actor": actor},
    )


def reject_ai_recognition_session_with_service(session_id: str) -> dict[str, Any]:
    return request_ai_recognition_service(
        "POST",
        f"/api/v1/sessions/{session_id}/reject",
    )
