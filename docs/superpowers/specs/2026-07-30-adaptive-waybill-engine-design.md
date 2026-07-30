# Adaptive Waybill Engine Design

## Goal

Build a deliverable recognition engine that starts with zero recognition and AI-learned rules in the isolated 6173 environment, learns only from constrained examples, and produces the same business workbook as the read-only 5173 field system for the same captured inputs.

The engine succeeds when it makes the local 4B model solve only bounded candidate-selection problems. The model must not write executable or declarative parsing rules.

## Hard Boundaries

- 5173 is a read-only business oracle. Do not restart, reconfigure, rebuild, deploy to, or write data into its containers or volumes.
- 6173 is the only trial chain.
- Preserve every collected print event. Identical business content is not a duplicate.
- Do not delete or recreate `cargo-platform-data`.
- "Zero rules" means zero recognition format rules and zero AI-learned rules. Product, SKU, stall, image, export-header, and other business assets remain available because they are not recognition answers.
- Do not import the 5173 recognition rule pack into 6173.
- The trial may read 5173 exports and parsed results to create gold labels and comparisons.
- No administrator or business UI redesign is part of this engine phase.
- No OCR and no decryption. If readable product information is absent, return `source_data_missing`.

## Release Architecture

### `services/waybill-parser`

This is the deterministic recognition engine and the single owner of rule semantics.

It owns:

- raw payload normalization
- source-path and source-span preservation
- structural fingerprinting
- grammar-signature generation
- candidate segmentation
- declarative rule execution
- rule synthesis from corrected five-field rows
- replay validation
- source tracing

It remains stateless with respect to the platform database.

### `services/ai-recognition`

This is an optional ambiguity resolver.

It receives only:

- allowed product-related source spans
- stable span identifiers
- bounded candidate groupings
- the five-field output schema

It returns only span selections and row groupings. It does not return rule programs, regular expressions, field paths, or executable expressions.

If this service is unavailable, known rules continue to run and unknown formats return `ai_unavailable`.

### `backend`

The backend orchestrates and stores:

- active rule-pack versions
- learned-rule versions
- gold learning samples
- replay reports
- recognition results

It does not contain parsing heuristics or import parser internals.

## Engine Pipeline

1. Canonicalize the raw payload without dropping source data.
2. Extract only allowed business fields and retain their original paths.
3. Produce a structural fingerprint from paths, container types, and array shapes, excluding business values.
4. Normalize punctuation and whitespace while retaining offsets into the original text.
5. Produce a grammar signature from token classes such as text, size, quantity, separators, anchors, and repeated groups.
6. Look up rules by structural fingerprint and grammar signature.
7. Execute matching deterministic rules and validate the five-field result.
8. If no rule matches, generate a small candidate graph.
9. Apply deterministic classifiers first; send only unresolved candidate choices to the 4B model.
10. Accept corrected five-field rows as gold input to the deterministic rule synthesizer.
11. Synthesize the smallest program from the engine's fixed rule primitives.
12. Replay the candidate rule against the current sample, all gold samples in its applicability group, and near-neighbor negative samples.
13. Save a new rule version only when every replay assertion passes.
14. Emit one or more five-field rows with per-field source traces.

## Canonical Evidence Contract

Each normalized source span has:

```json
{
  "span_id": "source-1:line-0:segment-2",
  "source_path": "task.documents[0].contents[0].data.ITEM_INFO",
  "original_text": "【HK】特2跑步鞋 灰蓝;39【1件】",
  "normalized_text": "【HK】特2跑步鞋 灰蓝;39【1件】",
  "start": 0,
  "end": 20,
  "token_class": "text"
}
```

Addresses, telephone numbers, order identifiers, tracking numbers, account names, and technical fields are excluded before model invocation.

## Five-Field Row Contract

The engine learns and emits only:

```json
{
  "product": "string",
  "sales_attr1": "string",
  "sales_attr2": "string",
  "quantity": 1,
  "remark": ""
}
```

Requirements:

- `product` is non-empty.
- `quantity` is a positive integer.
- `remark` may be empty.
- Multi-product waybills emit multiple rows.
- Every non-empty value has a source trace.
- Field labels such as `商品是` or `销售属性1是` are never part of field values.

## Rule Intermediate Representation

Rules are declarative data built only from a fixed allowlist:

- `select`
- `unwrap_json`
- `unwrap_xml`
- `iterate`
- `normalize`
- `tokenize`
- `split`
- `capture`
- `group_repeat`
- `assign`
- `to_positive_int`
- `validate`
- `emit`

The engine rejects unknown operations and arbitrary executable content.

The 4B model never creates this representation. The synthesizer derives it from source spans and corrected rows.

## Conservative Adaptation

The first rule for a format is intentionally narrow:

- applicability includes structural fingerprint
- applicability includes grammar signature
- required anchors and token classes must be present
- output validation must pass

Additional gold samples may widen a rule only if old samples still replay exactly. A new candidate cannot replace or broaden an existing rule when replay changes any prior confirmed row.

When no allowlisted program can reproduce the gold rows, return `compiler_capability_missing`. Developers then add one general primitive or improve candidate generation; they do not add a platform-specific parsing patch.

## Engine Status Contract

- `recognized`: a deterministic rule produced valid rows
- `format_unknown`: readable evidence exists but no rule applies
- `ai_unavailable`: unknown format requires candidate selection but the optional AI service is unavailable
- `candidate_invalid`: the model selected invalid or incomplete span identifiers
- `rule_replay_failed`: a candidate rule could not reproduce all gold samples
- `compiler_capability_missing`: the fixed rule primitives cannot represent the corrected result
- `source_data_missing`: readable product information is absent
- `rule_pack_missing`: no active rule pack exists
- `rule_pack_invalid`: the active rule pack cannot be executed
- parser service unavailable: the platform could not reach the deterministic engine

No status may silently fall back to hidden parsing.

## 5173 Oracle Protocol

1. Identify the newest three completed 5173 capture rounds without writing to 5173.
2. Copy their raw capture records and required non-recognition business assets into the isolated 6173 data copy.
3. Export the corresponding 5173 workbooks and parse them into a gold manifest.
4. Remove all recognition format rules and AI-learned rules from 6173 through backed-up, scoped data operations.
5. Group raw records by engine structural and grammar signatures.
6. Use the minimum gold examples needed to teach each previously unknown applicability group.
7. Keep the remaining examples hidden from rule synthesis as holdout records.
8. Run the completed 6173 chain through recognition, product/SKU/image matching, and export.
9. Compare the resulting workbook semantically with the 5173 gold workbook.

5173 gold output may supply corrected five-field learning rows, but its recognition rules and internal parser configuration may not be copied.

## Workbook Equivalence

Binary XLSX equality is not required because ZIP metadata and timestamps vary. Semantic equivalence requires:

- identical sheet names
- identical normal-row count
- identical row order
- identical values in `商品`, `销售属性1`, `图片`, `销售属性2`, `数量`, `备注`, and `图片匹配文本`
- identical duplicate multiplicity
- identical exception-row count and `图片匹配文本`
- equivalent embedded image content at the same business row, compared by decoded image hash

Any difference produces a structured diff artifact with task, sheet, row, column, expected value, and actual value.

## Acceptance Gates

### Engine Gate

- Rules are compiled by `waybill-parser`, never by the model.
- Known-rule parsing performs zero model calls.
- AI shutdown does not affect known rules.
- Every emitted field is traceable.
- Unknown or uncertain input remains an explicit exception.

### Learning Gate

- A corrected sample can compile a deterministic rule when the fixed primitives can represent it.
- The rule exactly reproduces all gold samples in its applicability group.
- Near-neighbor negative samples do not match.
- Replay failure leaves the prior active rule unchanged.

### Business-Closure Gate

- 6173 starts with zero recognition and learned rules.
- The newest three completed 5173 rounds are reproduced in 6173.
- Holdout records are not supplied to synthesis.
- The 6173 final workbook is semantically equivalent to the 5173 workbook.
- For each round:

```text
collected parent waybills = normal covered waybills + exception covered waybills
```

- Every collected print remains traceable; same-content prints are not deduplicated.

## Rollback and Isolation

Before each trial stage:

- create timestamped SQLite online backups
- record SHA-256 and `PRAGMA integrity_check=ok`
- record 5173 container IDs, images, start times, and restart counts
- tag the current code checkpoint
- record 6173 image names and container IDs

Only the affected 6173 service is recreated at each stage. Rollback restores the prior 6173 image and, only when data changed, the matching validation database backup.

No stage may modify or restart 5173.

