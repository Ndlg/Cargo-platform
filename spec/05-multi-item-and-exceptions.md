# 05 Multi-Item And Exceptions

## Multi-Product Waybills

A single waybill may contain several product items. The parser must turn these into multiple child rows.

Each child row must contain:

- product text
- first attribute
- second attribute
- quantity
- remark
- image match text
- parent waybill label
- child index
- child count

Example labels:

- `第1批-第24单-子1`
- `第1批-第24单-子2`

## Split Preview

The UI should show:

- parent raw text
- child rows
- why the split happened
- confidence/status
- what remains unresolved

## Quantity Rules

- Normalize `*1`, `1件`, `x2`, and similar formats to numbers.
- Default missing quantity to `1` only when the active rule pack allows it for that order type.
- If quantity is ambiguous, mark the row for review.

## Special Orders

Special/manual/message orders must not appear blank.

They should output visible rows or exceptions with:

- original text
- recognized special-order reason
- next action

If they do not participate in product/SKU/image matching, say so clearly.

## Exception Principles

Never silently drop a waybill or child row.

Exception reasons should be actionable:

- missing rule pack
- cannot split products
- missing product text
- missing quantity
- special order
- product unmatched
- SKU unmatched
- image unmatched
- conflict
