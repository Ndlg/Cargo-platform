# Export Module Maintenance Charter

## Ownership

This thread is responsible for the cargo-platform Export Module.

The Export Module exists to turn final 商品匹配结果 into the supplier reporting Excel workbook.

## Current Boundary

Input contract:

- Preferred source: `product_sku_linking_results` / `product_sku_linking_result`.
- Current new-chain source: `WaybillFieldMappingResult.source_trace.product_sku_linking_results`.
- Compatibility source: existing `StandardDetail.field_values.product_sku_linking_results`.
- Removed legacy alias: `product_definition_results` / `product_definition_result` are not valid export inputs and must fall into the missing-result pending/exception path if encountered.

Output contract:

- Excel workbook for supplier reporting.
- Normal report sheet columns:

```text
商品 / 销售属性1 / 图片 / 销售属性2 / 数量 / 备注 / 图片匹配文本
```

- Exception sheet:

```text
Sheet: 异常面单
Column: 图片匹配文本
```

Routing contract:

- `match_status == "matched"` enters the normal report sheet.
- Every non-`matched` status enters `异常面单`, including `product_unmatched`, `sku_unmatched`, `image_unmatched`, `conflict`, `pending`, missing 商品匹配结果, and any unknown status.
- The exception sheet is always present, even if it only contains the header.

## Explicit Non-Responsibilities

The Export Module must not:

- collect records;
- parse waybills;
- split or label text blocks;
- define or apply five-field mappings;
- match product, SKU, or image;
- learn rules;
- infer product/SKU/image from raw text, SKU names, product names, keywords, or similarity;
- revive old `match_rules`, `field_definitions`, `print_template_configs`, or waybill-structure APIs as report inputs;
- delete databases, Docker volumes, `cargo-platform-data`, or business data.

When a task needs upstream behavior, this thread should state the required upstream contract or blocking dependency instead of implementing that module.

## Current Entrypoints

Preferred report endpoints:

- `GET /api/v1/collector-control/tasks/{task_id}/report-preview`
- `GET /api/v1/collector-control/tasks/{task_id}/report-workbook`

Compatibility aliases:

- `GET /api/v1/collector-control/tasks/{task_id}/recognition-preview`
- `GET /api/v1/collector-control/tasks/{task_id}/recognition-report`

Frontend entrypoint:

- `frontend/src/views/workbench/ExportCenterView.vue`

Core backend implementation currently lives in:

- `backend/app/api/routes/collector_runtime.py`

The location is a known maintenance compromise. Export code should eventually move into a dedicated export router/service.

## Recent Stable Behavior

The current implementation supports:

- consuming Stage82/Stage83 商品匹配结果 from field-mapping result traces;
- expanding one waybill with multiple 商品匹配 result rows into multiple report rows;
- preserving uncovered old details as `pending` exceptions during partial migration;
- normalizing explicit quantity formats such as `*1`, `1件`, `1 件`, `x1`, and `×1`;
- inserting image assets when an image asset id is available;
- keeping workbook structure stable with an always-present `异常面单` sheet.

## Recent Validation

Primary regression command:

```powershell
scripts\backend_test.ps1 backend/tests/test_recognition_report_export.py
```

Frontend validation when touching export UI/API calls:

```powershell
scripts\frontend_typecheck.ps1
```

Current export regression coverage includes:

- fixed normal report headers;
- fixed exception sheet name and single-column header;
- matched rows entering the normal report;
- non-`matched` rows entering `异常面单`;
- missing 商品匹配 results becoming `pending`;
- new-chain multi-item result expansion;
- quantity normalization;
- empty exception sheet preservation;
- active export reader not calling legacy match-rule recognition.

## Near-Term Maintenance Focus

P0: Protect the report contract.

- Keep the normal sheet and exception sheet stable.
- Keep all non-`matched` rows out of the normal report.
- Never let export guess missing product, SKU, image, or sales attributes.

P1: Improve maintainability without changing business behavior.

- Move report export code out of `collector_runtime.py` into a dedicated export service/router.
- Keep old `recognition-*` URLs as aliases until all callers migrate.
- Rename frontend service functions away from `Recognition` when safe.

P2: Improve real-world confidence.

- Add an end-to-end workbook download test through the API.
- Add image insertion tests for missing, invalid, and valid image assets.
- Add UI smoke coverage for Export Center preview/download.

P3: Clean legacy affordances.

- Mark `standard-document` as legacy in UI/docs if it remains available.
- Remove dead legacy recognition code only after no route depends on it.

## Task Intake Checklist

For every future task, answer these before changing code:

1. Is this truly an Export Module task?
2. What exact 商品匹配结果 shape is being consumed?
3. Does the task preserve the fixed workbook contract?
4. Are all unresolved rows still routed to `异常面单`?
5. Does the change avoid field definition, waybill parsing, and product/SKU/image matching?
6. What regression test proves the export contract still holds?

If any answer is unclear, ask for or document the upstream contract instead of guessing.
