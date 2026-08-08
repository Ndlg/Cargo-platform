# Changelog

## Unreleased

## 1.0.2 - 2026-08-08

- Runs Windows deployment behavior tests on `windows-latest` and gates image publication on that result.
- Skips only platform-inapplicable PowerShell behavior tests on non-Windows runners.

## 1.0.1 - 2026-08-08

- Adds a verified macOS/Linux Docker server bundle and one-command deployment entrypoint.
- Publishes all four server images for both AMD64 and ARM64 under one immutable version.
- Refuses upgrades during active collection, snapshots SQLite, and restores prior images on failed readiness checks.
- Keeps the 1.0.0 collection, recognition, matching, and export behavior unchanged.

## 1.0.0 - 2026-08-08

- Promotes the field-validated collection, recognition, matching, and Excel export workflow.
- Adds recoverable collector enrollment, installation, upgrade, supervision, and rollback.
- Keeps every collected print traceable to either normal export coverage or an actionable exception.
- Adds configurable export-column visibility without changing stored business data.
- Publishes version-matched backend, tenant UI, admin UI, parser, and collector artifacts.

## 1.0.0-rc.1 - 2026-08-01

- Preserves the field collection-to-export workflow and removes manual order-row review.
- Keeps waybill parsing in the independent parser service.
- Keeps tenant fingerprint field selection and declarative, replay-validated rules.
- Removes the local model, model-session service, and model-specific approval workflow.
- Fixes corrected text waybills whose product follows attributes in the source text.
- Removes unreachable parser copies from the main backend.
- Publishes version-matched backend, tenant UI, admin UI, and parser images.
