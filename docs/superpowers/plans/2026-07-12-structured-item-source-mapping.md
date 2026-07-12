# Structured Item Source Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse structured item arrays declared by an active recognition rule pack so 1688 official printing payloads produce correct child order rows without application-specific parser branches.

**Architecture:** Extend the existing rule-pack validation and `draft_rows_from_payload` path. The parser evaluates a deliberately small dot-and-`[]` path contract, maps configured item fields into the existing item parser, and falls back to existing text parsing only when no structured mapping produces rows.

**Tech Stack:** Python 3.12, FastAPI, pytest, JSON rule-pack assets.

## Global Constraints

- Recognition remains inside `services/waybill-parser`.
- The main backend does not duplicate parsing heuristics.
- No new dependency or endpoint is added.
- Existing `order_row_drafts_v1` request and response shapes remain unchanged.
- Existing rule packs without `structured_item_sources` remain valid.
- Structured rows take precedence over text fallback for the same document.
- The parser does not branch on printing application names.

---

### Task 1: Rule-pack validation and structured item parsing

**Files:**
- Modify: `services/waybill-parser/service_app/main.py`
- Modify: `services/waybill-parser/service_app/order_row_engine.py`
- Test: `backend/tests/test_waybill_parser_service.py`

**Interfaces:**
- Consumes: `parser_policy.structured_item_sources[]` with `items_path`, ordered product/spec/quantity/remark field lists.
- Produces: existing `ParentWaybillDraft` and `OrderRowDraft` values through `draft_rows_from_payload(..., parser_policy=...)`.

- [ ] **Step 1: Write failing validation tests**

Add tests proving a valid mapping is accepted and malformed `items_path` or non-list field mappings produce `rule_pack_invalid` with a precise error path.

```python
def structured_rule_pack_payload() -> dict:
    payload = valid_rule_pack_payload()
    payload["parser_policy"]["structured_item_sources"] = [{
        "name": "package-item-detail",
        "items_path": "task.documents[].contents[].data.packageItemDetail[]",
        "product_fields": ["itemName", "simpleName"],
        "spec_fields": ["specName", "specSimpleName", "skuFullName"],
        "quantity_fields": ["itemNum"],
        "remark_fields": ["remark", "buyerRemark", "sellerRemark"],
    }]
    return payload
```

- [ ] **Step 2: Write failing parse test with a real 1688-shaped payload**

Post a raw record containing two `packageItemDetail` objects and assert:

```python
assert body["summary"]["parent_waybill_count"] == 1
assert body["summary"]["child_waybill_count"] == 2
assert [(row["product"], row["sales_attr1"], row["sales_attr2"], row["quantity"]) for row in body["rows"]] == [
    ("秒21 vap2025", "二代全白", "39", 1),
    ("范33 带木one帆布kw", "木村-3M反光", "42.5", 2),
]
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
scripts\backend_test.ps1 backend/tests/test_waybill_parser_service.py -q
```

Expected: the new tests fail because structured mappings are not validated or consumed.

- [ ] **Step 4: Implement minimal validation**

In `rule_pack_validation_errors`, accept an optional list and validate only the contract defined in `spec/11-structured-item-source-mapping.md`. Return errors such as:

```python
parser_policy.structured_item_sources[0].items_path
parser_policy.structured_item_sources[0].product_fields
```

- [ ] **Step 5: Implement minimal path evaluation and item conversion**

Add one path evaluator supporting object keys and key suffix `[]`. Add structured item extraction that:

```python
item_text = " ".join(part for part in (product_text, spec_text) if part)
fields = parse_item_text(
    item_text,
    fallback_quantity_text=quantity_text,
    remark_text=remark_text,
)
```

Pass `payload.rule_pack["parser_policy"]` from `parse_batch` to `draft_rows_from_payload`. If structured rows exist, return them without also running existing content text parsing.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```powershell
scripts\backend_test.ps1 backend/tests/test_waybill_parser_service.py backend/tests/test_order_row_drafts.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit parser increment**

```powershell
git add services/waybill-parser/service_app/main.py services/waybill-parser/service_app/order_row_engine.py backend/tests/test_waybill_parser_service.py
git commit -m "feat: parse rule-pack structured item sources"
```

---

### Task 2: Current shoe rule pack and runtime verification

**Files:**
- Modify: `rule-packs/current-user-shoes.v1.json`
- Test: `backend/tests/test_waybill_parser_service.py`

**Interfaces:**
- Consumes: structured item parsing from Task 1.
- Produces: current user shoe rule pack version `1.1.0` with the `packageItemDetail` mapping.

- [ ] **Step 1: Write failing rule-pack asset test**

Assert the repository asset contains the required mapping and validates through the parser service:

```python
assert pack["pack"]["version"] == "1.1.0"
source = pack["parser_policy"]["structured_item_sources"][0]
assert source["items_path"] == "task.documents[].contents[].data.packageItemDetail[]"
```

- [ ] **Step 2: Run the asset test and verify RED**

Run:

```powershell
scripts\backend_test.ps1 backend/tests/test_waybill_parser_service.py -q
```

Expected: FAIL because the mapping and version are absent.

- [ ] **Step 3: Update the rule-pack asset**

Change the pack version to `1.1.0` and add:

```json
"structured_item_sources": [{
  "name": "package-item-detail",
  "items_path": "task.documents[].contents[].data.packageItemDetail[]",
  "product_fields": ["itemName", "simpleName"],
  "spec_fields": ["specName", "specSimpleName", "skuFullName"],
  "quantity_fields": ["itemNum"],
  "remark_fields": ["remark", "buyerRemark", "sellerRemark"]
}]
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
scripts\backend_test.ps1 backend/tests/test_waybill_parser_service.py backend/tests/test_order_row_drafts.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit rule-pack increment**

```powershell
git add rule-packs/current-user-shoes.v1.json backend/tests/test_waybill_parser_service.py
git commit -m "feat: map structured items in shoe rule pack"
```

- [ ] **Step 6: Deploy only the parser and import the updated pack**

Rebuild `cargo-platform-waybill-parser`, import/activate the updated rule pack through the existing rule-pack API, and leave database volumes untouched.

- [ ] **Step 7: Verify task 42 against current runtime data**

Preview the latest 1688 records and confirm parent count, child count, product, attributes, quantity, no duplicate rows, and source trace. Verify `GET http://127.0.0.1:8010/health` returns HTTP 200.
