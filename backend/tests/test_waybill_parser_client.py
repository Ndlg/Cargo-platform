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
