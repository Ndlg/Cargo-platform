from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.services.order_row_contract import ORDER_ROW_DRAFTS_CONTRACT_VERSION


def waybill_parser_service_url() -> str:
    return get_settings().waybill_parser_url


def waybill_parser_service_enabled() -> bool:
    return bool(waybill_parser_service_url())


def post_waybill_parser_service(path: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    base_url = waybill_parser_service_url()
    if not base_url:
        raise RuntimeError("WAYBILL_PARSER_URL is not configured.")

    response = httpx.post(f"{base_url}{path}", json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def get_waybill_parser_service(path: str, *, timeout: float = 30.0) -> dict[str, Any]:
    base_url = waybill_parser_service_url()
    if not base_url:
        raise RuntimeError("WAYBILL_PARSER_URL is not configured.")

    response = httpx.get(f"{base_url}{path}", timeout=timeout)
    response.raise_for_status()
    return response.json()


def fingerprint_catalog_with_service() -> dict[str, Any]:
    return get_waybill_parser_service("/api/v1/fingerprints", timeout=10.0)


def inspect_waybill_fingerprint_with_service(
    *,
    raw_payload: dict[str, Any],
    source_component: str,
) -> dict[str, Any]:
    return post_waybill_parser_service(
        "/api/v1/fingerprints/inspect",
        {"raw_payload": raw_payload, "source_component": source_component},
        timeout=10.0,
    )


def analyze_waybill_with_service(
    *,
    raw_payload: dict[str, Any],
    source_component: str,
    selected_fields: list[str],
) -> dict[str, Any]:
    return post_waybill_parser_service(
        "/api/v1/analyze",
        {
            "raw_payload": raw_payload,
            "source_component": source_component,
            "selected_fields": selected_fields,
        },
        timeout=10.0,
    )


def validate_rule_pack_with_service(*, rule_pack: dict[str, Any]) -> dict[str, Any]:
    return post_waybill_parser_service(
        "/api/v1/rule-packs/validate",
        {"rule_pack": rule_pack},
        timeout=10.0,
    )


def explain_rule_pack_with_service(*, rule_pack: dict[str, Any]) -> dict[str, Any]:
    return post_waybill_parser_service(
        "/api/v1/rule-packs/explain",
        {"rule_pack": rule_pack},
        timeout=10.0,
    )


def synthesize_rule_with_service(
    *,
    raw_payload: dict[str, Any],
    source_component: str,
    corrected_rows: list[dict[str, Any]],
    gold_samples: list[dict[str, Any]],
    negative_samples: list[dict[str, Any]],
    selected_fields: list[str] | None = None,
    expected_evidence_sha256: str | None = None,
) -> dict[str, Any]:
    return post_waybill_parser_service(
        "/api/v1/rules/synthesize",
        {
            "raw_payload": raw_payload,
            "source_component": source_component,
            "corrected_rows": corrected_rows,
            "gold_samples": gold_samples,
            "negative_samples": negative_samples,
            "selected_fields": selected_fields,
            "expected_evidence_sha256": expected_evidence_sha256,
        },
        timeout=60.0,
    )


def preview_order_row_drafts_with_service(
    *,
    task_id: int,
    standard_details: list[dict[str, Any]] | None = None,
    raw_records: list[dict[str, Any]] | None = None,
    waybill_samples: list[dict[str, Any]] | None = None,
    rule_pack: dict[str, Any],
) -> dict[str, Any]:
    payload = post_waybill_parser_service(
        "/api/v1/parse/preview",
        {
            "task_id": task_id,
            "standard_details": standard_details or [],
            "raw_records": raw_records or [],
            "waybill_samples": waybill_samples or [],
            "rule_pack": rule_pack,
        },
    )
    if payload.get("contract_version") != ORDER_ROW_DRAFTS_CONTRACT_VERSION:
        raise RuntimeError(
            "Waybill parser contract mismatch: "
            f"{payload.get('contract_version')} != {ORDER_ROW_DRAFTS_CONTRACT_VERSION}"
        )
    return payload


def parse_order_row_drafts_with_service(
    *,
    workspace_id: int | None = None,
    task_id: int,
    standard_details: list[dict[str, Any]],
    raw_records: list[dict[str, Any]],
    waybill_samples: list[dict[str, Any]] | None = None,
    rule_pack: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = post_waybill_parser_service(
        "/api/v1/parse/batch",
        {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "standard_details": standard_details,
            "raw_records": raw_records,
            "waybill_samples": waybill_samples or [],
            "rule_pack": rule_pack,
        },
        timeout=180.0,
    )
    if payload.get("contract_version") != ORDER_ROW_DRAFTS_CONTRACT_VERSION:
        raise RuntimeError(
            "Waybill parser contract mismatch: "
            f"{payload.get('contract_version')} != {ORDER_ROW_DRAFTS_CONTRACT_VERSION}"
        )
    return payload
