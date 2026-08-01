from pathlib import Path
import tempfile

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.waybill_parser_client import get_waybill_parser_service


router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}


@router.get("/ready")
def readiness() -> dict[str, object]:
    settings = get_settings()
    components = {"database": "not_checked", "storage": "not_checked", "parser": "not_checked"}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1")).scalar_one()
        components["database"] = "ok"
    except Exception:
        components["database"] = "unavailable"

    try:
        storage_root = Path(settings.storage_root)
        storage_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=storage_root, prefix=".ready-", delete=True) as probe:
            probe.write(b"ok")
            probe.flush()
        components["storage"] = "ok"
    except Exception:
        components["storage"] = "unavailable"

    try:
        parser = get_waybill_parser_service("/health", timeout=3.0)
        if parser.get("status") != "ok":
            raise RuntimeError("parser unavailable")
        if parser.get("version") != settings.app_version:
            components["parser"] = "version_mismatch"
        else:
            components["parser"] = "ok"
    except Exception:
        components["parser"] = "unavailable"

    if any(component != "ok" for component in components.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "components": components},
        )

    return {
        "status": "ready",
        "app": settings.app_name,
        "version": settings.app_version,
        "components": components,
    }
