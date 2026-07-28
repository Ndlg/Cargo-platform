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
                    source_component TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    deterministic_failure_reason TEXT NOT NULL,
                    sanitized_payload TEXT NOT NULL,
                    candidate TEXT,
                    feedback TEXT NOT NULL DEFAULT '[]',
                    platform_response TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    model_calls INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.commit()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for field in ("sanitized_payload", "candidate", "feedback", "platform_response"):
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
                        source_component, fingerprint, deterministic_failure_reason,
                        sanitized_payload, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'model_running', ?, ?)
                    """,
                    (
                        session_id,
                        request_key,
                        workspace_id,
                        task_id,
                        raw_record_id,
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
        candidate: dict[str, Any] | None,
        status: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        with closing(self.connect()) as db:
            db.execute(
                """
                UPDATE recognition_sessions
                SET candidate = ?, status = ?, error = ?, model_calls = model_calls + 1, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    json.dumps(candidate, ensure_ascii=False) if candidate is not None else None,
                    status,
                    error,
                    utc_now(),
                    session_id,
                ),
            )
            db.commit()
        result = self.get(session_id)
        if result is None:
            raise KeyError(session_id)
        return result

    def append_feedback(self, session_id: str, message: str) -> dict[str, Any]:
        session = self.get(session_id)
        if session is None:
            raise KeyError(session_id)
        feedback = [*session["feedback"], message]
        with closing(self.connect()) as db:
            db.execute(
                """
                UPDATE recognition_sessions
                SET feedback = ?, status = 'model_running', error = NULL, updated_at = ?
                WHERE session_id = ?
                """,
                (json.dumps(feedback, ensure_ascii=False), utc_now(), session_id),
            )
            db.commit()
        result = self.get(session_id)
        if result is None:
            raise KeyError(session_id)
        return result

    def set_status(
        self,
        session_id: str,
        status: str,
        *,
        platform_response: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with closing(self.connect()) as db:
            db.execute(
                """
                UPDATE recognition_sessions
                SET status = ?, platform_response = ?, error = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    status,
                    json.dumps(platform_response, ensure_ascii=False)
                    if platform_response is not None
                    else None,
                    error,
                    utc_now(),
                    session_id,
                ),
            )
            db.commit()
        result = self.get(session_id)
        if result is None:
            raise KeyError(session_id)
        return result

    def ping(self) -> bool:
        with closing(self.connect()) as db:
            return db.execute("SELECT 1").fetchone()[0] == 1
