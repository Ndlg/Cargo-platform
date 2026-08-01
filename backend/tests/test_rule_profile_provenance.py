from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.services.recognition_rule_packs import save_learned_rule_profile


def test_saved_profile_keeps_server_controlled_learning_provenance() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    fingerprint = f"sha256:{'1' * 64}"

    with Session(engine) as db:
        pack = save_learned_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            learning_record_id="record-1",
            profile={
                "fingerprint": fingerprint,
                "strategy": "structured_items_v1",
                "items_path": "items[]",
                "fields": {"product": "name", "quantity": "quantity"},
            },
            validate=lambda payload: {"status": "valid", "errors": []},
        )

    assert pack.payload["parser_policy"]["format_profiles"][0]["provenance"] == {
        "source": "confirmed_learning_rule",
        "learning_record_id": "record-1",
    }


def test_relearning_text_profile_replaces_previous_rule_slot() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    fingerprint = f"sha256:{'1' * 64}"

    with Session(engine) as db:
        base_profile = {
            "fingerprint": fingerprint,
            "grammar_signature": "grammar-1",
            "strategy": "text_pipeline_v1",
            "selected_fields": ["item_info"],
            "source_path": "item_info",
        }
        for delimiter in (";", "|"):
            save_learned_rule_profile(
                db,
                tenant_id=1,
                workspace_id=1,
                learning_record_id="record-1",
                profile={**base_profile, "steps": [{"operation": "split", "delimiter": delimiter}]},
                validate=lambda payload: {"status": "valid", "errors": []},
            )

        pack = save_learned_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            learning_record_id="record-2",
            profile={**base_profile, "steps": [{"operation": "split", "delimiter": ","}]},
            validate=lambda payload: {"status": "valid", "errors": []},
        )

    profiles = pack.payload["parser_policy"]["format_profiles"]
    assert len(profiles) == 1
    assert profiles[0]["steps"][0]["delimiter"] == ","
    assert profiles[0]["provenance"]["learning_record_id"] == "record-2"
