# 09 Independent Recognition Engine

## Decision

Waybill recognition must be extracted into an independent module/service.

The main platform should not own product-scenario parsing logic. It should collect raw records, store rule packs, call the recognition engine, display returned order rows, and continue downstream product matching/export work.

The recognition engine should own only this transformation:

```text
raw waybill payload + recognition rule pack
  -> parsed order rows + parse diagnostics
```

This keeps the platform stable while recognition rules and parser behavior evolve.

## Current Implementation Baseline

As of the current rebuild, the parser is already separated at runtime:

- service source: `services/waybill-parser`
- service container: `cargo-platform-waybill-parser`
- service health: `GET /health`
- local health URL: `http://127.0.0.1:8010/health`
- backend configuration: `WAYBILL_PARSER_URL`
- backend adapter: `backend/app/services/waybill_parser_client.py`

The main backend should treat the parser service as an external dependency. It should not import parser service modules directly and should not keep a second embedded copy of parser behavior.

Deployment boundary:

- Rule-pack-only change: import/activate the revised pack; no container rebuild is required.
- Parser behavior change with unchanged HTTP contract: rebuild/restart `cargo-platform-waybill-parser` only.
- Parser HTTP contract change: update parser service, backend adapter, tests, and deployment together.
- Product matching, export, or UI-only change: do not modify parser behavior unless the parser contract is explicitly in scope.

This boundary is part of the product design. It exists so recognition can evolve without forcing a full business-system redeploy every time.

## Why This Is Needed

The current system has repeatedly become hard to maintain because parsing, field cleanup, multi-product splitting, product matching, UI preview, and export behavior were mixed together.

That caused several problems:

- changing recognition behavior required rebuilding/redeploying the full business system
- users could not tell whether behavior came from a rule pack or hidden code
- rule-pack preview still depended on platform parser functions
- old parsing paths could accidentally produce different data from the parsing page
- multi-product waybills and special waybills were difficult to reason about

The new goal is to make recognition replaceable, testable, and scenario-driven.

## Module Boundary

### Main Platform

The main platform may:

- store raw capture records
- store recognition rule packs and revisions
- activate, deactivate, import, export, and delete rule packs
- pass raw records and selected rule packs to the recognition engine
- store recognition outputs as order rows
- display order rows and diagnostics to the user
- let users correct rows and create learning records
- perform product/SKU/image matching from parsed rows
- export supplier workbooks

The main platform must not:

- run hidden default recognition
- contain shoe-specific recognition as its source of truth
- parse when no active rule pack exists
- mutate parser behavior outside a visible rule pack or learning record
- let product matching reach backward into raw parsing internals

### Recognition Engine Service

The recognition engine may:

- validate rule pack structure
- explain a rule pack in business-readable sections
- parse one raw record
- parse a batch of raw records
- preview the effect of a rule-pack revision
- return diagnostics and source traces

The recognition engine must not:

- connect to the platform database
- read or write products, SKUs, image assets, stalls, or export data
- decide final product/SKU/image matching
- export Excel
- silently use built-in scenario defaults when no rule pack is provided

## Rule Pack Constraint

Rule packs are declarative scenario assets.

Allowed:

- keywords
- cleanup labels
- separator rules
- size normalization rules
- quantity policies
- special-waybill policies
- multi-product split policies
- output field mapping policies
- diagnostic messages

Forbidden:

- executable code in rule packs
- hidden platform defaults masquerading as rule-pack behavior
- shoe-specific logic that runs without an active shoe rule pack
- database-dependent parser behavior

If a behavior cannot be explained from the rule pack or an inspectable learning record, it should be treated as a bug.

## Engine API

The engine exposes a small HTTP contract. The platform must call these endpoints instead of local parser functions.

### `GET /health`

Returns service health and engine version.

### `POST /api/v1/rule-packs/validate`

Input:

```json
{
  "rule_pack": {}
}
```

Output:

```json
{
  "valid": true,
  "errors": [],
  "warnings": [],
  "sections": []
}
```

Purpose: let the platform validate imports and editor changes without parsing business data.

### `POST /api/v1/rule-packs/explain`

Input:

```json
{
  "rule_pack": {}
}
```

Output: business-readable sections such as special rules, cleanup rules, quantity rules, multi-product rules, and matching field policy.

Purpose: support a user-friendly rule-pack editor.

### `POST /api/v1/parse/preview`

Input:

```json
{
  "rule_pack": {},
  "records": [
    {
      "record_key": "task19-record1",
      "source_component": "cainiao-cnprint",
      "payload": {}
    }
  ]
}
```

Output:

```json
{
  "summary": {
    "parent_waybill_count": 1,
    "order_row_count": 1,
    "special_count": 0,
    "needs_review_count": 0
  },
  "parents": [],
  "rows": [],
  "diagnostics": []
}
```

Purpose: show what a rule pack would do before saving, revising, or activating it.

### `POST /api/v1/parse/batch`

Same input shape as preview, but intended for applying the active rule pack to a selected batch.

Output must include:

- parent waybill label
- child order row label
- `product`
- `sales_attr1`
- `sales_attr2`
- `quantity`
- `remark`
- `image_match_text`
- status
- review reason
- source trace

## Failure Status Contract

The platform and UI must distinguish these cases:

| Case | Status | User-facing meaning |
| --- | --- | --- |
| No active rule pack | `rule_pack_missing` | 当前工作空间没有启用识别规则包，请导入或启用规则包。 |
| Active pack exists but parser rejects it | `rule_pack_invalid` | 当前规则包格式或解析器配置不完整，需要修正规则包。 |
| Parser service URL missing | service unavailable | 解析服务未配置，不能使用旧逻辑兜底。 |
| Parser service timeout/down | service unavailable | 解析服务暂时不可用，请检查解析服务容器。 |

The UI must not collapse these cases into one generic "启用规则包" message.

## Code Ownership Rules

Parser behavior belongs in the parser service and active rule pack.

Main backend may:

- fetch raw records from the database
- fetch the active rule pack
- call the parser service
- keep a contract-only DTO module such as `backend/app/services/order_row_contract.py`
- persist or display returned rows

Main backend must not:

- parse shoe order text directly
- keep parser implementation files such as `backend/app/services/order_row_drafts.py` or `backend/app/services/waybill_parser.py`
- normalize sizes, quantities, colors, or multi-product rows as a hidden fallback
- apply parser-specific heuristics inside product matching or export
- silently repair parser output by reading raw waybill text again

Product matching and export consume parser output only. If parser output is wrong, fix the parser service or rule pack, then regenerate the order rows.

## Rule Pack Editor Direction

The platform should provide a rule-pack editor that does not require the user to edit raw JSON.

Minimum useful editor:

1. Metadata panel:
   - name
   - code
   - version
   - description

2. Special-waybill rules:
   - keyword
   - status
   - display reason
   - whether to parse fields
   - whether to match product/image

3. Cleanup and normalization:
   - label prefixes to strip
   - separators
   - size normalization examples
   - purchase hint stripping

4. Quantity:
   - default quantity policy
   - accepted quantity patterns
   - whether to remove quantity text from fields

5. Multi-product:
   - split enabled/disabled
   - pair product lines with attribute lines
   - preserve parent text
   - sample patterns

6. Product/SKU matching policy:
   - allowed product match fields
   - allowed SKU match fields
   - no hidden guessing flag

7. Preview:
   - select task or sample records
   - show before/after rows
   - show impact counts
   - show warnings

8. Revision:
   - save as new version
   - revision note
   - activate after save option

Advanced JSON view may exist, but it should be secondary and should validate through the engine before saving.

## Migration Plan

### Stage 1: Document And Freeze Boundary

- Mark main-platform embedded recognition as transitional.
- Add tests that fail when parsing succeeds without an active rule pack.
- Add tests that verify recognition output comes from engine-style contracts.

### Stage 2: Extract Engine Adapter

- Move parser policy evaluation into a service boundary.
- Ensure the engine function accepts only `rule_pack` and `records`.
- Remove direct database access from parser logic.
- Keep current shoe behavior as `current-user-shoes-v1` rule pack data, not as hidden defaults.

### Stage 3: HTTP Service

- Expose the engine as a Docker service.
- Main backend calls it through HTTP for validate/preview/parse.
- Add timeout, error handling, and readable failure messages.
- If the engine is down, the platform should show "识别服务不可用", not blank rows.

### Stage 4: Rule Pack Editor

- Build the business-form editor.
- Add preview before save.
- Save revised packs as new versions.
- Keep import/export/activate/deactivate/delete controls.

### Stage 5: Remove Old Paths

- Audit for old parser fallbacks and hidden defaults.
- Remove or quarantine obsolete field-definition/match-rule/fingerprint paths.
- Keep only business-row parsing through the engine contract.

## Acceptance Criteria

The independent recognition direction is acceptable when:

- disabling all rule packs prevents parsing and returns `rule_pack_missing`
- activating an old or incomplete pack returns `rule_pack_invalid`, not misleading blank rows
- stopping the parser service returns a readable service-unavailable message and does not invoke embedded fallback parsing
- changing parser behavior can be done by changing a rule pack or engine module, not the main business UI
- the engine can validate and preview a rule pack without platform business tables
- the platform can call the engine and receive deterministic order rows
- multi-product waybills return multiple child rows
- special waybills return explicit special status and readable reason
- all parser output has source trace for audit
- product matching consumes parsed rows only and never reparses raw waybill text
- export consumes matched rows only and never performs recognition

## Current Technical Debt

The existing code may still contain parser logic inside the main backend or platform-facing services. Treat this as transitional debt.

Do not add new recognition behavior directly into platform routes or UI components. New recognition behavior should be implemented in the independent engine boundary and controlled by rule packs.
