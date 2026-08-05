from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit


def normalize_public_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("public_base_url must be an HTTP(S) URL.")
    return base_url


def build_connection_code(base_url: str, token: str) -> str:
    payload = json.dumps(
        {"v": 1, "base_url": normalize_public_base_url(base_url), "token": token},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "CP1." + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
