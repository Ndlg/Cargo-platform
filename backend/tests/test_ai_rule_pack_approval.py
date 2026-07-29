import json

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


def test_rule_replay_requires_exact_row_multiset() -> None:
    shoe = {
        "product": "shoe",
        "sales_attr1": "",
        "sales_attr2": "",
        "quantity": 1,
        "remark": "",
    }
    sibling = {**shoe, "product": "sibling shoe"}

    assert ai_route.rows_cover_expected([shoe], [shoe]) is True
    assert ai_route.rows_cover_expected([shoe, sibling], [shoe]) is False
    assert ai_route.rows_cover_expected([shoe], [shoe, shoe]) is False


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
            learning_record={"session_id": "session-1", "confirmed_rows": [{"product": "old"}]},
        )
        db.commit()
        second = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-2",
            profile=candidate_profile(fingerprint, product_path="new"),
            validate=lambda payload: {"status": "valid", "errors": []},
            learning_record={"session_id": "session-2", "confirmed_rows": [{"product": "new"}]},
        )
        db.commit()
        packs = db.scalars(select(RecognitionRulePack)).all()

    assert first.id == second.id
    assert len(packs) == 1
    profiles = packs[0].payload["parser_policy"]["format_profiles"]
    assert len(profiles) == 1
    assert profiles[0]["fields"]["product"] == "new"
    assert [
        item["session_id"]
        for item in packs[0].payload["ai_learning_records"]
    ] == ["session-1", "session-2"]


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
            "document_sequence": 1,
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


def test_internal_validation_replays_only_selected_document(monkeypatch) -> None:
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

    def replay_selected_document(**kwargs) -> dict:
        documents = kwargs["raw_records"][0]["payload"]["task"]["documents"]
        assert [document["documentID"] for document in documents] == ["SECOND"]
        return {
            "contract_version": "order_row_drafts_v1",
            "rows": [
                {
                    "product": "second shoe",
                    "sales_attr1": "",
                    "sales_attr2": "",
                    "quantity": 1,
                    "remark": "",
                }
            ],
        }

    monkeypatch.setattr(
        ai_route,
        "preview_order_row_drafts_with_service",
        replay_selected_document,
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
                raw_payload=json.dumps(
                    {
                        "task": {
                            "documents": [
                                {"documentID": "FIRST", "items": [{"name": "first shoe", "quantity": 1}]},
                                {"documentID": "SECOND", "items": [{"name": "second shoe", "quantity": 1}]},
                            ]
                        }
                    }
                ),
                status="pending",
            )
        )
        db.commit()
        response = ai_route.approve_ai_rule(
            AiRuleApprovalRequest(
                session_id="session-selected-document",
                workspace_id=1,
                task_id=61,
                raw_record_id=100,
                document_sequence=2,
                format_fingerprint=f"sha256:{'4' * 64}",
                candidate_rule=candidate_profile(f"sha256:{'0' * 64}"),
                candidate_output={
                    "parents": [
                        {
                            "source": {"sanitized_payload": {"items": [{"name": "second shoe", "quantity": 1}]}},
                            "rows": [
                                {
                                    "product": "second shoe",
                                    "sales_attr1": "",
                                    "sales_attr2": "",
                                    "quantity": 1,
                                    "remark": "",
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

    ai_route.get_settings.cache_clear()
    assert response["status"] == "valid"


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


def test_new_rule_cannot_break_prior_confirmed_sample_of_same_fingerprint(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("AI_RECOGNITION_ENABLED", "true")
    monkeypatch.setenv("AI_RECOGNITION_INTERNAL_TOKEN", "test-secret")
    ai_route.get_settings.cache_clear()
    fingerprint = f"sha256:{'6' * 64}"
    monkeypatch.setattr(
        ai_route,
        "validate_rule_pack_with_service",
        lambda **_kwargs: {"status": "valid", "errors": []},
    )

    def replay_by_record(**kwargs) -> dict:
        raw_record_id = kwargs["raw_records"][0]["raw_record_id"]
        product = "new shoe" if raw_record_id == 100 else "broken old shoe"
        return {
            "contract_version": "order_row_drafts_v1",
            "rows": [
                {
                    "product": product,
                    "sales_attr1": "",
                    "sales_attr2": "",
                    "quantity": 1,
                    "remark": "",
                }
            ],
        }

    monkeypatch.setattr(
        ai_route,
        "preview_order_row_drafts_with_service",
        replay_by_record,
    )

    with Session(engine) as db:
        db.add(Workspace(id=1, tenant_id=1, name="test", code="test"))
        for record_id, product in ((100, "new shoe"), (101, "old shoe")):
            db.add(
                RawCaptureRecord(
                    id=record_id,
                    tenant_id=1,
                    workspace_id=1,
                    task_id=61,
                    document_id=f"doc-{record_id}",
                    source_component="test",
                    source_index=str(record_id),
                    payload_format="json",
                    raw_payload=json.dumps({"items": [{"name": product, "quantity": 1}]}),
                    status="pending",
                )
            )
        old_pack = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-old",
            profile=candidate_profile(fingerprint, product_path="old_name"),
            validate=lambda payload: {"status": "valid", "errors": []},
            learning_record={
                "session_id": "session-old",
                "task_id": 61,
                "raw_record_id": 101,
                "document_sequence": 1,
                "source_component": "test",
                "confirmed_rows": [
                    {
                        "product": "old shoe",
                        "sales_attr1": "",
                        "sales_attr2": "",
                        "quantity": 1,
                        "remark": "",
                    }
                ],
            },
        )
        db.commit()
        old_pack_id = old_pack.id

        try:
            ai_route.approve_ai_rule(
                AiRuleApprovalRequest(
                    session_id="session-new",
                    workspace_id=1,
                    task_id=61,
                    raw_record_id=100,
                    document_sequence=1,
                    format_fingerprint=fingerprint,
                    candidate_rule=candidate_profile(fingerprint, product_path="name"),
                    candidate_output={
                        "parents": [
                            {
                                "source": {"sanitized_payload": {"items": [{"name": "new shoe", "quantity": 1}]}},
                                "rows": [
                                    {
                                        "product": "new shoe",
                                        "sales_attr1": "",
                                        "sales_attr2": "",
                                        "quantity": 1,
                                        "remark": "",
                                    }
                                ],
                            }
                        ]
                    },
                ),
                db,
                "test-secret",
            )
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
        else:
            raise AssertionError("rule that breaks a prior confirmed sample must be rejected")

        db.expire_all()
        persisted = db.get(RecognitionRulePack, old_pack_id)
        assert persisted is not None
        assert persisted.payload["parser_policy"]["format_profiles"][0]["fields"]["product"] == "old_name"
        assert [
            item["session_id"]
            for item in persisted.payload["ai_learning_records"]
        ] == ["session-old"]

        prior = db.get(RawCaptureRecord, 101)
        assert prior is not None
        prior.is_deleted = True
        db.commit()
        try:
            ai_route.approve_ai_rule(
                AiRuleApprovalRequest(
                    session_id="session-new",
                    workspace_id=1,
                    task_id=61,
                    raw_record_id=100,
                    document_sequence=1,
                    format_fingerprint=fingerprint,
                    candidate_rule=candidate_profile(fingerprint, product_path="name"),
                    candidate_output={
                        "parents": [{
                            "source": {"sanitized_payload": {"items": [{"name": "new shoe", "quantity": 1}]}},
                            "rows": [{
                                "product": "new shoe",
                                "sales_attr1": "",
                                "sales_attr2": "",
                                "quantity": 1,
                                "remark": "",
                            }],
                        }]
                    },
                ),
                db,
                "test-secret",
            )
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
        else:
            raise AssertionError("missing confirmed history must block rule approval")

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
