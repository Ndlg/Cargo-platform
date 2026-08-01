import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.context import CurrentUser
from app.models import (
    Base,
    RawCaptureRecord,
    RecognitionRulePack,
    RecognitionRulePackRevision,
    TenantFingerprintConfig,
    Workspace,
)


def system_admin() -> CurrentUser:
    return CurrentUser(
        id=7,
        username="admin",
        display_name="管理员",
        role_names=("system_admin",),
        tenant_ids=(1,),
        workspace_ids=(1,),
    )


def seeded_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all(
        [
            Workspace(id=1, tenant_id=1, name="test", code="test"),
            TenantFingerprintConfig(
                tenant_id=1,
                fingerprint_code="CN-ITEM-INFO",
                is_enabled=True,
                selected_fields=["item_info"],
            ),
            RawCaptureRecord(
                id=100,
                tenant_id=1,
                workspace_id=1,
                task_id=61,
                document_id="batch",
                source_component="cainiao-cnprint",
                source_index="100",
                payload_format="json",
                raw_payload=json.dumps(
                    {
                        "task": {
                            "documents": [
                                {
                                    "documentID": "FIRST",
                                    "contents": [{"data": {"ITEM_INFO": "商品甲 红色 39 1件"}}],
                                },
                                {
                                    "documentID": "SECOND",
                                    "contents": [{"data": {"ITEM_INFO": "商品乙 蓝色 40 2件"}}],
                                },
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                status="pending",
            ),
        ]
    )
    db.commit()
    return db


def parser_fingerprint(payload: dict) -> dict:
    value = payload["task"]["documents"][0]["contents"][0]["data"]["ITEM_INFO"]
    return {
        "fingerprint_code": "CN-ITEM-INFO",
        "fingerprint_name": "菜鸟商品文本型",
        "fields": [
            {
                "key": "item_info",
                "label": "商品信息",
                "path": "contents[].data.ITEM_INFO",
                "value": value,
            },
            {
                "key": "seller_memo",
                "label": "卖家备注",
                "path": "contents[].data.SELLER_MEMO",
                "value": "不应返回",
            },
        ],
    }


def parser_analysis(payload: dict) -> dict:
    assert payload["task"]["documents"][0]["documentID"] == "SECOND"
    return {
        "fingerprint_code": "CN-ITEM-INFO",
        "structural_fingerprint": f"v2:CN-ITEM-INFO:sha256:{'a' * 64}",
        "grammar_signature": f"grammar-v1:sha256:{'b' * 64}",
        "selected_fields": ["item_info"],
        "evidence_sha256": "e" * 64,
        "spans": [],
    }


def test_prepare_binds_selected_document_and_returns_only_tenant_selected_fields(monkeypatch) -> None:
    from app.api.routes import format_learning as route

    db = seeded_db()
    monkeypatch.setattr(
        route,
        "inspect_waybill_fingerprint_with_service",
        lambda *, raw_payload, source_component: parser_fingerprint(raw_payload),
    )
    monkeypatch.setattr(
        route,
        "analyze_waybill_with_service",
        lambda *, raw_payload, source_component, selected_fields: parser_analysis(raw_payload),
    )

    result = route.prepare_format_learning(
        task_id=61,
        request=route.FormatLearningPrepareRequest(
            raw_record_id=100,
            document_sequence=2,
            parent_sequence=2,
        ),
        db=db,
        _current_user=system_admin(),
        workspace_id=1,
    )

    assert result["fingerprint"] == {
        "code": "CN-ITEM-INFO",
        "name": "菜鸟商品文本型",
        "structural_fingerprint": f"v2:CN-ITEM-INFO:sha256:{'a' * 64}",
        "grammar_signature": f"grammar-v1:sha256:{'b' * 64}",
    }
    assert result["selected_fields"] == [
        {
            "key": "item_info",
            "label": "商品信息",
            "path": "contents[].data.ITEM_INFO",
            "values": ["商品乙 蓝色 40 2件"],
        }
    ]
    assert result["evidence_sha256"] == "e" * 64
    db.close()


def test_learn_compiles_admin_rows_into_versioned_active_rule_pack(monkeypatch) -> None:
    from app.api.routes import format_learning as route

    db = seeded_db()
    fingerprint = f"v2:CN-ITEM-INFO:sha256:{'a' * 64}"
    monkeypatch.setattr(
        route,
        "inspect_waybill_fingerprint_with_service",
        lambda *, raw_payload, source_component: parser_fingerprint(raw_payload),
    )
    monkeypatch.setattr(
        route,
        "analyze_waybill_with_service",
        lambda *, raw_payload, source_component, selected_fields: parser_analysis(raw_payload),
    )
    monkeypatch.setattr(
        route,
        "synthesize_rule_with_service",
        lambda **_kwargs: {
            "status": "compiled",
            "rule": {
                "fingerprint": fingerprint,
                "grammar_signature": f"grammar-v1:sha256:{'b' * 64}",
                "strategy": "structured_items_v1",
                "selected_fields": ["item_info"],
                "items_path": "task.documents[].contents[].data",
                "fields": {"product": "ITEM_INFO"},
                "defaults": {"quantity": 1},
            },
            "replay_report": [{"kind": "current", "passed": True}],
        },
    )
    monkeypatch.setattr(
        route,
        "validate_rule_pack_with_service",
        lambda **_kwargs: {"status": "valid", "errors": []},
    )
    monkeypatch.setattr(
        route,
        "rerun_affected_tasks",
        lambda _db, *, workspace_id, task_ids: (
            [{"task_id": value, "status": "completed"} for value in task_ids],
            [],
        ),
    )

    result = route.learn_format(
        task_id=61,
        request=route.FormatLearningRequest(
            raw_record_id=100,
            document_sequence=2,
            parent_sequence=2,
            expected_evidence_sha256="e" * 64,
            rows=[
                {
                    "product": "商品乙",
                    "sales_attr1": "蓝色",
                    "sales_attr2": "40",
                    "quantity": 2,
                    "remark": "",
                }
            ],
        ),
        db=db,
        current_user=system_admin(),
        workspace_id=1,
    )

    pack = db.scalar(select(RecognitionRulePack))
    revision = db.scalar(select(RecognitionRulePackRevision))
    assert result["status"] == "learned"
    assert result["replay_summary"] == {"passed": 1, "total": 1}
    assert pack is not None and pack.code == "adaptive-recognition-main"
    assert pack.name == "自适应识别规则包"
    assert pack.status == "active" and pack.is_enabled is True
    assert revision is not None and revision.payload == pack.payload
    assert pack.payload["learning_records"][0]["confirmed_rows"][0]["quantity"] == 2
    assert "administrator_rows" not in pack.payload["learning_records"][0]
    assert "model_candidate" not in pack.payload["learning_records"][0]
    assert pack.payload["parser_policy"]["format_profiles"][0]["provenance"]["source"] == "confirmed_learning_rule"
    assert pack.payload["parser_policy"]["format_profiles"][0]["provenance"]["learning_record_id"]
    db.close()


def test_relearning_same_waybill_replaces_wrong_sample_instead_of_keeping_conflict(monkeypatch) -> None:
    from app.api.routes import format_learning as route

    db = seeded_db()
    fingerprint = f"v2:CN-ITEM-INFO:sha256:{'a' * 64}"
    monkeypatch.setattr(
        route,
        "inspect_waybill_fingerprint_with_service",
        lambda *, raw_payload, source_component: parser_fingerprint(raw_payload),
    )
    monkeypatch.setattr(
        route,
        "analyze_waybill_with_service",
        lambda *, raw_payload, source_component, selected_fields: parser_analysis(raw_payload),
    )
    monkeypatch.setattr(
        route,
        "synthesize_rule_with_service",
        lambda **_kwargs: {
            "status": "compiled",
            "rule": {
                "fingerprint": fingerprint,
                "grammar_signature": f"grammar-v1:sha256:{'b' * 64}",
                "strategy": "structured_items_v1",
                "selected_fields": ["item_info"],
                "items_path": "task.documents[].contents[].data",
                "fields": {"product": "ITEM_INFO"},
                "defaults": {"quantity": 1},
            },
            "replay_report": [{"kind": "current", "passed": True}],
        },
    )
    monkeypatch.setattr(
        route,
        "validate_rule_pack_with_service",
        lambda **_kwargs: {"status": "valid", "errors": []},
    )
    monkeypatch.setattr(
        route,
        "rerun_affected_tasks",
        lambda _db, *, workspace_id, task_ids: ([], []),
    )

    locator = {
        "raw_record_id": 100,
        "document_sequence": 2,
        "parent_sequence": 2,
    }
    route.learn_format(
        task_id=61,
        request=route.FormatLearningRequest(
            **locator,
            expected_evidence_sha256="e" * 64,
            rows=[
                {
                    "product": "错误商品",
                    "sales_attr1": "蓝色",
                    "sales_attr2": "40",
                    "quantity": 2,
                    "remark": "",
                }
            ],
        ),
        db=db,
        current_user=system_admin(),
        workspace_id=1,
    )
    route.learn_format(
        task_id=61,
        request=route.FormatLearningRequest(
            **locator,
            expected_evidence_sha256="e" * 64,
            rows=[
                {
                    "product": "商品乙",
                    "sales_attr1": "蓝色",
                    "sales_attr2": "40",
                    "quantity": 2,
                    "remark": "",
                }
            ],
        ),
        db=db,
        current_user=system_admin(),
        workspace_id=1,
    )

    pack = db.scalar(select(RecognitionRulePack))
    revisions = db.scalars(select(RecognitionRulePackRevision)).all()
    assert pack is not None
    assert len(pack.payload["learning_records"]) == 1
    assert pack.payload["learning_records"][0]["confirmed_rows"][0]["product"] == "商品乙"
    assert len(revisions) == 2
    db.close()


def test_learn_rejects_stale_prepared_evidence_before_rule_synthesis(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.api.routes import format_learning as route

    db = seeded_db()
    monkeypatch.setattr(
        route,
        "inspect_waybill_fingerprint_with_service",
        lambda *, raw_payload, source_component: parser_fingerprint(raw_payload),
    )
    monkeypatch.setattr(
        route,
        "analyze_waybill_with_service",
        lambda *, raw_payload, source_component, selected_fields: parser_analysis(raw_payload),
    )
    synthesis_called = False

    def fail_if_called(**_kwargs):
        nonlocal synthesis_called
        synthesis_called = True
        raise AssertionError("stale evidence must be rejected before synthesis")

    monkeypatch.setattr(route, "synthesize_rule_with_service", fail_if_called)

    try:
        route.learn_format(
            task_id=61,
            request=route.FormatLearningRequest(
                raw_record_id=100,
                document_sequence=2,
                parent_sequence=2,
                expected_evidence_sha256="f" * 64,
                rows=[
                    {
                        "product": "商品乙",
                        "sales_attr1": "蓝色",
                        "sales_attr2": "40",
                        "quantity": 2,
                        "remark": "",
                    }
                ],
            ),
            db=db,
            current_user=system_admin(),
            workspace_id=1,
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "重新打开" in str(exc.detail)
    else:
        raise AssertionError("stale evidence must be rejected")
    assert synthesis_called is False
    db.close()


def test_queue_binds_diagnostics_and_filters_resolved_samples(monkeypatch) -> None:
    from app.api.routes import format_learning as route

    db = seeded_db()
    monkeypatch.setattr(
        route,
        "task_order_row_drafts_payload",
        lambda *args, **kwargs: {
            "status": "format_profile_missing",
            "diagnostics": [
                {
                    "raw_record_id": 100,
                    "document_sequence": 2,
                    "parent_sequence": 2,
                    "reason": "",
                },
                {
                    "raw_record_id": 100,
                    "document_sequence": 1,
                    "parent_sequence": 1,
                    "reason": "format_profile_missing",
                },
            ],
            "parents": [
                {
                    "parent_label": "面单 2",
                    "raw_record_id": 100,
                    "parent_sequence": 2,
                    "rows": [
                        {
                            "product": "商品乙",
                            "sales_attr1": "蓝色",
                            "sales_attr2": "40",
                            "quantity": 2,
                            "remark": "",
                        }
                    ],
                },
                {
                    "parent_label": "面单 1",
                    "raw_record_id": 100,
                    "parent_sequence": 1,
                    "rows": [],
                },
            ],
        },
    )

    pending = route.list_format_learning_queue(
        task_id=61,
        include_all=False,
        db=db,
        _current_user=system_admin(),
        workspace_id=1,
    )
    all_items = route.list_format_learning_queue(
        task_id=61,
        include_all=True,
        db=db,
        _current_user=system_admin(),
        workspace_id=1,
    )

    assert [(item["document_sequence"], item["reason"]) for item in pending["items"]] == [
        (1, "format_profile_missing")
    ]
    assert len(all_items["items"]) == 2
    assert all_items["items"][1]["rows"][0]["product"] == "商品乙"
    assert all_items["summary"] == {"total_count": 2, "learning_required_count": 1}
    db.close()
