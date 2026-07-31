from app.services import waybill_parser_client


def test_batch_parse_allows_local_ai_inference_time(monkeypatch):
    captured: dict[str, float] = {}

    def fake_post(_path, _payload, *, timeout):
        captured["timeout"] = timeout
        return {"contract_version": waybill_parser_client.ORDER_ROW_DRAFTS_CONTRACT_VERSION}

    monkeypatch.setattr(waybill_parser_client, "post_waybill_parser_service", fake_post)

    waybill_parser_client.parse_order_row_drafts_with_service(
        workspace_id=1,
        task_id=1,
        standard_details=[],
        raw_records=[],
        rule_pack=None,
    )

    assert captured["timeout"] == 180.0


def test_rule_synthesis_is_forwarded_to_parser_without_changing_rows(monkeypatch):
    captured: dict = {}
    row = {
        "product": "鞋",
        "sales_attr1": "",
        "sales_attr2": "",
        "quantity": 1,
        "remark": "",
    }

    def fake_post(path, payload, *, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"status": "compiled", "rule": {}}

    monkeypatch.setattr(waybill_parser_client, "post_waybill_parser_service", fake_post)

    waybill_parser_client.synthesize_rule_with_service(
        raw_payload={"items": []},
        source_component="test",
        corrected_rows=[row, row],
        gold_samples=[],
        negative_samples=[],
        selected_fields=["item_name", "item_quantity"],
        expected_evidence_sha256="e" * 64,
    )

    assert captured == {
        "path": "/api/v1/rules/synthesize",
        "payload": {
            "raw_payload": {"items": []},
            "source_component": "test",
            "corrected_rows": [row, row],
            "gold_samples": [],
            "negative_samples": [],
            "selected_fields": ["item_name", "item_quantity"],
            "expected_evidence_sha256": "e" * 64,
        },
        "timeout": 60.0,
    }
