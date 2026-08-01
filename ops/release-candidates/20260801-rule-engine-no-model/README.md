# Deterministic Rule Engine Closeout

This source closeout removes the optional model runtime and keeps the
business path on the independent waybill parser plus declarative rule packs.

## Runtime boundary

- Port 5173 and its five field containers were not restarted or replaced.
- The no-model candidate is deployed only on the isolated 6173/6174 ports.
- The release stack contains backend, tenant UI, admin UI, and waybill parser.
- Platform data, raw captures, rule packs, rule-pack revisions, tenant
  fingerprint configuration, product assets, and exports are retained.

## Verified result

The release gate passed on `2026-08-01`:

1. The validation group contains only backend, tenant UI, admin UI, and parser.
2. Backend tests: `306 passed`; frontend tests, typecheck, tenant build, and
   server-admin build passed.
3. Starting from zero rules, 25 prior administrator confirmations were replayed
   through the public learning API with zero failures. They produced 24 unique
   immutable learning records and 21 active grammar slots.
4. All 283 collected parent waybills are covered by a normal result or an
   explicit exception. The normal/export and exception counts equal the
   previous accepted 6173 baseline.
5. The three supplier workbooks download successfully.

Exact image digests, container IDs, data hashes, task coverage, and the frozen
5173 evidence are recorded in `manifest.json`.

## Rollback

The pre-closeout source point is tag `rollback/ai-enabled-20260801`. Its data
volume `cargo-platform-validation-zero-platform-20260731-172907` remains
untouched. The previous 6173 manifest remains available in Git history:

```powershell
git show 8c8427d:ops/release-candidates/20260731-adaptive-engine-final/manifest.json
```

Keep the recorded rollback images and volume until the user accepts 6173. If
validation fails, replace only the `cargo-platform-validation` group with those
recorded assets. Never use this rollback procedure against port 5173 or
`cargo-platform-data`.
