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

A learning record captures a user-confirmed correction or rule revision.

It should include:

- module
- source sample or rows
- before/after values
- affected count
- rule pack version
- created/updated time
- confirmation operator
- enabled/disabled state
- revision note

## Safe Learning Flow

1. System shows suggested correction or grouped exception.
2. User previews impact.
3. User confirms.
4. System saves a visible learning record or rule-pack revision.
5. Future parsing/matching uses the revised rule pack.

No uncertain rule should be created silently.
