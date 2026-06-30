# Stage82C Export Minimal Business Loop

## Module Boundary

Changed module: Export Module.

Input: 商品匹配结果 written by Stage82B to `standard_details.field_values.product_sku_linking_results`.

Output: supplier reporting Excel workbook.

The export module must not:

- parse waybill raw text;
- change field definition rules or results;
- create, learn, or apply 商品匹配规则;
- query or revive old `match_rules`, `field_definitions`, or waybill-structure routes as report inputs;
- infer product, SKU, image, or sales attributes from similar waybills, product names, SKU names, keywords, or raw payloads.

## Active Export Entrypoints

- `raw-document`: retained raw collection export.
- `standard-document`: legacy dynamic field document; not the Stage82 supplier report contract.
- `recognition-preview`: compatibility URL. Active data source is 商品匹配结果.
- `recognition-report`: compatibility URL/download name. Active data source is 商品匹配结果.

The `recognition-*` names are compatibility names only; they do not mean old recognition-report logic is active.

## Input Contract

Preferred stored shape:

```json
{
  "product_sku_linking_results": [
    {
      "standard_fields": {
        "product": "string",
        "sales_attr1": "string",
        "sales_attr2": "string",
        "quantity": "string",
        "remark": "string"
      },
      "product": "string",
      "product_id": 1,
      "sku": "string",
      "sku_id": 1,
      "image": "string",
      "image_asset_id": 1,
      "match_status": "matched | product_unmatched | sku_unmatched | image_unmatched | conflict | pending | unmatched",
      "exception_reason": "string",
      "image_match_text": "string",
      "trace": {}
    }
  ]
}
```

Single-row shape is accepted as `product_sku_linking_result`.

Legacy `product_definition_results` and `product_definition_result` keys are no longer report export inputs. If they appear without the new 商品匹配 result keys, export treats the row as missing 商品匹配结果 and sends it to the exception flow instead of trying old matching behavior.

## Sheet Routing

Normal report sheet:

- includes only rows whose `match_status` is exactly `matched`;
- columns remain exactly `商品 / 销售属性1 / 图片 / 销售属性2 / 数量 / 备注 / 图片匹配文本`;
- `商品`, `SKU`, and image fields come only from 商品匹配结果;
- `销售属性1`, `销售属性2`, `数量`, and `备注` come only from `standard_fields`.

Exception sheet:

- sheet name: `异常面单`;
- single column: `图片匹配文本`;
- includes `product_unmatched`, `sku_unmatched`, `image_unmatched`, `conflict`, `pending`, `unmatched`, missing 商品匹配结果, and any other non-`matched` status.

## Frontend Scope

The existing export center remains the entrypoint. Stage82C only updates status copy and exception counting so users understand that export consumes 商品匹配结果 and that missing results require field definition plus 商品匹配 first.

No new complex export page is introduced in Stage82C.

## Tests

Stage82C coverage in `backend/tests/test_recognition_report_export.py` verifies:

- matched 商品匹配 results enter the normal sheet;
- missing 商品匹配 results become pending exceptions;
- all non-`matched` statuses route to `异常面单`;
- normal and exception headers remain unchanged;
- active report reading does not call legacy match-rule recognition.
