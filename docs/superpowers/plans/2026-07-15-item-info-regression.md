# ITEM_INFO Regression Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore `ITEM_INFO` parsing on the production `raw_records` path without changing any other service or data contract.

**Architecture:** Keep structured rule-pack parsing first. Extend only the existing raw content text-key fallback, then prove the real `/api/v1/parse/batch` entry handles a four-document record plus a one-document record without blank originals or count drift.

**Tech Stack:** Python 3.12, FastAPI TestClient, pytest, Docker parser service.

## Global Constraints

- Do not modify frontend, backend adapter, collector, database, production rule pack, or business data.
- Do not change the parser HTTP contract.
- Do not deploy until isolated tests and task45 comparison pass.
- Production replacement is parser-only and must retain the previous image for rollback.

---

### Task 1: Lock the production raw-record regression

**Files:**
- Modify: `backend/tests/test_waybill_parser_service.py`
- Modify: `services/waybill-parser/service_app/order_row_engine.py:1232-1245`

**Interfaces:**
- Consumes: `POST /api/v1/parse/batch` with `raw_records[].payload.task.documents[].contents[].data.ITEM_INFO`.
- Produces: one parent and one non-review order row per document, with non-empty `product`, `quantity`, and `original_text`.

- [ ] **Step 1: Write the failing test**

Add a test that posts two raw records containing four plus one documents:

```python
def test_waybill_parser_service_raw_records_parse_item_info_documents() -> None:
    item_texts = [
        "2026赤足跑步鞋 5.0黑白蓝;42 【1件】",
        "2026赤足跑步鞋 5.0黑白紫;36 【1件】",
        "2026赤足跑步鞋 5.0灰橙;37.5 【1件】",
        "2026赤足跑步鞋 5.0黑白紫;36.5 【1件】",
        "2026超轻减震跑步鞋 4.0二代黑白;36.5 【1件】",
    ]
    def documents(values: list[str]) -> list[dict]:
        return [
            {
                "documentID": f"DOC-{index}",
                "contents": [
                    {"encryptedData": "AES:carrier-data"},
                    {"data": {"ITEM_INFO": value, "ITEM_TOTAL_COUNT": "1"}},
                ],
            }
            for index, value in enumerate(values, start=1)
        ]

    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/batch",
            json={
                "task_id": 45,
                "rule_pack": structured_rule_pack_payload(),
                "raw_records": [
                    {
                        "raw_record_id": 1234,
                        "task_id": 45,
                        "source_component": "cainiao-cnprint",
                        "source_index": "2656",
                        "payload": {"task": {"documents": documents(item_texts[:4])}},
                    },
                    {
                        "raw_record_id": 1237,
                        "task_id": 45,
                        "source_component": "cainiao-cnprint",
                        "source_index": "2657",
                        "payload": {"task": {"documents": documents(item_texts[4:])}},
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "parent_waybill_count": 5,
        "child_waybill_count": 5,
        "draft_count": 5,
        "needs_review_count": 0,
        "special_count": 0,
    }
    assert [row["quantity"] for row in body["rows"]] == [1, 1, 1, 1, 1]
    assert [row["original_text"] for row in body["rows"]] == item_texts
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
& .\scripts\backend_test.ps1 backend/tests/test_waybill_parser_service.py::test_waybill_parser_service_raw_records_parse_item_info_documents -q
```

Expected: FAIL because all five rows currently have `review_reason == "no_product_text"` and blank `original_text`.

- [ ] **Step 3: Apply the minimal parser fix**

Keep existing ordering and add the uppercase alias to both existing raw-content key lists:

```python
def content_product_text(data: dict[str, Any]) -> str:
    for key in ("productShortInfo", "productInfo", "SPInfo", "ITEM_NAME", "itemInfo", "ITEM_INFO"):
        value = text_value(data.get(key))
        if value:
            return value
    return ""

def content_product_full_text(data: dict[str, Any]) -> str:
    for key in ("productInfo", "productShortInfo", "SPInfo", "ITEM_NAME", "itemInfo", "ITEM_INFO"):
        value = text_value(data.get(key))
        if value:
            return value
    return ""
```

- [ ] **Step 4: Run focused and related tests and verify GREEN**

Run:

```powershell
& .\scripts\backend_test.ps1 backend/tests/test_waybill_parser_service.py backend/tests/test_order_row_drafts.py -q
```

Expected: 54 tests pass, including existing structured `packageItemDetail` and sample-based `ITEM_INFO` coverage.

- [ ] **Step 5: Commit the isolated fix**

```powershell
git add backend/tests/test_waybill_parser_service.py services/waybill-parser/service_app/order_row_engine.py
git commit -m "fix: parse ITEM_INFO from raw waybill records"
```

### Task 2: Isolated runtime and task45 verification

**Files:**
- No repository files modified.

**Interfaces:**
- Consumes: isolated parser image plus read-only task45 raw payload and current active rule pack.
- Produces: before/after count comparison and rollback-ready image identifiers.

- [ ] **Step 1: Build without replacing production**

Build a uniquely tagged parser image from the isolated worktree. Do not run `docker compose up`.

- [ ] **Step 2: Start an isolated parser container**

Run the new image on an unused local port with no database or production volume mounts.

- [ ] **Step 3: Compare task45 read-only output**

Send task45 raw payload plus the current active rule pack to the isolated parser and assert:

- parent waybills remain `63`;
- order rows remain `66`;
- needs-review rows caused by `ITEM_INFO` change from `5` to `0`;
- every recovered row keeps non-empty `original_text`;
- existing structured and special rows keep their prior values.

- [ ] **Step 4: Stop before production deployment**

Record the production parser image identifier and the isolated image tag. Present the comparison to the user and wait for explicit deployment approval.
