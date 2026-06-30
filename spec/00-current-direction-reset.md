# 00 Current Direction Reset

## Decision

The product is being rebuilt around business order rows and switchable recognition rule packs.

The previous technical workflow is retired. Do not keep improving it, do not make it the main path, and do not reintroduce its screens, APIs, tables, or hidden grouping behavior as the foundation.

## New Foundation

The system should expose a simple business flow:

```text
collector payload
  -> raw capture record
  -> independent recognition engine + active rule pack
  -> editable order rows
  -> product/SKU/image matching
  -> supplier Excel
```

## Required Behavior

- If no recognition rule pack is active, parsing must stop with a clear message telling the user to import or enable a rule pack.
- Recognition behavior must be provided by an independent engine service that receives raw records plus a rule pack and returns order rows. The main platform must not hide business-specific parser functions as default behavior.
- The user should mainly work with editable rows, not technical blocks or internal IDs.
- The parser may suggest and split, but uncertain output must be reviewable.
- User corrections should become visible learning records or rule-pack revisions.
- Different product businesses should be handled by switching rule packs, not by changing hidden code.

## Non-Negotiables

- Preserve raw capture records and business assets.
- Do not delete databases or Docker volumes.
- Do not silently guess product meaning when rule coverage is missing.
- Do not let one module write another module's results.
- Do not collapse multi-product waybills into one unreadable row.
- Do not let the recognition engine read/write business tables, product assets, SKU assets, image assets, or export data.
