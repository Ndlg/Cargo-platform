from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


def structure_signature(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): structure_signature(item)
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
        }
    if isinstance(value, list):
        signatures: list[Any] = []
        seen: set[str] = set()
        for item in value[:10]:
            signature = structure_signature(item)
            encoded = json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if encoded not in seen:
                seen.add(encoded)
                signatures.append(signature)
        return {"type": "list", "items": signatures}
    if isinstance(value, str):
        return {"type": "scalar"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, (bool, int, float)):
        return {"type": "scalar"}
    return {"type": type(value).__name__}


def structural_fingerprint(payload: dict[str, Any], source_component: str) -> str:
    signature = {
        "source_component": source_component.strip().lower(),
        "payload": structure_signature(payload),
    }
    encoded = json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"
