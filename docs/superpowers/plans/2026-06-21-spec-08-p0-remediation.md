# 2026-06-21 Spec 08 P0 Remediation Plan

## Goal

Bring the current order-reporting workflow closer to the `spec/08-usability-audit-and-remediation-plan.md` contract:

- Use one clean order-row source between parsing, product matching, exception handling, and export.
- Do not silently use hidden/default recognition or product matching when a recognition rule pack is missing.
- Keep product matching explainable: only user learning records should match products, and conflicts must show the exact conflicting records.
- Remove confusing UI concepts such as print-count language, duplicate/technical tables, and unexplained intermediate counters.

## Tasks

- [ ] Audit current parser, product matching, exception, and export paths against spec 08.
- [ ] Add focused regression tests for product matching scope, conflicts, and rule-pack behavior.
- [ ] Fix backend contract bugs first:
  - [ ] no parser-service silent fallback where it would create a different data source;
  - [ ] product matching uses enabled learning records only;
  - [ ] conflicts return actionable rule identifiers and summaries;
  - [ ] counts expose waybill/order-row counts, not print/raw-record counts.
- [ ] Fix front-end flow after backend is stable:
  - [ ] remove duplicate/illogical tables;
  - [ ] keep technical ids in diagnostics;
  - [ ] make exception/action buttons route to the correct admin page;
  - [ ] make rule list searchable and filterable.
- [ ] Run focused backend tests and frontend typecheck.
- [ ] Deploy only if runtime verification needs refreshed containers, preserving `cargo-platform-data`.

## Checkpoints

- Backend tests must cover the behavior before claiming a bug is fixed.
- Runtime self-test should use task 18 or the latest task and verify: collected waybill count, order-row count, matched/unmatched/conflict counts, and export row totals.
- Any old route or old table used as a hidden core dependency must be either removed from the path or explicitly documented as temporary technical debt.
