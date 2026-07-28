from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
import httpx
from pydantic import ValidationError

from .contracts import AiCandidate, FeedbackRequest, RecognizeRequest
from .fingerprint import structural_fingerprint
from .model_client import OllamaModelClient
from .sanitizer import sanitize_payload
from .store import SessionStore


ApprovalSender = Callable[[dict[str, Any], str], dict[str, Any]]


def default_approval_sender(platform_url: str) -> ApprovalSender:
    endpoint = f"{platform_url.rstrip('/')}/internal/ai-recognition/approve"

    def send(payload: dict[str, Any], token: str) -> dict[str, Any]:
        response = httpx.post(
            endpoint,
            json=payload,
            headers={"X-AI-Recognition-Token": token},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("platform approval response is not an object")
        return result

    return send


def create_app(
    *,
    model_client: Any | None = None,
    db_path: Path | None = None,
    console_base_url: str | None = None,
    internal_token: str | None = None,
    approval_sender: ApprovalSender | None = None,
) -> FastAPI:
    database_path = db_path or Path(os.getenv("AI_RECOGNITION_DB", "/data/ai-recognition.db"))
    console_base = (console_base_url or os.getenv("AI_CONSOLE_BASE_URL", "")).rstrip("/")
    token = internal_token if internal_token is not None else os.getenv("AI_RECOGNITION_INTERNAL_TOKEN", "")
    platform_url = os.getenv("PLATFORM_INTERNAL_URL", "")
    sender = approval_sender or (default_approval_sender(platform_url) if platform_url else None)
    model = model_client or OllamaModelClient(
        base_url=os.getenv("OLLAMA_URL", "http://local-model:11434"),
        model=os.getenv("OLLAMA_MODEL", "qwen3.5:4b-q4_K_M"),
    )
    store = SessionStore(database_path)
    app = FastAPI(title="Cargo Platform Local AI Recognition", version="1.0.0")

    def response_payload(session: dict[str, Any]) -> dict[str, Any]:
        session_id = session["session_id"]
        console_url = f"{console_base}/console?session={session_id}" if console_base else f"/console?session={session_id}"
        return {
            "session_id": session_id,
            "status": session["status"],
            "fingerprint": session["fingerprint"],
            "workspace_id": session["workspace_id"],
            "task_id": session["task_id"],
            "raw_record_id": session["raw_record_id"],
            "source_component": session["source_component"],
            "deterministic_failure_reason": session["deterministic_failure_reason"],
            "candidate": session["candidate"],
            "feedback": session["feedback"],
            "platform_response": session["platform_response"],
            "error": session["error"],
            "model_calls": session["model_calls"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "console_url": console_url,
        }

    def run_model(session: dict[str, Any]) -> dict[str, Any]:
        try:
            result = model.recognize(
                session["sanitized_payload"],
                session["fingerprint"],
                session["feedback"],
            )
            candidate = AiCandidate.model_validate(result).model_copy(
                update={"fingerprint": session["fingerprint"]}
            )
            updated = store.set_candidate(
                session["session_id"],
                candidate=candidate.model_dump(mode="json"),
                status="ai_rule_pending",
            )
        except (ValidationError, ValueError, TypeError, KeyError, httpx.HTTPError) as exc:
            updated = store.set_candidate(
                session["session_id"],
                candidate=None,
                status="ai_parse_failed",
                error=str(exc)[:2000],
            )
        return response_payload(updated)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok" if store.ping() else "error",
            "service": "ai-recognition",
            "model": getattr(model, "model", "injected"),
        }

    @app.post("/api/v1/recognize")
    def recognize(request: RecognizeRequest) -> dict[str, Any]:
        fingerprint = structural_fingerprint(request.payload, request.source_component)
        sanitized = sanitize_payload(request.payload)
        payload_hash = sha256(
            json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        request_key = ":".join(
            [
                str(request.workspace_id),
                str(request.task_id),
                str(request.raw_record_id),
                fingerprint,
                payload_hash,
            ]
        )
        session, created = store.reserve(
            request_key=request_key,
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            raw_record_id=request.raw_record_id,
            source_component=request.source_component,
            fingerprint=fingerprint,
            deterministic_failure_reason=request.deterministic_failure_reason,
            sanitized_payload=sanitized,
        )
        if not created:
            return response_payload(session)
        return run_model(session)

    @app.get("/api/v1/sessions")
    def list_sessions(limit: int = Query(default=100, ge=1, le=100)) -> list[dict[str, Any]]:
        return [response_payload(session) for session in store.list(limit)]

    @app.get("/api/v1/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        return response_payload(session)

    @app.post("/api/v1/sessions/{session_id}/feedback")
    def add_feedback(session_id: str, request: FeedbackRequest) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        if session["status"] in {"approved", "rejected"}:
            raise HTTPException(status_code=409, detail="Recognition session is closed.")
        return run_model(store.append_feedback(session_id, request.message.strip()))

    @app.post("/api/v1/sessions/{session_id}/approve")
    def approve(session_id: str) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        if session["status"] != "ai_rule_pending" or not session["candidate"]:
            raise HTTPException(status_code=409, detail="Recognition session has no approvable candidate.")
        if sender is None or not token:
            raise HTTPException(status_code=503, detail="Platform rule approval is not configured.")
        candidate = session["candidate"]
        payload = {
            "session_id": session_id,
            "workspace_id": session["workspace_id"],
            "task_id": session["task_id"],
            "raw_record_id": session["raw_record_id"],
            "format_fingerprint": session["fingerprint"],
            "candidate_rule": candidate["candidate_rule"],
            "rule_evidence": candidate["rule_evidence"],
            "candidate_output": candidate,
        }
        try:
            platform_response = sender(payload, token)
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail=f"Platform rule approval failed: {exc}") from exc
        return response_payload(store.set_status(session_id, "approved", platform_response=platform_response))

    @app.post("/api/v1/sessions/{session_id}/reject")
    def reject(session_id: str) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        if session["status"] == "approved":
            raise HTTPException(status_code=409, detail="Approved recognition session cannot be rejected.")
        return response_payload(store.set_status(session_id, "rejected"))

    @app.get("/console", response_class=HTMLResponse)
    def console() -> HTMLResponse:
        html = (Path(__file__).parent / "static" / "console.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    return app


app = create_app()


__all__ = ["OllamaModelClient", "app", "create_app"]
