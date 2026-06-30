# Stage81 商品匹配模块 Contract Cleanup

## Module Boundary

Changed module: 商品匹配模块.

Input contract:

```json
{
  "product": "string",
  "sales_attr1": "string",
  "sales_attr2": "string",
  "quantity": "string",
  "remark": "string"
}
```

Output contract: 商品匹配结果.

```json
{
  "product": "object | null",
  "sku": "object | null",
  "image": "object | null",
  "match_status": "matched | product_unmatched | sku_unmatched | image_unmatched | conflict",
  "exception_reason": "string"
}
```

This module must not consume raw waybill payloads, text blocks, print template structures, waybill similarity, cluster keys, or field-definition internals.

Current direction correction:

- The system cannot reliably decide same-type or similar waybills automatically.
- 商品匹配模块 must not be built around similar-waybill clustering, fingerprints, generalized inference, or a complex rule-learning workbench.
- 商品匹配模块 consumes only the five standard fields and user-confirmed 商品匹配规则.
- Unrecognized or ambiguous rows become exception/review rows.

Bulk processing boundary:

- Bulk input is a batch of five-field rows already produced by the Field Definition Module.
- This module may preview and match those five-field rows in bulk.
- This module must not reverse-parse waybills, decide similar waybills automatically, or modify field mappings.

## Progressive Rule Learning

This module learns only from user-confirmed five-field 商品匹配规则:

```text
product / sales_attr1 / sales_attr2 / quantity / remark
  -> product / SKU / image
```

First batches may require more manual matching setup. Later batches should automatically apply confirmed rules and surface only:

- new five-field patterns
- conflicts
- product/SKU/image misses
- rows whose existing rule has been disabled or revised

Learning requirements for every reusable 商品匹配规则:

- Explainable match fields: which of the five fields are used and what exact values are expected.
- Source five-field samples: examples that caused the user to create or revise the rule.
- Field sources: trace labels from the field definition output, not raw waybill parsing.
- Target assets: target product, SKU, and image asset ids.
- Preview result: matched count, conflict count, and unmatched count before saving.
- Conflict handling: ambiguous rules must produce `conflict`, not pick a target silently.
- Revision controls: rule can be disabled, revised, and previewed again.

Forbidden learning sources:

- hidden business guesses
- similar-waybill grouping
- raw waybill structure
- print-template fingerprints
- automatic product classification from text similarity

## Audit

### Keep

- `products`, `product_skus`, and `image_assets` resource APIs are business assets and should remain available.
- `product_assets.py` image upload/download helpers remain useful for maintaining SKU images.
- Product/SKU/stall/image fields in `entities.py` remain useful assets for the new module.
- Export/report code can keep consuming recognized result rows until the export module is reconnected to the new contract.

### Isolate

- `product_recognition.py` is the old match-rule recognition engine. It still reads projected standard detail rows and legacy rule payload fields such as `print_template_config_id`, `field_definition_match_fingerprint`, and `custom_*` fields. Treat it as legacy compatibility code, not the foundation for the new 商品匹配模块.
- `sku_matching.py` is tied to old rule payloads and `custom_*` field aliases. It should not be imported by the new 商品匹配模块 service.
- `resources.py` still contains unreachable `match_rules` validation and preview branches because `match_rules` is not included in `RESOURCE_ROUTES`. Keep it isolated until a separate cleanup can remove or archive it safely.
- `collector_runtime.py` still uses `MatchRule` for the old recognition preview/report path. Do not expand that dependency in 商品匹配模块 work.

Compatibility/cleanup boundary:

- Old `match_rules` may remain only as temporary compatibility for existing report preview/export paths.
- New 商品匹配模块 work must not save to `match_rules`, expose `/match-rules`, or translate five-field 商品匹配规则 into legacy rule payloads.
- Cleanup should happen in a separate task: first reconnect export to 商品匹配结果, then retire old `MatchRule` recognition paths when no runtime path depends on them.

## New Interface

Implemented with current technical route names:

- `GET /api/v1/product-sku-linking/contract`
- `POST /api/v1/product-sku-linking/preview`

The preview endpoint accepts:

```json
{
  "rows": [
    {
      "product": "鞋",
      "sales_attr1": "黑色",
      "sales_attr2": "42",
      "quantity": "2",
      "remark": ""
    }
  ],
  "linking_rules": [
    {
      "name": "鞋-黑色",
      "match_fields": ["product", "sales_attr1"],
      "field_values": {"product": "鞋", "sales_attr1": "黑色"},
      "field_sources": {"product": "field_definition.product", "sales_attr1": "field_definition.sales_attr1"},
      "source_samples": [{"product": "鞋", "sales_attr1": "黑色", "quantity": "2"}],
      "match_type": "exact",
      "product_id": 1,
      "sku_id": 2,
      "image_asset_id": null,
      "is_enabled": true,
      "revision_note": "用户确认黑色鞋关联黑色 SKU"
    }
  ]
}
```

The endpoint is read-only. It validates matches against active products, SKUs, and images in the current workspace, returns per-rule preview counts, and returns exceptions instead of guessing.

The endpoint does not accept waybill similarity, sample cluster keys, raw waybill structure, or field-definition metadata as matching input.

## Save Interface Draft

Suggested later table: `product_matching_rules`.

Suggested fields:

- `workspace_id`
- `tenant_id`
- `name`
- `match_fields`
- `field_values`
- `match_type`
- `product_id`
- `sku_id`
- `image_asset_id`
- `source_samples`
- `field_sources`
- `last_preview_summary`
- `revision_note`
- `revision`
- `priority`
- `is_enabled`

Suggested endpoints:

- `GET /api/v1/product-matching/rules`
- `POST /api/v1/product-matching/rules`
- `PATCH /api/v1/product-matching/rules/{id}`
- `DELETE /api/v1/product-matching/rules/{id}`
- `POST /api/v1/product-matching/preview`

Do not name these endpoints `/match-rules`, and do not write to the old `match_rules` table.

## Notes

- No hard-coded business mapping was added. Values such as `4.0` only match when the user explicitly sets them in `field_values`.
- No unmatched clustering workbench was added.
- No same-template or similar-waybill automatic product classification was added.
- Progressive learning is based only on user-confirmed five-field 商品匹配规则.
- Unmatched rows return `product_unmatched`; missing SKU/image bindings return `sku_unmatched` or `image_unmatched`.
