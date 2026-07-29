# Business Shape Fingerprint V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make learned rules reusable by business format, separate incompatible
text layouts, and support field cleanup for structured multi-product waybills.

**Architecture:** A pure shared module calculates legacy or v2 fingerprints
without I/O. AI and parser use that module; rule packs choose the strategy.
Structured rules reuse existing text operations after reading each item.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, Docker Compose.

## Global Constraints

- Modify and deploy only the isolated 6173 validation environment.
- Never modify, restart or deploy 5173.
- Preserve the current database and active rule pack with timestamped backups.
- Unknown v2 formats fail closed as `format_profile_missing`.
- Keep the five-field and exact replay approval contracts unchanged.
- Do not add product-specific or platform-result patches.

---

### Task 1: Shared business-shape fingerprint

**Files:**
- Create: `services/shared/__init__.py`
- Create: `services/shared/waybill_fingerprint.py`
- Test: `backend/tests/test_waybill_fingerprint_v2.py`

**Interfaces:**
- Produces: `fingerprint_catalog()`, `inspect_fingerprint(payload, source_component)`,
  `legacy_structural_fingerprint(payload, source_component)`,
  `business_shape_fingerprint(payload, source_component)`, and
  `fingerprint_for_payload(payload, source_component, strategy)`.

- [ ] **Step 1: Write failing golden-vector tests**

Add tests that assert:

```python
assert business_shape_fingerprint(xml_a, "cainiao-cnprint") == (
    business_shape_fingerprint(xml_a_new_values, "cainiao-cnprint")
)
assert business_shape_fingerprint(xml_a, "cainiao-cnprint") != (
    business_shape_fingerprint(xml_other_layout, "cainiao-cnprint")
)
assert business_shape_fingerprint(package_payload_a, "cainiao-cnprint") == (
    business_shape_fingerprint(package_payload_with_unrelated_keys, "cainiao-cnprint")
)
```

- [ ] **Step 2: Verify RED**

Run:

`scripts/backend_test.ps1 backend/tests/test_waybill_fingerprint_v2.py -q`

Expected: import or assertion failure because the shared module does not exist.

- [ ] **Step 3: Implement the pure module**

Use named catalog detection and return:

```python
f"v2:{code}:sha256:{sha256(encoded).hexdigest()}"
```

Text grammar preserves only layout punctuation and normalized whitespace.
Package identity records maintained field names and scalar types, ignoring
values, item count and unrelated keys.

- [ ] **Step 4: Verify GREEN**

Run:

`scripts/backend_test.ps1 backend/tests/test_waybill_fingerprint_v2.py -q`

Expected: all tests pass.

### Task 2: Parser strategy and structured row steps

**Files:**
- Modify: `services/waybill-parser/service_app/declarative_rules.py`
- Modify: `services/waybill-parser/service_app/main.py`
- Modify: `services/waybill-parser/Dockerfile`
- Test: `backend/tests/test_declarative_waybill_rules.py`
- Test: `backend/tests/test_waybill_parser_service.py`

**Interfaces:**
- Consumes: `fingerprint_for_payload(..., strategy)`.
- Produces: rule-pack validation for
  `parser_policy.fingerprint_strategy` and optional
  `structured_items_v1.steps`.

- [ ] **Step 1: Write failing parser tests**

Cover:

```python
parser_policy["fingerprint_strategy"] = "business_shape_v2"
profile["fingerprint"] = business_shape_fingerprint(payload, source)
profile["steps"] = [{
    "op": "rsplit",
    "source": "sales_attr1",
    "delimiter": " ",
    "targets": ["sales_attr1", "sales_attr2"],
}]
```

Assert one package item yields separated attributes, two items yield two rows,
and an unknown v2 shape returns `format_profile_missing` without legacy
fallback.

- [ ] **Step 2: Verify RED**

Run:

`scripts/backend_test.ps1 backend/tests/test_declarative_waybill_rules.py backend/tests/test_waybill_parser_service.py -q`

Expected: validation rejects the new strategy/steps or parsing does not apply
them.

- [ ] **Step 3: Implement minimal parser changes**

- Allow `fingerprint_strategy` in parser policy.
- Accept legacy and v2 fingerprint strings.
- Calculate the selected fingerprint once per parent payload.
- Validate structured `steps` with the existing text-step validator.
- Execute the existing text-step function after item field mapping.
- Copy `services/shared` into the parser image.

- [ ] **Step 4: Verify GREEN**

Run the same two test files. Expected: all tests pass.

### Task 3: AI uses the same fingerprint contract

**Files:**
- Modify: `services/ai-recognition/service_app/fingerprint.py`
- Modify: `services/ai-recognition/service_app/main.py`
- Modify: `services/ai-recognition/service_app/model_client.py`
- Modify: `services/ai-recognition/Dockerfile`
- Test: `backend/tests/test_ai_recognition_service.py`
- Test: `backend/tests/test_waybill_fingerprint_v2.py`

**Interfaces:**
- Consumes: the shared fingerprint module.
- Produces: new AI sessions whose opaque fingerprint matches parser v2 and
  structured candidate rules with optional `steps`.

- [ ] **Step 1: Write failing AI tests**

Assert:

```python
session["fingerprint"].startswith("v2:CN-PACKAGE-ITEMS:sha256:")
candidate_rule_schema["oneOf"][0]["properties"]["steps"]
```

Also compare AI and parser fingerprints for the same golden vectors.

- [ ] **Step 2: Verify RED**

Run:

`scripts/backend_test.ps1 backend/tests/test_ai_recognition_service.py backend/tests/test_waybill_fingerprint_v2.py -q`

Expected: AI still emits a legacy hash and structured schema has no `steps`.

- [ ] **Step 3: Implement minimal AI changes**

- Delegate catalog/inspection/fingerprint logic to the shared module.
- Calculate v2 after fingerprint inspection and before session reservation.
- Keep sanitization and tenant field selection unchanged.
- Permit structured `steps` in the model output schema and prompt.
- Copy `services/shared` into the AI image.

- [ ] **Step 4: Verify GREEN**

Run the same test files. Expected: all tests pass.

### Task 4: Safe 6173 migration

**Files:**
- Create: `scripts/migrate_validation_rule_pack_v2.py`
- Test: `backend/tests/test_migrate_validation_rule_pack_v2.py`

**Interfaces:**
- Consumes: a source rule-pack JSON plus raw validation records.
- Produces: a candidate v2 JSON file and validation report; it does not write
  the database.

- [ ] **Step 1: Write failing migration tests**

Assert migration is deterministic and idempotent, copies only exactly replayed
groups, keeps conflicts unresolved, and never mutates the input object.

- [ ] **Step 2: Verify RED**

Run:

`scripts/backend_test.ps1 backend/tests/test_migrate_validation_rule_pack_v2.py -q`

Expected: script/module import failure.

- [ ] **Step 3: Implement the offline transformer**

The script writes files only beneath a caller-supplied output directory. It
must require parser preview success before emitting a candidate with
`fingerprint_strategy=business_shape_v2`.

- [ ] **Step 4: Verify GREEN**

Run the migration tests. Expected: all pass.

### Task 5: Full validation and deployment

**Files:**
- Runtime evidence only under
  `ops/validation-stages/20260729-ai/runtime/<timestamp>/`

**Interfaces:**
- Consumes: committed source, candidate v2 pack and 6173 copied data.
- Produces: health, database, replay and rollback evidence.

- [ ] **Step 1: Run all automated checks**

Run:

`scripts/backend_test.ps1 -q`

Expected: no failures.

- [ ] **Step 2: Back up 6173**

Copy both SQLite files, export the active rule pack, record SHA-256 values, and
tag the current parser and AI images. Do not address any 5173 container.

- [ ] **Step 3: Build and recreate only parser and AI**

Build tagged images from the committed source and recreate only:

- `cargo-platform-validation-parser`
- `cargo-platform-validation-ai-recognition`

- [ ] **Step 4: Validate before activating v2**

Confirm health 200, both SQLite integrity checks `ok`, restart counts zero, and
the three 5173 container IDs equal the pre-change IDs.

- [ ] **Step 5: Preview candidate v2 pack**

For tasks 62, 63 and 64 verify:

- parent count equals 164, 118 and 94
- package items total 141 rows
- the three known multi-product parents each contain two rows
- no duplicate child identity exists
- every unresolved parent has a diagnostic

- [ ] **Step 6: Activate only after replay passes**

Import/activate the v2 candidate through the existing rule-pack API or the
same backend service function. If any check fails, leave the current pack
active and restore the prior parser/AI images.

- [ ] **Step 7: Record checkpoint**

Commit source and non-sensitive evidence. Report exact commit, image tags,
backup paths, hashes, passed/failed fingerprint groups and remaining explicit
exceptions.

