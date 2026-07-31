import importlib
from pathlib import Path
import sys
from typing import Any


SERVICE_ROOT = Path(__file__).resolve().parents[2] / "services" / "waybill-parser"
sys.path.insert(0, str(SERVICE_ROOT))
ai_client = importlib.import_module("service_app.ai_client")


def test_parser_ai_client_sends_existing_internal_token(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "session_id": "session-token",
                "status": "ai_rule_pending",
            }

    def post(*_args, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setenv("AI_RECOGNITION_URL", "http://ai-recognition:8011")
    monkeypatch.setenv("AI_RECOGNITION_INTERNAL_TOKEN", "existing-token")
    monkeypatch.setattr(ai_client.httpx, "post", post)

    result = ai_client.recognize_with_ai(
        workspace_id=1,
        task_id=61,
        raw_record_id=901,
        document_sequence=1,
        source_component="test",
        deterministic_failure_reason="missing",
        evidence={},
    )

    assert result["session_id"] == "session-token"
    assert captured["headers"] == {
        "X-AI-Recognition-Token": "existing-token",
    }
