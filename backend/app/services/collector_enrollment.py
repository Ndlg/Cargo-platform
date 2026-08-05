from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit


def normalize_public_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("public_base_url must be an HTTP(S) origin.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("public_base_url must be an HTTP(S) origin.")
    return f"{parsed.scheme}://{parsed.netloc}"


def build_connection_code(base_url: str, token: str) -> str:
    payload = json.dumps(
        {"v": 1, "base_url": normalize_public_base_url(base_url), "token": token},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "CP1." + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
