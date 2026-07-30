# Cargo Platform 0.2.0-rc.1

This directory records the source, runtime, data-integrity, and rollback evidence for the isolated 6173 release candidate.

## Hard boundary

- The 5173 field stack is the production baseline and must not be changed during this validation.
- `cargo-platform-data` must not be deleted or recreated.
- Validation uses only the `cargo-platform-validation-*` containers and validation data volumes.

## Pre-closeout rollback

- Source tag: `validation-6173-pre-release-closeout-20260730`
- Source commit: `acdb6480a6e318518932d13639cb5de2f8401b2f`
- 6173 backend/parser/UI images: `delivery-905b0a5-20260730-133456`
- 6173 AI image: `confirm-lock-acdb648-20260730-175714`

The SQLite backups listed in `manifest.json` were created with the SQLite online backup API while the 6173 containers remained running. Both backups returned `PRAGMA integrity_check=ok`.

## Final verification

- Release source: `716f8c7`
- Backend tests: `280 passed`
- AI-focused service, rule, fingerprint, replay, and fallback tests: `66 passed`
- Frontend tenant/admin builds: passed
- Frontend production dependency audit: 0 vulnerabilities
- Validation health: UI `6173`, backend `18001`, parser `18010`, AI `18111` all HTTP 200
- Browser acceptance: corrected 面单18, button immediately entered `正在处理…`, then reached `已确认并同步`; console had no errors or warnings
- Rule reuse: four other same-fingerprint waybills were parsed without another model call
- AI fail-open: known rules still worked while AI was stopped; unknown input became an explicit `ai_unavailable` exception
- Coverage:
  - task 62: 164 parents = 142 normal + 22 exceptions
  - task 63: 118 parents = 105 normal + 13 exceptions
  - task 64: 94 parents = 70 normal + 24 exceptions
- Export: all normal sheets have the seven business columns and the exception sheet has only `图片匹配文本`
- Runtime logs: no backend, parser, UI, or AI error patterns in the final 15-minute window
- 5173 field container IDs, images, start times, and restart counts remained unchanged

Machine-readable evidence is in `final-validation.json`. The final SQLite backups were created after browser validation and both returned `PRAGMA integrity_check=ok`.
