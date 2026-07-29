# Tenant Fingerprint Configuration Implementation Plan

> **For agentic workers:** Execute inline in this worktree. Keep each task independently testable and preserve checkpoint `c8b445df5502f5b43d96a0f1ec374249db51e8a0`.

**Goal:** Let a tenant administrator configure which fields from each authorized, code-defined waybill fingerprint are displayed and sent to manual AI recognition, without modifying existing recognition rules.

**Architecture:** Named fingerprint definitions and their extraction/normalization logic live only in `services/ai-recognition`. The backend stores tenant-specific field selections and proxies catalog/sample inspection from the AI service. The tenant UI on port 6173 edits only the current tenant configuration. Port 6174 and production port 5173 remain untouched.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, Vue 3, Element Plus, npm, pytest, Docker Compose.

## Global Constraints

- Fingerprint detection, names, candidate fields, and XML-to-text cleanup are code assets.
- Tenant field selections are keyed by `tenant_id`; no `workspace_id` or rule-pack ownership.
- Only fingerprints authorized for a tenant are visible; authorization UI on 6174 is deferred.
- Saving tenant selections must not insert, update, activate, deactivate, or delete `RecognitionRulePack` records.
- Existing SHA-256 format fingerprints remain the recognition-rule keys; named capability codes only control input preparation.
- No changes, restart, or deployment to port 5173.

---

### Task 1: Named fingerprint catalog and inspection

**Files:**
- Modify: `services/ai-recognition/service_app/fingerprint.py`
- Modify: `services/ai-recognition/service_app/main.py`
- Test: `backend/tests/test_ai_recognition_service.py`

**Interfaces:**
- Produces: `fingerprint_catalog() -> list[dict]`
- Produces: `inspect_fingerprint(payload, source_component) -> dict`
- HTTP: `GET /api/v1/fingerprints`
- HTTP: `POST /api/v1/fingerprints/inspect`

- [ ] Write failing tests for the five stable codes, structured field extraction, unknown payload, and `printXML` CDATA plain text.
- [ ] Run the focused test file and confirm failures are caused by missing catalog behavior.
- [ ] Implement the five detectors and minimal field extractors in the existing fingerprint module.
- [ ] Add catalog and inspect endpoints.
- [ ] Run the focused tests until green.

### Task 2: Tenant-scoped persistence and API

**Files:**
- Modify: `backend/app/models/entities.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/core/database.py`
- Create: `backend/app/api/routes/tenant_fingerprint_configs.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_tenant_fingerprint_configs.py`

**Interfaces:**
- Model: `TenantFingerprintConfig(tenant_id, fingerprint_code, is_enabled, selected_fields)`
- HTTP: `GET /tenant-fingerprint-configs`
- HTTP: `PUT /tenant-fingerprint-configs/{fingerprint_code}`

- [ ] Write failing API tests proving tenant isolation, allowed-field validation, disabled-fingerprint rejection, and unchanged rule-pack rows.
- [ ] Run the focused tests and confirm expected failures.
- [ ] Add the model/table and SQLite compatibility handling.
- [ ] Add a small AI-recognition HTTP client for catalog and sample inspection.
- [ ] Implement GET/PUT using the current workspace only to resolve its tenant.
- [ ] Run the focused tests until green.

### Task 3: Tenant configuration page

**Files:**
- Create: `frontend/src/views/workbench/TenantFingerprintSettingsView.vue`
- Modify: `frontend/src/router/clientAdminRoutes.ts`
- Modify: `frontend/src/layouts/ClientAdminLayout.vue`
- Modify: `frontend/src/services/api.ts`

**Interfaces:**
- Consumes: `GET/PUT /tenant-fingerprint-configs`
- Produces: route `/admin/fingerprint-settings`

- [ ] Add frontend API types and request functions.
- [ ] Build a tenant-only page listing authorized named fingerprints.
- [ ] Show candidate fields, saved selections, and normalized real sample values; XML is displayed as plain text.
- [ ] Save one fingerprint at a time with clear success/error states.
- [ ] Run frontend typecheck and production build.

### Task 4: Manual AI input uses tenant selection

**Files:**
- Modify: `services/ai-recognition/service_app/contracts.py`
- Modify: `services/ai-recognition/service_app/main.py`
- Modify: `services/waybill-parser/service_app/ai_client.py`
- Modify: `services/waybill-parser/service_app/main.py`
- Modify: `backend/app/services/waybill_parser_client.py`
- Modify: `backend/app/api/routes/order_row_drafts.py`
- Test: `backend/tests/test_ai_recognition_service.py`
- Test: `backend/tests/test_waybill_parser_ai_fallback.py`
- Test: `backend/tests/test_tenant_fingerprint_configs.py`

**Interfaces:**
- `RecognizeRequest.fingerprint_fields: dict[str, list[str]]`
- Manual parser request receives the current tenant field map.

- [ ] Write failing tests proving the model receives only selected normalized fields and existing SHA-256 rule fingerprints remain unchanged.
- [ ] Pass the current tenant field map only through the manual-AI route.
- [ ] Filter/normalize the AI payload inside the AI service after named fingerprint detection.
- [ ] Verify the approval path still updates only the explicitly approved format profile.
- [ ] Run all focused backend and parser tests.

### Task 5: Isolated deployment and acceptance

**Files:**
- Modify only the validation Compose/runtime files already used by the 6173 environment when image tags must change.

- [ ] Run backend focused tests, frontend typecheck/build, and AI/parser contract tests.
- [ ] Build new validation images without replacing 5173 images or containers.
- [ ] Back up the 6173 copied database and verify integrity.
- [ ] Insert authorization rows for all five fingerprints only for the current validation tenant.
- [ ] Deploy only the 6173 validation services that changed.
- [ ] Verify health, restart counts, logs, tenant page behavior, XML plain text, saved selections, manual AI input, and unchanged rule packs.
- [ ] Commit and push the branch, recording a rollback checkpoint for every deployed image.
