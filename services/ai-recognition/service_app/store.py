from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS recognition_sessions (
                    session_id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL UNIQUE,
                    workspace_id INTEGER NOT NULL,
                    task_id INTEGER NOT NULL,
                    raw_record_id INTEGER NOT NULL,
                    document_sequence INTEGER NOT NULL,
                    source_component TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    deterministic_failure_reason TEXT NOT NULL,
                    sanitized_payload TEXT NOT NULL,
                    model_candidate TEXT,
                    administrator_rows TEXT,
                    compiler_result TEXT,
                    candidate TEXT,
                    feedback TEXT NOT NULL DEFAULT '[]',
                    platform_response TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    generation INTEGER NOT NULL DEFAULT 0,
                    model_calls INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(recognition_sessions)").fetchall()
            }
            if "document_sequence" not in columns:
                db.execute(
                    "ALTER TABLE recognition_sessions "
                    "ADD COLUMN document_sequence INTEGER NOT NULL DEFAULT 0"
                )
            if "generation" not in columns:
                db.execute(
                    "ALTER TABLE recognition_sessions "
                    "ADD COLUMN generation INTEGER NOT NULL DEFAULT 0"
                )
            added_stage_columns = {
                column
                for column in (
                    "model_candidate",
                    "administrator_rows",
                    "compiler_result",
                )
                if column not in columns
            }
            for column in added_stage_columns:
                db.execute(f"ALTER TABLE recognition_sessions ADD COLUMN {column} TEXT")
            if "administrator_rows" in added_stage_columns:
                for row in db.execute(
                    "SELECT session_id, feedback FROM recognition_sessions "
                    "WHERE feedback != '[]'"
                ).fetchall():
                    corrected_rows = self.legacy_administrator_rows(row["feedback"])
                    if corrected_rows:
                        db.execute(
                            "UPDATE recognition_sessions SET administrator_rows = ? "
                            "WHERE session_id = ?",
                            (
                                json.dumps(corrected_rows, ensure_ascii=False),
                                row["session_id"],
                            ),
                        )
            if "model_candidate" in added_stage_columns:
                for row in db.execute(
                    "SELECT session_id, candidate, feedback FROM recognition_sessions "
                    "WHERE candidate IS NOT NULL"
                ).fetchall():
                    if self.legacy_has_feedback(row["feedback"]):
                        continue
                    db.execute(
                        "UPDATE recognition_sessions SET model_candidate = ? "
                        "WHERE session_id = ?",
                        (row["candidate"], row["session_id"]),
                    )
            if "compiler_result" in added_stage_columns:
                for row in db.execute(
                    "SELECT session_id, platform_response FROM recognition_sessions "
                    "WHERE platform_response IS NOT NULL"
                ).fetchall():
                    compiler_result = self.legacy_compiler_result(
                        row["platform_response"]
                    )
                    if compiler_result is not None:
                        db.execute(
                            "UPDATE recognition_sessions SET compiler_result = ? "
                            "WHERE session_id = ?",
                            (
                                json.dumps(compiler_result, ensure_ascii=False),
                                row["session_id"],
                            ),
                        )
            db.commit()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def legacy_administrator_rows(feedback_json: str) -> list[dict[str, Any]] | None:
        try:
            messages = json.loads(feedback_json or "[]")
        except json.JSONDecodeError:
            return None
        for message in reversed(messages if isinstance(messages, list) else []):
            try:
                rows = json.loads(message).get("corrected_rows")
            except (AttributeError, TypeError, json.JSONDecodeError):
                continue
            if isinstance(rows, list) and rows:
                return [row for row in rows if isinstance(row, dict)] or None
        return None

    @staticmethod
    def legacy_has_feedback(feedback_json: str) -> bool:
        try:
            messages = json.loads(feedback_json or "[]")
        except json.JSONDecodeError:
            return True
        return bool(messages) if isinstance(messages, list) else True

    @staticmethod
    def legacy_compiler_result(
        platform_response_json: str,
    ) -> dict[str, Any] | None:
        try:
            platform_response = json.loads(platform_response_json)
        except json.JSONDecodeError:
            return None
        compiler_result = (
            platform_response.get("compiler_result")
            if isinstance(platform_response, dict)
            else None
        )
        return compiler_result if isinstance(compiler_result, dict) else None

    @staticmethod
    def decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for field in (
            "sanitized_payload",
            "model_candidate",
            "administrator_rows",
            "compiler_result",
            "candidate",
            "feedback",
            "platform_response",
        ):
            if result.get(field):
                result[field] = json.loads(result[field])
            elif field == "feedback":
                result[field] = []
            else:
                result[field] = None
        return result

    def reserve(
        self,
        *,
        request_key: str,
        workspace_id: int,
        task_id: int,
        raw_record_id: int,
        document_sequence: int,
        source_component: str,
        fingerprint: str,
        deterministic_failure_reason: str,
        sanitized_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        session_id = uuid4().hex
        with closing(self.connect()) as db:
            try:
                db.execute(
                    """
                    INSERT INTO recognition_sessions(
                        session_id, request_key, workspace_id, task_id, raw_record_id,
                        document_sequence, source_component, fingerprint, deterministic_failure_reason,
                        sanitized_payload, generation, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'model_running', ?, ?)
                    """,
                    (
                        session_id,
                        request_key,
                        workspace_id,
                        task_id,
                        raw_record_id,
                        document_sequence,
                        source_component,
                        fingerprint,
                        deterministic_failure_reason,
                        json.dumps(sanitized_payload, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                db.commit()
                created = True
            except sqlite3.IntegrityError:
                created = False
            row = db.execute(
                "SELECT * FROM recognition_sessions WHERE request_key = ?",
                (request_key,),
            ).fetchone()
        decoded = self.decode(row)
        if decoded is None:
            raise RuntimeError("failed to reserve recognition session")
        if not created and decoded["status"] in {
            "approved",
            "rejected",
            "ai_parse_failed",
            "ai_unavailable",
        }:
            return self.reserve(
                request_key=f"{request_key}:{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task_id,
                raw_record_id=raw_record_id,
                document_sequence=document_sequence,
                source_component=source_component,
                fingerprint=fingerprint,
                deterministic_failure_reason=deterministic_failure_reason,
                sanitized_payload=sanitized_payload,
            )
        return decoded, created

    def get(self, session_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT * FROM recognition_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self.decode(row)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT * FROM recognition_sessions ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [decoded for row in rows if (decoded := self.decode(row)) is not None]

    def set_candidate(
        self,
        session_id: str,
        *,
        generation: int,
        candidate: dict[str, Any] | None,
        status: str,
        error: str | None = None,
        count_model_call: bool = True,
        record_model_candidate: bool = True,
    ) -> dict[str, Any]:
        encoded_candidate = (
            json.dumps(candidate, ensure_ascii=False) if candidate is not None else None
        )
        current_candidate = encoded_candidate if status == "ai_rule_pending" else None
        with closing(self.connect()) as db:
            db.execute(
                """
                UPDATE recognition_sessions
                SET candidate = ?,
                    model_candidate = CASE
                        WHEN ? AND model_candidate IS NULL THEN ?
                        ELSE model_candidate
                    END,
                    status = ?, error = ?,
                    model_calls = model_calls + ?, updated_at = ?
                WHERE session_id = ? AND generation = ? AND status = 'model_running'
                """,
                (
                    current_candidate,
                    int(record_model_candidate),
                    encoded_candidate,
                    status,
                    error,
                    int(count_model_call),
                    utc_now(),
                    session_id,
                    generation,
                ),
            )
            db.commit()
        result = self.get(session_id)
        if result is None:
            raise KeyError(session_id)
        return result

    def append_feedback(
        self,
        session_id: str,
        message: str,
        administrator_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT feedback, status FROM recognition_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            if row["status"] in {"approved", "rejected", "approving"}:
                raise ValueError("Recognition session is closed.")
            feedback = [*json.loads(row["feedback"] or "[]"), message]
            db.execute(
                """
                UPDATE recognition_sessions
                SET feedback = ?, administrator_rows = COALESCE(?, administrator_rows),
                    candidate = NULL, status = 'model_running',
                    error = NULL, generation = generation + 1, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    json.dumps(feedback, ensure_ascii=False),
                    (
                        json.dumps(administrator_rows, ensure_ascii=False)
                        if administrator_rows is not None
                        else None
                    ),
                    utc_now(),
                    session_id,
                ),
            )
            db.commit()
        result = self.get(session_id)
        if result is None:
            raise KeyError(session_id)
        return result

    def claim_approval(
        self,
        session_id: str,
        generation: int,
        administrator_rows: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        with closing(self.connect()) as db:
            cursor = db.execute(
                """
                UPDATE recognition_sessions
                SET status = 'approving',
                    administrator_rows = COALESCE(administrator_rows, ?),
                    updated_at = ?
                WHERE session_id = ? AND generation = ? AND status = 'ai_rule_pending'
                """,
                (
                    json.dumps(administrator_rows, ensure_ascii=False),
                    utc_now(),
                    session_id,
                    generation,
                ),
            )
            db.commit()
        return self.get(session_id) if cursor.rowcount == 1 else None

    def set_status(
        self,
        session_id: str,
        status: str,
        *,
        platform_response: dict[str, Any] | None = None,
        compiler_result: dict[str, Any] | None = None,
        error: str | None = None,
        generation: int | None = None,
        expected_status: str | None = None,
        expected_statuses: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        with closing(self.connect()) as db:
            conditions = ["session_id = ?"]
            parameters: list[Any] = [
                status,
                json.dumps(platform_response, ensure_ascii=False)
                if platform_response is not None
                else None,
                json.dumps(compiler_result, ensure_ascii=False)
                if compiler_result is not None
                else None,
                error,
                utc_now(),
                session_id,
            ]
            if generation is not None:
                conditions.append("generation = ?")
                parameters.append(generation)
            if expected_status is not None:
                conditions.append("status = ?")
                parameters.append(expected_status)
            if expected_statuses:
                conditions.append(f"status IN ({','.join('?' for _ in expected_statuses)})")
                parameters.extend(expected_statuses)
            cursor = db.execute(
                f"""
                UPDATE recognition_sessions
                SET status = ?, platform_response = ?, compiler_result = ?, error = ?, updated_at = ?
                WHERE {' AND '.join(conditions)}
                """,
                parameters,
            )
            db.commit()
        if cursor.rowcount != 1:
            raise ValueError("Recognition session changed before the operation completed.")
        result = self.get(session_id)
        if result is None:
            raise KeyError(session_id)
        return result

    def ping(self) -> bool:
        with closing(self.connect()) as db:
            return db.execute("SELECT 1").fetchone()[0] == 1
