from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import ai_recognition as ai_route
from app.api.routes.ai_recognition import AiRuleApprovalRequest
from app.models import Base, RawCaptureRecord, RecognitionRulePack, Workspace
from app.services import order_row_reader
from app.services.recognition_rule_packs import save_ai_rule_profile


def candidate_profile(fingerprint: str, *, product_path: str = "name") -> dict:
    return {
        "fingerprint": fingerprint,
        "strategy": "structured_items_v1",
        "items_path": "items[]",
        "fields": {"product": product_path, "quantity": "quantity"},
    }


def test_ai_approval_updates_one_rule_pack_in_place() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    first_fingerprint = f"sha256:{'1' * 64}"
    second_fingerprint = f"sha256:{'2' * 64}"

    with Session(engine) as db:
        first = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-1",
            profile=candidate_profile(first_fingerprint),
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        first_payload = first.payload

        second = save_ai_rule_profile(
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

    assert [pack.code for pack in packs] == ["ai-recognition-main"]
    assert first.id == second.id == packs[0].id
    assert packs[0].status == "active"
    assert packs[0].is_enabled is True
    assert packs[0].payload != first_payload
    assert {
        profile["fingerprint"]
        for profile in packs[0].payload["parser_policy"]["format_profiles"]
    } == {first_fingerprint, second_fingerprint}


def test_ai_approval_replaces_existing_fingerprint() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    fingerprint = f"sha256:{'1' * 64}"

    with Session(engine) as db:
        first = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-1",
            profile=candidate_profile(fingerprint, product_path="old"),
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        second = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-2",
            profile=candidate_profile(fingerprint, product_path="new"),
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        packs = db.scalars(select(RecognitionRulePack)).all()

    assert first.id == second.id
    assert len(packs) == 1
    profiles = packs[0].payload["parser_policy"]["format_profiles"]
    assert len(profiles) == 1
    assert profiles[0]["fields"]["product"] == "new"


def test_ai_approval_rejects_invalid_candidate_without_creating_pack() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        try:
            save_ai_rule_profile(
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
    def replay_original_record(**kwargs) -> dict:
        assert kwargs["raw_records"][0]["payload"]["receiver"] == "secret"
        return {
            "contract_version": "order_row_drafts_v1",
            "rows": [
                {
                    "product": "sibling shoe",
                    "sales_attr1": "",
                    "sales_attr2": "",
                    "quantity": 1,
                    "remark": "",
                    "image_match_text": "sibling shoe",
                },
                {
                    "product": "shoe",
                    "sales_attr1": "",
                    "sales_attr2": "",
                    "quantity": 1,
                    "remark": "",
                    "image_match_text": "shoe",
                }
            ],
        }

    monkeypatch.setattr(ai_route, "preview_order_row_drafts_with_service", replay_original_record)

    with Session(engine) as db:
        db.add(Workspace(id=1, tenant_id=1, name="test", code="test"))
        db.add(
            RawCaptureRecord(
                id=100,
                tenant_id=1,
                workspace_id=1,
                task_id=61,
                document_id="doc",
                source_component="test",
                source_index="1",
                payload_format="json",
                raw_payload='{"receiver":"secret","items":[{"name":"shoe","quantity":1}]}',
                status="pending",
            )
        )
        db.commit()
        response = ai_route.approve_ai_rule(
            AiRuleApprovalRequest(
                session_id="session-api",
                workspace_id=1,
                task_id=61,
                raw_record_id=100,
                format_fingerprint=f"sha256:{'4' * 64}",
                candidate_rule=candidate_profile(f"sha256:{'0' * 64}"),
                candidate_output={
                    "parents": [
                        {
                            "source": {
                                "sanitized_payload": {
                                    "items": [{"name": "shoe", "quantity": 1}]
                                }
                            },
                            "rows": [
                                {
                                    "product": "shoe",
                                    "sales_attr1": "",
                                    "sales_attr2": "",
                                    "quantity": 1,
                                    "remark": "",
                                    "image_match_text": "",
                                }
                            ],
                        }
                    ]
                },
            ),
            db,
            "test-secret",
        )

    ai_route.get_settings.cache_clear()
    assert response["status"] == "activated"
    assert response["rule_pack"]["code"] == "ai-recognition-main"
    assert response["rerun_task_id"] == 61
    learning_records = db.scalar(select(RecognitionRulePack)).payload["ai_learning_records"]
    assert learning_records == [
        {
            "fingerprint": f"sha256:{'4' * 64}",
            "session_id": "session-api",
            "task_id": 61,
            "raw_record_id": 100,
            "source_component": "test",
            "sample_payload": {"items": [{"name": "shoe", "quantity": 1}]},
            "confirmed_rows": [
                {
                    "product": "shoe",
                    "sales_attr1": "",
                    "sales_attr2": "",
                    "quantity": 1,
                    "remark": "",
                }
            ],
            "rule_evidence": [],
        }
    ]


def test_internal_validation_replays_candidate_without_persisting_pack(monkeypatch) -> None:
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
    monkeypatch.setattr(
        ai_route,
        "preview_order_row_drafts_with_service",
        lambda **_kwargs: {
            "contract_version": "order_row_drafts_v1",
            "rows": [
                {
                    "product": "shoe",
                    "sales_attr1": "",
                    "sales_attr2": "",
                    "quantity": 1,
                    "remark": "",
                    "image_match_text": "shoe",
                }
            ],
        },
    )

    with Session(engine) as db:
        db.add(Workspace(id=1, tenant_id=1, name="test", code="test"))
        db.add(
            RawCaptureRecord(
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
        )
        db.commit()
        response = ai_route.approve_ai_rule(
            AiRuleApprovalRequest(
                session_id="session-validate",
                workspace_id=1,
                task_id=61,
                raw_record_id=100,
                format_fingerprint=f"sha256:{'4' * 64}",
                candidate_rule=candidate_profile(f"sha256:{'0' * 64}"),
                candidate_output={
                    "parents": [
                        {
                            "source": {"sanitized_payload": {"items": [{"name": "shoe", "quantity": 1}]}},
                            "rows": [
                                {
                                    "product": "shoe",
                                    "sales_attr1": "",
                                    "sales_attr2": "",
                                    "quantity": 1,
                                    "remark": "",
                                    "image_match_text": "",
                                }
                            ],
                        }
                    ]
                },
                validate_only=True,
            ),
            db,
            "test-secret",
        )

        assert response == {
            "status": "valid",
            "format_fingerprint": f"sha256:{'4' * 64}",
        }
        assert db.scalar(select(RecognitionRulePack)) is None

    ai_route.get_settings.cache_clear()


def test_internal_approval_rejects_rule_that_cannot_reproduce_candidate(monkeypatch) -> None:
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
    monkeypatch.setattr(
        ai_route,
        "preview_order_row_drafts_with_service",
        lambda **_kwargs: {
            "contract_version": "order_row_drafts_v1",
            "rows": [
                {
                    "product": "wrong",
                    "sales_attr1": "",
                    "sales_attr2": "",
                    "quantity": 1,
                    "remark": "",
                    "image_match_text": "",
                }
            ],
        },
    )

    with Session(engine) as db:
        db.add(Workspace(id=1, tenant_id=1, name="test", code="test"))
        db.add(
            RawCaptureRecord(
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
        )
        db.commit()
        request = AiRuleApprovalRequest(
            session_id="session-bad-replay",
            workspace_id=1,
            task_id=61,
            raw_record_id=100,
            format_fingerprint=f"sha256:{'5' * 64}",
            candidate_rule=candidate_profile(f"sha256:{'0' * 64}"),
            candidate_output={
                "parents": [
                    {
                        "source": {
                            "sanitized_payload": {
                                "items": [{"name": "shoe", "quantity": 1}]
                            }
                        },
                        "rows": [
                            {
                                "product": "shoe",
                                "sales_attr1": "",
                                "sales_attr2": "",
                                "quantity": 1,
                                "remark": "",
                                "image_match_text": "",
                            }
                        ],
                    }
                ]
            },
        )

        try:
            ai_route.approve_ai_rule(request, db, "test-secret")
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
        else:
            raise AssertionError("non-reproducible AI rule must be rejected")
        assert db.scalar(select(RecognitionRulePack)) is None

    ai_route.get_settings.cache_clear()


def test_no_pack_raw_records_do_not_reach_parser_from_business_flow(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[dict] = []
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
        try:
            order_row_reader.parse_raw_records_to_order_rows(
                db,
                workspace_id=1,
                task_id=61,
                records=[record],
            )
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
        else:
            raise AssertionError("business parsing must require a rule pack")

    assert calls == []
