import json
import os
from pathlib import Path


TEST_DB = Path(__file__).resolve().parent / "waybill_reading_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["SECRET_KEY"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CaptureTask, RawCaptureRecord, StandardDetail  # noqa: E402
from app.services.waybill_reading import should_expose_raw_field, split_selectable_segments  # noqa: E402


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "waybill_reading"


def load_waybill_reading_fixtures() -> list[dict[str, object]]:
    return json.loads((FIXTURE_DIR / "realistic_payloads.json").read_text(encoding="utf-8"))


def test_selectable_segment_split_handles_trailing_quantity_markers() -> None:
    assert split_selectable_segments("商品描述*1") == ["商品描述", "*1"]
    assert split_selectable_segments("商品描述 * 1") == ["商品描述", "*1"]
    assert split_selectable_segments("商品描述×1") == ["商品描述", "×1"]
    assert split_selectable_segments("商品描述x1") == ["商品描述", "x1"]
    assert split_selectable_segments("商品描述X1") == ["商品描述", "X1"]
    assert split_selectable_segments("5.0二代灰色，38.5") == ["5.0二代灰色", "38.5"]
    assert split_selectable_segments(
        "【现货新款4.0鞋子跑步鞋公路四代男鞋女鞋网面透气系带跑道5.0】4.0灰蓝 42.5 1 件"
    ) == ["【现货新款4.0鞋子跑步鞋公路四代男鞋女鞋网面透气系带跑道5.0】4.0灰蓝 42.5 1 件"]
    assert split_selectable_segments(
        "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代 4.0黑白灰;42 【1件】"
    ) == [
        "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代 4.0黑白灰",
        "42 【1件】",
    ]
    assert split_selectable_segments(
        "夏季赤足4.0轻便透气休闲运动跑步鞋男女通用健身体考鞋赤足5.0 4.0二代黑白/42.5 1件"
    ) == [
        "夏季赤足4.0轻便透气休闲运动跑步鞋男女通用健身体考鞋赤足5.0 4.0二代黑白",
        "42.5 1件",
    ]
    assert split_selectable_segments("颜色分类:黑白/TheRoger Advantage;鞋码:42") == [
        "颜色分类:黑白/TheRoger Advantage",
        "鞋码:42",
    ]
    assert split_selectable_segments("颜色分类: 白橙/Cloudstratus3;鞋码:43, *1") == [
        "颜色分类: 白橙/Cloudstratus3",
        "鞋码:43",
        "*1",
    ]
    assert split_selectable_segments("微信--阿迪银灰色 42") == [
        "微信--阿迪银灰色",
        "42",
    ]
    assert split_selectable_segments("微信至尚--HOKA 拖鞋 43") == [
        "微信至尚--HOKA 拖鞋",
        "43",
    ]


def test_capture_task_list_exposes_waybill_count_not_raw_record_count() -> None:
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "DOC-WAYBILL-COUNT-1",
                    "contents": [{"printXML": "<text><![CDATA[商品A 40 1件]]></text>"}],
                },
                {
                    "documentID": "DOC-WAYBILL-COUNT-2",
                    "contents": [{"printXML": "<text><![CDATA[商品B 41 1件]]></text>"}],
                },
            ]
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            task = CaptureTask(
                tenant_id=1,
                workspace_id=1,
                name="waybill count regression",
                status="completed",
            )
            db.add(task)
            db.flush()
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=task.id,
                document_id="DOC-WAYBILL-COUNT-PARENT",
                source_component="cloud-print-client",
                source_index="count-regression",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()
            task_id = task.id

        response = client.get("/api/v1/capture-tasks?limit=2000", headers=headers)

        assert response.status_code == 200
        matching = [item for item in response.json() if item["id"] == task_id]
        assert len(matching) == 1
        assert matching[0]["record_count"] == 2
        assert matching[0]["raw_record_count"] == 1
        assert matching[0]["waybill_count"] == 2
        assert matching[0]["parent_waybill_count"] == 2


def test_capture_task_list_returns_latest_tasks_first() -> None:
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            older = CaptureTask(
                tenant_id=1,
                workspace_id=1,
                name="older capture task",
                status="completed",
            )
            newer = CaptureTask(
                tenant_id=1,
                workspace_id=1,
                name="newer capture task",
                status="completed",
            )
            db.add_all([older, newer])
            db.commit()
            older_id = older.id
            newer_id = newer.id

        response = client.get("/api/v1/capture-tasks?limit=2", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert [item["id"] for item in body[:2]] == [newer_id, older_id]


def test_waybill_reading_keeps_multi_item_text_as_selectable_blocks_without_structuring() -> None:
    multi_item_line = (
        "【登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑潮湿越野鞋跑步鞋】低帮黑白 42 1件；"
        "【登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑潮湿越野鞋跑步鞋】低帮深卡其 39 1件；"
        "【登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑潮湿越野鞋跑步鞋】低帮浅绿 36 1件"
    )
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "DOC-MULTI-ITEM",
                    "contents": [{"printXML": f"<text><![CDATA[3件]]></text><text><![CDATA[{multi_item_line}]]></text>"}],
                }
            ]
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8100,
                document_id="DOC-MULTI-ITEM",
                source_component="cloud-print-client",
                source_index="24",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            raw_record_id = record.id

        response = client.get(
            f"/api/v1/waybill-reading/samples?raw_record_id={raw_record_id}",
            headers=headers,
        )

        assert response.status_code == 200
        blocks = response.json()["samples"][0]["text_blocks"]
        assert not [block for block in blocks if block["block_kind"] == "multi_item_group"]
        assert any(block["text"] == multi_item_line and block["block_kind"] == "original" for block in blocks)
        assert any("低帮黑白 42 1件" in block["text"] for block in blocks if block["block_kind"] == "derived_child")


def test_raw_field_filter_hides_technical_fields_and_keeps_business_fields() -> None:
    assert should_expose_raw_field("raw_capture_record.source_columns.rowid", "7113") is False
    assert should_expose_raw_field("raw_capture_record.source_columns.component_task_id", "BVCn4m6yAAAA") is False
    assert should_expose_raw_field("raw_capture_record.source_columns.task_time", "2026-06-16 14:27:17") is False
    assert should_expose_raw_field("raw_capture_record.source_columns.db_path", r"C:\Program Files\printer\print.db") is False
    assert should_expose_raw_field("task.documents[0].contents[0].data.shopName", "帅盒体育") is False
    assert should_expose_raw_field("task.documents[0].contents[0].data.ITEM_TOTAL_PRICE", "138.00") is False
    assert should_expose_raw_field("task.documents[0].contents[0].data.TOTAL_LINE", "共【1件】") is False
    assert should_expose_raw_field("task.documents[0].contents[0].data.showItemInfo", "True") is False
    assert should_expose_raw_field("task.documents[0].contents[0].data.ITEM_TOTAL_COUNT", "1件") is False
    assert should_expose_raw_field("task.documents[0].contents[0].data.BUYER_NICK", "在**") is False
    assert should_expose_raw_field("task.documents[0].contents[0].data.quantity", "1") is True
    assert should_expose_raw_field("task.documents[0].contents[0].data.productCount", "2") is True
    assert should_expose_raw_field("task.documents[0].contents[0].data.productInfo", "秒45 默认默认") is True
    assert should_expose_raw_field("task.documents[0].contents[0].data.remark", "买家备注 默认") is True


def test_waybill_reading_samples_expose_text_blocks_without_standardizing() -> None:
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "605912844697866752-woda-605912837668213248+++0",
                    "contents": [
                        {
                            "printXML": "<text><![CDATA[Sample Shoe，Black，42*1]]></text>"
                            "<text><![CDATA[Buyer note: ship today]]></text>"
                        }
                    ],
                }
            ]
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8101,
                document_id="DOC-WODA-1",
                source_component="cainiao-cnprint",
                source_index="1",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            raw_record_id = record.id
            standard_detail_count = len(db.scalars(select(StandardDetail)).all())

        response = client.get(
            f"/api/v1/waybill-reading/samples?raw_record_id={raw_record_id}",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["contract_version"] == "waybill_reading_sample_v1"
        assert body["batch"]["bulk_supported"] is True
        assert body["batch"]["record_count"] == 1
        assert body["batch"]["sample_count"] == 1
        assert body["batch"]["total_sample_count"] == 1
        assert body["batch"]["total_sample_count_exact"] is True
        assert body["batch"]["total_sample_count_note"] is None
        assert body["batch"]["offset"] == 0
        assert body["batch"]["ordered_by"] == ["raw_capture_records.id", "document_sequence", "text_blocks.order"]
        assert body["batch"]["scope"] == "single_record"
        assert body["batch"]["suggestion_groups"] == []
        assert body["batch"]["suggestion_policy"] == "suggestion_only_empty_by_default"
        assert body["output_contract"]["consumer"] == "order_row_mapping"
        assert body["output_contract"]["text_block_role"] == "selectable_text_only"
        assert body["output_contract"]["sample_status_fields"] == ["parse_status", "warnings"]
        assert body["output_contract"]["diagnostics"] == "records_without_readable_text_are_reported_without_creating_field_rules"
        assert body["output_contract"]["original_blocks_preserved"] is True
        assert body["output_contract"]["derived_child_blocks"] == "auxiliary_selectable_candidates_only"
        assert body["output_contract"]["child_blocks_replace_parent"] is False
        assert body["output_contract"]["hidden_raw_fields"] == "filtered_metadata_not_selectable_text_blocks"
        assert body["output_contract"]["bulk_consumer_rule"] == "bulk_mapping_must_use_user_confirmed_type_or_scope_before_apply"
        assert body["output_contract"]["automatic_grouping"] is False
        assert body["output_contract"]["automatic_template_detection"] is False
        assert body["output_contract"]["automatic_field_mapping"] is False
        assert "technical_mapping_targets" not in body["output_contract"]
        assert body["output_contract"]["text_block_fields"] == [
            "block_id",
            "text",
            "source",
            "block_kind",
            "line_index",
            "order",
            "raw_record_id",
            "trace",
        ]

        samples = body["samples"]
        assert len(samples) == 1
        sample = samples[0]
        assert sample["raw_record_id"] == raw_record_id
        assert sample["parse_status"] == "readable"
        assert sample["warnings"] == []
        assert sample["record_order"] == 0
        assert sample["sample_order"] == 0
        assert sample["document_sequence"] == 1
        assert sample["sample_text"] == "Sample Shoe，Black，42*1\nBuyer note: ship today"

        blocks = sample["text_blocks"]
        assert [block["text"] for block in blocks] == [
            "Sample Shoe，Black，42*1",
            "Sample Shoe",
            "Black",
            "42",
            "*1",
            "Buyer note: ship today",
        ]
        assert all(block["raw_record_id"] == raw_record_id for block in blocks)
        assert blocks[0]["block_id"] == f"raw-{raw_record_id}-sample-1-block-1"
        assert blocks[0]["source"] == "printed_text"
        assert blocks[0]["block_kind"] == "original"
        assert blocks[0]["line_index"] == 0
        assert blocks[0]["order"] == 0
        assert blocks[0]["trace"] == {
            "selector_key": "printed_text:task.documents[0].contents[0].printXML.cdata[0]:text-0:line-0:segment-original",
            "sample_id": f"raw-{raw_record_id}-sample-1",
            "raw_record_id": raw_record_id,
            "task_id": 8101,
            "document_id": "605912844697866752-woda-605912837668213248+++0",
            "document_sequence": 1,
            "source_component": "cainiao-cnprint",
            "source_index": "1",
            "source": "printed_text",
            "source_path": "task.documents[0].contents[0].printXML.cdata[0]",
            "source_text_order": 0,
            "source_line_index": 0,
            "line_index": 0,
            "segment_index": "original",
            "order": 0,
        }
        assert blocks[4]["text"] == "*1"
        assert blocks[4]["block_kind"] == "derived_child"
        assert blocks[4]["parent_block_id"] == blocks[0]["block_id"]
        assert blocks[4]["parent_text"] == "Sample Shoe，Black，42*1"
        assert blocks[4]["split_reason"] == "safe_delimiter_and_trailing_marker"
        assert blocks[-1]["line_index"] == 1

        with SessionLocal() as db:
            assert len(db.scalars(select(StandardDetail)).all()) == standard_detail_count


def test_waybill_reading_samples_endpoint_declares_response_schema() -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    response_schema = schema["paths"]["/api/v1/waybill-reading/samples"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/WaybillReadingSamplesResponse"}
    schemas = schema["components"]["schemas"]
    assert "WaybillReadingSampleResponse" in schemas
    assert "WaybillTextBlockResponse" in schemas
    assert "WaybillReadingDiagnosticResponse" in schemas


def test_waybill_reading_reports_empty_records_with_diagnostics() -> None:
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8103,
                document_id="DOC-EMPTY-1",
                source_component="cainiao-cnprint",
                source_index="empty",
                payload_format="json",
                raw_payload=json.dumps({"cmd": "print", "task": {"documents": []}}, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            raw_record_id = record.id
            standard_detail_count = len(db.scalars(select(StandardDetail)).all())

        response = client.get(
            f"/api/v1/waybill-reading/samples?raw_record_id={raw_record_id}",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["samples"] == []
        assert body["batch"]["sample_count"] == 0
        assert body["diagnostics"] == [
            {
                "raw_record_id": raw_record_id,
                "task_id": 8103,
                "document_id": "DOC-EMPTY-1",
                "source_component": "cainiao-cnprint",
                "source_index": "empty",
                "payload_format": "json",
                "parse_status": "empty",
                "empty_reason": "payload_has_no_task_documents_or_business_raw_fields",
                "warnings": ["payload_has_no_task_documents_or_business_raw_fields"],
                "record_order": 0,
            }
        ]

        with SessionLocal() as db:
            assert len(db.scalars(select(StandardDetail)).all()) == standard_detail_count


def test_waybill_reading_replays_realistic_payload_fixtures() -> None:
    fixtures = load_waybill_reading_fixtures()
    assert fixtures

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        for index, fixture in enumerate(fixtures, start=1):
            with SessionLocal() as db:
                record = RawCaptureRecord(
                    tenant_id=1,
                    workspace_id=1,
                    task_id=9000 + index,
                    document_id=str(fixture["document_id"]),
                    source_component=str(fixture["source_component"]),
                    source_index=str(fixture["source_index"]),
                    payload_format="json",
                    raw_payload=json.dumps(fixture["raw_payload"], ensure_ascii=False),
                    source_columns=fixture.get("source_columns") if isinstance(fixture.get("source_columns"), dict) else {},
                    status="pending",
                )
                db.add(record)
                db.commit()
                db.refresh(record)
                raw_record_id = record.id

            response = client.get(
                f"/api/v1/waybill-reading/samples?raw_record_id={raw_record_id}",
                headers=headers,
            )

            assert response.status_code == 200, fixture["case_id"]
            body = response.json()
            assert body["diagnostics"] == []
            assert body["samples"], fixture["case_id"]
            sample = body["samples"][0]
            assert sample["parse_status"] == "readable"
            assert sample["raw_record_id"] == raw_record_id
            blocks = sample["text_blocks"]
            block_texts = [block["text"] for block in blocks]

            for expected_text in fixture["expected_present_texts"]:
                assert expected_text in block_texts, (fixture["case_id"], expected_text)
            for hidden_text in fixture["expected_hidden_texts"]:
                assert hidden_text not in block_texts, (fixture["case_id"], hidden_text)

            if fixture["case_id"] == "plain_printxml_text_node_with_attributes":
                plain_parent = next(block for block in blocks if block["text"] == "Plain Shoe，Green，41*1")
                assert plain_parent["source"] == "printed_text"
                assert plain_parent["source_path"] == "task.documents[0].contents[0].printXML.text[0]"
                assert plain_parent["trace"]["selector_key"] == (
                    "printed_text:task.documents[0].contents[0].printXML.text[0]:"
                    "text-0:line-0:segment-original"
                )

            original_blocks = [block for block in blocks if block["block_kind"] == "original"]
            assert original_blocks
            assert all(block["trace"]["selector_key"] for block in blocks)
            assert all(block["trace"]["raw_record_id"] == raw_record_id for block in blocks)
            assert all("field" not in block for block in blocks)
            assert all("role" not in block for block in blocks)


def test_waybill_reading_task_scope_preserves_record_and_sample_order() -> None:
    def payload_for(document_id: str, text: str) -> str:
        return json.dumps(
            {
                "cmd": "print",
                "task": {
                    "documents": [
                        {
                            "documentID": document_id,
                            "contents": [{"printXML": f"<text><![CDATA[{text}]]></text>"}],
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            first = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8102,
                document_id="DOC-BULK-1",
                source_component="cainiao-cnprint",
                source_index="10",
                payload_format="json",
                raw_payload=payload_for("DOC-BULK-1", "First Shoe，White，40*1"),
                status="pending",
            )
            second = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8102,
                document_id="DOC-BULK-2",
                source_component="cainiao-cnprint",
                source_index="11",
                payload_format="json",
                raw_payload=payload_for("DOC-BULK-2", "Second Shoe，Black，41*2"),
                status="pending",
            )
            db.add_all([first, second])
            db.commit()
            first_id = first.id
            second_id = second.id

        response = client.get("/api/v1/waybill-reading/samples?task_id=8102", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["batch"]["scope"] == "task"
        assert body["batch"]["record_count"] == 2
        assert body["batch"]["sample_count"] == 2
        assert body["batch"]["total_sample_count"] == 2
        assert body["batch"]["total_sample_count_exact"] is True
        assert body["batch"]["total_sample_count_note"] is None
        assert body["batch"]["offset"] == 0
        assert body["batch"]["has_more_records"] is False
        assert body["batch"]["suggestion_groups"] == []

        samples = body["samples"]
        assert [sample["raw_record_id"] for sample in samples] == [first_id, second_id]
        assert [sample["record_order"] for sample in samples] == [0, 1]
        assert [sample["sample_order"] for sample in samples] == [0, 1]
        assert samples[0]["text_blocks"][0]["text"] == "First Shoe，White，40*1"
        assert samples[1]["text_blocks"][0]["text"] == "Second Shoe，Black，41*2"
        assert samples[0]["text_blocks"][0]["order"] == 0
        assert samples[1]["text_blocks"][0]["order"] == 0
        assert samples[0]["text_blocks"][0]["trace"]["selector_key"] == (
            "printed_text:task.documents[0].contents[0].printXML.cdata[0]:"
            "text-0:line-0:segment-original"
        )
        assert samples[1]["text_blocks"][0]["trace"]["raw_record_id"] == second_id
        assert samples[1]["text_blocks"][0]["trace"]["source_index"] == "11"

        first_page = client.get("/api/v1/waybill-reading/samples?task_id=8102&limit=1", headers=headers)
        assert first_page.status_code == 200
        first_page_body = first_page.json()
        assert first_page_body["batch"]["record_count"] == 1
        assert first_page_body["batch"]["loaded_record_count"] == 1
        assert first_page_body["batch"]["total_record_count"] == 2
        assert first_page_body["batch"]["total_sample_count"] == 1
        assert first_page_body["batch"]["total_sample_count_exact"] is False
        assert first_page_body["batch"]["total_sample_count_note"] == (
            "paginated_response_reports_loaded_sample_count_only_to_avoid_full_task_parse"
        )
        assert first_page_body["batch"]["offset"] == 0
        assert first_page_body["batch"]["limit"] == 1
        assert first_page_body["batch"]["has_more_records"] is True
        assert [sample["raw_record_id"] for sample in first_page_body["samples"]] == [first_id]
        assert [sample["record_order"] for sample in first_page_body["samples"]] == [0]

        second_page = client.get("/api/v1/waybill-reading/samples?task_id=8102&limit=1&offset=1", headers=headers)
        assert second_page.status_code == 200
        second_page_body = second_page.json()
        assert second_page_body["batch"]["record_count"] == 1
        assert second_page_body["batch"]["loaded_record_count"] == 1
        assert second_page_body["batch"]["total_record_count"] == 2
        assert second_page_body["batch"]["total_sample_count"] == 1
        assert second_page_body["batch"]["total_sample_count_exact"] is False
        assert second_page_body["batch"]["total_sample_count_note"] == (
            "paginated_response_reports_loaded_sample_count_only_to_avoid_full_task_parse"
        )
        assert second_page_body["batch"]["offset"] == 1
        assert second_page_body["batch"]["limit"] == 1
        assert second_page_body["batch"]["has_more_records"] is False
        assert [sample["raw_record_id"] for sample in second_page_body["samples"]] == [second_id]
        assert [sample["record_order"] for sample in second_page_body["samples"]] == [1]


def test_waybill_reading_keeps_original_blocks_and_adds_child_candidates() -> None:
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "DOC-QTY-1",
                    "contents": [
                        {
                            "data": {
                                "productInfo": "秒45 2025新款网页面女鞋男鞋情侣透气跑步鞋 默认默认",
                                "remark": "默认默认",
                            },
                            "printXML": (
                                "<text><![CDATA[颜色分类: 白橙/Cloudstratus3;鞋码:43, *1]]></text>"
                                "<text><![CDATA[2025新款网页面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1]]></text>"
                            )
                        }
                    ],
                }
            ]
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8303,
                document_id="DOC-QTY-1",
                source_component="cainiao-cnprint",
                source_index="83B",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                source_columns={"original_item": "秒45 原始列值 默认默认"},
                status="pending",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            raw_record_id = record.id

        response = client.get(
            f"/api/v1/waybill-reading/samples?raw_record_id={raw_record_id}",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        blocks = body["samples"][0]["text_blocks"]
        raw_field_texts = [
            block["text"]
            for block in blocks
            if block["source"] == "raw_field" and block["block_kind"] == "original"
        ]
        assert "秒45 原始列值 默认默认" in raw_field_texts
        assert "秒45 2025新款网页面女鞋男鞋情侣透气跑步鞋 默认默认" in raw_field_texts
        assert "默认默认" in raw_field_texts

        original_texts = [
            block["text"]
            for block in blocks
            if block["block_kind"] == "original"
        ]
        assert "颜色分类: 白橙/Cloudstratus3;鞋码:43, *1" in original_texts
        assert "2025新款网页面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1" in original_texts

        printed_parent = next(
            block
            for block in blocks
            if block["text"] == "2025新款网页面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1"
        )
        child_blocks = [
            block for block in blocks
            if block["parent_block_id"] == printed_parent["block_id"]
        ]
        assert [block["text"] for block in child_blocks] == [
            "2025新款网页面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身",
            "*1",
        ]
        assert all(block["block_kind"] == "derived_child" for block in child_blocks)
        assert all(block["parent_text"] == printed_parent["text"] for block in child_blocks)
        assert all(block["split_reason"] == "safe_delimiter_and_trailing_marker" for block in child_blocks)
        assert child_blocks[1]["trace"]["parent_block_id"] == printed_parent["block_id"]
        assert child_blocks[1]["trace"]["parent_text"] == printed_parent["text"]
        assert child_blocks[1]["trace"]["split_reason"] == "safe_delimiter_and_trailing_marker"
        assert child_blocks[1]["trace"]["selector_key"] == (
            "printed_text:task.documents[0].contents[0].printXML.cdata[1]:"
            "text-4:line-0:segment-1"
        )
        assert child_blocks[1]["source_path"] == "task.documents[0].contents[0].printXML.cdata[1]"
        assert child_blocks[1]["trace"]["source_line_index"] == 0
        assert child_blocks[1]["trace"]["source_text_order"] == 4
        assert child_blocks[1]["trace"]["raw_record_id"] == raw_record_id
        assert "field" not in child_blocks[1]
        assert "role" not in child_blocks[1]


def test_waybill_reading_keeps_redundant_product_raw_fields_but_hides_technical_shop_fields() -> None:
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "DOC-REDUNDANT-1",
                    "contents": [
                        {
                            "data": {
                                "allProductInfo": "2026热卖5.0潮鞋跑道透气超轻潮流减震男女鞋跑步鞋网面夏季系带 5.0黑白蓝 40 1 件",
                                "productCount": "1件",
                                "productInfo": "【2026热卖5.0潮鞋跑道透气超轻潮流减震男女鞋跑步鞋网面夏季系带】5.0黑白蓝 40 1 件",
                                "productShortInfo": "【热卖5.0跑鞋透气超轻潮】5.0黑白蓝 40 1 件",
                                "sPSInfo": "3504082608222466【热卖5.0跑鞋透气超轻潮】5.0黑白蓝 40 1 件",
                                "shopName": "帅盒体育",
                            },
                            "printXML": "<text><![CDATA[打印文本仍然保留*1]]></text>",
                        }
                    ],
                }
            ]
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8306,
                document_id="DOC-REDUNDANT-1",
                source_component="cainiao-cnprint",
                source_index="83F",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            raw_record_id = record.id

        response = client.get(
            f"/api/v1/waybill-reading/samples?raw_record_id={raw_record_id}",
            headers=headers,
        )

        assert response.status_code == 200
        blocks = response.json()["samples"][0]["text_blocks"]
        raw_original_blocks = [
            block for block in blocks
            if block["source"] == "raw_field" and block["block_kind"] == "original"
        ]
        raw_source_paths = {block["source_path"] for block in raw_original_blocks}
        raw_texts = [block["text"] for block in raw_original_blocks]

        assert "task.documents[0].contents[0].data.productShortInfo" in raw_source_paths
        assert "task.documents[0].contents[0].data.productCount" in raw_source_paths
        assert "task.documents[0].contents[0].data.allProductInfo" in raw_source_paths
        assert "task.documents[0].contents[0].data.productInfo" in raw_source_paths
        assert "task.documents[0].contents[0].data.sPSInfo" in raw_source_paths
        assert "task.documents[0].contents[0].data.shopName" not in raw_source_paths
        assert "【热卖5.0跑鞋透气超轻潮】5.0黑白蓝 40 1 件" in raw_texts
        assert "1件" in raw_texts
        assert "帅盒体育" not in [block["text"] for block in blocks]
        assert "打印文本仍然保留*1" in [block["text"] for block in blocks]
        hidden_raw_fields = response.json()["samples"][0]["hidden_raw_fields"]
        assert any(field["source_path"] == "task.documents[0].contents[0].data.shopName" for field in hidden_raw_fields)


def test_waybill_reading_keeps_bracketed_product_short_info_without_semantic_splitting() -> None:
    product_short_info = "【现货新款4.0鞋子跑步鞋公路四代男鞋女鞋网面透气系带跑道5.0】 4.0灰蓝 42.5 1件"
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "DOC-BRACKETED-1",
                    "contents": [
                        {
                            "data": {
                                "productShortInfo": product_short_info,
                                "productCount": "1件",
                            },
                        }
                    ],
                }
            ]
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8307,
                document_id="DOC-BRACKETED-1",
                source_component="cainiao-cnprint",
                source_index="83G",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            raw_record_id = record.id

        response = client.get(
            f"/api/v1/waybill-reading/samples?raw_record_id={raw_record_id}",
            headers=headers,
        )

        assert response.status_code == 200
        blocks = response.json()["samples"][0]["text_blocks"]
        parent = next(block for block in blocks if block["text"] == product_short_info)
        child_blocks = [block for block in blocks if block["parent_block_id"] == parent["block_id"]]

        assert child_blocks == []


def test_waybill_reading_filters_internal_total_fields_and_splits_bracketed_quantity() -> None:
    product_line = "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代 4.0黑白灰;42 【1件】"
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "DOC-INTERNAL-TOTALS-1",
                    "contents": [
                        {
                            "data": {
                                "productInfo": product_line,
                                "ITEM_TOTAL_PRICE": "138.00",
                                "TOTAL_LINE": "共【1件】",
                                "showItemInfo": True,
                                "ITEM_TOTAL_COUNT": "1件",
                                "BUYER_NICK": "在**",
                            },
                        }
                    ],
                }
            ]
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8308,
                document_id="DOC-INTERNAL-TOTALS-1",
                source_component="cainiao-cnprint",
                source_index="83H",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            raw_record_id = record.id

        response = client.get(
            f"/api/v1/waybill-reading/samples?raw_record_id={raw_record_id}",
            headers=headers,
        )

        assert response.status_code == 200
        blocks = response.json()["samples"][0]["text_blocks"]
        block_texts = [block["text"] for block in blocks]
        raw_original_paths = {
            block["source_path"]
            for block in blocks
            if block["source"] == "raw_field" and block["block_kind"] == "original"
        }

        assert "138.00" not in block_texts
        assert "共【1件】" not in block_texts
        assert "True" not in block_texts
        assert "在**" not in block_texts
        assert "task.documents[0].contents[0].data.ITEM_TOTAL_PRICE" not in raw_original_paths
        assert "task.documents[0].contents[0].data.TOTAL_LINE" not in raw_original_paths
        assert "task.documents[0].contents[0].data.showItemInfo" not in raw_original_paths
        assert "task.documents[0].contents[0].data.ITEM_TOTAL_COUNT" not in raw_original_paths
        assert "task.documents[0].contents[0].data.BUYER_NICK" not in raw_original_paths

        parent = next(block for block in blocks if block["text"] == product_line)
        child_blocks = [block for block in blocks if block["parent_block_id"] == parent["block_id"]]
        assert [block["text"] for block in child_blocks] == [
            "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代 4.0黑白灰",
            "42 【1件】",
        ]


def test_waybill_reading_filters_technical_raw_fields_but_keeps_business_raw_fields() -> None:
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "DOC-FILTER-1",
                    "contents": [
                        {
                            "data": {
                                "rowid": 7113,
                                "component_task_id": "BVCn4m6yAAAA",
                                "task_time": "2026-06-16 14:27:17",
                                "db_path": r"C:\Program Files\printer\print.db",
                                "quantity": "1",
                                "productInfo": "秒45 黑白 网面跑步鞋 默认默认",
                                "remark": "买家备注 加急",
                            },
                            "printXML": "<text><![CDATA[打印文本仍然保留*1]]></text>",
                        }
                    ],
                }
            ]
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8305,
                document_id="DOC-FILTER-1",
                source_component="cainiao-cnprint",
                source_index="83E",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                source_columns={
                    "rowid": 7113,
                    "component_task_id": "BVCn4m6yBBBB",
                    "task_time": "2026-06-16 14:27:17",
                    "db_path": r"C:\Program Files\printer\print.db",
                    "productInfo": "source_columns 秒45 商品",
                    "seller_remark": "source_columns 卖家备注",
                },
                status="pending",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            raw_record_id = record.id

        response = client.get(
            f"/api/v1/waybill-reading/samples?raw_record_id={raw_record_id}",
            headers=headers,
        )

        assert response.status_code == 200
        blocks = response.json()["samples"][0]["text_blocks"]
        block_texts = [block["text"] for block in blocks]
        assert "7113" not in block_texts
        assert "BVCn4m6yAAAA" not in block_texts
        assert "BVCn4m6yBBBB" not in block_texts
        assert "2026-06-16 14:27:17" not in block_texts
        assert r"C:\Program Files\printer\print.db" not in block_texts
        assert "1" in block_texts
        assert "秒45 黑白 网面跑步鞋 默认默认" in block_texts
        assert "买家备注 加急" in block_texts
        assert "source_columns 秒45 商品" in block_texts
        assert "source_columns 卖家备注" in block_texts
        assert "打印文本仍然保留*1" in block_texts

        business_raw_blocks = [
            block for block in blocks
            if block["source"] == "raw_field" and block["block_kind"] == "original"
        ]
        assert {block["source_path"] for block in business_raw_blocks} >= {
            "task.documents[0].contents[0].data.productInfo",
            "task.documents[0].contents[0].data.quantity",
            "task.documents[0].contents[0].data.remark",
            "raw_capture_record.source_columns.productInfo",
            "raw_capture_record.source_columns.seller_remark",
        }
        hidden_paths = {
            field["source_path"]
            for field in response.json()["samples"][0]["hidden_raw_fields"]
        }
        assert {
            "task.documents[0].contents[0].data.rowid",
            "task.documents[0].contents[0].data.component_task_id",
            "task.documents[0].contents[0].data.task_time",
            "task.documents[0].contents[0].data.db_path",
            "raw_capture_record.source_columns.rowid",
            "raw_capture_record.source_columns.component_task_id",
            "raw_capture_record.source_columns.task_time",
            "raw_capture_record.source_columns.db_path",
        }.issubset(hidden_paths)


def test_waybill_reading_adds_tail_size_child_candidates_without_replacing_parent() -> None:
    mixed_line = "微信--阿迪银灰色 42"
    forwarding_note = "官方转运备注 https://example.com/track?id=abc"
    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "DOC-MIXED-TAIL-1",
                    "contents": [
                        {
                            "data": {
                                "productInfo": mixed_line,
                                "remark": forwarding_note,
                            },
                            "printXML": f"<text><![CDATA[{mixed_line}]]></text>",
                        }
                    ],
                }
            ]
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8310,
                document_id="DOC-MIXED-TAIL-1",
                source_component="cainiao-cnprint",
                source_index="83J",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            raw_record_id = record.id

        response = client.get(
            f"/api/v1/waybill-reading/samples?raw_record_id={raw_record_id}",
            headers=headers,
        )

        assert response.status_code == 200
        sample = response.json()["samples"][0]
        blocks = sample["text_blocks"]
        block_texts = [block["text"] for block in blocks]

        assert mixed_line in block_texts
        assert forwarding_note in block_texts
        assert "微信--阿迪银灰色" in block_texts
        assert "42" in block_texts
        assert sample["warnings"] == [
            "contains_url_candidate_noise",
            "contains_forwarding_note_candidate_noise",
        ]

        parent = next(
            block
            for block in blocks
            if block["text"] == mixed_line and block["source"] == "raw_field" and block["block_kind"] == "original"
        )
        child_blocks = [block for block in blocks if block["parent_block_id"] == parent["block_id"]]
        assert [block["text"] for block in child_blocks] == ["微信--阿迪银灰色", "42"]
        assert all(block["block_kind"] == "derived_child" for block in child_blocks)
        assert all(block["parent_text"] == mixed_line for block in child_blocks)
        assert all(block["split_reason"] == "tail_size_candidate" for block in child_blocks)
        assert child_blocks[1]["trace"]["selector_key"].endswith(":segment-1")


def teardown_module() -> None:
    from app.core.database import engine

    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()
