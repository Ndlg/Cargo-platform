# Single AI Recognition Rule Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every administrator-approved AI format update one editable `AI识别规则包` in 6173 instead of creating visible revision packs.

**Architecture:** Reuse the existing `RecognitionRulePack.payload` JSON and declarative parser contract. Store executable profiles in `parser_policy.format_profiles` and non-executable sanitized learning evidence in a top-level `ai_learning_records` list; update the one stable pack row in place after parser validation and replay.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, Vue 3, TypeScript, Element Plus, existing waybill parser HTTP service.

## Global Constraints

- Never access, modify, restart, rebuild, or deploy 5173.
- Implement and deploy only in the existing 6173 validation worktree and containers.
- Do not add database tables, migrations, dependencies, rule revisions, drafts, or rollback history.
- Keep AI invocation manual and isolated from normal business pages.
- Do not store receiver, phone, address, or other privacy fields in learning evidence.
- A failed validation or replay must leave the current rule pack unchanged.

---

### Task 1: Update one stable AI rule pack in place

**Files:**
- Modify: `backend/app/services/recognition_rule_packs.py`
- Modify: `backend/app/api/routes/ai_recognition.py`
- Test: `backend/tests/test_ai_rule_pack_approval.py`

**Interfaces:**
- Consumes: AI approval `profile`, `session_id`, `task_id`, `raw_record_id`, sanitized sample payload, confirmed business rows.
- Produces: `save_ai_rule_profile(db: Session, *, tenant_id: int | None, workspace_id: int, session_id: str, profile: dict[str, Any], learning_record: dict[str, Any], validate: Callable[[dict[str, Any]], dict[str, Any]]) -> RecognitionRulePack`.

- [ ] **Step 1: Replace the immutable-revision test with a stable-pack test**

```python
def test_ai_approval_updates_one_rule_pack_in_place() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-1",
            profile=candidate_profile(f"sha256:{'1' * 64}"),
            learning_record={"fingerprint": f"sha256:{'1' * 64}"},
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        second = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-2",
            profile=candidate_profile(f"sha256:{'2' * 64}"),
            learning_record={"fingerprint": f"sha256:{'2' * 64}"},
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        packs = db.scalars(select(RecognitionRulePack)).all()
    assert len(packs) == 1
    assert packs[0].code == "ai-recognition-main"
    assert len(packs[0].payload["parser_policy"]["format_profiles"]) == 2
    assert first.id == second.id
```

- [ ] **Step 2: Add same-fingerprint replacement coverage**

```python
def test_ai_approval_replaces_existing_fingerprint() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    fingerprint = f"sha256:{'1' * 64}"
    with Session(engine) as db:
        save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-1",
            profile=candidate_profile(fingerprint, product_path="old"),
            learning_record={"fingerprint": fingerprint},
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        pack = save_ai_rule_profile(
            db,
            tenant_id=1,
            workspace_id=1,
            session_id="session-2",
            profile=candidate_profile(fingerprint, product_path="new"),
            learning_record={"fingerprint": fingerprint},
            validate=lambda payload: {"status": "valid", "errors": []},
        )
        db.commit()
        profiles = pack.payload["parser_policy"]["format_profiles"]
    assert len(profiles) == 1
    assert profiles[0]["fields"]["product"] == "new"
```

- [ ] **Step 3: Run the focused test and confirm it fails**

Run:

```powershell
pwsh.exe -File scripts/backend_test.ps1 backend/tests/test_ai_rule_pack_approval.py -q
```

Expected: the existing implementation creates `ai-cold-start-r0001` and `ai-cold-start-r0002`.

- [ ] **Step 4: Implement the stable upsert**

Use constants:

```python
AI_RULE_PACK_CODE = "ai-recognition-main"
AI_RULE_PACK_NAME = "AI识别规则包"
```

Load only the non-deleted stable pack, replace or append profiles and learning records by fingerprint, validate the completed payload, then mutate the existing row or create it once. Keep `activate_recognition_rule_pack` as the only activation path.

- [ ] **Step 5: Pass sanitized evidence from approval**

Build one bounded learning record:

```python
learning_record = {
    "fingerprint": request.format_fingerprint,
    "session_id": request.session_id,
    "task_id": request.task_id,
    "raw_record_id": request.raw_record_id,
    "source_component": record.source_component,
    "sample_payload": evidence_payload,
    "confirmed_rows": expected_rows,
    "rule_evidence": request.rule_evidence,
}
```

Persist only `evidence_payload`, which is produced by the existing AI `sanitize_payload`: sensitive keys are removed, depth is capped at 12, dictionaries at 200 entries, lists at 100 entries and strings at 4000 characters. Never read the original raw payload into `ai_learning_records`.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
pwsh.exe -File scripts/backend_test.ps1 backend/tests/test_ai_rule_pack_approval.py -q
```

Expected: all tests pass and both approvals return `ai-recognition-main`.

- [ ] **Step 7: Commit the backend increment**

```powershell
git add backend/app/services/recognition_rule_packs.py backend/app/api/routes/ai_recognition.py backend/tests/test_ai_rule_pack_approval.py
git commit -m "feat: keep AI learning in one rule pack"
```

### Task 2: Validate edited packs before saving

**Files:**
- Modify: `backend/app/api/routes/recognition_rule_packs.py`
- Test: `backend/tests/test_order_row_drafts.py`

**Interfaces:**
- Consumes: existing `POST /api/v1/recognition-rule-packs/import`.
- Produces: the same response contract, with parser-service validation enforced before database mutation.

- [ ] **Step 1: Add invalid-edit rollback coverage**

```python
def test_rule_pack_import_rejects_parser_invalid_payload(monkeypatch) -> None:
    from app.api.routes import recognition_rule_packs as rule_pack_route

    monkeypatch.setattr(
        rule_pack_route,
        "validate_rule_pack_with_service",
        lambda **_kwargs: {
            "status": "invalid",
            "errors": ["parser_policy.format_profiles[0].fields.product"],
        },
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-Workspace-Id": "1",
        }
        response = client.post(
            "/api/v1/recognition-rule-packs/import",
            headers=headers,
            json={"payload": ACTIVE_RULE_PACK_PAYLOAD, "activate": True},
        )
        listing = client.get("/api/v1/recognition-rule-packs", headers=headers)
    assert response.status_code == 422
    assert "format_profiles[0].fields.product" in response.json()["detail"]
    assert listing.json()["active_pack"] is None
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```powershell
pwsh.exe -File scripts/backend_test.ps1 backend/tests/test_order_row_drafts.py -k recognition_rule_pack -q
```

Expected: invalid payload is currently accepted after only local normalization.

- [ ] **Step 3: Validate before upsert**

Call `validate_rule_pack_with_service(rule_pack=normalized)`. Return HTTP 422 with the first business-readable validation error when status is not `valid`; return HTTP 502 when the parser service is unavailable. Do not call `upsert_recognition_rule_pack` until validation succeeds.

- [ ] **Step 4: Run the focused tests**

Run:

```powershell
pwsh.exe -File scripts/backend_test.ps1 backend/tests/test_order_row_drafts.py -k recognition_rule_pack -q
```

Expected: import, activation, export and deletion still pass; invalid edits do not persist.

- [ ] **Step 5: Commit the validation increment**

```powershell
git add backend/app/api/routes/recognition_rule_packs.py backend/tests/test_order_row_drafts.py
git commit -m "fix: validate recognition rules before saving"
```

### Task 3: Show and edit learned child rules

**Files:**
- Modify: `frontend/src/services/api.ts`
- Create: `frontend/src/components/recognition/RecognitionProfileEditor.vue`
- Modify: `frontend/src/views/workbench/RecognitionRulePacksView.vue`

**Interfaces:**
- Consumes: existing list/export/import/delete APIs and `payload.parser_policy.format_profiles`.
- Produces: a business-facing child-rule list and editor that saves the full validated payload through the existing import API.

- [ ] **Step 1: Define frontend payload types**

Add `RecognitionFormatProfile`, `RecognitionLearningRecord`, and `RecognitionRulePackPayload` types covering both `structured_items_v1` and `text_pipeline_v1`; keep unknown top-level rule-pack fields with an index signature so import/export stays lossless.

- [ ] **Step 2: Build the focused profile editor**

The component accepts:

```typescript
const props = defineProps<{
  modelValue: RecognitionFormatProfile
  learningRecord?: RecognitionLearningRecord
}>()
const emit = defineEmits<{
  'update:modelValue': [value: RecognitionFormatProfile]
  delete: []
}>()
```

Render common name/description fields, read-only strategy/fingerprint, sanitized sample JSON, confirmed seven business fields, structured paths/defaults, and text-pipeline steps. Use native Element Plus inputs already installed; do not add dependencies.

- [ ] **Step 3: Replace the misleading general-policy dialog**

On `RecognitionRulePacksView.vue`, treat `ai-recognition-main` as the single AI package, show its child-rule count, and open the child-rule list. Preserve unrelated top-level payload fields when saving. If the pack is absent, show “请先在 AI 面单解析中确认一条新格式”; do not create an invalid empty pack.

- [ ] **Step 4: Implement child deletion**

Remove the selected fingerprint from both `format_profiles` and `ai_learning_records`. Refuse to save zero profiles and direct the administrator to the existing whole-pack delete button.

- [ ] **Step 5: Type-check the frontend**

Run:

```powershell
pwsh.exe -File scripts/frontend_typecheck.ps1
```

Expected: no TypeScript errors.

- [ ] **Step 6: Commit the editor increment**

```powershell
git add frontend/src/services/api.ts frontend/src/components/recognition/RecognitionProfileEditor.vue frontend/src/views/workbench/RecognitionRulePacksView.vue
git commit -m "feat: edit learned rules inside the AI pack"
```

### Task 4: Verify and deploy only to 6173

**Files:**
- Modify only if evidence needs recording: `ops/validation-stages/20260729-ai/README.md`

**Interfaces:**
- Consumes: completed backend and frontend increments.
- Produces: verified 6173 containers and rollback-by-commit evidence without any 5173 operation.

- [ ] **Step 1: Run backend regression**

Run:

```powershell
pwsh.exe -File scripts/backend_test.ps1 -q
```

Expected: all backend tests pass.

- [ ] **Step 2: Run frontend type checking**

Run:

```powershell
pwsh.exe -File scripts/frontend_typecheck.ps1
```

Expected: no TypeScript errors.

- [ ] **Step 3: Build new 6173 images**

Use the existing validation compose/build scripts and unique `single-ai-pack-*` tags. Do not run commands against the 5173 compose project or ports.

- [ ] **Step 4: Replace only 6173 services**

Recreate only `cargo-platform-validation-backend` and `cargo-platform-validation-ui`; recreate parser only if its HTTP contract or validation code changed. Confirm:

```text
http://127.0.0.1:6173/admin/recognition-rule-packs
http://127.0.0.1:18000/health
http://127.0.0.1:18010/health
```

- [ ] **Step 5: Perform the business smoke test**

Confirm two different formats through the independent AI page, verify one visible AI pack with two editable child rules, modify one field path and save, then verify the affected waybill uses the change while the other rule still works.

- [ ] **Step 6: Prove the 5173 boundary**

Compare the previously recorded 5173 container IDs, image tags and restart counts with the existing validation record. This is a read-only comparison against the already recorded snapshot; do not query or operate the live stack.

- [ ] **Step 7: Record evidence and commit**

```powershell
git add ops/validation-stages/20260729-ai/README.md
git commit -m "ops: record single AI pack validation"
```
