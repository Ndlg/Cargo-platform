# 04 Rule Pack And Learning Model

## Rule Packs

A recognition rule pack is an importable/exportable business scenario asset.

It can contain:

- parser policies
- special-order policies
- multi-product split policies
- cleanup and normalization policies
- product matching policies
- SKU matching policies
- image matching policies
- exception policies

Rule packs must be visible and switchable. They are not hidden model memory.

## Missing Rule Pack

If no active rule pack exists:

- parsing must not run hidden defaults
- APIs should return a clear `rule_pack_missing` style response
- UI should guide the user to import or activate a pack

## Learning Records

A learning record captures an administrator-confirmed sample used to compile a
reusable declarative rule. It is not an edit to a live order row.

It should include:

- module
- source sample locator and evidence digest
- the five confirmed business fields
- affected count
- rule pack version
- created/updated time
- confirmation operator
- enabled/disabled state
- revision note

## Safe Learning Flow

1. System shows the tenant-selected source fields for one unknown format.
2. Administrator labels the five business fields, adding rows for multi-product waybills.
3. Parser compiles a declarative rule and replays the current and historical samples.
4. Only a successful replay creates an immutable rule-pack revision and activates it.
5. The affected capture rounds are recalculated and future matching reuses the rule.

No uncertain rule should be created silently.
