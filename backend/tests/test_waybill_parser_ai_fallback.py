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


def known_ai_payload() -> dict[str, Any]:
    return {"contents": [{"data": {"productInfo": "范74 45 1件"}}]}


def raw_record(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "raw_record_id": 901,
        "task_id": 61,
        "document_sequence": 1,
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


def pack(
    profiles: list[dict[str, Any]],
    *,
    fingerprint_strategy: str | None = None,
) -> dict[str, Any]:
    parser_policy: dict[str, Any] = {
        "requires_active_rule_pack": True,
        "order_row_parser": "declarative_v1",
        "format_profiles": profiles,
    }
    if fingerprint_strategy:
        parser_policy["fingerprint_strategy"] = fingerprint_strategy
    return {
        "contract_version": "recognition_rule_pack_v1",
        "pack": {"code": "ai-r1", "name": "AI R1", "version": "1.0.0"},
        "parser_policy": parser_policy,
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
                    "error": "model failed" if status == "ai_unavailable" else None,
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


def parse(
    client: TestClient,
    rule_pack: dict[str, Any] | None,
    value: dict[str, Any],
    *,
    allow_ai: bool = False,
    ai_field_selections: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    record = raw_record(value)
    if ai_field_selections is not None:
        record["ai_field_selections"] = ai_field_selections
    return client.post(
        "/api/v1/parse/batch",
        json={
            "workspace_id": 1,
            "task_id": 61,
            "raw_records": [record],
            "rule_pack": rule_pack,
            "allow_ai": allow_ai,
        },
    ).json()


def test_normal_parse_never_calls_ai_when_rule_pack_is_missing(
    monkeypatch,
) -> None:
    app, _rules = load_parser()
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(client, None, payload())

    assert body["status"] == "rule_pack_missing"
    assert body["summary"]["parent_waybill_count"] == 1
    assert body["summary"]["child_waybill_count"] == 0
    assert body["rows"] == []
    assert requests == []


def test_manual_single_waybill_parse_creates_one_ai_session(monkeypatch) -> None:
    app, _rules = load_parser()
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(
                client,
                None,
                known_ai_payload(),
                allow_ai=True,
                ai_field_selections={"CLOUD-PRODUCT-INFO": ["product_info"]},
            )

    assert body["status"] == "ai_rule_pending"
    assert body["rows"] == []
    assert body["ai_sessions"][0]["session_id"] == "session-1"
    assert "AI候选商品" not in json.dumps(body, ensure_ascii=False)
    assert len(requests) == 1
    assert requests[0]["workspace_id"] == 1
    assert requests[0]["document_sequence"] == 1
    assert requests[0]["deterministic_failure_reason"] == "rule_pack_missing"


def test_approved_synthesized_rule_parses_same_grammar_without_ai(monkeypatch) -> None:
    app, _rules = load_parser()
    synthesizer = importlib.import_module("service_app.rule_synthesizer")

    def print_xml(text: str) -> dict[str, Any]:
        return {
            "task": {
                "documents": [
                    {
                        "contents": [
                            {
                                "printXML": (
                                    '<layout id="CUSTOM_AREA"><text><![CDATA['
                                    f"{text}"
                                    "]]></text></layout>"
                                )
                            }
                        ]
                    }
                ]
            }
        }

    learned = synthesizer.synthesize_rule(
        payload=print_xml("灰黑，38 商品名称*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[
            {
                "product": "商品名称",
                "sales_attr1": "灰黑",
                "sales_attr2": "38",
                "quantity": 1,
                "remark": "",
            }
        ],
        gold_samples=[],
        negative_samples=[],
        selected_fields=["print_text"],
    )
    assert learned["status"] == "compiled"

    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(
                client,
                pack(
                    [learned["rule"]],
                    fingerprint_strategy="business_shape_v2",
                ),
                print_xml("黄色，43 另一个商品*2"),
                allow_ai=True,
            )

    assert body["status"] == "parsed"
    assert body["rows"][0]["product"] == "另一个商品"
    assert body["rows"][0]["quantity"] == 2
    assert requests == []


def test_manual_parse_forwards_tenant_fingerprint_field_selection(monkeypatch) -> None:
    app, _rules = load_parser()
    record = raw_record(
        {
            "contents": [
                {
                    "data": {
                        "productInfo": "范74 5代白金 45 1件",
                        "productShortInfo": "范74",
                        "remark": "不要传给模型",
                        "productCount": "1件",
                    }
                }
            ]
        }
    )
    record["ai_field_selections"] = {
        "CLOUD-PRODUCT-INFO": ["product_info", "product_count"],
    }
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/parse/batch",
                json={
                    "workspace_id": 1,
                    "task_id": 61,
                    "raw_records": [record],
                    "rule_pack": None,
                    "allow_ai": True,
                },
            )

    assert response.status_code == 200
    assert "payload" not in requests[0]
    assert "field_selections" not in requests[0]
    evidence = requests[0]["evidence"]
    assert evidence["contract_version"] == "waybill_evidence_v1"
    assert evidence["fingerprint_code"] == "CLOUD-PRODUCT-INFO"
    paths = {span["source_path"] for span in evidence["spans"]}
    assert any(path.endswith(".productInfo") for path in paths)
    assert any(path.endswith(".productCount") for path in paths)
    assert all(not path.endswith(".remark") for path in paths)


def test_manual_parse_without_tenant_field_selection_does_not_use_catalog_defaults(monkeypatch) -> None:
    app, _rules = load_parser()
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(
                client,
                None,
                {"contents": [{"data": {"productInfo": "范74 45 1件"}}]},
                allow_ai=True,
            )

    assert body["status"] == "fingerprint_field_selection_required"
    assert body["diagnostics"][0]["reason"] == "fingerprint_field_selection_required"
    assert requests == []


def test_manual_parse_unknown_fingerprint_requires_adapter_without_ai(monkeypatch) -> None:
    app, _rules = load_parser()
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(client, None, {"unknown": "unreadable"}, allow_ai=True)

    assert body["status"] == "fingerprint_adapter_required"
    assert body["diagnostics"][0]["reason"] == "fingerprint_adapter_required"
    assert requests == []


def test_manual_parse_without_readable_authorized_evidence_requires_adapter(monkeypatch) -> None:
    app, _rules = load_parser()
    record = raw_record({"contents": [{"data": {"productInfo": "范74 45 1件"}}]})
    record["ai_field_selections"] = {"CLOUD-PRODUCT-INFO": ["product_count"]}
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = client.post(
                "/api/v1/parse/batch",
                json={
                    "workspace_id": 1,
                    "task_id": 61,
                    "raw_records": [record],
                    "rule_pack": None,
                    "allow_ai": True,
                },
            ).json()

    assert body["status"] == "fingerprint_adapter_required"
    assert body["diagnostics"][0]["reason"] == "fingerprint_adapter_required"
    assert requests == []


def test_manual_parse_known_authorized_fingerprint_without_profile_still_calls_ai(monkeypatch) -> None:
    app, rules = load_parser()
    record = raw_record({"contents": [{"data": {"productInfo": "范74 45 1件"}}]})
    record["ai_field_selections"] = {"CLOUD-PRODUCT-INFO": ["product_info"]}
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = client.post(
                "/api/v1/parse/batch",
                json={
                    "workspace_id": 1,
                    "task_id": 61,
                    "raw_records": [record],
                    "rule_pack": pack([profile(rules, payload())]),
                    "allow_ai": True,
                },
            ).json()

    assert body["status"] == "ai_rule_pending"
    assert body["diagnostics"][0]["deterministic_reason"] == "format_profile_missing"
    assert len(requests) == 1


def test_manual_parse_keeps_invalid_span_selection_editable(monkeypatch) -> None:
    app, _rules = load_parser()
    with fake_ai("candidate_invalid") as (url, _requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(
                client,
                None,
                known_ai_payload(),
                allow_ai=True,
                ai_field_selections={"CLOUD-PRODUCT-INFO": ["product_info"]},
            )

    assert body["status"] == "candidate_invalid"
    assert body["ai_sessions"][0]["session_id"] == "session-1"
    assert body["ai_sessions"][0]["console_url"].endswith("session=session-1")
    assert body["diagnostics"][0]["reason"] == "candidate_invalid"
    assert body["summary"]["pending_rule_pack_count"] == 1


def test_ai_service_unavailable_status_is_preserved(monkeypatch) -> None:
    app, _rules = load_parser()
    with fake_ai("ai_unavailable") as (url, _requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(
                client,
                None,
                known_ai_payload(),
                allow_ai=True,
                ai_field_selections={"CLOUD-PRODUCT-INFO": ["product_info"]},
            )

    assert body["status"] == "ai_unavailable"
    assert body["diagnostics"][0]["reason"] == "ai_unavailable"
    assert body["diagnostics"][0]["error"] == "model failed"


def test_manual_parse_keeps_rule_approval_in_progress(monkeypatch) -> None:
    app, _rules = load_parser()
    with fake_ai("approving") as (url, _requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(
                client,
                None,
                known_ai_payload(),
                allow_ai=True,
                ai_field_selections={"CLOUD-PRODUCT-INFO": ["product_info"]},
            )

    assert body["status"] == "approving"
    assert body["diagnostics"][0]["reason"] == "approving"
    assert body["summary"]["pending_rule_pack_count"] == 1


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


def test_normal_parse_keeps_incomplete_profile_as_exception_without_calling_ai(monkeypatch) -> None:
    app, rules = load_parser()
    baseline = payload()
    incomplete = payload(quantity="")
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(client, pack([profile(rules, baseline)]), incomplete)

    assert body["status"] == "format_profile_incomplete"
    assert body["rows"] == []
    assert body["diagnostics"][0]["reason"] == "missing_quantity"
    assert requests == []


def test_manual_parse_rejects_more_than_one_waybill(monkeypatch) -> None:
    app, _rules = load_parser()
    value = payload()
    value["task"]["documents"].append(value["task"]["documents"][0])
    with fake_ai() as (url, requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/parse/batch",
                json={
                    "workspace_id": 1,
                    "task_id": 61,
                    "raw_records": [raw_record(value)],
                    "rule_pack": None,
                    "allow_ai": True,
                },
            )

    assert response.status_code == 422
    assert requests == []


def test_ai_network_failure_is_business_exception_not_parser_500(monkeypatch) -> None:
    app, _rules = load_parser()
    monkeypatch.setenv("AI_RECOGNITION_URL", "http://127.0.0.1:1")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "workspace_id": 1,
                "task_id": 61,
                "raw_records": [
                    {
                        **raw_record(known_ai_payload()),
                        "ai_field_selections": {
                            "CLOUD-PRODUCT-INFO": ["product_info"]
                        },
                    }
                ],
                "rule_pack": None,
                "allow_ai": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ai_unavailable"
    assert response.json()["diagnostics"][0]["reason"] == "ai_unavailable"


def test_ai_unavailable_response_is_distinct_from_transport_failure(monkeypatch) -> None:
    app, _rules = load_parser()
    with fake_ai("ai_unavailable") as (url, _requests):
        monkeypatch.setenv("AI_RECOGNITION_URL", url)
        with TestClient(app) as client:
            body = parse(
                client,
                None,
                known_ai_payload(),
                allow_ai=True,
                ai_field_selections={"CLOUD-PRODUCT-INFO": ["product_info"]},
            )

    assert body["status"] == "ai_unavailable"
    assert body["diagnostics"][0]["reason"] == "ai_unavailable"
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
