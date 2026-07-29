from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.request import Request, urlopen

from ai_validation_dataset import CLEARED_TABLES, sha256_file


TASK_NAMES = {
    "single": "AI验收-已知单商品",
    "multi": "AI验收-多商品且不去重",
    "text": "AI验收-可读文本格式",
    "unknown": "AI验收-不完整陌生格式",
    "real": "AI验收-真实1688单文档",
}
KEY_FIELDS = ("product", "sales_attr1", "sales_attr2", "quantity")


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def load_answer_set(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entries = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    manifest_path = path.with_name(f"{path.stem}.manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["answer_set_sha256"] == sha256_file(path)
    return entries, manifest


def coverage(response: dict[str, Any]) -> dict[str, int]:
    parents = response.get("parents") or []
    diagnostics = {
        str(item.get("parent_label") or ""): str(item.get("reason") or "")
        for item in response.get("diagnostics") or []
        if isinstance(item, dict)
    }
    normal = sum(bool(parent.get("rows")) for parent in parents)
    exceptions = sum(
        not parent.get("rows") and bool(diagnostics.get(str(parent.get("parent_label") or "")))
        for parent in parents
    )
    assert len(parents) == normal + exceptions
    return {"prints": len(parents), "normal": normal, "exceptions": exceptions}


def key_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in KEY_FIELDS}


def cold_database_report(path: Path) -> dict[str, Any]:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        cleared = {
            table: int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in CLEARED_TABLES & tables
        }
        assert all(count == 0 for count in cleared.values())
        return {
            "sha256": sha256_file(path),
            "raw_capture_records": int(
                db.execute("SELECT COUNT(*) FROM raw_capture_records").fetchone()[0]
            ),
            "cleared_tables": cleared,
        }


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ai-url", required=True)
    parser.add_argument("--answer-set", type=Path, required=True)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--workspace-id", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/") + "/api/v1"
    ai_url = args.ai_url.rstrip("/")
    assert request_json(f"{base_url}/health")["status"] == "ok"
    assert request_json(f"{ai_url}/health")["status"] == "ok"

    token = request_json(
        f"{base_url}/auth/login",
        method="POST",
        payload={"username": args.username, "password": args.password},
    )["access_token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Workspace-Id": str(args.workspace_id),
    }
    tasks = request_json(f"{base_url}/capture-tasks?limit=200", headers=headers)
    task_ids = {task["name"]: int(task["id"]) for task in tasks if task.get("name") in TASK_NAMES.values()}
    assert set(task_ids) == set(TASK_NAMES.values())

    sessions_before = request_json(f"{ai_url}/api/v1/sessions?limit=100")
    model_calls_before = sum(int(session.get("model_calls") or 0) for session in sessions_before)
    responses = {
        kind: request_json(
            f"{base_url}/order-row-drafts/tasks/{task_ids[name]}",
            headers=headers,
            timeout=180,
        )
        for kind, name in TASK_NAMES.items()
    }

    assert responses["single"]["status"] == "parsed"
    assert len(responses["single"]["rows"]) == 1
    assert responses["multi"]["status"] == "parsed"
    assert len(responses["multi"]["parents"]) == 2
    assert len(responses["multi"]["rows"]) == 4
    assert key_fields(responses["multi"]["rows"][0]) == key_fields(responses["multi"]["rows"][2])
    assert key_fields(responses["multi"]["rows"][1]) == key_fields(responses["multi"]["rows"][3])
    assert responses["text"]["status"] == "parsed"
    assert key_fields(responses["text"]["rows"][0]) == {
        "product": "文本鞋",
        "sales_attr1": "黑色",
        "sales_attr2": "42",
        "quantity": 2,
    }
    assert not responses["single"].get("ai_sessions")
    assert not responses["multi"].get("ai_sessions")
    assert not responses["text"].get("ai_sessions")
    assert not responses["unknown"].get("rows")
    assert responses["unknown"]["status"] in {
        "ai_rule_pending",
        "ai_parse_failed",
        "ai_unavailable",
    }
    assert responses["real"]["status"] == "ai_rule_pending"
    assert not responses["real"].get("rows")

    real_session_id = responses["real"]["ai_sessions"][0]["session_id"]
    real_session = request_json(f"{ai_url}/api/v1/sessions/{real_session_id}")
    answer_entries, answer_manifest = load_answer_set(args.answer_set)
    expected_row = answer_entries[0]["response"]["rows"][0]
    candidate_row = real_session["candidate"]["parents"][0]["rows"][0]
    assert key_fields(candidate_row) == key_fields(expected_row)

    sessions_after = request_json(f"{ai_url}/api/v1/sessions?limit=100")
    model_calls_after = sum(int(session.get("model_calls") or 0) for session in sessions_after)
    assert model_calls_after == model_calls_before

    packs = request_json(f"{base_url}/recognition-rule-packs", headers=headers)
    assert packs["active_pack"]["code"] == "ai-cold-start-r0002"
    report = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "backend_health": request_json(f"{base_url}/health"),
        "ai_health": request_json(f"{ai_url}/health"),
        "cold_database": cold_database_report(args.answer_set.with_name("cold-start.db")),
        "answer_set": {
            "sha256": answer_manifest["answer_set_sha256"],
            "task_count": answer_manifest["task_count"],
            "raw_record_count": answer_manifest["raw_record_count"],
            "expected_parent_count": sum(
                len(entry["response"].get("parents") or []) for entry in answer_entries
            ),
            "expected_row_count": sum(
                len(entry["response"].get("rows") or []) for entry in answer_entries
            ),
            "real_candidate_key_fields_match": True,
        },
        "active_rule_pack": packs["active_pack"],
        "model_calls_unchanged_during_rule_reuse": model_calls_after,
        "tasks": {
            kind: {
                "task_id": task_ids[name],
                "status": responses[kind]["status"],
                "row_count": len(responses[kind].get("rows") or []),
                "coverage": coverage(responses[kind]),
            }
            for kind, name in TASK_NAMES.items()
        },
    }
    output = args.output or args.answer_set.with_name("acceptance-report.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
