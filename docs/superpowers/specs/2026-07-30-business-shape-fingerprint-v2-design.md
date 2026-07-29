# Business Shape Fingerprint V2 Design

## Goal

Make one learned recognition rule reusable for other waybills with the same
business format, while treating genuinely different layouts as unfamiliar.
Keep the five-field result contract:

- `product`
- `sales_attr1`
- `sales_attr2`
- `quantity`
- `remark`

## Current Failure

The legacy fingerprint hashes the structure of the complete raw payload and
replaces every string value with `scalar`.

- It is too coarse for text: 57 `CN-PRINT-XML` documents with several
  incompatible text layouts share one fingerprint.
- It is too fine for structured items: 138 `CN-PACKAGE-ITEMS` documents are
  split across 14 fingerprints because unrelated raw fields differ.
- A rule approved for one sample can therefore fail on another document with
  the same legacy fingerprint.

The 6173 validation copy currently contains 376 documents. Only 156 produce
rows; 220 remain explicit unresolved parents. No parent is silently dropped.

## Selected Design

### Fingerprint strategy

Recognition rule packs gain an optional
`parser_policy.fingerprint_strategy`:

- missing or `legacy_structure_v1`: preserve the existing algorithm
- `business_shape_v2`: use the new algorithm

V2 values are opaque strings:

`v2:<fingerprint-code>:sha256:<64 lowercase hex characters>`

The v2 hash consumes only:

- normalized source component
- the named fingerprint capability
- a canonical business-format shape

Text values are converted to grammar shapes. Product, attribute and quantity
tokens are discarded while brackets, separators, whitespace boundaries,
newlines and quantity-unit layout remain. Repeated lines with the same shape
do not create a new format solely because the print count changes.

`CN-PACKAGE-ITEMS` uses the paths and scalar types of its maintained business
fields. It ignores unrelated raw payload keys, business values and the number
of items.

AI recognition and the parser import the same pure fingerprint module. The
module performs no database or network access. The parser can therefore use
learned rules while the local AI service is offline.

### Matching behavior

Legacy packs keep legacy matching.

V2 packs calculate only v2 fingerprints. A named but unknown v2 format returns
`format_profile_missing`; it must not fall back to a legacy profile, because
that would reproduce the current collision.

### Structured items

`structured_items_v1` gains an optional `steps` array. After the configured
fields are read from one item, the parser runs the existing text-pipeline
operations against that row state.

No new operation is required for current package data:

```json
{
  "strategy": "structured_items_v1",
  "items_path": "task.documents[].contents[].data.packageItemDetail[]",
  "fields": {
    "product": "itemName",
    "sales_attr1": "specName",
    "quantity": "itemNum"
  },
  "steps": [
    {
      "op": "rsplit",
      "source": "sales_attr1",
      "delimiter": " ",
      "targets": ["sales_attr1", "sales_attr2"]
    }
  ],
  "defaults": {
    "remark": ""
  }
}
```

Each `packageItemDetail[]` item remains one child row. Identical business
content is not deduplicated.

### Approval and migration

The database schema and AI approval API do not change. The fingerprint remains
an opaque string shorter than the existing 128-character limit.

6173 migration is staged:

1. Back up the validation database, AI session database and active rule pack.
2. Compute v2 groups from the copied raw records.
3. Clone a legacy profile only when it replays every confirmed sample in that
   v2 group exactly.
4. Add a package-items profile only after all 138 documents produce 141 valid
   rows and the three two-product documents produce two rows each.
5. Activate `business_shape_v2` only after validation succeeds.
6. Keep the prior Docker images and databases as rollback assets.

Conflicting `CN-PRINT-XML` groups are not migrated blindly. They remain
unfamiliar until a representative sample is confirmed and its rule passes the
existing exact replay gate.

## Invariants

- 5173 is never rebuilt, restarted, written or deployed during this work.
- A rule is never saved from manual rows alone; exact replay remains mandatory.
- Unknown or incomplete formats remain actionable exceptions.
- `collected waybills = normal parent coverage + exception parent coverage`.
- Multi-product parents create one child row per item.
- A row with no product or invalid quantity never enters normal output.
- No hidden platform or product-specific parser patch is introduced.

## Verification

- Golden vectors prove AI and parser calculate identical v2 fingerprints.
- Same layout with changed product values keeps one v2 fingerprint.
- Different XML grammars receive different v2 fingerprints.
- All 14 legacy package structures collapse to one v2 fingerprint.
- Legacy packs retain their prior behavior.
- V2 packs never fall back to colliding legacy profiles.
- Structured steps split attributes and preserve every package item.
- Parser-only validation passes with the AI container stopped or unreachable.
- Full 6173 replay checks all three copied tasks and both SQLite databases.

