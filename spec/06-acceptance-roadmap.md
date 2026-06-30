# 06 Acceptance Roadmap

## P0: Clean Business Loop

Use task 18 or the latest real capture task.

Acceptance:

- collected waybill count is visible
- active rule pack is visible
- parser outputs order rows
- multi-product waybills create multiple child rows
- special orders are visible
- confirmed rows enter product matching
- matched rows and exception rows add up
- Excel normal sheet and `异常面单` sheet are generated

## P0 Verification Commands

Run focused backend tests:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backend_test.ps1 backend/tests/test_waybill_parser_service.py backend/tests/test_woda_printxml_parser.py backend/tests/test_waybill_reading.py backend/tests/test_order_row_drafts.py backend/tests/test_product_sku_linking.py backend/tests/test_product_matching_stage82b.py backend/tests/test_multi_item_export_rows.py backend/tests/test_recognition_report_export.py backend/tests/test_collector_client_runtime.py backend/tests/test_workspace_isolation.py
```

Run frontend type check:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\frontend_typecheck.ps1
```

## P1: User Experience

- Replace technical identifiers with business labels.
- Add keyboard navigation.
- Make row counts consistent across pages.
- Add quick filters for unresolved rows.
- Add clear import/export entry for rule packs.

## P2: Rule Pack Portability

- Export current shoe scenario as a rule pack.
- Import the pack into a clean workspace.
- Confirm no parsing runs without an active pack.
- Test another product scenario by switching packs.

## P3: Regression Suite

Maintain fixtures for:

- single-product normal waybill
- multi-product waybill
- special/manual order
- missing quantity
- noisy size text
- product unmatched
- SKU unmatched
- image unmatched
- conflict
