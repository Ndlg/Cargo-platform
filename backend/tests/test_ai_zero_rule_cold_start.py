from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import ai_recognition as ai_route
from app.models import (
    Base,
    RawCaptureRecord,
    RecognitionRulePack,
    TenantFingerprintConfig,
    Workspace,
)
from test_ai_recognition_service import (
    AI_HEADERS,
    AI_TOKEN,
    load_ai_service,
    wait_for_status,
)


ROOT = Path(__file__).resolve().parents[2]
PARSER_ROOT = ROOT / "services" / "waybill-parser"
if str(PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSER_ROOT))

import service_app.main as parser_service  # noqa: E402


SELECTED_FIELDS = [
    "item_name",
    "spec_name",
    "sku_size",
    "item_quantity",
]
ADMINISTRATOR_ROWS = [
    {
        "product": "训练运动鞋",
        "sales_attr1": "蓝灰",
        "sales_attr2": "39",
        "quantity": 2,
        "remark": "",
    }
]
HOLDOUT_ROWS = [
    {
        "product": "复用休闲鞋",
        "sales_attr1": "黑白",
        "sales_attr2": "42",
        "quantity": 3,
        "remark": "",
    }
]


def package_payload(
    product: str,
    spec: str,
    size: str,
    quantity: int,
    *,
    unselected: str,
) -> dict[str, Any]:
    return {
        "contents": [
            {
                "data": {
                    "packageItemDetail": [
                        {
                            "itemName": product,
                            "simpleName": unselected,
                            "specName": spec,
                            "skuSize": size,
                            "itemNum": quantity,
                        }
                    ]
                }
            }
        ]
    }


def parse_request(
    *,
    raw_record_id: int,
    task_id: int,
    payload: dict[str, Any],
    rule_pack: dict[str, Any] | None,
) -> parser_service.BatchParseRequest:
    return parser_service.BatchParseRequest(
        workspace_id=1,
        task_id=task_id,
        raw_records=[
            parser_service.RawRecordParseInput(
                raw_record_id=raw_record_id,
                task_id=task_id,
                document_sequence=1,
                source_component="cainiao-cnprint",
                source_index=str(raw_record_id),
                payload=payload,
                ai_field_selections={"CN-PACKAGE-ITEMS": SELECTED_FIELDS},
            )
        ],
        rule_pack=rule_pack,
        allow_ai=True,
    )


class CandidateGroupOnlyModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def recognize(self, evidence: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(deepcopy(evidence))
        groups = evidence["candidate_groups"]
        item = groups["structured_list_item"][0]
        return {
            "rows": [
                {
                    "product_span_ids": [item[0]],
                    "sales_attr1_span_ids": [],
                    "sales_attr2_span_ids": [
                        groups["shoe_size_like_numeric_segment"][0][0]
                    ],
                    "quantity_span_id": groups[
                        "positive_integer_quantity"
                    ][0][0],
                    "remark_span_ids": [],
                }
            ]
        }


def test_zero_rule_learning_compiles_once_then_reuses_without_ai(
    tmp_path: Path,
    monkeypatch,
) -> None:
    training_payload = package_payload(
        "训练运动鞋",
        "蓝灰",
        "39",
        2,
        unselected="UNSELECTED_SECRET",
    )
    holdout_payload = package_payload(
        "复用休闲鞋",
        "黑白",
        "42",
        3,
        unselected="UNSELECTED_HOLDOUT_SECRET",
    )
    platform_db = tmp_path / "platform.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{platform_db.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Workspace(id=1, tenant_id=1, name="cold-start", code="cold-start"))
        db.add(
            TenantFingerprintConfig(
                tenant_id=1,
                fingerprint_code="CN-PACKAGE-ITEMS",
                is_enabled=True,
                selected_fields=SELECTED_FIELDS,
            )
        )
        for raw_record_id, task_id, payload in (
            (901, 61, training_payload),
            (902, 62, holdout_payload),
        ):
            db.add(
                RawCaptureRecord(
                    id=raw_record_id,
                    tenant_id=1,
                    workspace_id=1,
                    task_id=task_id,
                    document_id=f"doc-{raw_record_id}",
                    source_component="cainiao-cnprint",
                    source_index=str(raw_record_id),
                    payload_format="json",
                    raw_payload=json.dumps(payload, ensure_ascii=False),
                    status="pending",
                )
            )
        db.commit()

    monkeypatch.setenv("AI_RECOGNITION_ENABLED", "true")
    monkeypatch.setenv("AI_RECOGNITION_INTERNAL_TOKEN", AI_TOKEN)
    ai_route.get_settings.cache_clear()
    claims: dict[str, dict[str, Any]] = {}
    synthesis_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ai_route,
        "consume_approval_claim",
        lambda claim: claims.pop(claim, None),
    )

    def synthesize(**kwargs: Any) -> dict[str, Any]:
        synthesis_calls.append(deepcopy(kwargs))
        return parser_service.synthesize_rule(
            parser_service.RuleSynthesisRequest(
                raw_payload=kwargs["raw_payload"],
                source_component=kwargs["source_component"],
                corrected_rows=kwargs["corrected_rows"],
                gold_samples=kwargs["gold_samples"],
                negative_samples=kwargs["negative_samples"],
                selected_fields=kwargs["selected_fields"],
            )
        )

    monkeypatch.setattr(ai_route, "synthesize_rule_with_service", synthesize)
    monkeypatch.setattr(
        ai_route,
        "validate_rule_pack_with_service",
        lambda **kwargs: parser_service.validate_rule_pack(
            parser_service.RulePackRequest(rule_pack=kwargs["rule_pack"])
        ),
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

    def approve_on_platform(
        payload: dict[str, Any],
        token: str,
    ) -> dict[str, Any]:
        with Session(engine) as db:
            return ai_route.approve_ai_rule(
                ai_route.AiRuleApprovalRequest.model_validate(payload),
                db,
                token,
            )

    model = CandidateGroupOnlyModel()
    ai_module = load_ai_service(tmp_path / "ai-import.db")
    ai_app = ai_module.create_app(
        model_client=model,
        db_path=tmp_path / "ai.db",
        internal_token=AI_TOKEN,
        approval_sender=approve_on_platform,
    )

    try:
        with TestClient(ai_app) as ai_client:
            def recognize_through_ai(**kwargs: Any) -> dict[str, Any]:
                response = ai_client.post(
                    "/api/v1/recognize",
                    headers=AI_HEADERS,
                    json={
                        "workspace_id": kwargs["workspace_id"],
                        "task_id": kwargs["task_id"],
                        "raw_record_id": kwargs["raw_record_id"],
                        "document_sequence": kwargs["document_sequence"],
                        "source_component": kwargs["source_component"],
                        "deterministic_failure_reason": kwargs[
                            "deterministic_failure_reason"
                        ],
                        "evidence": kwargs["evidence"],
                    },
                )
                assert response.status_code == 200
                created = response.json()
                return wait_for_status(
                    ai_client,
                    created["session_id"],
                    "ai_rule_pending",
                )

            monkeypatch.setattr(
                parser_service,
                "recognize_with_ai",
                recognize_through_ai,
            )
            first = parser_service.parse_batch(
                parse_request(
                    raw_record_id=901,
                    task_id=61,
                    payload=training_payload,
                    rule_pack=None,
                )
            )

            assert first["status"] == "ai_rule_pending"
            assert first["rows"] == []
            assert first["parents"][0]["rows"] == []
            assert len(model.calls) == 1
            assert set(model.calls[0]) == {
                "fingerprint_code",
                "spans",
                "candidate_groups",
            }
            model_input = json.dumps(model.calls[0], ensure_ascii=False)
            for forbidden in (
                "UNSELECTED_SECRET",
                "simpleName",
                "raw_payload",
                "oracle",
                "gold",
                "holdout",
            ):
                assert forbidden not in model_input

            session_id = first["ai_sessions"][0]["session_id"]
            original = wait_for_status(
                ai_client,
                session_id,
                "ai_rule_pending",
            )
            feedback = ai_client.post(
                f"/api/v1/sessions/{session_id}/feedback",
                headers=AI_HEADERS,
                json={"corrected_rows": ADMINISTRATOR_ROWS},
            )
            assert feedback.status_code == 200
            corrected = wait_for_status(
                ai_client,
                session_id,
                "ai_rule_pending",
            )
            assert len(model.calls) == corrected["model_calls"] == 1
            assert corrected["model_candidate"] == original["model_candidate"]
            assert corrected["administrator_rows"] == ADMINISTRATOR_ROWS
            assert (
                corrected["model_candidate"]["parents"][0]["rows"]
                != corrected["administrator_rows"]
            )
            assert corrected["compiler_result"] is None

            approval_claim = "cold-start-approval-claim-0001"
            claims[approval_claim] = {
                "session_id": session_id,
                "workspace_id": 1,
                "task_id": 61,
                "raw_record_id": 901,
                "document_sequence": 1,
                "fingerprint": corrected["fingerprint"],
                "fingerprint_code": corrected["fingerprint_code"],
                "actor": {
                    "id": 7,
                    "username": "admin",
                    "display_name": "管理员",
                },
                "model_candidate_sha256": ai_route.canonical_sha256(
                    corrected["model_candidate"]
                ),
                "administrator_rows_sha256": ai_route.canonical_sha256(
                    corrected["administrator_rows"]
                ),
            }
            approved_response = ai_client.post(
                f"/api/v1/sessions/{session_id}/approve",
                headers=AI_HEADERS,
                json={"approval_claim": approval_claim},
            )
            assert approved_response.status_code == 200
            approved = approved_response.json()

            assert approved["status"] == "approved"
            assert approved["model_candidate"] == original["model_candidate"]
            assert approved["administrator_rows"] == ADMINISTRATOR_ROWS
            assert approved["compiler_result"]["status"] == "compiled"
            assert synthesis_calls[0]["gold_samples"] == []
            assert synthesis_calls[0]["negative_samples"] == []
            assert synthesis_calls[0]["selected_fields"] == SELECTED_FIELDS

            with Session(engine) as db:
                pack = db.scalar(select(RecognitionRulePack))
                assert pack is not None
                rule_pack = deepcopy(pack.payload)
            profile = rule_pack["parser_policy"]["format_profiles"][0]
            learning = rule_pack["ai_learning_records"][0]
            assert profile["selected_fields"] == SELECTED_FIELDS
            assert profile["provenance"] == {
                "source": "confirmed_ai_rule",
                "learning_session_id": session_id,
            }
            assert learning["model_candidate"] == original["model_candidate"]
            assert learning["administrator_rows"] == ADMINISTRATOR_ROWS
            assert learning["compiler_result"]["status"] == "compiled"

            monkeypatch.setattr(
                parser_service,
                "recognize_with_ai",
                lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("a learned format must not call AI")
                ),
            )
            second = parser_service.parse_batch(
                parse_request(
                    raw_record_id=902,
                    task_id=62,
                    payload=holdout_payload,
                    rule_pack=rule_pack,
                )
            )

            assert second["status"] == "parsed"
            assert second["ai_sessions"] == []
            assert [
                {
                    field: row[field]
                    for field in (
                        "product",
                        "sales_attr1",
                        "sales_attr2",
                        "quantity",
                        "remark",
                    )
                }
                for row in second["rows"]
            ] == HOLDOUT_ROWS
            trace = second["rows"][0]["source_trace"]["compiled_rule"]
            assert trace["source"] == "confirmed_ai_rule"
            assert trace["learning_session_id"] == session_id
            assert trace["ai_call_count"] == 0
            assert len(model.calls) == 1
    finally:
        ai_route.get_settings.cache_clear()
