# Structured Item Source Mapping

## Problem

Different printing applications can carry the same business order data in different JSON shapes.

The current parser recognizes text fields such as `productInfo` and `productShortInfo`. The 1688 official printing payload instead provides one or more business items under a structured list such as:

```text
task.documents[].contents[].data.packageItemDetail[]
```

Each item already contains separate product, specification, and quantity values. Treating the whole document as free text loses that structure and causes incorrect rows.

The parser must not hard-code a printing application name. Source shapes are declared by the active recognition rule pack.

## Scope

This change belongs only to:

- the recognition rule-pack contract
- the independent waybill parser service
- parser contract tests
- the current user shoe rule-pack asset

It does not change collection, order-row review, product matching, image matching, or Excel export.

## Rule-Pack Contract

`parser_policy.structured_item_sources` is an optional ordered list. Each entry describes one structured item collection:

```json
{
  "name": "package-item-detail",
  "items_path": "task.documents[].contents[].data.packageItemDetail[]",
  "product_fields": ["itemName", "simpleName"],
  "spec_fields": ["specName", "specSimpleName", "skuFullName"],
  "quantity_fields": ["itemNum"],
  "remark_fields": ["remark", "buyerRemark", "sellerRemark"]
}
```

Rules:

1. `name` and `items_path` are required non-empty strings.
2. `product_fields`, `spec_fields`, `quantity_fields`, and `remark_fields` are ordered string lists.
3. The first non-empty value in each field list is used.
4. Unknown keys are ignored for forward compatibility.
5. Invalid configured types make the rule pack invalid. The parser must not silently ignore a malformed mapping.
6. A pack without `structured_item_sources` retains the existing behavior.

The path syntax is deliberately small:

- dot-separated object keys
- `[]` after a key to iterate a list
- no expressions, filters, scripts, wildcards, or executable code

## Parsing Behavior

For each parent document:

1. Evaluate structured mappings in declaration order.
2. When a mapping produces item objects, create one child order row per item.
3. Read the product, specification, quantity, and remark values using the configured field precedence.
4. Send the specification text through the existing field cleanup and attribute parser.
5. Normalize quantity through the existing quantity policy; use the configured default only when the source value is absent.
6. Preserve the item object path and original values in row source trace.
7. Do not also parse the same document through free-text fallback after structured rows were produced.
8. If no structured mapping produces an item, continue with the existing text-field and printed-text behavior.

For the current 1688 payload:

```text
itemName: 秒21 vap2025
specName: 二代全白 39
itemNum: 1
```

the expected row is:

```text
商品: 秒21 vap2025
销售属性1: 二代全白
销售属性2: 39
数量: 1
```

If `packageItemDetail` contains three items, the parser returns three child rows under the same parent waybill.

## Precedence And Duplicate Prevention

Structured data is preferred because it preserves item boundaries supplied by the printing application.

- Structured item rows win over `productInfo`, `productShortInfo`, or rendered text from the same document.
- Existing text parsing remains available only when structured mappings produce no rows.
- Multiple structured mappings may be declared, but the first mapping that produces rows for a document wins.
- The parser never merges item objects from unrelated mappings.

## Error Handling

- Missing active rule pack: `rule_pack_missing`.
- Invalid structured mapping contract: `rule_pack_invalid`, including the invalid field path.
- Configured path absent in a payload: not an error; try the next mapping or existing text parsing.
- Item exists but product is empty: emit a reviewable row with `no_product_text`.
- Quantity missing: apply the active rule pack quantity policy.
- Unexpected scalar/list shape at the configured path: do not guess; return a diagnostic and continue according to mapping precedence.

## Compatibility

- The HTTP request and response shapes remain `order_row_drafts_v1`.
- Existing rule packs remain valid because `structured_item_sources` is optional.
- Existing Douyin/Cainiao text and printXML parsing must remain unchanged.
- Only the parser service needs rebuilding when this behavior is implemented and the HTTP contract remains unchanged.
- Importing the updated shoe rule pack must not require rebuilding the main platform.

## Acceptance Tests

1. A 1688-style payload with one `packageItemDetail` item produces one correct order row.
2. A payload with multiple items produces one child row per item and one parent waybill.
3. `specName` such as `二代全白 39` becomes sales attribute 1 `二代全白` and sales attribute 2 `39`.
4. `itemNum` becomes an integer quantity.
5. Structured rows are not duplicated by free-text fallback.
6. A payload without the configured path still follows the existing parser behavior.
7. An invalid mapping is rejected during rule-pack validation.
8. Source trace identifies the configured item path and item index.
9. Existing parser tests remain green.

## Out Of Scope

- Automatic discovery of arbitrary item arrays
- Decrypting carrier `encryptedData`
- Printing-application-specific branches in parser code
- Product, SKU, image, or stall matching changes
- UI changes

