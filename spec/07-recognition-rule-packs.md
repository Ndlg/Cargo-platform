# 07 Recognition Rule Packs

## Contract

Rule packs are portable scenario definitions.

Rule packs are declarative configuration. They must not contain executable code, hidden Python/JavaScript snippets, or business logic that only works because the main platform has a hard-coded parser path.

The main platform stores and selects rule packs. The independent recognition engine validates and applies them.

Minimum payload:

```json
{
  "contract_version": "recognition_rule_pack_v1",
  "pack": {
    "code": "shoe-order-pack",
    "name": "Shoe order pack",
    "version": "1.0.0",
    "description": "Rules for one shoe order scenario"
  },
  "parser_policy": {
    "requires_active_rule_pack": true,
    "order_row_parser": "shoe_waybill_v1"
  },
  "special_order_policy": {},
  "multi_product_policy": {},
  "cleanup_policy": {},
  "product_matching_policy": {},
  "sku_matching_policy": {},
  "image_matching_policy": {},
  "exception_policy": {}
}
```

`parser_policy.order_row_parser` is required. It tells the independent recognition engine which parser capability this pack is allowed to use.

Currently supported value:

- `shoe_waybill_v1`: current user's shoe/order waybill parser capability

If this field is missing or unsupported, the pack is not usable for parsing and must be reported as `rule_pack_invalid`.

## Lifecycle

Supported operations:

- import
- export
- activate
- disable
- delete
- inspect
- revise
- validate
- preview

## Runtime Behavior

- Exactly one pack should be active per workspace/scenario at runtime.
- If no pack is active, parsing stops with a missing-pack response.
- If a pack is active but invalid, parsing stops with an invalid-pack response.
- The main platform sends the active pack payload to the recognition engine. It must not run hidden built-in parsing.
- A pack revision should not overwrite history silently.
- Any behavior that affects parsing or matching should be traceable to a pack revision.

## Current Shoe Scenario

The current user's shoe-waybill behavior should be exportable as a pack.

It may include:

- shoe size normalization
- color/style cleanup
- multi-product splitting patterns
- special message order handling
- default quantity policy
- product/SKU/image matching hints

It must not become a hidden built-in default.

## Rule Pack Editor

The user-facing editor should turn JSON sections into business controls:

- basic metadata: code, name, version, description
- special-waybill keywords and display reasons
- label cleanup words such as color, size, shoe size, and spec labels
- quantity defaults and quantity text patterns
- size normalization examples and cleanup rules
- multi-product split policy and sample patterns
- allowed product/SKU matching fields
- export and exception policy

The editor may expose an advanced JSON tab, but the primary flow should not require the user to read or write JSON.

Every edit must support preview before activation:

- selected sample raw records
- before/after parsed order rows
- affected waybill count
- generated child row count
- special row count
- exception/review count
- validation warnings

Saving an edit should create a new revision or version, not silently mutate the active pack in place.

## Import/Export UI

The UI should provide a clear `规则包` entry with:

- active pack summary
- import file action
- export active pack action
- activate/deactivate controls
- revision list
- validation errors

## Validation

When importing:

- validate contract version
- validate required sections
- validate `pack.code`, `pack.name`, and `pack.version`
- validate `parser_policy.order_row_parser`
- show warnings before activation
- never apply a broken pack silently

When previewing:

- call the independent recognition engine validation/preview API
- do not use platform business tables except to provide raw sample payloads
- show parser diagnostics in business language
- keep raw JSON/source traces in expandable diagnostics only

## Error Language

Rule-pack related messages must be specific:

- `rule_pack_missing`: no active pack exists in this workspace.
- `rule_pack_invalid`: an active pack exists, but required configuration is missing or unsupported.
- parser service unavailable: the independent recognition engine is not reachable or not configured.

Do not tell the user to "启用规则包" when a pack is already active but invalid. The correct action is to repair, re-import, or revise the active pack.
