# Deterministic Rule Engine Source Closeout

This source closeout removes the optional model runtime and keeps the
business path on the independent waybill parser plus declarative rule packs.

## Runtime boundary

- No container, volume, collector, or field runtime was changed while creating
  this source candidate.
- Port 5173 remains outside this candidate. Validation and deployment are for
  the isolated 6173 environment only.
- The release stack contains backend, tenant UI, admin UI, and waybill parser.
- Platform data, raw captures, rule packs, rule-pack revisions, tenant
  fingerprint configuration, product assets, and exports are retained.

## Release gate

Before switching 6173, use a cloned validation data volume and verify:

1. Compose renders without the removed services or environment variables.
2. Backend tests, frontend typecheck/builds, and parser tests pass.
3. Every collected parent waybill is covered by a normal row or an exception.
4. Multi-product waybills keep every child row and duplicate print events stay
   traceable.
5. Unknown formats remain explicit exceptions and never invoke hidden parsing.
6. Supplier workbooks retain the seven-column normal sheet and the one-column
   exception sheet.

## Rollback

The pre-closeout source point is commit `8c8427d`. The previous 6173 manifest
remains available in Git history:

```powershell
git show 8c8427d:ops/release-candidates/20260731-adaptive-engine-final/manifest.json
```

Keep the pre-closeout 6173 images and volumes until the new candidate passes
the release gate. If validation fails, stop only the new 6173 candidate and
restore the recorded images and untouched validation volumes. Never use this
rollback procedure against port 5173 or `cargo-platform-data`.
