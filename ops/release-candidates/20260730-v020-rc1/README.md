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

No branch or release tag may be pushed until the final verification section is complete.
