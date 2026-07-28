from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from threading import Thread
from typing import Any, Iterator

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = REPO_ROOT / "services" / "waybill-parser"
SERVICE_MAIN = SERVICE_ROOT / "service_app" / "main.py"


def load_parser():
    service_root = str(SERVICE_ROOT)
    if service_root not in sys.path:
        sys.path.insert(0, service_root)
    spec = importlib.util.spec_from_file_location("waybill_parser_ai_fallback_main", SERVICE_MAIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    rules = importlib.import_module("service_app.declarative_rules")
    return module.app, rules


def payload(quantity: object = 1) -> dict[str, Any]:
    return {
        "task": {
            "documents": [
                {
                    "contents": [
                        {
                            "data": {
                                "items": [
                                    {
                                        "product": "范74",
                                        "attr1": "5代白金",
                                        "attr2": "45",
                                        "quantity": quantity,
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    }


def raw_record(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "raw_record_id": 901,
        "task_id": 61,
        "source_component": "cainiao-cnprint",
        "source_index": "1",
        "payload": value,
    }


def profile(rules: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "fingerprint": rules.structural_fingerprint(value, "cainiao-cnprint"),
        "strategy": "structured_items_v1",
        "items_path": "task.documents[].contents[].data.items[]",
        "fields": {
            "product": "product",
            "sales_attr1": "attr1",
            "sales_attr2": "attr2",
            "quantity": "quantity",
        },
    }


def pack(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract_version": "recognition_rule_pack_v1",
        "pack": {"code": "ai-r1", "name": "AI R1", "version": "1.0.0"},
        "parser_policy": {
            "requires_active_rule_pack": True,
            "order_row_parser": "declarative_v1",
            "format_profiles": profiles,
        },
    }


@contextmanager
def fake_ai(status: str = "ai_rule_pending") -> Iterator[tuple[str, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            body = json.dumps(
                {
                    "session_id": f"session-{len(requests)}",
                    "status": status,
                    "fingerprint": "sha256:" + "a" * 64,
                    "candidate": {
                        "parents": [{"rows": [{"product": "AI候选商品", "quantity": 1}]}]
                    },
                    "console_url": f"http://127.0.0.1:6183/console?session=session-{len(requests)}",
                    "error": "model failed" if status == "ai_parse_failed" else None,
                },
                ensure_ascii=False,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def parse(client: TestClient, rule_pack: dict[str, Any] | None, value: dict[str, Any]) -> dict[str, Any]:
    return client.post(
        "/api/v1/parse/batch",
        json={
            "workspace_id": 1,
            "task_id": 61,
            "raw_records": [raw_record(value)],
            "rule_pack": rule_pack,
        },
    ).json()


def test_no_rule_pack_creates_pending_ai_session_without_returning_candidate_rows(
    monkeypatch,
) -> None:
    app, _rules = load_parser()
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(client, None, payload())

    assert body["status"] == "ai_rule_pending"
    assert body["summary"]["parent_waybill_count"] == 1
    assert body["summary"]["child_waybill_count"] == 0
    assert body["rows"] == []
    assert body["ai_sessions"][0]["session_id"] == "session-1"
    assert "AI候选商品" not in json.dumps(body, ensure_ascii=False)
    assert requests[0]["workspace_id"] == 1
    assert requests[0]["deterministic_failure_reason"] == "rule_pack_missing"


def test_complete_known_profile_does_not_call_ai(monkeypatch) -> None:
    app, rules = load_parser()
    value = payload()
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(client, pack([profile(rules, value)]), value)

    assert body["status"] == "parsed"
    assert body["rows"][0]["product"] == "范74"
    assert requests == []


def test_incomplete_known_profile_calls_ai_and_withholds_partial_row(monkeypatch) -> None:
    app, rules = load_parser()
    baseline = payload()
    incomplete = payload(quantity="")
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(client, pack([profile(rules, baseline)]), incomplete)

    assert body["status"] == "ai_rule_pending"
    assert body["rows"] == []
    assert body["diagnostics"][0]["deterministic_reason"] == "missing_quantity"
    assert requests[0]["deterministic_failure_reason"] == "missing_quantity"


def test_ai_network_failure_is_business_exception_not_parser_500(monkeypatch) -> None:
    app, _rules = load_parser()
    monkeypatch.setenv("AI_RECOGNITION_URL", "http://127.0.0.1:1")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "workspace_id": 1,
                "task_id": 61,
                "raw_records": [raw_record(payload())],
                "rule_pack": None,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ai_unavailable"
    assert response.json()["diagnostics"][0]["reason"] == "ai_unavailable"


def test_ai_parse_failure_is_distinct_from_unavailable(monkeypatch) -> None:
    app, _rules = load_parser()
    with fake_ai("ai_parse_failed") as (url, _requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(client, None, payload())

    assert body["status"] == "ai_parse_failed"
    assert body["diagnostics"][0]["reason"] == "ai_parse_failed"
    assert body["diagnostics"][0]["error"] == "model failed"


def test_ai_disabled_keeps_explicit_rule_pack_missing(monkeypatch) -> None:
    app, _rules = load_parser()
    monkeypatch.delenv("AI_RECOGNITION_URL", raising=False)

    with TestClient(app) as client:
        body = parse(client, None, payload())

    assert body["status"] == "rule_pack_missing"


def test_known_rule_keeps_working_while_ai_is_down(monkeypatch) -> None:
    app, rules = load_parser()
    value = payload()
    monkeypatch.setenv("AI_RECOGNITION_URL", "http://127.0.0.1:1")

    with TestClient(app) as client:
        body = parse(client, pack([profile(rules, value)]), value)

    assert body["status"] == "parsed"
    assert body["rows"][0]["product"] == "范74"
