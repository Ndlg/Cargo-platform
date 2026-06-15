from typing import Any


FIELD_FALLBACKS = {
    "custom_sales_attr1_text": "custom_spec_text",
    "custom_sales_attr2_text": "custom_size_text",
}


def match_value(values: dict[str, Any], field_code: str) -> Any:
    value = values.get(field_code)
    if value in (None, "") and field_code in FIELD_FALLBACKS:
        return values.get(FIELD_FALLBACKS[field_code], "")
    return value


def build_match_key(values: dict[str, Any], field_codes: list[str]) -> str:
    return "|".join(str(match_value(values, code) or "").strip() for code in field_codes)
