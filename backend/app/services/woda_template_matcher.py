from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PrintTemplateConfig


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def woda_template_source_key(config: PrintTemplateConfig) -> str:
    payload = config.config if isinstance(config.config, dict) else {}
    match_payload = payload.get("template_match") if isinstance(payload.get("template_match"), dict) else {}
    return (
        _text(match_payload.get("source_template_key"))
        or _text(payload.get("source_template_key"))
        or _text(config.template_key)
    )


def is_printxml_layout_config(config: PrintTemplateConfig) -> bool:
    return (
        _text(getattr(config, "template_source", "")) == "printxml_layout"
        or woda_template_source_key(config).startswith("printxml:")
    )


def unique_printxml_fallback_config(configs: list[PrintTemplateConfig]) -> PrintTemplateConfig | None:
    printxml_configs = [config for config in configs if is_printxml_layout_config(config)]
    return printxml_configs[0] if len(printxml_configs) == 1 else None


def find_template_config(
    db: Session | None,
    workspace_id: int | None,
    waybill_mode: str,
    source_template_key: str,
) -> PrintTemplateConfig | None:
    if db is None or workspace_id is None or not source_template_key:
        return None

    configs = db.scalars(
        select(PrintTemplateConfig).where(
            PrintTemplateConfig.workspace_id == workspace_id,
            PrintTemplateConfig.waybill_mode == waybill_mode,
            PrintTemplateConfig.is_enabled.is_(True),
            PrintTemplateConfig.is_deleted.is_(False),
        )
        .order_by(PrintTemplateConfig.updated_at.desc(), PrintTemplateConfig.id.desc())
    ).all()
    for config in configs:
        if woda_template_source_key(config) == source_template_key:
            return config
    return None


def find_woda_template_config(
    db: Session | None,
    workspace_id: int | None,
    print_template_key: str,
) -> PrintTemplateConfig | None:
    if db is None or workspace_id is None or not print_template_key:
        return None

    configs = db.scalars(
        select(PrintTemplateConfig).where(
            PrintTemplateConfig.workspace_id == workspace_id,
            PrintTemplateConfig.waybill_mode == "cainiao_woda_printxml",
            PrintTemplateConfig.is_enabled.is_(True),
            PrintTemplateConfig.is_deleted.is_(False),
        )
        .order_by(PrintTemplateConfig.updated_at.desc(), PrintTemplateConfig.id.desc())
    ).all()
    for config in configs:
        if woda_template_source_key(config) == print_template_key:
            return config
    return unique_printxml_fallback_config(configs)
