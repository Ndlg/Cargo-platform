# Cargo Platform V1 Candidate Closeout

This source closeout removes the optional model runtime and keeps the
business path on the independent waybill parser plus declarative rule packs.

## Runtime boundary

- Port 5173 and its five field containers were not restarted or replaced.
- The no-model candidate is deployed only on the isolated 6173/6174 ports.
- The release stack contains backend, tenant UI, admin UI, and waybill parser.
- Platform data, raw captures, rule packs, rule-pack revisions, tenant
  fingerprint configuration, product assets, and exports are retained.

## Verified result

The release gate passed on `2026-08-02` for candidate
`1.0.0-rc.2-e8b88f7`:

1. The validation group contains only backend, tenant UI, admin UI, and parser.
2. Backend tests: `377 passed`; four frontend contract tests, typecheck,
   tenant build, and server-admin build passed.
3. Starting from zero rules, 25 prior administrator confirmations were replayed
   through the public learning API with zero failures. They produced 24 unique
   immutable learning records and 21 active grammar slots.
4. All 283 collected parent waybills are covered by a normal result or an
   explicit exception. The normal/export and exception counts equal the
   previous accepted 6173 baseline.
5. The three supplier workbooks download successfully.
6. Collector protocol v2 now uses task windows, persistent source epochs,
   atomic lease takeover, and an explicit stale-upload rejection. Cross-protocol
   similarity is never used to discard a print; uncertain reprints are retained.
7. Three independent P0/P1 reviews passed, and the live rollback round trip
   `rc.2 -> rc.1 -> rc.2` completed without changing business rows.

Exact image digests, container IDs, data hashes, task coverage, and the frozen
5173 evidence are recorded in `manifest.json`.

## Release scope

This candidate is verified for the packaged Docker/SQLite runtime used by the
isolated 6173 environment. It does not claim an upgrade path for an existing
MySQL database.

## Rollback

The immediate rollback tag is `1.0.0-rc.1`, using the same validation volume.
The rc.1 image archive and the consistent predeploy SQLite snapshot are stored
under `.worktrees/cargo-platform-validation-backups` and their hashes are in
`manifest.json`. The rollback was executed once and the candidate was restored.

Keep those assets until the user accepts 6173. If validation fails, replace
only the `cargo-platform-validation` group. Never use this rollback procedure
against port 5173 or `cargo-platform-data`.
