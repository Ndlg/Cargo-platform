import httpx
import pytest

from app.services import ai_recognition_client


def test_backend_ai_client_sends_internal_token(monkeypatch) -> None:
    captured: dict = {}

    class Settings:
        ai_recognition_url = "http://ai-recognition:8011"
        ai_recognition_internal_token = "existing-token"

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"fingerprints": []}

    def request(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return Response()

    monkeypatch.setattr(ai_recognition_client, "get_settings", Settings)
    monkeypatch.setattr(ai_recognition_client.httpx, "request", request)

    assert ai_recognition_client.fingerprint_catalog_with_service() == {
        "fingerprints": [],
    }
    assert captured["kwargs"]["headers"] == {
        "X-AI-Recognition-Token": "existing-token",
    }


@pytest.mark.parametrize("status_code", [404, 409, 422, 503])
def test_backend_ai_client_preserves_controlled_service_errors(
    monkeypatch,
    status_code: int,
) -> None:
    class Settings:
        ai_recognition_url = "http://ai-recognition:8011"
        ai_recognition_internal_token = "existing-token"

    response = httpx.Response(
        status_code,
        json={"detail": f"business detail {status_code}"},
        request=httpx.Request("GET", "http://ai-recognition:8011/api/v1/sessions/stale"),
    )
    monkeypatch.setattr(ai_recognition_client, "get_settings", Settings)
    monkeypatch.setattr(
        ai_recognition_client.httpx,
        "request",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(ai_recognition_client.AiRecognitionServiceError) as exc:
        ai_recognition_client.get_ai_recognition_session_with_service("stale")

    assert exc.value.status_code == status_code
    assert exc.value.detail == f"business detail {status_code}"
