from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree

from services.shared.waybill_fingerprint import (
    business_shape_fingerprint,
    fingerprint_catalog,
    fingerprint_for_payload,
    inspect_fingerprint,
    legacy_structural_fingerprint,
)


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item)


def _print_text(payload: dict[str, Any]) -> str:
    # Keep the sanitizer's established XML-to-text normalization unchanged.
    blocks: list[str] = []
    for node in _walk_dicts(payload):
        xml = node.get("printXML")
        if not isinstance(xml, str) or not xml.strip():
            continue
        try:
            root = ElementTree.fromstring(xml)
            blocks.extend(
                text
                for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] == "text"
                for text in [" ".join("".join(element.itertext()).split())]
                if text
            )
        except ElementTree.ParseError:
            blocks.extend(" ".join(text.split()) for text in re.findall(r"<!\[CDATA\[(.*?)\]\]>", xml, re.S) if text.strip())
    return "\n".join(dict.fromkeys(blocks))


def structural_fingerprint(payload: dict[str, Any], source_component: str) -> str:
    return legacy_structural_fingerprint(payload, source_component)
