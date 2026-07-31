from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
from typing import Annotated, Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Path as ApiPath, Query, status
import httpx

from .contracts import (
    AiOrderRow,
    ApprovalRequest,
    FeedbackRequest,
    FingerprintInspectRequest,
    RecognizeRequest,
)
from .fingerprint import fingerprint_catalog, inspect_fingerprint
from .model_client import OllamaModelClient, ollama_json_schema
from .sanitizer import sanitize_evidence, validate_selection
from .store import SessionStore


ApprovalSender = Callable[[dict[str, Any], str], dict[str, Any]]
AiSessionId = Annotated[
    str,
    ApiPath(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]


class RuleValidationRejected(ValueError):
    pass


def administrator_corrected_rows(feedback: list[str]) -> list[dict[str, Any]] | None:
    for message in reversed(feedback):
        try:
            rows = json.loads(message).get("corrected_rows")
        except (AttributeError, json.JSONDecodeError):
            continue
        if isinstance(rows, list) and rows:
            return [AiOrderRow.model_validate(row).model_dump(mode="json") for row in rows]
    return None


def default_approval_sender(platform_url: str) -> ApprovalSender:
    endpoint = f"{platform_url.rstrip('/')}/internal/ai-recognition/approve"

    def send(payload: dict[str, Any], token: str) -> dict[str, Any]:
        response = httpx.post(
            endpoint,
            json=payload,
            headers={"X-AI-Recognition-Token": token},
            timeout=90,
        )
        if response.status_code == 422:
            try:
                detail = response.json().get("detail")
            except (AttributeError, ValueError):
                detail = None
            raise RuleValidationRejected(detail or "平台拒绝了候选识别规则。")
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("platform approval response is not an object")
        return result

    return send


def platform_rule_payload(
    session: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    model_candidate = session["model_candidate"]
    administrator_rows = session["administrator_rows"]
    return {
        "session_id": session["session_id"],
        "workspace_id": session["workspace_id"],
        "task_id": session["task_id"],
        "raw_record_id": session["raw_record_id"],
        "document_sequence": session["document_sequence"],
        "format_fingerprint": session["fingerprint"],
        "fingerprint_code": session["sanitized_payload"]["fingerprint_code"],
        "candidate_output": {
            "parents": [{"rows": administrator_rows}],
        },
        "model_candidate": model_candidate,
        "administrator_rows": administrator_rows,
        "model_candidate_sha256": canonical_sha256(model_candidate),
        "administrator_rows_sha256": canonical_sha256(administrator_rows),
        "actor": actor,
    }


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def business_rows(candidate: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = [
        row
        for parent in (candidate or {}).get("parents") or []
        if isinstance(parent, dict)
        for row in parent.get("rows") or []
        if isinstance(row, dict)
    ]
    return [AiOrderRow.model_validate(row).model_dump(mode="json") for row in rows]


def create_app(
    *,
    model_client: Any | None = None,
    db_path: Path | None = None,
    internal_token: str | None = None,
    approval_sender: ApprovalSender | None = None,
) -> FastAPI:
    database_path = db_path or Path(os.getenv("AI_RECOGNITION_DB", "/data/ai-recognition.db"))
    token = internal_token if internal_token is not None else os.getenv("AI_RECOGNITION_INTERNAL_TOKEN", "")
    platform_url = os.getenv("PLATFORM_INTERNAL_URL", "")
    sender = approval_sender or (default_approval_sender(platform_url) if platform_url else None)
    model = model_client or OllamaModelClient(
        base_url=os.getenv("OLLAMA_URL", "http://local-model:11434"),
        model=os.getenv("OLLAMA_MODEL", "qwen3.5:4b-q4_K_M"),
    )
    store = SessionStore(database_path)
    app = FastAPI(
        title="Cargo Platform Local AI Recognition",
        version=os.getenv("APP_VERSION", "0.2.0-rc.1"),
    )
    model_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="waybill-ai")
    app.router.add_event_handler(
        "shutdown",
        lambda: model_executor.shutdown(wait=False, cancel_futures=True),
    )

    def require_internal_token(
        supplied_token: str = Header(default="", alias="X-AI-Recognition-Token"),
    ) -> None:
        if not token or not secrets.compare_digest(supplied_token, token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid AI recognition token.",
            )

    def response_payload(
        session: dict[str, Any],
        *,
        include_model_input: bool = False,
    ) -> dict[str, Any]:
        session_id = session["session_id"]
        payload = {
            "session_id": session_id,
            "status": session["status"],
            "fingerprint": session["fingerprint"],
            "workspace_id": session["workspace_id"],
            "task_id": session["task_id"],
            "raw_record_id": session["raw_record_id"],
            "document_sequence": session["document_sequence"],
            "source_component": session["source_component"],
            "deterministic_failure_reason": session["deterministic_failure_reason"],
            "candidate": (
                session["candidate"]
                if session["status"] in {"ai_rule_pending", "approving", "approved"}
                else None
            ),
            "model_candidate": session["model_candidate"],
            "administrator_rows": session["administrator_rows"],
            "compiler_result": session["compiler_result"],
            "feedback": session["feedback"],
            "platform_response": session["platform_response"],
            "error": session["error"],
            "generation": session["generation"],
            "model_calls": session["model_calls"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
        }
        if include_model_input:
            payload["model_input"] = {
                "sanitized_payload": session["sanitized_payload"],
            }
        return payload

    def run_model(session: dict[str, Any]) -> dict[str, Any]:
        corrected_rows = (
            session["administrator_rows"]
            or administrator_corrected_rows(session["feedback"])
        )
        if corrected_rows:
            return response_payload(
                store.set_candidate(
                    session["session_id"],
                    generation=session["generation"],
                    candidate={
                        "contract_version": "ai_span_selection_candidate_v1",
                        "fingerprint": session["fingerprint"],
                        "parents": [{"rows": corrected_rows}],
                    },
                    status="ai_rule_pending",
                    count_model_call=False,
                    record_model_candidate=False,
                )
            )
        try:
            result = model.recognize(session["sanitized_payload"])
        except Exception as exc:
            return response_payload(
                store.set_candidate(
                    session["session_id"],
                    generation=session["generation"],
                    candidate=None,
                    status="ai_unavailable",
                    error=str(exc)[:2000],
                )
            )
        resolved = (
            validate_selection(result, session["sanitized_payload"])
            if isinstance(result, dict)
            else {"status": "candidate_invalid", "error": "model output is not an object"}
        )
        candidate = {
            "contract_version": "ai_span_selection_candidate_v1",
            "fingerprint": session["fingerprint"],
            "span_selection": result if isinstance(result, dict) else {},
            "parents": (
                [{"rows": resolved["rows"]}]
                if resolved["status"] == "candidate_valid"
                else []
            ),
        }
        return response_payload(
            store.set_candidate(
                session["session_id"],
                generation=session["generation"],
                candidate=candidate,
                status=(
                    "ai_rule_pending"
                    if resolved["status"] == "candidate_valid"
                    else "candidate_invalid"
                ),
                error=resolved.get("error"),
            )
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok" if store.ping() else "error",
            "service": "ai-recognition",
            "model": getattr(model, "model", "injected"),
        }

    @app.get("/api/v1/fingerprints", dependencies=[Depends(require_internal_token)])
    def list_fingerprints() -> dict[str, Any]:
        return {
            "contract_version": "waybill_fingerprint_catalog_v1",
            "fingerprints": fingerprint_catalog(),
        }

    @app.post(
        "/api/v1/fingerprints/inspect",
        dependencies=[Depends(require_internal_token)],
    )
    def inspect_waybill_fingerprint(request: FingerprintInspectRequest) -> dict[str, Any]:
        result = inspect_fingerprint(request.payload, request.source_component)
        if result is None:
            raise HTTPException(status_code=422, detail="unsupported_fingerprint")
        return result

    @app.post("/api/v1/recognize", dependencies=[Depends(require_internal_token)])
    def recognize(request: RecognizeRequest) -> dict[str, Any]:
        evidence_source = str(request.evidence.get("source_component") or "")
        fingerprint = str(request.evidence.get("structural_fingerprint") or "")
        if evidence_source != request.source_component or not fingerprint or len(fingerprint) > 256:
            raise HTTPException(status_code=422, detail="invalid_evidence_identity")
        try:
            sanitized = sanitize_evidence(request.evidence)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        payload_hash = sha256(
            json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        request_key = ":".join(
            [
                str(request.workspace_id),
                str(request.task_id),
                str(request.raw_record_id),
                str(request.document_sequence),
                fingerprint,
                payload_hash,
            ]
        )
        session, created = store.reserve(
            request_key=request_key,
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            raw_record_id=request.raw_record_id,
            document_sequence=request.document_sequence,
            source_component=request.source_component,
            fingerprint=fingerprint,
            deterministic_failure_reason=request.deterministic_failure_reason,
            sanitized_payload=sanitized,
        )
        if not created:
            return response_payload(session)
        model_executor.submit(run_model, session)
        return response_payload(session)

    @app.get("/api/v1/sessions", dependencies=[Depends(require_internal_token)])
    def list_sessions(limit: int = Query(default=100, ge=1, le=100)) -> list[dict[str, Any]]:
        return [response_payload(session) for session in store.list(limit)]

    @app.get(
        "/api/v1/sessions/{session_id}",
        dependencies=[Depends(require_internal_token)],
    )
    def get_session(session_id: AiSessionId) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        return response_payload(session, include_model_input=True)

    @app.post(
        "/api/v1/sessions/{session_id}/feedback",
        dependencies=[Depends(require_internal_token)],
    )
    def add_feedback(session_id: AiSessionId, request: FeedbackRequest) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        if session["document_sequence"] < 1:
            raise HTTPException(status_code=409, detail="旧会话无法确定所选面单，请重新创建识别会话。")
        if session["status"] in {"approved", "rejected", "approving"}:
            raise HTTPException(status_code=409, detail="Recognition session is closed.")
        message = request.message.strip() or request.note.strip()
        if request.corrected_rows:
            administrator_rows = [
                row.model_dump(mode="json")
                for row in request.corrected_rows
            ]
            message = json.dumps(
                {
                    "corrected_rows": administrator_rows,
                    "note": request.note.strip() or request.message.strip(),
                },
                ensure_ascii=False,
            )
        else:
            administrator_rows = None
        if (
            session["status"] == "model_running"
            and session["feedback"]
            and session["feedback"][-1] == message
        ):
            return response_payload(session)
        try:
            updated = store.append_feedback(
                session_id,
                message,
                administrator_rows,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        model_executor.submit(run_model, updated)
        return response_payload(updated)

    @app.post(
        "/api/v1/sessions/{session_id}/approve",
        dependencies=[Depends(require_internal_token)],
    )
    def approve(session_id: AiSessionId, request: ApprovalRequest) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        if session["document_sequence"] < 1:
            raise HTTPException(status_code=409, detail="旧会话无法确定所选面单，请重新创建识别会话。")
        if session["status"] != "ai_rule_pending" or not session["candidate"]:
            raise HTTPException(status_code=409, detail="Recognition session has no approvable candidate.")
        if sender is None or not token:
            raise HTTPException(status_code=503, detail="Platform rule approval is not configured.")
        administrator_rows = session["administrator_rows"] or business_rows(session["candidate"])
        if not administrator_rows:
            raise HTTPException(status_code=409, detail="Recognition session has no administrator result.")
        claimed = store.claim_approval(
            session_id,
            session["generation"],
            administrator_rows,
        )
        if claimed is None:
            raise HTTPException(status_code=409, detail="Recognition session changed before approval.")
        try:
            platform_response = sender(
                platform_rule_payload(
                    claimed,
                    request.actor.model_dump(mode="json"),
                ),
                token,
            )
        except (ValueError, httpx.HTTPError) as exc:
            store.set_status(
                session_id,
                "ai_rule_pending",
                error=str(exc)[:2000],
                generation=claimed["generation"],
                expected_status="approving",
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return response_payload(
            store.set_status(
                session_id,
                "approved",
                platform_response=platform_response,
                compiler_result=(
                    platform_response["compiler_result"]
                    if isinstance(platform_response.get("compiler_result"), dict)
                    else platform_response
                ),
                generation=claimed["generation"],
                expected_status="approving",
            )
        )

    @app.post(
        "/api/v1/sessions/{session_id}/reject",
        dependencies=[Depends(require_internal_token)],
    )
    def reject(session_id: AiSessionId) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        if session["status"] in {"approved", "approving"}:
            raise HTTPException(status_code=409, detail="Approved recognition session cannot be rejected.")
        try:
            rejected = store.set_status(
                session_id,
                "rejected",
                generation=session["generation"],
                expected_statuses=(
                    "model_running",
                    "ai_rule_pending",
                    "candidate_invalid",
                    "ai_unavailable",
                    "ai_rule_invalid",
                    "ai_result_invalid",
                    "ai_parse_failed",
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return response_payload(rejected)

    return app


app = create_app()


__all__ = [
    "OllamaModelClient",
    "RuleValidationRejected",
    "app",
    "create_app",
    "ollama_json_schema",
    "sanitize_evidence",
    "validate_selection",
]
