from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
import httpx
from pydantic import ValidationError

from .contracts import AiCandidate, AiOrderRow, FeedbackRequest, FingerprintInspectRequest, RecognizeRequest
from .fingerprint import fingerprint_catalog, inspect_fingerprint, structural_fingerprint
from .model_client import OllamaModelClient
from .sanitizer import sanitize_payload
from .store import SessionStore


ApprovalSender = Callable[[dict[str, Any], str], dict[str, Any]]


class RuleValidationRejected(ValueError):
    pass


def _dict_nodes(value: Any, path: tuple[str, ...] = ()) -> list[tuple[str, dict[str, Any]]]:
    nodes: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        if path:
            nodes.append((".".join(path), value))
        for key, item in value.items():
            nodes.extend(_dict_nodes(item, (*path, str(key))))
    elif isinstance(value, list) and path:
        list_path = (*path[:-1], f"{path[-1]}[]")
        for item in value[:10]:
            nodes.extend(_dict_nodes(item, list_path))
    return nodes


def _has_relative_path(value: dict[str, Any], path: str) -> bool:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def normalize_candidate_rule(result: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    rule = result.get("candidate_rule")
    if not isinstance(rule, dict) or rule.get("strategy") != "structured_items_v1":
        return result
    fields = rule.get("fields")
    if not isinstance(fields, dict):
        return result

    normalized_fields = {
        str(field): str(path).removeprefix("sanitized_payload.").lstrip(".")
        for field, path in fields.items()
    }
    matching_paths = {
        path
        for path, item in _dict_nodes(payload)
        if normalized_fields
        and all(_has_relative_path(item, field_path) for field_path in normalized_fields.values())
    }
    normalized_rule = {**rule, "fields": normalized_fields}
    if len(matching_paths) == 1:
        normalized_rule["items_path"] = matching_paths.pop()
    return {**result, "candidate_rule": normalized_rule}


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
            timeout=30,
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
    candidate: dict[str, Any],
    *,
    validate_only: bool = False,
) -> dict[str, Any]:
    return {
        "session_id": session["session_id"],
        "workspace_id": session["workspace_id"],
        "task_id": session["task_id"],
        "raw_record_id": session["raw_record_id"],
        "document_sequence": session["document_sequence"],
        "format_fingerprint": session["fingerprint"],
        "candidate_rule": candidate["candidate_rule"],
        "rule_evidence": candidate["rule_evidence"],
        "candidate_output": candidate,
        "validate_only": validate_only,
    }


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
    model_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="waybill-ai")
    app.router.add_event_handler(
        "shutdown",
        lambda: model_executor.shutdown(wait=False, cancel_futures=True),
    )

    def response_payload(
        session: dict[str, Any],
        *,
        include_model_input: bool = False,
    ) -> dict[str, Any]:
        session_id = session["session_id"]
        console_url = f"{console_base}/console?session={session_id}" if console_base else f"/console?session={session_id}"
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
            "candidate": session["candidate"],
            "feedback": session["feedback"],
            "platform_response": session["platform_response"],
            "error": session["error"],
            "generation": session["generation"],
            "model_calls": session["model_calls"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "console_url": console_url,
        }
        if include_model_input:
            payload["model_input"] = {
                "fingerprint": session["fingerprint"],
                "sanitized_payload": session["sanitized_payload"],
                "administrator_feedback": session["feedback"],
            }
        return payload

    def run_model(session: dict[str, Any]) -> dict[str, Any]:
        try:
            corrected_rows = administrator_corrected_rows(session["feedback"])
            model_feedback = list(session["feedback"])
            for attempt in range(2):
                result = model.recognize(
                    session["sanitized_payload"],
                    session["fingerprint"],
                    model_feedback,
                )
                result = normalize_candidate_rule(result, session["sanitized_payload"])
                if corrected_rows:
                    parents = result.get("parents")
                    parent = parents[0] if isinstance(parents, list) and parents and isinstance(parents[0], dict) else {}
                    result["parents"] = [{**parent, "rows": corrected_rows}]
                for parent in result.get("parents") or []:
                    if isinstance(parent, dict):
                        parent["source"] = {"sanitized_payload": session["sanitized_payload"]}
                result.update(
                    {
                        "contract_version": "ai_waybill_candidate_v1",
                        "fingerprint": session["fingerprint"],
                        "rule_evidence": [
                            json.dumps(session["sanitized_payload"], ensure_ascii=False, sort_keys=True)
                        ],
                        "warnings": [],
                    }
                )
                try:
                    candidate = AiCandidate.model_validate(result)
                except ValidationError as exc:
                    parents = result.get("parents")
                    rows = [
                        row
                        for parent in parents or []
                        if isinstance(parent, dict)
                        for row in parent.get("rows") or []
                        if isinstance(row, dict)
                    ] if isinstance(parents, list) else []
                    if not rows:
                        raise
                    updated = store.set_candidate(
                        session["session_id"],
                        generation=session["generation"],
                        candidate=result,
                        status="ai_result_invalid",
                        error=(
                            "AI 返回的字段值包含字段名称，请修改后重新生成规则。"
                            if any(
                                "field value contains its field name" in str(error.get("msg") or "")
                                for error in exc.errors()
                            )
                            else "AI 未完整识别商品或数量，请修改后重新生成规则。"
                        ),
                    )
                    return response_payload(updated)
                candidate_payload = candidate.model_dump(mode="json")
                validation_error = None
                repairable = False
                if sender is not None and token:
                    try:
                        sender(platform_rule_payload(session, candidate_payload, validate_only=True), token)
                    except RuleValidationRejected as exc:
                        validation_error = str(exc)[:2000]
                        repairable = True
                    except (ValueError, httpx.HTTPError) as exc:
                        validation_error = str(exc)[:2000]
                if validation_error and corrected_rows and repairable and attempt == 0:
                    model_feedback = [
                        *model_feedback,
                        json.dumps(
                            {
                                "corrected_rows": corrected_rows,
                                "rule_validation_error": validation_error,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ]
                    continue
                updated = store.set_candidate(
                    session["session_id"],
                    generation=session["generation"],
                    candidate=candidate_payload,
                    status="ai_rule_invalid" if validation_error else "ai_rule_pending",
                    error=validation_error,
                )
                return response_payload(updated)
        except Exception as exc:
            updated = store.set_candidate(
                session["session_id"],
                generation=session["generation"],
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

    @app.get("/api/v1/fingerprints")
    def list_fingerprints() -> dict[str, Any]:
        return {
            "contract_version": "waybill_fingerprint_catalog_v1",
            "fingerprints": fingerprint_catalog(),
        }

    @app.post("/api/v1/fingerprints/inspect")
    def inspect_waybill_fingerprint(request: FingerprintInspectRequest) -> dict[str, Any]:
        result = inspect_fingerprint(request.payload, request.source_component)
        if result is None:
            raise HTTPException(status_code=422, detail="unsupported_fingerprint")
        return result

    @app.post("/api/v1/recognize")
    def recognize(request: RecognizeRequest) -> dict[str, Any]:
        fingerprint = structural_fingerprint(request.payload, request.source_component)
        inspected = inspect_fingerprint(request.payload, request.source_component)
        allowed_source_keys = None
        if inspected and inspected["fingerprint_code"] in request.field_selections:
            selected_fields = set(request.field_selections[inspected["fingerprint_code"]])
            allowed_source_keys = {
                str(field["path"]).rsplit(".", 1)[-1].split("//", 1)[0]
                for field in inspected["fields"]
                if field["key"] in selected_fields
            }
        sanitized = sanitize_payload(
            request.payload,
            allowed_source_keys=allowed_source_keys,
        )
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

    @app.get("/api/v1/sessions")
    def list_sessions(limit: int = Query(default=100, ge=1, le=100)) -> list[dict[str, Any]]:
        return [response_payload(session) for session in store.list(limit)]

    @app.get("/api/v1/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        return response_payload(session, include_model_input=True)

    @app.post("/api/v1/sessions/{session_id}/feedback")
    def add_feedback(session_id: str, request: FeedbackRequest) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        if session["document_sequence"] < 1:
            raise HTTPException(status_code=409, detail="旧会话无法确定所选面单，请重新创建识别会话。")
        if session["status"] in {"approved", "rejected", "approving"}:
            raise HTTPException(status_code=409, detail="Recognition session is closed.")
        message = request.message.strip() or request.note.strip()
        if request.corrected_rows:
            message = json.dumps(
                {
                    "corrected_rows": [
                        row.model_dump(mode="json")
                        for row in request.corrected_rows
                    ],
                    "note": request.note.strip() or request.message.strip(),
                },
                ensure_ascii=False,
            )
        try:
            updated = store.append_feedback(session_id, message)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        model_executor.submit(run_model, updated)
        return response_payload(updated)

    @app.post("/api/v1/sessions/{session_id}/approve")
    def approve(session_id: str) -> dict[str, Any]:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Recognition session not found.")
        if session["document_sequence"] < 1:
            raise HTTPException(status_code=409, detail="旧会话无法确定所选面单，请重新创建识别会话。")
        if session["status"] != "ai_rule_pending" or not session["candidate"]:
            raise HTTPException(status_code=409, detail="Recognition session has no approvable candidate.")
        if sender is None or not token:
            raise HTTPException(status_code=503, detail="Platform rule approval is not configured.")
        claimed = store.claim_approval(session_id, session["generation"])
        if claimed is None:
            raise HTTPException(status_code=409, detail="Recognition session changed before approval.")
        candidate = claimed["candidate"]
        try:
            platform_response = sender(platform_rule_payload(claimed, candidate), token)
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
                generation=claimed["generation"],
                expected_status="approving",
            )
        )

    @app.post("/api/v1/sessions/{session_id}/reject")
    def reject(session_id: str) -> dict[str, Any]:
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
                    "ai_rule_invalid",
                    "ai_result_invalid",
                    "ai_parse_failed",
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return response_payload(rejected)

    @app.get("/console", response_class=HTMLResponse)
    def console() -> HTMLResponse:
        html = (Path(__file__).parent / "static" / "console.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    return app


app = create_app()


__all__ = ["OllamaModelClient", "RuleValidationRejected", "app", "create_app"]
