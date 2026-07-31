from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.services.recognition_rule_packs import save_ai_rule_profile


def test_saved_ai_profile_keeps_server_controlled_learning_provenance() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    fingerprint = f"sha256:{'1' * 64}"

    with Session(engine) as db:
        pack = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-1",
            profile={
                "fingerprint": fingerprint,
                "strategy": "structured_items_v1",
                "items_path": "items[]",
                "fields": {"product": "name", "quantity": "quantity"},
            },
            validate=lambda payload: {"status": "valid", "errors": []},
        )

    assert pack.payload["parser_policy"]["format_profiles"][0]["provenance"] == {
        "source": "confirmed_ai_rule",
        "learning_session_id": "session-1",
    }
