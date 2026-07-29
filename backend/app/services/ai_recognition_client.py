from typing import Any

import httpx

from app.core.config import get_settings


def fingerprint_catalog_with_service() -> dict[str, Any]:
    base_url = get_settings().ai_recognition_url
    if not base_url:
        raise RuntimeError("AI_RECOGNITION_URL is not configured.")
    response = httpx.get(f"{base_url}/api/v1/fingerprints", timeout=10.0)
    response.raise_for_status()
    return response.json()
