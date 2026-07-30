from copy import deepcopy
import json

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes import ai_recognition as ai_route
from app.api.routes.ai_recognition import AiRuleApprovalRequest
from app.models import (
    Base,
    RawCaptureRecord,
    RecognitionRulePack,
    RecognitionRulePackRevision,
    TenantFingerprintConfig,
    Workspace,
)
from app.services import order_row_reader
from app.services.recognition_rule_packs import save_ai_rule_profile


FINGERPRINT = f"sha256:{'4' * 64}"
SHOE_ROW = {
    "product": "shoe",
    "sales_attr1": "",
    "sales_attr2": "",
    "quantity": 1,
    "remark": "",
}


@pytest.fixture(autouse=True)
def clear_settings_cache():
    ai_route.get_settings.cache_clear()
    yield
    ai_route.get_settings.cache_clear()


def candidate_profile(fingerprint: str, *, product_path: str = "name") -> dict:
    return {
        "fingerprint": fingerprint,
        "strategy": "structured_items_v1",
        "items_path": "items[]",
        "fields": {"product": product_path, "quantity": "quantity"},
    }


def compiled_result(
    fingerprint: str = FINGERPRINT,
    *,
    replay_report: list[dict] | None = None,
) -> dict:
    return {
        "status": "compiled",
        "rule": {
            **candidate_profile(fingerprint),
            "grammar_signature": "grammar-a",
        },
        "replay_report": replay_report or [{"kind": "current", "passed": True}],
    }


def approval_request(
    *,
    session_id: str = "session-api",
    raw_record_id: int = 100,
    document_sequence: int = 1,
    rows: list[dict] | None = None,
    validate_only: bool = False,
) -> AiRuleApprovalRequest:
    return AiRuleApprovalRequest(
        session_id=session_id,
        workspace_id=1,
        task_id=61,
        raw_record_id=raw_record_id,
        document_sequence=document_sequence,
        format_fingerprint=FINGERPRINT,
        fingerprint_code="CN-PACKAGE-ITEMS",
        candidate_output={"parents": [{"rows": rows or [SHOE_ROW]}]},
        validate_only=validate_only,
    )


def enable_ai(monkeypatch) -> None:
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
        "rerun_task_with_active_rule",
        lambda _db, *, workspace_id, task_id: {
            "task_id": task_id,
            "parsed_row_count": 0,
            "match_summary": {},
        },
    )


def add_workspace(db: Session) -> None:
    db.add(Workspace(id=1, tenant_id=1, name="test", code="test"))


def add_field_config(
    db: Session,
    *,
    fingerprint_code: str = "CN-PACKAGE-ITEMS",
    selected_fields: list[str] | None = None,
) -> None:
    db.add(
        TenantFingerprintConfig(
            tenant_id=1,
            fingerprint_code=fingerprint_code,
            is_enabled=True,
            selected_fields=selected_fields or ["item_name", "item_quantity"],
        )
    )


def add_record(
    db: Session,
    *,
    record_id: int = 100,
    task_id: int = 61,
    payload: dict | None = None,
    product: str = "shoe",
) -> RawCaptureRecord:
    record = RawCaptureRecord(
        id=record_id,
        tenant_id=1,
        workspace_id=1,
        task_id=task_id,
        document_id=f"doc-{record_id}",
        source_component="test",
        source_index=str(record_id),
        payload_format="json",
        raw_payload=json.dumps(
            payload or {"items": [{"name": product, "quantity": 1}]}
        ),
        status="pending",
    )
    db.add(record)
    return record


def test_ai_approval_keeps_immutable_rule_pack_revisions() -> None:
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
        packs = db.scalars(select(RecognitionRulePack)).all()
        revisions = db.scalars(
            select(RecognitionRulePackRevision).order_by(RecognitionRulePackRevision.revision)
        ).all()

    assert first.id == second.id == packs[0].id
    assert len(packs) == 1
    assert [revision.revision for revision in revisions] == [1, 2]
    assert revisions[0].payload == first_payload
    assert revisions[1].payload == packs[0].payload
    assert packs[0].version == revisions[1].version
    assert packs[0].status == "active"
    assert packs[0].is_enabled is True
    assert packs[0].payload != first_payload
    assert {
        profile["fingerprint"]
        for profile in packs[0].payload["parser_policy"]["format_profiles"]
    } == {first_fingerprint, second_fingerprint}


def test_ai_approval_replaces_existing_fingerprint_but_keeps_learning_history() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        first = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-1",
            profile=candidate_profile(FINGERPRINT, product_path="old"),
            validate=lambda payload: {"status": "valid", "errors": []},
            learning_record={"session_id": "session-1", "confirmed_rows": [{"product": "old"}]},
        )
        db.commit()
        second = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-2",
            profile=candidate_profile(FINGERPRINT, product_path="new"),
            validate=lambda payload: {"status": "valid", "errors": []},
            learning_record={"session_id": "session-2", "confirmed_rows": [{"product": "new"}]},
        )
        db.commit()
        pack = db.scalar(select(RecognitionRulePack))

    assert first.id == second.id
    assert pack is not None
    assert pack.payload["parser_policy"]["format_profiles"][0]["fields"]["product"] == "new"
    assert [
        item["session_id"]
        for item in pack.payload["ai_learning_records"]
    ] == ["session-1", "session-2"]


def test_ai_approval_keeps_other_grammar_profile_for_same_fingerprint() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-a",
            profile={
                **candidate_profile(FINGERPRINT, product_path="grammar_a"),
                "grammar_signature": "grammar-a",
            },
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        pack = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-b",
            profile={
                **candidate_profile(FINGERPRINT, product_path="grammar_b"),
                "grammar_signature": "grammar-b",
            },
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        payload = pack.payload

    assert {
        profile["grammar_signature"]
        for profile in payload["parser_policy"]["format_profiles"]
    } == {"grammar-a", "grammar-b"}
    assert payload["parser_policy"]["fingerprint_strategy"] == "business_shape_v2"


def test_ai_approval_rejects_invalid_candidate_without_creating_pack() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        with pytest.raises(ValueError, match="format_profiles"):
            save_ai_rule_profile(
                db,
                tenant_id=1,
                workspace_id=1,
                session_id="session-invalid",
                profile=candidate_profile(FINGERPRINT),
                validate=lambda payload: {
                    "status": "invalid",
                    "errors": ["parser_policy.format_profiles[0].fields"],
                },
            )
        assert db.scalar(select(RecognitionRulePack)) is None


def test_internal_approval_synthesizes_from_original_and_preserves_duplicates(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    calls: list[dict] = []

    def synthesize(**kwargs) -> dict:
        calls.append(kwargs)
        return compiled_result(replay_report=[{"kind": "current", "passed": True}])

    monkeypatch.setattr(ai_route, "synthesize_rule_with_service", synthesize)

    with Session(engine) as db:
        add_workspace(db)
        add_field_config(db)
        add_record(
            db,
            payload={
                "receiver": "secret",
                "items": [{"name": "shoe", "quantity": 1}],
            },
        )
        db.commit()
        response = ai_route.approve_ai_rule(
            approval_request(rows=[SHOE_ROW, SHOE_ROW]),
            db,
            "test-secret",
        )
        pack = db.scalar(select(RecognitionRulePack))

    assert response["status"] == "approved"
    assert calls == [
        {
            "raw_payload": {
                "receiver": "secret",
                "items": [{"name": "shoe", "quantity": 1}],
            },
            "source_component": "test",
            "corrected_rows": [SHOE_ROW, SHOE_ROW],
            "gold_samples": [],
            "negative_samples": [],
            "selected_fields": ["item_name", "item_quantity"],
        }
    ]
    assert pack is not None
    learning = pack.payload["ai_learning_records"]
    assert learning[0]["confirmed_rows"] == [SHOE_ROW, SHOE_ROW]
    assert learning[0]["replay_report"] == [{"kind": "current", "passed": True}]
    assert learning[0]["grammar_signature"] == "grammar-a"
    assert learning[0]["negative_replay"] == "not_available"
    assert "sample_payload" not in learning[0]
    assert "rule_evidence" not in learning[0]
    assert response["negative_replay"] == "not_available"


def test_internal_approval_flushes_before_commit_and_reruns_without_refresh(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    monkeypatch.setattr(
        ai_route,
        "synthesize_rule_with_service",
        lambda **_kwargs: compiled_result(),
    )
    reruns: list[tuple[int, int]] = []

    def rerun(db: Session, *, workspace_id: int, task_id: int) -> dict:
        reruns.append((workspace_id, task_id))
        return {
            "task_id": task_id,
            "parsed_row_count": 1,
            "match_summary": {"matched": 1},
        }

    monkeypatch.setattr(ai_route, "rerun_task_with_active_rule", rerun)

    with Session(engine) as db:
        add_workspace(db)
        add_record(db)
        db.commit()
        monkeypatch.setattr(
            db,
            "refresh",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("approval must not refresh after commit")
            ),
        )
        response = ai_route.approve_ai_rule(
            approval_request(),
            db,
            "test-secret",
        )

    assert response["status"] == "approved"
    assert response["reruns"] == [
        {
            "task_id": 61,
            "status": "completed",
            "parsed_row_count": 1,
            "match_summary": {"matched": 1},
        }
    ]
    assert response["warnings"] == []
    assert reruns == [(1, 61)]


def test_committed_approval_stays_approved_when_rerun_fails_and_retry_is_idempotent(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    synthesis_calls: list[dict] = []
    monkeypatch.setattr(
        ai_route,
        "synthesize_rule_with_service",
        lambda **kwargs: synthesis_calls.append(kwargs) or compiled_result(),
    )
    rerun_calls: list[int] = []

    def unavailable(_db: Session, *, workspace_id: int, task_id: int) -> dict:
        assert workspace_id == 1
        rerun_calls.append(task_id)
        raise RuntimeError("parser offline")

    monkeypatch.setattr(ai_route, "rerun_task_with_active_rule", unavailable)

    with Session(engine) as db:
        add_workspace(db)
        add_record(db)
        db.commit()
        first = ai_route.approve_ai_rule(
            approval_request(),
            db,
            "test-secret",
        )
        second = ai_route.approve_ai_rule(
            approval_request(),
            db,
            "test-secret",
        )
        revisions = db.scalars(select(RecognitionRulePackRevision)).all()

    for response in (first, second):
        assert response["status"] == "approved"
        assert response["reruns"] == [
            {
                "task_id": 61,
                "status": "failed",
                "error": "parser offline",
            }
        ]
        assert response["warnings"] == [
            "采集轮次 61 重算失败：parser offline"
        ]
    assert len(synthesis_calls) == 1
    assert len(revisions) == 1
    assert rerun_calls == [61, 61]


def test_approval_reruns_all_same_fingerprint_grammar_tasks_independently(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    monkeypatch.setattr(
        ai_route,
        "synthesize_rule_with_service",
        lambda **_kwargs: compiled_result(),
    )
    rerun_calls: list[tuple[int, int]] = []

    def rerun(_db: Session, *, workspace_id: int, task_id: int) -> dict:
        rerun_calls.append((workspace_id, task_id))
        if task_id == 61:
            raise RuntimeError("current task failed")
        return {
            "task_id": task_id,
            "parsed_row_count": 2,
            "match_summary": {"matched": 2},
        }

    monkeypatch.setattr(ai_route, "rerun_task_with_active_rule", rerun)

    with Session(engine) as db:
        add_workspace(db)
        add_record(db, record_id=100, task_id=61, product="new shoe")
        add_record(db, record_id=101, task_id=62, product="old shoe")
        save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-old",
            profile={
                **candidate_profile(FINGERPRINT, product_path="old_name"),
                "grammar_signature": "grammar-a",
            },
            validate=lambda payload: {"status": "valid", "errors": []},
            learning_record={
                "session_id": "session-old",
                "task_id": 62,
                "raw_record_id": 101,
                "document_sequence": 1,
                "source_component": "test",
                "confirmed_rows": [{**SHOE_ROW, "product": "old shoe"}],
            },
        )
        db.commit()
        response = ai_route.approve_ai_rule(
            approval_request(
                session_id="session-new",
                rows=[{**SHOE_ROW, "product": "new shoe"}],
            ),
            db,
            "test-secret",
        )

    assert response["status"] == "approved"
    assert response["reruns"] == [
        {
            "task_id": 61,
            "status": "failed",
            "error": "current task failed",
        },
        {
            "task_id": 62,
            "status": "completed",
            "parsed_row_count": 2,
            "match_summary": {"matched": 2},
        },
    ]
    assert response["warnings"] == [
        "采集轮次 61 重算失败：current task failed"
    ]
    assert rerun_calls == [(1, 61), (1, 62)]


def test_revision_conflict_returns_409_and_rolls_back(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    monkeypatch.setattr(
        ai_route,
        "synthesize_rule_with_service",
        lambda **_kwargs: compiled_result(),
    )

    with Session(engine) as db:
        add_workspace(db)
        add_record(db)
        db.commit()
        original_flush = db.flush

        def conflicting_flush(*args, **kwargs):
            has_new_revision = any(
                isinstance(item, RecognitionRulePackRevision)
                for item in db.new
            )
            original_flush(*args, **kwargs)
            if has_new_revision:
                raise IntegrityError("revision conflict", {}, Exception("duplicate revision"))

        monkeypatch.setattr(db, "flush", conflicting_flush)
        with pytest.raises(HTTPException) as exc:
            ai_route.approve_ai_rule(
                approval_request(),
                db,
                "test-secret",
            )

        assert exc.value.status_code == 409
        assert db.scalar(select(RecognitionRulePack)) is None
        assert db.scalar(select(RecognitionRulePackRevision)) is None


def test_internal_validation_rolls_back_new_pack(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    monkeypatch.setattr(
        ai_route,
        "synthesize_rule_with_service",
        lambda **_kwargs: compiled_result(),
    )

    with Session(engine) as db:
        add_workspace(db)
        add_record(db)
        db.commit()
        response = ai_route.approve_ai_rule(
            approval_request(validate_only=True),
            db,
            "test-secret",
        )
        assert response == {
            "status": "valid",
            "format_fingerprint": FINGERPRINT,
        }
        assert db.scalar(select(RecognitionRulePack)) is None


def test_internal_approval_synthesizes_only_selected_document(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    seen: list[dict] = []

    def synthesize(**kwargs) -> dict:
        seen.append(kwargs["raw_payload"])
        return compiled_result()

    monkeypatch.setattr(ai_route, "synthesize_rule_with_service", synthesize)
    payload = {
        "task": {
            "documents": [
                {"documentID": "FIRST", "items": [{"name": "first", "quantity": 1}]},
                {"documentID": "SECOND", "items": [{"name": "second", "quantity": 1}]},
            ]
        }
    }

    with Session(engine) as db:
        add_workspace(db)
        add_record(db, payload=payload)
        db.commit()
        response = ai_route.approve_ai_rule(
            approval_request(document_sequence=2, validate_only=True),
            db,
            "test-secret",
        )

    assert response["status"] == "valid"
    assert [document["documentID"] for document in seen[0]["task"]["documents"]] == ["SECOND"]


@pytest.mark.parametrize(
    "synthesis",
    [
        {"status": "rule_replay_failed", "rule": None, "replay_report": []},
        {"status": "compiler_capability_missing", "rule": None, "replay_report": []},
        compiled_result(f"sha256:{'9' * 64}"),
    ],
)
def test_failed_synthesis_keeps_previous_pack_unchanged(monkeypatch, synthesis: dict) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    monkeypatch.setattr(
        ai_route,
        "synthesize_rule_with_service",
        lambda **_kwargs: synthesis,
    )

    with Session(engine) as db:
        add_workspace(db)
        add_record(db)
        pack = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-old",
            profile=candidate_profile(FINGERPRINT, product_path="old_name"),
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        original = (deepcopy(pack.payload), pack.status, pack.is_enabled)

        with pytest.raises(HTTPException) as exc:
            ai_route.approve_ai_rule(
                approval_request(session_id="session-new"),
                db,
                "test-secret",
            )
        assert exc.value.status_code == 422
        db.expire_all()
        persisted = db.get(RecognitionRulePack, pack.id)
        assert persisted is not None
        assert (persisted.payload, persisted.status, persisted.is_enabled) == original


def test_parser_outage_keeps_previous_pack_unchanged(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)

    def unavailable(**_kwargs):
        raise RuntimeError("parser offline")

    monkeypatch.setattr(ai_route, "synthesize_rule_with_service", unavailable)

    with Session(engine) as db:
        add_workspace(db)
        add_record(db)
        pack = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-old",
            profile=candidate_profile(FINGERPRINT, product_path="old_name"),
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        original = deepcopy(pack.payload)
        with pytest.raises(HTTPException) as exc:
            ai_route.approve_ai_rule(
                approval_request(session_id="session-new"),
                db,
                "test-secret",
            )
        assert exc.value.status_code == 502
        db.expire_all()
        assert db.get(RecognitionRulePack, pack.id).payload == original


def test_same_fingerprint_history_is_loaded_as_gold_from_original_record(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    calls: list[dict] = []

    def synthesize(**kwargs) -> dict:
        calls.append(kwargs)
        return compiled_result()

    monkeypatch.setattr(ai_route, "synthesize_rule_with_service", synthesize)

    with Session(engine) as db:
        add_workspace(db)
        add_record(db, record_id=100, product="new shoe")
        add_record(
            db,
            record_id=101,
            payload={
                "receiver": "old secret",
                "items": [{"name": "old shoe", "quantity": 1}],
            },
        )
        save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-old",
            profile=candidate_profile(FINGERPRINT, product_path="old_name"),
            validate=lambda payload: {"status": "valid", "errors": []},
            learning_record={
                "session_id": "session-old",
                "task_id": 61,
                "raw_record_id": 101,
                "document_sequence": 1,
                "source_component": "test",
                "confirmed_rows": [{**SHOE_ROW, "product": "old shoe"}],
            },
        )
        db.commit()
        response = ai_route.approve_ai_rule(
            approval_request(
                session_id="session-new",
                rows=[{**SHOE_ROW, "product": "new shoe"}],
            ),
            db,
            "test-secret",
        )

    assert response["status"] == "approved"
    assert calls[0]["gold_samples"] == [
        {
            "raw_payload": {
                "receiver": "old secret",
                "items": [{"name": "old shoe", "quantity": 1}],
            },
            "source_component": "test",
            "rows": [{**SHOE_ROW, "product": "old shoe"}],
        }
    ]


@pytest.mark.parametrize("corruption", ["deleted", "missing_sequence", "invalid_rows"])
def test_invalid_historical_gold_blocks_synthesis(monkeypatch, corruption: str) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    calls: list[dict] = []
    monkeypatch.setattr(
        ai_route,
        "synthesize_rule_with_service",
        lambda **kwargs: calls.append(kwargs) or compiled_result(),
    )

    with Session(engine) as db:
        add_workspace(db)
        add_record(db, record_id=100, product="new shoe")
        old_record = add_record(db, record_id=101, product="old shoe")
        pack = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-old",
            profile=candidate_profile(FINGERPRINT),
            validate=lambda payload: {"status": "valid", "errors": []},
            learning_record={
                "session_id": "session-old",
                "task_id": 61,
                "raw_record_id": 101,
                "document_sequence": 1,
                "source_component": "test",
                "confirmed_rows": [{**SHOE_ROW, "product": "old shoe"}],
            },
        )
        db.commit()
        if corruption == "deleted":
            old_record.is_deleted = True
        else:
            payload = deepcopy(pack.payload)
            if corruption == "missing_sequence":
                payload["ai_learning_records"][0].pop("document_sequence")
            else:
                payload["ai_learning_records"][0]["confirmed_rows"] = [{}]
            pack.payload = payload
        db.commit()

        with pytest.raises(HTTPException) as exc:
            ai_route.approve_ai_rule(
                approval_request(session_id="session-new"),
                db,
                "test-secret",
            )
        assert exc.value.status_code == 422
        assert calls == []


def test_invalid_current_rows_do_not_call_synthesis(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    enable_ai(monkeypatch)
    calls: list[dict] = []
    monkeypatch.setattr(
        ai_route,
        "synthesize_rule_with_service",
        lambda **kwargs: calls.append(kwargs) or compiled_result(),
    )

    with Session(engine) as db:
        add_workspace(db)
        add_record(db)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            ai_route.approve_ai_rule(
                approval_request(rows=[{**SHOE_ROW, "product": ""}]),
                db,
                "test-secret",
            )
        assert exc.value.status_code == 422
        assert calls == []


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
        with pytest.raises(HTTPException) as exc:
            order_row_reader.parse_raw_records_to_order_rows(
                db,
                workspace_id=1,
                task_id=61,
                records=[record],
            )
        assert exc.value.status_code == 422
    assert calls == []
