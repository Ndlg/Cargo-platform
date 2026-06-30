# Stage83C Export Practical Closeout

## Scope

Changed module: Export Module.

Input: 商品匹配结果 from the current chain:

```text
字段定义 standard_rows -> 商品匹配 product_sku_linking_results -> 导出 Excel
```

Output: supplier reporting Excel workbook.

The export module remains read-only toward upstream decisions. It does not parse waybills, define fields, match products, match SKUs, match images, learn rules, or revive legacy `match_rules`.

## Current Behavior

Export preview and download use `recognition_rows_for_task`, which now prioritizes `WaybillFieldMappingResult.source_trace.product_sku_linking_results` from the new field-definition/product-matching chain.

When field-mapping results cover only part of a capture task, export keeps uncovered old `StandardDetail` rows as `pending` exceptions instead of dropping them.

Stage83C follow-up cleanup added clear report aliases:

- `GET /collector-control/tasks/{task_id}/report-preview`
- `GET /collector-control/tasks/{task_id}/report-workbook`

The older `recognition-preview` and `recognition-report` paths remain compatibility aliases.

## Workbook Contract

Normal report sheet:

```text
商品 / 销售属性1 / 图片 / 销售属性2 / 数量 / 备注 / 图片匹配文本
```

Only `match_status == "matched"` rows enter the normal sheet.

Exception sheet:

```text
Sheet: 异常面单
Column: 图片匹配文本
```

All non-`matched` rows enter the exception sheet, including:

- `product_unmatched`
- `sku_unmatched`
- `image_unmatched`
- `conflict`
- `pending`
- missing 商品匹配结果

The `异常面单` sheet is always generated, even when it contains only the header row.

Quantity parsing accepts only explicit quantity shapes such as `*1`, `1件`, `1 件`, `x1`, and `×1`. Ambiguous text such as a size plus quantity fragment is not parsed by taking the first number.

## Stage83C Test Coverage

`backend/tests/test_recognition_report_export.py` now covers:

- new-chain multi-item 商品匹配 results expanding into multiple export rows;
- non-`matched` rows entering `异常面单`;
- quantity formats such as `*1`, `1件`, and `1 件` aggregating as numeric `1`;
- partially migrated batches preserving uncovered old details as pending exceptions.
- empty exception sheets still preserving the `异常面单` structure.
