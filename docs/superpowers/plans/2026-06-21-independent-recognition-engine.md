# Independent Recognition Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make recognition rule packs and waybill parsing run through a clear independent engine contract instead of hidden platform parser behavior.

**Architecture:** The main platform stores rule packs and raw records, then calls the parser service through HTTP. The parser service owns validation, explanation, preview, and batch parsing using only the request payload and the active rule pack. Product/SKU/image matching and Excel export remain in the main platform and consume parsed order rows only.

**Tech Stack:** Python 3.12, FastAPI parser service under `services/waybill-parser`, backend FastAPI client wrappers, pytest via `scripts/backend_test.ps1`, frontend typecheck via `scripts/frontend_typecheck.ps1`.

---

## File Structure

- Modify: `services/waybill-parser/service_app/main.py`
  - Add explicit service endpoints for rule-pack validation, rule-pack explanation, parse preview, and batch parse.
  - Keep no-rule-pack behavior as `rule_pack_missing`.
- Modify: `backend/tests/test_waybill_parser_service.py`
  - Add contract tests for the new parser service endpoints before implementation.
- Modify: `backend/app/services/waybill_parser_client.py`
  - Add client helpers for validation, explanation, and preview so future platform UI can call the engine without reaching into parser internals.
- Test only if needed: `backend/tests/test_order_row_drafts.py`
  - Keep existing no-rule-pack and parser contract tests green.
- Later extraction target: `services/waybill-parser/service_app/order_row_engine.py`
  - Move parser logic here when decoupling the service from `backend/app/services/order_row_drafts.py`.
- Later cleanup target: `services/waybill-parser/Dockerfile`
  - Stop copying backend parser modules into the service image after `order_row_engine.py` exists.

## Task 1: Add Parser Service Contract Tests

**Files:**
- Modify: `backend/tests/test_waybill_parser_service.py`

- [ ] **Step 1: Write failing tests for validation, explanation, preview, and missing pack**

Add these tests to `backend/tests/test_waybill_parser_service.py`:

```python
def valid_rule_pack_payload() -> dict:
    return {
        "contract_version": "recognition_rule_pack_v1",
        "pack": {"code": "test-shoes", "name": "测试鞋类规则包", "version": "1.0.0"},
        "parser_policy": {"requires_active_rule_pack": True},
    }


def test_waybill_parser_service_validates_rule_pack() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/rule-packs/validate", json={"rule_pack": valid_rule_pack_payload()})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "valid"
    assert body["contract_version"] == "recognition_rule_pack_v1"
    assert body["pack"]["code"] == "test-shoes"
    assert body["errors"] == []


def test_waybill_parser_service_rejects_invalid_rule_pack() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/rule-packs/validate", json={"rule_pack": {"pack": {"code": ""}}})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "invalid"
    assert "contract_version" in body["errors"]
    assert "pack.code" in body["errors"]
    assert "pack.name" in body["errors"]
    assert "pack.version" in body["errors"]


def test_waybill_parser_service_explains_rule_pack_without_business_db() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/rule-packs/explain", json={"rule_pack": valid_rule_pack_payload()})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "valid"
    assert body["pack"]["name"] == "测试鞋类规则包"
    assert "requires active rule pack" in " ".join(body["capabilities"])
    assert body["business_db_access"] is False


def test_waybill_parser_service_preview_is_read_only_and_returns_rows() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/preview",
            json={
                "task_id": 19,
                "rule_pack": valid_rule_pack_payload(),
                "waybill_samples": [
                    {
                        "raw_record_id": 7132,
                        "task_id": 19,
                        "parent_sequence": 1,
                        "source_component": "cainiao-cnprint",
                        "source_index": "7132",
                        "sample_text": "范33 带木one帆布kw，木村-3M反光，42.5*1",
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["preview"] is True
    assert body["summary"]["draft_count"] == 1
    assert body["rows"][0]["product"] == "范33 带木one帆布kw"
    assert body["rows"][0]["sales_attr1"] == "木村-3M反光"
    assert body["rows"][0]["sales_attr2"] == "42.5"


def test_waybill_parser_service_preview_requires_rule_pack() -> None:
    app = load_parser_service_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse/preview",
            json={
                "task_id": 19,
                "waybill_samples": [
                    {
                        "raw_record_id": 7132,
                        "task_id": 19,
                        "parent_sequence": 1,
                        "sample_text": "范33 带木one帆布kw，木村-3M反光，42.5*1",
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rule_pack_missing"
    assert body["rule_pack_required"] is True
    assert body["rows"] == []
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backend_test.ps1 backend\tests\test_waybill_parser_service.py -q
```

Expected: new tests fail because `/api/v1/rule-packs/validate`, `/api/v1/rule-packs/explain`, and `/api/v1/parse/preview` do not exist.

## Task 2: Implement Parser Service Rule-Pack Endpoints

**Files:**
- Modify: `services/waybill-parser/service_app/main.py`

- [ ] **Step 1: Add request models and validation helpers**

Add the following near the existing `BatchParseRequest` model:

```python
class RulePackRequest(BaseModel):
    rule_pack: dict[str, Any] | None = None


def rule_pack_validation_errors(rule_pack: dict[str, Any] | None) -> list[str]:
    if not isinstance(rule_pack, dict):
        return ["rule_pack"]
    errors: list[str] = []
    if rule_pack.get("contract_version") != "recognition_rule_pack_v1":
        errors.append("contract_version")
    pack = rule_pack.get("pack")
    if not isinstance(pack, dict):
        return [*errors, "pack"]
    for field in ("code", "name", "version"):
        if not str(pack.get(field) or "").strip():
            errors.append(f"pack.{field}")
    return errors


def rule_pack_summary(rule_pack: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(rule_pack, dict):
        return None
    pack = rule_pack.get("pack")
    if not isinstance(pack, dict):
        return None
    return {
        "code": str(pack.get("code") or "").strip(),
        "name": str(pack.get("name") or "").strip(),
        "version": str(pack.get("version") or "").strip(),
    }
```

- [ ] **Step 2: Add validation and explanation endpoints**

Add these routes before `parse_batch`:

```python
@app.post("/api/v1/rule-packs/validate")
def validate_rule_pack(payload: RulePackRequest) -> dict[str, Any]:
    errors = rule_pack_validation_errors(payload.rule_pack)
    return {
        "contract_version": "recognition_rule_pack_v1",
        "status": "invalid" if errors else "valid",
        "errors": errors,
        "warnings": [],
        "pack": rule_pack_summary(payload.rule_pack),
    }


@app.post("/api/v1/rule-packs/explain")
def explain_rule_pack(payload: RulePackRequest) -> dict[str, Any]:
    errors = rule_pack_validation_errors(payload.rule_pack)
    parser_policy = payload.rule_pack.get("parser_policy") if isinstance(payload.rule_pack, dict) else {}
    capabilities = [
        "requires active rule pack" if isinstance(parser_policy, dict) and parser_policy.get("requires_active_rule_pack") else "active rule pack optional",
        "special waybill policy" if isinstance(parser_policy, dict) and parser_policy.get("special_text_keywords") else "no special waybill policy",
        "quantity normalization" if isinstance(parser_policy, dict) and parser_policy.get("quantity") else "default quantity behavior",
    ]
    return {
        "contract_version": "recognition_rule_pack_v1",
        "status": "invalid" if errors else "valid",
        "errors": errors,
        "pack": rule_pack_summary(payload.rule_pack),
        "capabilities": capabilities,
        "business_db_access": False,
        "mutates_platform_data": False,
    }
```

- [ ] **Step 3: Add preview endpoint**

Add:

```python
@app.post("/api/v1/parse/preview")
def parse_preview(payload: BatchParseRequest) -> dict[str, Any]:
    result = parse_batch(payload)
    result["preview"] = True
    result["mutates_platform_data"] = False
    return result
```

- [ ] **Step 4: Run parser service tests**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backend_test.ps1 backend\tests\test_waybill_parser_service.py -q
```

Expected: all parser service tests pass.

## Task 3: Add Backend Client Helpers For Rule-Pack Tooling

**Files:**
- Modify: `backend/app/services/waybill_parser_client.py`
- Test: `backend/tests/test_waybill_parser_service.py` remains service-level; backend route integration can be added when UI consumes the helpers.

- [ ] **Step 1: Add generic post helper**

Refactor `parse_order_row_drafts_with_service` to use:

```python
def post_waybill_parser_service(path: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    base_url = waybill_parser_service_url()
    if not base_url:
        raise RuntimeError("WAYBILL_PARSER_URL is not configured.")
    response = httpx.post(f"{base_url}{path}", json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 2: Add explicit client methods**

Add:

```python
def validate_rule_pack_with_service(*, rule_pack: dict[str, Any]) -> dict[str, Any]:
    return post_waybill_parser_service("/api/v1/rule-packs/validate", {"rule_pack": rule_pack}, timeout=10.0)


def explain_rule_pack_with_service(*, rule_pack: dict[str, Any]) -> dict[str, Any]:
    return post_waybill_parser_service("/api/v1/rule-packs/explain", {"rule_pack": rule_pack}, timeout=10.0)


def preview_order_row_drafts_with_service(
    *,
    task_id: int,
    standard_details: list[dict[str, Any]] | None = None,
    raw_records: list[dict[str, Any]] | None = None,
    waybill_samples: list[dict[str, Any]] | None = None,
    rule_pack: dict[str, Any],
) -> dict[str, Any]:
    payload = post_waybill_parser_service(
        "/api/v1/parse/preview",
        {
            "task_id": task_id,
            "standard_details": standard_details or [],
            "raw_records": raw_records or [],
            "waybill_samples": waybill_samples or [],
            "rule_pack": rule_pack,
        },
    )
    if payload.get("contract_version") != ORDER_ROW_DRAFTS_CONTRACT_VERSION:
        raise RuntimeError(
            "Waybill parser contract mismatch: "
            f"{payload.get('contract_version')} != {ORDER_ROW_DRAFTS_CONTRACT_VERSION}"
        )
    return payload
```

- [ ] **Step 3: Keep existing batch parse behavior unchanged**

Update `parse_order_row_drafts_with_service` to call `post_waybill_parser_service("/api/v1/parse/batch", ...)` and keep its contract-version check.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backend_test.ps1 backend\tests\test_waybill_parser_service.py backend\tests\test_order_row_drafts.py -q
```

Expected: all selected tests pass.

## Task 4: Verify Typecheck And Runtime

**Files:**
- No source edits unless tests expose failures.

- [ ] **Step 1: Run frontend typecheck**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\frontend_typecheck.ps1
```

Expected: typecheck passes.

- [ ] **Step 2: Deploy parser service only if service code changed**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy_waybill_parser.ps1
```

Expected: waybill parser container rebuilds without touching `cargo-platform-data`.

- [ ] **Step 3: Smoke-test parser endpoints**

Run:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8010/health
```

Expected: `status` is `ok`.

## Task 5: Extract Parser Logic Into Service-Local Engine

**Files:**
- Create: `services/waybill-parser/service_app/order_row_engine.py`
- Modify: `services/waybill-parser/service_app/main.py`
- Modify: `services/waybill-parser/Dockerfile`
- Test: `backend/tests/test_waybill_parser_service.py`

- [ ] **Step 1: Move parser code under service boundary**

Move the pure parser functions currently imported from `backend/app/services/order_row_drafts.py` into `services/waybill-parser/service_app/order_row_engine.py`. The moved module must expose exactly:

```python
ORDER_ROW_DRAFTS_CONTRACT_VERSION
draft_rows_from_payload
draft_rows_from_standard_detail_values
draft_rows_from_waybill_sample
order_row_draft_summary
```

- [ ] **Step 2: Update service imports**

Change `services/waybill-parser/service_app/main.py` to import from:

```python
from service_app.order_row_engine import (
    ORDER_ROW_DRAFTS_CONTRACT_VERSION,
    draft_rows_from_payload,
    draft_rows_from_standard_detail_values,
    draft_rows_from_waybill_sample,
    order_row_draft_summary,
)
```

- [ ] **Step 3: Remove backend module copies from service image**

Change `services/waybill-parser/Dockerfile` so it no longer copies `backend/app/services/order_row_drafts.py` or `backend/app/services/douyin_product_info.py`.

- [ ] **Step 4: Run parser service tests**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backend_test.ps1 backend\tests\test_waybill_parser_service.py -q
```

Expected: parser service tests pass without backend parser modules copied into the service image.

## Self-Review

- Spec coverage: Tasks 1-4 create the engine contract for validation, explanation, preview, and no-rule-pack behavior from `spec/09-independent-recognition-engine.md`. Task 5 addresses service ownership and decoupling.
- Placeholder scan: no `TBD`, `TODO`, or unspecified behavior remains in this plan.
- Type consistency: service payload uses existing `BatchParseRequest`, `rule_pack`, `standard_details`, `raw_records`, and `waybill_samples` names already used by the current parser client.

