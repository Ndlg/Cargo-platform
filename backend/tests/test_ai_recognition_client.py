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
