# AI Five-Field Rule Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AI approval save only rules that reproduce the administrator-confirmed five business fields and remain compatible with previously confirmed samples of the same fingerprint.

**Architecture:** Keep candidate row editing in the isolated AI console. Propagate the selected document sequence through the parser and AI session, then validate the candidate profile against that exact document and all retained confirmed samples for the same fingerprint before committing the single AI rule pack.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, SQLite session store, pytest, Vue static console.

## Global Constraints

- AI candidate rows contain only `product`, `sales_attr1`, `sales_attr2`, `quantity`, and `remark`.
- `remark` may be empty; `product` must be non-empty and `quantity` must be at least 1.
- Do not deduplicate identical rows.
- Do not modify or deploy the 5173 field system.
- Deploy and browser-test only the 6173 validation environment.

---

### Task 1: Exact five-field candidate contract

**Files:**
- Modify: `services/ai-recognition/service_app/contracts.py`
- Modify: `services/ai-recognition/service_app/model_client.py`
- Test: `backend/tests/test_ai_recognition_service.py`

**Interfaces:**
- Consumes: Ollama JSON candidate response.
- Produces: `AiOrderRow` objects containing exactly the five administrator-visible fields.

- [ ] Add a failing contract test proving model rows with field names embedded in values are rejected and the output schema exposes exactly five fields.
- [ ] Run `scripts/backend_test.ps1 backend/tests/test_ai_recognition_service.py -q` and confirm the new test fails for the missing contract.
- [ ] Remove AI-only `image_match_text` and `source_trace` output fields and keep the existing field-name value guard.
- [ ] Run the targeted tests and confirm they pass.

### Task 2: Selected-document exact replay

**Files:**
- Modify: `backend/app/api/routes/order_row_drafts.py`
- Modify: `services/waybill-parser/service_app/main.py`
- Modify: `services/waybill-parser/service_app/ai_client.py`
- Modify: `services/ai-recognition/service_app/contracts.py`
- Modify: `services/ai-recognition/service_app/store.py`
- Modify: `services/ai-recognition/service_app/main.py`
- Modify: `backend/app/api/routes/ai_recognition.py`
- Test: `backend/tests/test_ai_recognition_service.py`
- Test: `backend/tests/test_ai_rule_pack_approval.py`

**Interfaces:**
- Consumes: `document_sequence` from the manual AI request.
- Produces: approval payload tied to the exact selected document and an exact five-field multiset comparison.

- [ ] Add failing tests proving an extra replay row is rejected and the selected document sequence is preserved through the AI session and approval payload.
- [ ] Run the two targeted test files and confirm the failures describe the old subset/full-record behavior.
- [ ] Propagate and persist `document_sequence`, isolate that document during approval, and replace subset comparison with exact multiset equality.
- [ ] Run the targeted tests and confirm they pass.

### Task 3: Same-fingerprint confirmed-sample regression gate

**Files:**
- Modify: `backend/app/services/recognition_rule_packs.py`
- Modify: `backend/app/api/routes/ai_recognition.py`
- Test: `backend/tests/test_ai_rule_pack_approval.py`

**Interfaces:**
- Consumes: retained `ai_learning_records` with record, document, fingerprint, and confirmed rows.
- Produces: rejection when a replacement profile breaks any available previously confirmed sample of the same fingerprint.

- [ ] Add a failing test proving a later rule cannot replace a same-fingerprint rule when it breaks a prior confirmed sample.
- [ ] Run the targeted approval tests and confirm failure because learning records are currently replaced.
- [ ] Retain confirmed learning records by session and replay every available same-fingerprint record before commit.
- [ ] Run targeted tests and confirm current plus historical sample validation passes.

### Task 4: Full verification and isolated deployment

**Files:**
- Modify only deployment metadata under `ops/validation-stages/20260729-ai/` if required by the existing 6173 deployment pattern.

**Interfaces:**
- Consumes: tested backend, parser, AI service, and UI images.
- Produces: rollback-capable 6173 validation deployment.

- [ ] Run `scripts/backend_test.ps1`.
- [ ] Run `scripts/frontend_typecheck.ps1` and `npm run build` from `frontend`.
- [ ] Inspect the diff for unrelated changes and secrets, then commit atomic save points.
- [ ] Rebuild only 6173 validation images and keep the previous image tags for rollback.
- [ ] Verify health endpoints, one editable five-field session, exact replay rejection, successful rule approval, and unchanged 5173 container IDs.
