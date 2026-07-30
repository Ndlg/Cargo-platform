# Cargo Platform Release Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a rollback-safe `cargo-platform` release candidate in which administrator-corrected five-field AI results generate replayable recognition rules, every collected print is covered by export or exception, and all required runtime images can be built and deployed.

**Architecture:** Keep the current field workflow and module boundaries. The backend stores rules and business assets, the independent parser is the only order-row recognition runtime, and the independent AI service proposes declarative rules that must pass parser replay validation before activation. The release is proven only in the isolated 6173 stack; the 5173 field stack is frozen.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, Vue 3, TypeScript, npm, Docker Compose, GitHub Actions.

## Global Constraints

- Do not stop, rebuild, restart, reconfigure, or write data to the 5173 field stack.
- Do not delete or recreate `cargo-platform-data`.
- Do not deduplicate print events by business content.
- Do not add manual order-row editing, confirmation, exclusion, or approval.
- AI output contains exactly `商品`, `销售属性1`, `销售属性2`, `数量`, and optional `备注`.
- A corrected AI result is not saved as a business-row override; it compiles a declarative rule and must pass replay validation.
- An AI-generated rule must not activate unless it reproduces the administrator-confirmed rows and preserves prior confirmed samples for the same fingerprint.
- Existing activated rules continue working when the local AI service is unavailable.
- Every tested capture round must satisfy `collected prints = normal export coverage + exception coverage`.
- A row without a matched product must not enter the normal supplier workbook.
- No new runtime dependency is allowed.
- Each implementation slice must be testable and independently revertable.

---

### Task 1: Freeze the release baseline and record rollback evidence

**Files:**
- Create: `ops/release-candidates/20260730-v020-rc1/README.md`
- Create: `ops/release-candidates/20260730-v020-rc1/manifest.json`

**Interfaces:**
- Consumes: Git commit `acdb648`, current 6173 container identities, validation data volumes, and SQLite online backups.
- Produces: An immutable pre-closeout tag and a manifest used by Tasks 5 and 6.

- [ ] **Step 1: Record the exact source and runtime baseline**

Run:

```powershell
git status --short --branch
git rev-parse HEAD
docker ps --format '{{.ID}}|{{.Names}}|{{.Image}}|{{.Ports}}|{{.Status}}'
docker volume ls --format '{{.Name}}'
```

Expected: the candidate worktree is clean, `acdb648` remains the pre-closeout source rollback point, 6173 validation containers are distinct from the 5173 containers, and both data volumes still exist.

- [ ] **Step 2: Create a rollback tag**

Run:

```powershell
git tag validation-6173-pre-release-closeout-20260730 acdb648
```

Expected: the tag resolves to `acdb648`.

- [ ] **Step 3: Back up the 6173 platform and AI SQLite databases**

Use SQLite online backup, then run:

```sql
PRAGMA integrity_check;
```

Expected: both backups return `ok`; record absolute paths, byte counts, and SHA-256 values in `manifest.json`.

- [ ] **Step 4: Commit only the baseline manifest**

Run:

```powershell
git add ops/release-candidates/20260730-v020-rc1
git commit -m "docs: record release closeout baseline"
```

### Task 2: Compile administrator corrections into replay-safe text rules

**Files:**
- Modify: `services/ai-recognition/service_app/main.py`
- Test: `backend/tests/test_ai_recognition_service.py`
- Test: `backend/tests/test_declarative_waybill_rules.py`

**Interfaces:**
- Consumes: one sanitized text field and one administrator-confirmed five-field row.
- Produces: `compile_corrected_text_rule(payload, corrected_rows) -> dict[str, Any] | None`, using only delimiters and source paths—never product, color, size, or order values as literals.

- [ ] **Step 1: Add the failing regression for 面单17**

Add a test using:

```python
payload = {
    "task": {
        "documents": [{
            "contents": [{
                "printXML": (
                    "5.0二代灰黑，38 "
                    "2026网面女鞋男鞋情侣透气跑步鞋休闲赤足时尚运动鞋健身一脚蹬*1"
                )
            }]
        }]
    }
}
corrected_rows = [{
    "product": "2026网面女鞋男鞋情侣透气跑步鞋休闲赤足时尚运动鞋健身一脚蹬",
    "sales_attr1": "5.0二代灰黑",
    "sales_attr2": "38",
    "quantity": 1,
    "remark": "",
}]
```

Assert that the compiled rule uses the selected `printXML` path, contains no confirmed business values, and replays to exactly `corrected_rows`.

- [ ] **Step 2: Verify the regression fails for the current restriction**

Run:

```powershell
scripts/backend_test.ps1 backend/tests/test_ai_recognition_service.py -q -k "product_follows_attributes"
```

Expected: FAIL because `compile_corrected_text_rule()` currently requires the product to begin the text and rejects alphanumeric product boundaries.

- [ ] **Step 3: Implement one generic ordered-field compiler**

Replace the product-first restriction with a minimal compiler that:

```python
fields = ("product", "sales_attr1", "sales_attr2", "quantity")
```

1. locates each non-empty confirmed value as a unique, non-overlapping segment;
2. orders segments by their source positions rather than field name;
3. accepts only short non-alphanumeric delimiters between segments;
4. emits left-to-right `split` steps and one `to_positive_int` step;
5. emits `defaults` only for empty optional fields;
6. returns `None` for ambiguous occurrences, dynamic alphanumeric gaps, multi-row text, or non-empty remarks.

Do not add a second compiler class, rule DSL, strategy interface, or dependency.

- [ ] **Step 4: Verify green and preserve rejection cases**

Run:

```powershell
scripts/backend_test.ps1 backend/tests/test_ai_recognition_service.py -q -k "product_follows_attributes or corrected_text_rule"
scripts/backend_test.ps1 backend/tests/test_declarative_waybill_rules.py -q
```

Expected: the new regression passes; unconfirmed dynamic literals remain rejected.

- [ ] **Step 5: Commit the single root-cause fix**

Run:

```powershell
git add services/ai-recognition/service_app/main.py backend/tests/test_ai_recognition_service.py backend/tests/test_declarative_waybill_rules.py
git commit -m "fix(ai): compile corrected text fields in source order"
```

### Task 3: Remove backend parser implementations that cannot run

**Files:**
- Delete: `backend/app/services/douyin_product_info.py`
- Delete: `backend/app/services/woda_fields.py`
- Delete: `backend/app/services/woda_printxml_parser.py`
- Delete: `backend/tests/test_woda_printxml_parser.py`

**Interfaces:**
- Consumes: runtime import graph and full backend test suite.
- Produces: a backend that reaches parsing only through `backend/app/services/waybill_parser_client.py`.

- [ ] **Step 1: Prove the files have no runtime importers**

Run:

```powershell
rg -n "app\.services\.(douyin_product_info|woda_printxml_parser|woda_fields)" backend services
```

Expected: only the obsolete parser test imports these modules.

- [ ] **Step 2: Delete the unreachable backend parser copies**

Delete the four files. Do not move their logic into another backend module.

- [ ] **Step 3: Add an architecture regression**

In the existing parser-boundary test file, assert that no Python file under `backend/app` imports `service_app`, `services/waybill-parser`, or the deleted parser module names.

- [ ] **Step 4: Verify backend behavior remains green**

Run:

```powershell
scripts/backend_test.ps1 backend/tests -q
```

Expected: all tests pass and no production backend parser copy remains.

- [ ] **Step 5: Commit the deletion separately**

Run:

```powershell
git add -A backend/app/services backend/tests
git commit -m "refactor: remove unreachable backend parsers"
```

### Task 4: Complete the publishable runtime and image build contract

**Files:**
- Modify: `.github/workflows/release-images.yml`
- Modify: `docker-compose.release.yml`
- Create: `docker-compose.ai.yml`
- Modify: `deploy.env.example`
- Modify: `README.md`
- Create: `CHANGELOG.md`

**Interfaces:**
- Consumes: version tag `v0.2.0-rc.1`, existing backend/parser/UI/AI Dockerfiles, and Ollama.
- Produces: version-matched backend, tenant UI, admin UI, parser, and AI images plus an opt-in local-model/AI Compose overlay.

- [ ] **Step 1: Add the missing release images**

Extend the existing GitHub Actions matrix with:

```yaml
- image: cargo-platform-waybill-parser
  context: .
  dockerfile: services/waybill-parser/Dockerfile
  build_args: ""
- image: cargo-platform-ai-recognition
  context: .
  dockerfile: services/ai-recognition/Dockerfile
  build_args: ""
```

- [ ] **Step 2: Add the opt-in AI Compose overlay**

Define only:

```yaml
services:
  local-model:
    image: ollama/ollama:latest
  ai-recognition:
    image: ghcr.io/ndlg/cargo-platform-ai-recognition:${CARGO_PLATFORM_VERSION}
  waybill-parser:
    environment:
      AI_RECOGNITION_URL: http://ai-recognition:8011
  backend:
    environment:
      AI_RECOGNITION_URL: http://ai-recognition:8011
      AI_RECOGNITION_ENABLED: "true"
      AI_RECOGNITION_INTERNAL_TOKEN: ${AI_RECOGNITION_INTERNAL_TOKEN:?set AI_RECOGNITION_INTERNAL_TOKEN}
```

Use named model/session volumes. Do not make the AI overlay mandatory for existing-rule parsing.

- [ ] **Step 3: Document version and failure behavior**

Set the example version to `0.2.0-rc.1`, document one-command base deployment and base-plus-AI deployment, and state:

- activated recognition rules continue without AI;
- unfamiliar formats become named exceptions when AI is unavailable;
- 5173 deployment is not part of validation.

- [ ] **Step 4: Add a human release changelog**

Record user-visible additions and fixes under `0.2.0-rc.1`: independent parser, administrator AI rule learning, fingerprint field controls, complete print coverage, exception routing, and duplicate-confirmation prevention.

- [ ] **Step 5: Validate all Compose combinations**

Run:

```powershell
docker compose -f docker-compose.release.yml config
docker compose -f docker-compose.release.yml -f docker-compose.ai.yml --env-file deploy.env.example config
```

Expected: both commands exit 0; no validation service name, 6173 port, or validation data volume appears in the release configuration.

- [ ] **Step 6: Commit publishing changes**

Run:

```powershell
git add .github/workflows/release-images.yml docker-compose.release.yml docker-compose.ai.yml deploy.env.example README.md CHANGELOG.md
git commit -m "build: package the complete recognition runtime"
```

### Task 5: Prove the release in the isolated 6173 stack

**Files:**
- Modify: `ops/release-candidates/20260730-v020-rc1/manifest.json`
- Modify: `ops/release-candidates/20260730-v020-rc1/README.md`

**Interfaces:**
- Consumes: release candidate images built from the exact candidate commit and copied tasks 62, 63, and 64.
- Produces: health, integrity, coverage, browser, image identity, restart, and rollback evidence.

- [ ] **Step 1: Build versioned candidate images**

Build parser, AI, backend, tenant UI, and admin UI images from the same commit and tag each `0.2.0-rc.1`.

- [ ] **Step 2: Validate configuration before switching**

Render the 6173 Compose candidate and assert it contains no `5173` port and no `cargo-platform-data` volume.

- [ ] **Step 3: Switch only 6173 candidate services**

Preserve existing 6173 platform/model/session volumes. Record previous image IDs and exact rollback Compose command before switching.

- [ ] **Step 4: Re-run the real 面单17 correction flow**

Confirm the five fields shown in Task 2. Expected state sequence:

```text
model_running -> ai_rule_pending -> approving -> approved
```

Then parse a second same-format waybill with different product/color/size/quantity values. Expected: the activated rule parses it without another model call.

- [ ] **Step 5: Verify three copied capture rounds**

For tasks 62, 63, and 64, record:

```text
collected prints
normal export coverage
exception coverage
product rows
unmatched product rows
```

Require `collected prints = normal export coverage + exception coverage` for every task and require unmatched products to stay out of normal Excel rows.

- [ ] **Step 6: Verify AI fail-open behavior**

Stop only the 6173 AI container, parse one known fingerprint and one unknown fingerprint, then restart it.

Expected: the known fingerprint parses from the activated rule; the unknown fingerprint becomes `ai_unavailable` and remains visible as an exception.

- [ ] **Step 7: Verify browser behavior**

Using the 6173 UI, inspect collection, AI parsing, fingerprint settings, recognition rule pack, exceptions, and export center. Require:

- no manual order-row edit/confirm/exclude controls;
- confirmation button locks and shows progress;
- AI input fields show exact selected paths and values;
- no console errors;
- navigation responds without duplicate requests.

- [ ] **Step 8: Record database and runtime evidence**

Require both SQLite integrity checks to return `ok`, all candidate containers to have restart count `0`, health endpoints to return `200`, and recent logs to contain no traceback.

### Task 6: Final self-review and release candidate

**Files:**
- Modify: `ops/release-candidates/20260730-v020-rc1/README.md`
- Modify: `ops/release-candidates/20260730-v020-rc1/manifest.json`

**Interfaces:**
- Consumes: all commits and Task 5 evidence.
- Produces: an auditable `v0.2.0-rc.1` tag that can be rolled back by stage.

- [ ] **Step 1: Run full verification**

Run:

```powershell
scripts/backend_test.ps1 backend/tests -q
scripts/frontend_typecheck.ps1
Set-Location frontend
npm ci
npm run build
npm run build:server-admin
```

Expected: all commands exit 0.

- [ ] **Step 2: Review the diff on five axes**

Review tests first, then implementation for correctness, simplicity, architecture, security, and performance. Required checks:

- no confirmed business values embedded in generated rules;
- no hidden backend parser;
- no secret or token committed;
- no validation-only port/volume in release Compose;
- no dependency or lockfile change;
- no unrelated formatting rewrite;
- no new file over 1,000 lines.

- [ ] **Step 3: Run dead-code and complexity checks**

Run:

```powershell
rg -n "app\.services\.(douyin_product_info|woda_printxml_parser|woda_fields)" backend services
rg -n "确认订单行|编辑订单行|排除订单行|批量确认" backend frontend
git diff --check main...HEAD
git status --short
```

Expected: both prohibited searches are empty, diff check exits 0, and the worktree is clean after the evidence commit.

- [ ] **Step 4: Confirm field runtime remained untouched**

Compare the 5173 backend, UI, parser, admin UI, and Redis container IDs against the Task 1 manifest.

Expected: all IDs and start times are unchanged.

- [ ] **Step 5: Create the release candidate tag**

Run:

```powershell
git tag -a v0.2.0-rc.1 -m "Cargo Platform 0.2.0 release candidate 1"
```

Do not push the branch or tag until all required checks pass.
