# Stage81-Export-Contract-Cleanup

## Module Contract

Export module input: 商品匹配结果 stored as `product_sku_linking_results` or `product_sku_linking_result` on a `StandardDetail.field_values` payload.

The export module consumes only:

- final 商品匹配 output: `product`, `sku`, `image` / `image_asset_id`, `match_status`, `exception_reason`;
- five-field display values already supplied by upstream modules: `product`, `sales_attr1`, `sales_attr2`, `quantity`, `remark`.

The export module must not infer missing product, SKU, image, or display fields from similar waybills, automatic templates, old match rules, or neighboring text.

Progressive rule learning belongs upstream:

- Field Definition learns user-confirmed mappings from text blocks to five standard fields.
- 商品匹配 learns user-confirmed links from five-field values to product, SKU, and image.
- Export does not learn rules, create rules, patch rules, or enrich rows by reverse-parsing waybills.

Export module output: supplier reporting Excel workbook.

Normal sheet columns remain:

```text
商品 / 销售属性1 / 图片 / 销售属性2 / 数量 / 备注 / 图片匹配文本
```

Exception sheet remains:

```text
异常面单
图片匹配文本
```

## Current Export Path Audit

- `raw-document`: retained. It reads `RawCaptureRecord` and exports raw collection text only.
- `standard-document`: legacy field-definition document export. It still depends on `FieldDefinition.export_enabled` and dynamic export headers, so it is not the new supplier report contract.
- `recognition-preview`: compatibility URL only. Changed in Stage81 export cleanup so it no longer calls the old match-rule recognition path; it reads only 商品匹配结果 payloads and marks missing payloads as pending exceptions.
- `recognition-report`: compatibility URL/download filename only. Changed in Stage81 export cleanup so it uses the same 商品匹配结果 rows as preview and keeps the target workbook headers/sheets.

## Legacy Dependency Isolation

The old match-rule based recognition path is retained only as `legacy_recognition_rows_for_task` for code isolation and reference. The active preview/report endpoints call `recognition_rows_for_task`, which now delegates to `recognition_rows_from_product_sku_linking_results`.

When a detail has no new 商品匹配 output, export emits a row with status `pending` and writes a clear exception message instead of guessing from old parsed fields.

When 商品匹配 output is present but incomplete, export leaves missing fields blank or routes non-`matched` rows to `异常面单`. It does not backfill final product/SKU/image from the five-field display payload.

Rows are split strictly by `match_status`:

- `matched`: normal report sheet.
- `pending`, `conflict`, `product_unmatched`, `sku_unmatched`, `image_unmatched`, `unmatched`, or any other non-`matched` status: `异常面单` / exception flow.

## 商品匹配结果 Shape

Preferred multi-row shape:

```json
{
  "product_sku_linking_results": [
    {
      "product": "string",
      "sku": "string",
      "image_asset_id": 1,
      "image_label": "string",
      "match_status": "matched | product_unmatched | sku_unmatched | image_unmatched | conflict",
      "exception_reason": "string",
      "standard_fields": {
        "product": "string",
        "sales_attr1": "string",
        "sales_attr2": "string",
        "quantity": "string",
        "remark": "string"
      },
      "image_match_text": "string"
    }
  ]
}
```

Single-row shape is also accepted under `product_sku_linking_result`.

The `standard_fields.product` value is source display text only. It is not a fallback for the final Excel `商品` column; only 商品匹配 `product` fills that column.

Legacy boundary:

- `product_definition_results` and `product_definition_result` are no longer accepted as report export inputs.
- If these legacy keys appear without `product_sku_linking_results` / `product_sku_linking_result`, export treats the row as missing 商品匹配结果 and routes it to `异常面单`.
- These legacy keys must not invoke old recognition, old match rules, field definitions, print-template configs, similarity grouping, or reverse parsing.
- New upstream work should write `product_sku_linking_results` / `product_sku_linking_result`.

## Tests

Stage81 export cleanup adds contract tests in `backend/tests/test_recognition_report_export.py` for:

- consuming 商品匹配结果 as the only active report input;
- converting missing 商品匹配 output into a pending exception row;
- preventing final product/SKU/image fields from being backfilled from five-field display values;
- preserving the legacy payload key only as a compatibility alias;
- preserving the target normal and exception sheet columns.

