# Adaptive Waybill Engine 6173 Trial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start 6173 with zero recognition and AI-learned rules, learn deterministic rules through a constrained 4B-assisted engine, and reproduce the read-only 5173 business workbooks for the same newest three completed capture rounds.

**Architecture:** `waybill-parser` owns evidence extraction, candidate generation, rule synthesis, execution, and replay. `ai-recognition` selects bounded source-span identifiers only. The backend stores versions and orchestrates the services; 5173 is never changed.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, SQLAlchemy, openpyxl, pytest, Docker Compose, Vue 3.

## Global Constraints

- 5173 is read-only: no data writes, restarts, rebuilds, configuration changes, or deployments.
- 6173 is the only trial chain.
- Do not delete or recreate `cargo-platform-data`.
- Zero rules means no recognition format profiles and no AI-learned rules; retain product, SKU, stall, image, matching, and export assets.
- Do not import any 5173 recognition rule pack into 6173.
- Preserve every print event, including same-content prints.
- No OCR, decryption, hidden built-in parser, or arbitrary model-generated rule code.
- The engine emits only `product`, `sales_attr1`, `sales_attr2`, `quantity`, and `remark`, with source traces.
- Multi-product waybills emit multiple rows.
- A candidate rule cannot become active unless replay is exact.
- Every stage creates a rollback tag and timestamped 6173 database backups with SHA-256 and `PRAGMA integrity_check=ok`.

---

### Task 1: Freeze the Oracle and Build a Semantic Comparison Harness

**Files:**
- Modify: `scripts/ai_validation_dataset.py`
- Create: `scripts/compare_business_workbooks.py`
- Create: `backend/tests/test_business_workbook_compare.py`
- Create at runtime only: `ops/validation-stages/20260729-ai/runtime/adaptive-engine-oracle/`

**Interfaces:**
- Consumes: read-only SQLite backups and downloaded XLSX files from 5173.
- Produces: `oracle-manifest.json`, `gold-rows.jsonl`, and a structured workbook-diff JSON.

- [ ] **Step 1: Write failing comparison tests**

```python
def test_compare_preserves_duplicate_multiplicity(tmp_path):
    expected = workbook_with_rows([["A", "红", "", "40", 1, "", "A 红 40"], ["A", "红", "", "40", 1, "", "A 红 40"]])
    actual = workbook_with_rows([["A", "红", "", "40", 1, "", "A 红 40"]])
    report = compare_workbooks(expected, actual)
    assert report["equivalent"] is False
    assert report["differences"][0]["kind"] == "row_count"


def test_compare_ignores_xlsx_zip_metadata(tmp_path):
    expected = workbook_with_rows([["A", "红", "", "40", 1, "", "A 红 40"]])
    actual = workbook_with_rows([["A", "红", "", "40", 1, "", "A 红 40"]])
    assert compare_workbooks(expected, actual)["equivalent"] is True
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```powershell
scripts/backend_test.ps1 backend/tests/test_business_workbook_compare.py -q
```

Expected: collection or import failure because `scripts.compare_business_workbooks` does not exist.

- [ ] **Step 3: Implement the smallest semantic comparator**

Create functions:

```python
BUSINESS_HEADERS = (
    "商品",
    "销售属性1",
    "图片",
    "销售属性2",
    "数量",
    "备注",
    "图片匹配文本",
)


def workbook_manifest(path: Path) -> dict[str, Any]:
    """Return ordered sheet names, ordered cell rows, and image SHA-256 values."""


def compare_workbooks(expected_path: Path, actual_path: Path) -> dict[str, Any]:
    """Return equivalent plus row/column/image differences without comparing ZIP metadata."""
```

Reuse `openpyxl`; do not add another spreadsheet dependency.

- [ ] **Step 4: Extend answer-set export without copying rules**

Add repeatable CLI arguments to `scripts/ai_validation_dataset.py`:

```text
--task-id <id>              repeatable
--gold-output <path>
--exclude-rule-tables
```

The export must contain raw record identifiers, raw payloads, expected parent ordering, and the five gold fields. It must not contain `recognition_rule_packs` payloads or AI session rules.

- [ ] **Step 5: Run the tests**

Run:

```powershell
scripts/backend_test.ps1 backend/tests/test_business_workbook_compare.py backend/tests/test_ai_validation_dataset.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Record the read-only 5173 baseline**

Record:

- exact container IDs, images, start times, and restart counts
- newest three completed capture-round identifiers
- online SQLite backup SHA-256 and integrity result
- downloaded workbook SHA-256 values

Write only under the ignored runtime oracle directory.

- [ ] **Step 7: Commit**

```powershell
git add scripts/ai_validation_dataset.py scripts/compare_business_workbooks.py backend/tests/test_business_workbook_compare.py backend/tests/test_ai_validation_dataset.py
git commit -m "test(engine): add 5173 workbook oracle"
```

---

### Task 2: Make the Parser Produce Canonical Evidence and Candidate Spans

**Files:**
- Create: `services/waybill-parser/service_app/evidence.py`
- Modify: `services/waybill-parser/service_app/main.py`
- Modify: `services/shared/waybill_fingerprint.py`
- Create: `backend/tests/test_waybill_engine_evidence.py`

**Interfaces:**
- Consumes: one raw payload, source component, and selected source fields.
- Produces: `EvidenceBundle` containing structural fingerprint, grammar signature, source spans, candidate groups, and excluded-field counts.

- [ ] **Step 1: Write failing evidence tests**

```python
def test_evidence_retains_paths_but_excludes_pii():
    evidence = build_evidence(
        {
            "task": {
                "documents": [{
                    "contents": [{
                        "data": {
                            "ITEM_INFO": "范33 带木one帆布kw，木村-3M反光，40*1",
                            "receiverAddress": "福建省某地址",
                            "mobile": "13800000000",
                        }
                    }]
                }]
            }
        },
        "cainiao-cnprint",
    )
    assert [span["source_path"] for span in evidence["spans"]] == [
        "task.documents[0].contents[0].data.ITEM_INFO"
    ]
    assert "福建" not in json.dumps(evidence, ensure_ascii=False)
    assert "13800000000" not in json.dumps(evidence)


def test_value_changes_keep_the_same_grammar_signature():
    first = build_evidence(item_info("黄色，43 商品甲*1"), "cainiao-cnprint")
    second = build_evidence(item_info("灰色，39 商品乙*2"), "cainiao-cnprint")
    assert first["grammar_signature"] == second["grammar_signature"]
```

- [ ] **Step 2: Run and verify failure**

```powershell
scripts/backend_test.ps1 backend/tests/test_waybill_engine_evidence.py -q
```

Expected: import failure for `service_app.evidence`.

- [ ] **Step 3: Implement canonical evidence**

Use standard-library dataclasses and existing fingerprint inspection:

```python
@dataclass(frozen=True)
class SourceSpan:
    span_id: str
    source_path: str
    original_text: str
    normalized_text: str
    start: int
    end: int
    token_class: str


def build_evidence(
    payload: dict[str, Any],
    source_component: str,
    selected_fields: list[str] | None = None,
) -> dict[str, Any]:
    ...
```

Generate span IDs from source path and offsets. Normalize full-width punctuation and whitespace while retaining original offsets. Reuse `inspect_fingerprint`; do not duplicate the fingerprint catalogue.

- [ ] **Step 4: Add candidate groups**

Implement only these general candidates:

- structured list item
- line
- delimiter-separated segment
- positive integer quantity
- shoe-size-like numeric segment
- repeated line or array group

Return candidates as stable span-ID lists. Do not assign business fields yet.

- [ ] **Step 5: Expose parser analysis**

Add:

```python
class AnalyzeRequest(BaseModel):
    raw_payload: dict[str, Any]
    source_component: str
    selected_fields: list[str] = Field(default_factory=list)


@app.post("/api/v1/analyze")
def analyze(payload: AnalyzeRequest) -> dict[str, Any]:
    return build_evidence(payload.raw_payload, payload.source_component, payload.selected_fields or None)
```

- [ ] **Step 6: Run evidence and fingerprint tests**

```powershell
scripts/backend_test.ps1 backend/tests/test_waybill_engine_evidence.py backend/tests/test_declarative_waybill_rules.py backend/tests/test_ai_recognition_service.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```powershell
git add services/waybill-parser/service_app/evidence.py services/waybill-parser/service_app/main.py services/shared/waybill_fingerprint.py backend/tests/test_waybill_engine_evidence.py
git commit -m "feat(parser): emit canonical waybill evidence"
```

---

### Task 3: Move Rule Synthesis into the Deterministic Parser

**Files:**
- Create: `services/waybill-parser/service_app/rule_synthesizer.py`
- Modify: `services/waybill-parser/service_app/declarative_rules.py`
- Modify: `services/waybill-parser/service_app/main.py`
- Modify: `services/ai-recognition/service_app/main.py`
- Create: `backend/tests/test_waybill_rule_synthesizer.py`
- Modify: `backend/tests/test_ai_recognition_service.py`

**Interfaces:**
- Consumes: raw payload, source component, corrected five-field rows, prior gold samples, and negative samples.
- Produces: a declarative candidate rule plus an exact replay report, or `compiler_capability_missing`.

- [ ] **Step 1: Write failing synthesis tests**

```python
def test_synthesizer_compiles_source_order_without_model_rule():
    result = synthesize_rule(
        payload=print_xml("灰黑，38 商品名称*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("商品名称", "灰黑", "38", 1)],
        gold_samples=[],
        negative_samples=[],
    )
    assert result["status"] == "compiled"
    assert replay(result["rule"], print_xml("黄色，43 另一个商品*2")) == [
        row("另一个商品", "黄色", "43", 2)
    ]


def test_synthesizer_refuses_rule_that_breaks_prior_gold():
    result = synthesize_rule(
        payload=print_xml("黄色，43 新商品*1"),
        source_component="cainiao-cnprint",
        corrected_rows=[row("新商品", "黄色", "43", 1)],
        gold_samples=[gold_sample("商品 灰色;39【1件】", row("商品", "灰色", "39", 1))],
        negative_samples=[],
    )
    assert result["status"] == "rule_replay_failed"
    assert result["rule"] is None
```

- [ ] **Step 2: Run and verify failure**

```powershell
scripts/backend_test.ps1 backend/tests/test_waybill_rule_synthesizer.py -q
```

Expected: import failure for `service_app.rule_synthesizer`.

- [ ] **Step 3: Implement allowlisted synthesis**

Create:

```python
ALLOWED_OPERATIONS = {
    "select",
    "unwrap_json",
    "unwrap_xml",
    "iterate",
    "normalize",
    "tokenize",
    "split",
    "capture",
    "group_repeat",
    "assign",
    "to_positive_int",
    "validate",
    "emit",
}


def synthesize_rule(
    *,
    payload: dict[str, Any],
    source_component: str,
    corrected_rows: list[dict[str, Any]],
    gold_samples: list[dict[str, Any]],
    negative_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    ...
```

Enumerate the smallest existing structured or text program that reproduces the corrected rows. Prefer direct structured paths, then exact delimiter capture, then repeated groups. Return `compiler_capability_missing` instead of adding a one-off heuristic.

- [ ] **Step 4: Make replay exact**

Compare ordered row multisets across all five fields. Reject:

- extra rows
- missing rows
- duplicate-count changes
- empty product
- non-positive quantity
- field-label prefixes in values
- a negative sample that produces rows

- [ ] **Step 5: Add one parser endpoint**

```python
class RuleSynthesisRequest(BaseModel):
    raw_payload: dict[str, Any]
    source_component: str
    corrected_rows: list[dict[str, Any]]
    gold_samples: list[dict[str, Any]] = Field(default_factory=list)
    negative_samples: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/api/v1/rules/synthesize")
def synthesize(payload: RuleSynthesisRequest) -> dict[str, Any]:
    return synthesize_rule(...)
```

- [ ] **Step 6: Remove rule compilation from AI service**

Delete `compile_corrected_structured_rule`, `compile_source_order_text_rule`, and `compile_corrected_text_rule` from `services/ai-recognition/service_app/main.py`. Keep candidate rows and feedback storage only.

Update tests to assert:

```python
assert "candidate_rule" not in model_request_schema
assert not hasattr(ai_main, "compile_source_order_text_rule")
```

- [ ] **Step 7: Run parser and AI tests**

```powershell
scripts/backend_test.ps1 backend/tests/test_waybill_rule_synthesizer.py backend/tests/test_declarative_waybill_rules.py backend/tests/test_ai_recognition_service.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```powershell
git add services/waybill-parser/service_app/rule_synthesizer.py services/waybill-parser/service_app/declarative_rules.py services/waybill-parser/service_app/main.py services/ai-recognition/service_app/main.py backend/tests/test_waybill_rule_synthesizer.py backend/tests/test_ai_recognition_service.py
git commit -m "refactor(engine): move rule synthesis into parser"
```

---

### Task 4: Constrain 4B to Span Selection

**Files:**
- Modify: `services/ai-recognition/service_app/contracts.py`
- Modify: `services/ai-recognition/service_app/model_client.py`
- Modify: `services/ai-recognition/service_app/main.py`
- Modify: `services/ai-recognition/service_app/sanitizer.py`
- Modify: `backend/tests/test_ai_recognition_service.py`
- Modify: `backend/tests/test_ai_recognition_e2e_contract.py`

**Interfaces:**
- Consumes: parser evidence with stable span IDs and candidate groups.
- Produces: row groupings whose five fields contain source span IDs, never parsing programs.

- [ ] **Step 1: Write failing schema tests**

```python
def test_model_schema_exposes_only_span_selection():
    schema = ollama_json_schema(evidence_bundle())
    encoded = json.dumps(schema)
    assert "candidate_rule" not in encoded
    assert "steps" not in encoded
    assert "source_path" not in encoded
    assert "product_span_ids" in encoded
    assert "quantity_span_id" in encoded


def test_unknown_span_id_is_rejected():
    result = validate_selection(
        {"rows": [{"product_span_ids": ["not-present"], "quantity_span_id": "q1"}]},
        evidence_bundle(),
    )
    assert result["status"] == "candidate_invalid"
```

- [ ] **Step 2: Run and verify failure**

```powershell
scripts/backend_test.ps1 backend/tests/test_ai_recognition_service.py backend/tests/test_ai_recognition_e2e_contract.py -q
```

Expected: schema assertions fail because the current model contract still contains `candidate_rule`.

- [ ] **Step 3: Replace the model output contract**

Use:

```python
class SpanSelectionRow(BaseModel):
    product_span_ids: list[str] = Field(min_length=1)
    sales_attr1_span_ids: list[str] = Field(default_factory=list)
    sales_attr2_span_ids: list[str] = Field(default_factory=list)
    quantity_span_id: str
    remark_span_ids: list[str] = Field(default_factory=list)
```

The model receives span labels and short normalized values only. It does not receive addresses, identifiers, complete raw JSON, parser operations, or rule schemas.

- [ ] **Step 4: Resolve selections deterministically**

Map selected IDs back to source spans, join spans in source order, and validate:

- every ID exists
- a span cannot populate conflicting fields in one row
- product is non-empty
- quantity resolves to a positive integer
- the number of rows does not exceed candidate repeat groups

- [ ] **Step 5: Keep AI optional**

When model invocation fails, return `ai_unavailable` or `candidate_invalid`; do not call an embedded parser and do not build a rule.

- [ ] **Step 6: Run AI tests**

```powershell
scripts/backend_test.ps1 backend/tests/test_ai_recognition_service.py backend/tests/test_ai_recognition_e2e_contract.py backend/tests/test_waybill_parser_ai_fallback.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```powershell
git add services/ai-recognition/service_app/contracts.py services/ai-recognition/service_app/model_client.py services/ai-recognition/service_app/main.py services/ai-recognition/service_app/sanitizer.py backend/tests/test_ai_recognition_service.py backend/tests/test_ai_recognition_e2e_contract.py
git commit -m "refactor(ai): limit model to span selection"
```

---

### Task 5: Orchestrate Learning Without Putting Parsing Back in the Backend

**Files:**
- Modify: `backend/app/services/waybill_parser_client.py`
- Modify: `backend/app/services/ai_recognition_client.py`
- Modify: `backend/app/api/routes/ai_recognition.py`
- Modify: `backend/tests/test_ai_rule_pack_approval.py`
- Modify: `backend/tests/test_independent_parser_boundary.py`

**Interfaces:**
- Consumes: corrected five-field rows and stored gold samples.
- Produces: parser-synthesized rule versions, exact replay reports, and a rerun of affected capture rounds.

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_approval_asks_parser_to_synthesize_before_pack_update(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_route, "synthesize_rule_with_service", lambda **kwargs: calls.append(kwargs) or compiled_rule())
    response = ai_route.approve_ai_rule(approval_request(), db, "shared-token")
    assert calls[0]["corrected_rows"] == approval_request().candidate_output["parents"][0]["rows"]
    assert response["status"] == "approved"


def test_failed_replay_keeps_previous_pack(monkeypatch):
    previous = active_pack(db)
    monkeypatch.setattr(ai_route, "synthesize_rule_with_service", lambda **kwargs: {"status": "rule_replay_failed", "rule": None})
    with pytest.raises(HTTPException) as exc:
        ai_route.approve_ai_rule(approval_request(), db, "shared-token")
    assert exc.value.status_code == 422
    assert active_pack(db).payload == previous.payload
```

- [ ] **Step 2: Run and verify failure**

```powershell
scripts/backend_test.ps1 backend/tests/test_ai_rule_pack_approval.py -q
```

Expected: failure because `synthesize_rule_with_service` does not exist.

- [ ] **Step 3: Add the parser client operation**

```python
def synthesize_rule_with_service(
    *,
    raw_payload: dict[str, Any],
    source_component: str,
    corrected_rows: list[dict[str, Any]],
    gold_samples: list[dict[str, Any]],
    negative_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    return post_waybill_parser_service("/api/v1/rules/synthesize", {...}, timeout=60.0)
```

- [ ] **Step 4: Make approval transactional**

Approval order:

1. validate corrected rows
2. load prior gold samples for the applicability group
3. ask parser to synthesize and replay
4. reject on any non-`compiled` status
5. create a new immutable rule-pack revision
6. activate the new revision
7. store the new gold sample and replay report
8. commit once
9. rerun affected capture rounds

Do not partially update the active pack before replay succeeds.

- [ ] **Step 5: Enforce the module boundary**

Extend `test_independent_parser_boundary.py` to reject imports of:

- `services.ai-recognition.service_app`
- `services.waybill-parser.service_app`
- `rule_synthesizer`
- `declarative_rules`

from `backend/app`.

- [ ] **Step 6: Run backend tests**

```powershell
scripts/backend_test.ps1 backend/tests/test_ai_rule_pack_approval.py backend/tests/test_independent_parser_boundary.py backend/tests/test_waybill_parser_ai_fallback.py -q
scripts/backend_test.ps1 -q
```

Expected: targeted tests and full backend suite pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/services/waybill_parser_client.py backend/app/services/ai_recognition_client.py backend/app/api/routes/ai_recognition.py backend/tests/test_ai_rule_pack_approval.py backend/tests/test_independent_parser_boundary.py
git commit -m "feat(engine): validate learned rules through parser"
```

---

### Task 6: Execute the Zero-Rule 6173 Business-Closure Trial

**Files:**
- Modify: `ops/validation-stages/20260729-ai/README.md`
- Create: `ops/release-candidates/20260730-adaptive-engine-trial/README.md`
- Create: `ops/release-candidates/20260730-adaptive-engine-trial/manifest.json`
- Runtime only: `ops/validation-stages/20260729-ai/runtime/adaptive-engine-trial-<timestamp>/`

**Interfaces:**
- Consumes: final engine images, 5173 oracle artifacts, and the isolated 6173 data copy.
- Produces: a zero-rule learning log, holdout report, three semantic workbook comparisons, coverage report, rollback evidence, and final release verdict.

- [ ] **Step 1: Freeze stage zero**

Before any 6173 data write:

- record current 5173 identities and restart counts
- create 6173 platform and AI SQLite online backups
- verify both backups with `PRAGMA integrity_check`
- record SHA-256 values
- tag source as `validation-6173-adaptive-engine-pre-trial-20260730`

- [ ] **Step 2: Build exact source images**

Build versioned images for:

- validation parser
- validation AI service
- validation backend

Do not rebuild the UI unless its API contract changed.

Run:

```powershell
docker compose -f docker-compose.yml -f docker-compose.release.yml -f docker-compose.ai.yml config -q
```

Expected: exit code 0.

- [ ] **Step 3: Deploy only 6173 services**

Recreate only the changed `cargo-platform-validation-*` services. Verify:

- UI 6173 HTTP 200
- backend 18001 HTTP 200
- parser 18010 `/health` HTTP 200
- AI 18111 `/health` HTTP 200
- restart counts are 0
- 5173 identities are unchanged

- [ ] **Step 4: Reset recognition state only**

Against the backed-up 6173 validation database:

- deactivate and remove recognition format rule-pack records
- clear AI learned sessions and gold learning samples
- preserve raw capture records
- preserve products, SKUs, images, stalls, matching rules, and export definitions

Record table counts before and after. Assert recognition-rule counts are zero before the first parse.

- [ ] **Step 5: Load the oracle inputs**

Copy the newest three completed 5173 capture rounds' raw records into 6173 using the existing data-copy mechanism. Preserve record order and duplicate multiplicity. Do not connect the 6173 collector to live business machines.

- [ ] **Step 6: Split learning and holdout records**

Group by structural fingerprint plus grammar signature. For each previously unknown group:

- choose the first record as the learning sample
- keep all remaining records as holdout
- if one sample cannot disambiguate multiple rows, add only the next sample needed

Write the split manifest before learning starts. The engine may not read holdout gold rows during synthesis.

- [ ] **Step 7: Learn through the engine**

For each learning sample:

1. run parser analysis
2. run constrained 4B span selection
3. replace the five candidate values with oracle values when correction is required
4. call parser synthesis
5. require exact replay
6. activate the resulting rule version

If synthesis returns `compiler_capability_missing`, stop the trial for that group, add one general parser primitive with a failing test, redeploy only the parser, and retry. Do not add a payload-value or platform-name special case.

- [ ] **Step 8: Run blind holdout recognition**

Process every holdout record without supplying oracle rows. Record:

- model call count
- recognized rows
- exceptions by explicit reason
- source-trace completeness
- rule version used

Known-rule holdout processing must not increase model call count.

- [ ] **Step 9: Close the full business chain**

For all three rounds:

- run product, SKU, image, and stall matching
- generate final supplier workbooks
- run `compare_business_workbooks.py` against the 5173 oracle
- verify parent coverage
- verify duplicate multiplicity

The release gate is three reports with `"equivalent": true`.

- [ ] **Step 10: Test AI fail-open**

Stop only `cargo-platform-validation-ai-recognition`:

- rerun a known holdout record and require identical rows
- run an unknown synthetic record and require `ai_unavailable`

Restart AI and verify health. Do not touch 5173.

- [ ] **Step 11: Run final verification**

```powershell
scripts/backend_test.ps1 -q
Push-Location frontend
npm run build:all
npm audit --omit=dev --audit-level=high
Pop-Location
git diff --check
```

Verify 6173 health, zero restart counts, no error-pattern logs, final database integrity, and unchanged 5173 identities.

- [ ] **Step 12: Record release evidence**

`manifest.json` must contain:

- source commit and tags
- image and container IDs
- database paths, sizes, hashes, and integrity checks
- oracle task identifiers and workbook hashes
- learning/holdout split hashes
- model calls during learning and holdout
- coverage equations
- workbook comparison reports
- 5173 unchanged evidence
- exact rollback resources

- [ ] **Step 13: Commit and tag**

```powershell
git add ops/validation-stages/20260729-ai/README.md ops/release-candidates/20260730-adaptive-engine-trial
git commit -m "docs: record adaptive engine business closure"
git tag -a "v0.3.0-adaptive-engine-rc.1" -m "6173 zero-rule adaptive engine business closure"
```

Do not merge to `main` or deploy to 5173 in this task.

