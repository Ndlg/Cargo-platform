from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import ai_recognition as ai_route
from app.api.routes.ai_recognition import AiRuleApprovalRequest
from app.models import Base, RawCaptureRecord, RecognitionRulePack, Workspace
from app.services import order_row_reader
from app.services.recognition_rule_packs import create_ai_rule_pack_revision


def candidate_profile(fingerprint: str, *, product_path: str = "name") -> dict:
    return {
        "fingerprint": fingerprint,
        "strategy": "structured_items_v1",
        "items_path": "items[]",
        "fields": {"product": product_path, "quantity": "quantity"},
    }


def test_ai_approval_creates_immutable_rule_pack_revisions() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    first_fingerprint = f"sha256:{'1' * 64}"
    second_fingerprint = f"sha256:{'2' * 64}"

    with Session(engine) as db:
        first = create_ai_rule_pack_revision(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-1",
            profile=candidate_profile(first_fingerprint),
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        first_payload = first.payload

        second = create_ai_rule_pack_revision(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-2",
            profile=candidate_profile(second_fingerprint),
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()

        packs = db.scalars(
            select(RecognitionRulePack).order_by(RecognitionRulePack.id)
        ).all()

    assert [pack.code for pack in packs] == ["ai-cold-start-r0001", "ai-cold-start-r0002"]
    assert packs[0].status == "inactive"
    assert packs[0].is_enabled is False
    assert packs[0].payload == first_payload
    assert len(packs[0].payload["parser_policy"]["format_profiles"]) == 1
    assert packs[1].status == "active"
    assert packs[1].is_enabled is True
    assert {
        profile["fingerprint"]
        for profile in packs[1].payload["parser_policy"]["format_profiles"]
    } == {first_fingerprint, second_fingerprint}


def test_ai_approval_rejects_invalid_candidate_without_creating_pack() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        try:
            create_ai_rule_pack_revision(
                db,
                tenant_id=1,
                workspace_id=1,
                session_id="session-invalid",
                profile=candidate_profile(f"sha256:{'3' * 64}"),
                validate=lambda payload: {
                    "status": "invalid",
                    "errors": ["parser_policy.format_profiles[0].fields"],
                },
            )
        except ValueError as exc:
            assert "format_profiles" in str(exc)
        else:
            raise AssertionError("invalid AI rule must be rejected")

        assert db.scalar(select(RecognitionRulePack)) is None


def test_internal_approval_activates_validated_revision(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("AI_RECOGNITION_ENABLED", "true")
    monkeypatch.setenv("AI_RECOGNITION_INTERNAL_TOKEN", "test-secret")
    ai_route.get_settings.cache_clear()
    monkeypatch.setattr(
        ai_route,
        "validate_rule_pack_with_service",
        lambda **_kwargs: {"status": "valid", "errors": []},
    )

    with Session(engine) as db:
        db.add(Workspace(id=1, tenant_id=1, name="test", code="test"))
        db.commit()
        response = ai_route.approve_ai_rule(
            AiRuleApprovalRequest(
                session_id="session-api",
                workspace_id=1,
                task_id=61,
                raw_record_id=100,
                format_fingerprint=f"sha256:{'4' * 64}",
                candidate_rule=candidate_profile(f"sha256:{'0' * 64}"),
            ),
            db,
            "test-secret",
        )

    ai_route.get_settings.cache_clear()
    assert response["status"] == "activated"
    assert response["rule_pack"]["code"] == "ai-cold-start-r0001"
    assert response["rerun_task_id"] == 61


def test_no_pack_raw_records_reach_parser_when_ai_is_enabled(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[dict] = []
    monkeypatch.setattr(
        order_row_reader,
        "get_settings",
        lambda: type("Settings", (), {"ai_recognition_enabled": True})(),
    )
    monkeypatch.setattr(order_row_reader, "waybill_parser_service_enabled", lambda: True)

    def fake_parser(**kwargs) -> dict:
        calls.append(kwargs)
        return {
            "contract_version": "order_row_drafts_v1",
            "task_id": kwargs["task_id"],
            "status": "ai_rule_pending",
            "summary": {},
            "parents": [],
            "rows": [],
        }

    monkeypatch.setattr(order_row_reader, "parse_order_row_drafts_with_service", fake_parser)
    record = RawCaptureRecord(
        id=100,
        tenant_id=1,
        workspace_id=1,
        task_id=61,
        document_id="doc",
        source_component="test",
        source_index="1",
        payload_format="json",
        raw_payload='{"items":[{"name":"shoe","quantity":1}]}',
        status="pending",
    )

    with Session(engine) as db:
        rows, sources = order_row_reader.parse_raw_records_to_order_rows(
            db,
            workspace_id=1,
            task_id=61,
            records=[record],
        )

    assert rows == []
    assert sources == []
    assert calls[0]["workspace_id"] == 1
    assert calls[0]["rule_pack"] is None
