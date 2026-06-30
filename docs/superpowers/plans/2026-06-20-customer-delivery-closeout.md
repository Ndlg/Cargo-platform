# Customer Delivery Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make cargo-platform usable for a customer to turn collected waybill/order data into a supplier reporting Excel workbook.

**Architecture:** Keep the clean pipeline defined in `AGENTS.md`: collection -> waybill parsing/order rows -> product/SKU/image matching -> exception handling -> export. Recognition behavior must be controlled by an active rule pack; no hidden default recognition. User-facing pages must show business rows and consistent counts rather than technical IDs or stale internal state.

**Tech Stack:** FastAPI backend, Vue 3 + Element Plus frontend, Docker Compose runtime, PowerShell verification scripts, task 18 runtime dataset for business self-test.

---

## Delivery Acceptance Gates

The project is customer-deliverable only when all gates below pass on runtime data:

1. Login and workspace selection work for the business user and admin user.
2. Business navigation has only customer-facing work: `业务首页`, `采集记录`, `面单解析`, `异常处理`, `导出中心`.
3. Admin navigation contains configuration work: `商品/SKU`, `商品匹配`, `识别规则包`, `导出表头`, `系统设置`.
4. No customer-facing page shows raw database IDs, source paths, or JSON as the primary workflow.
5. `面单解析` shows parent waybill count, order-row count, special count, and review count from the same order-row API consumed downstream.
6. `商品匹配` consumes parsed order rows only, defaults to the latest task, auto-checks enabled matching rules, and never starts with misleading all-zero cards when data exists.
7. `异常处理` shows only rows that need action; special rows are counted separately as normal skip rows.
8. `导出中心` uses the same recognition preview contract as exceptions and product matching; normal rows + exception rows + special rows must reconcile with order rows.
9. Exported workbook has the expected normal sheet columns and `异常面单` sheet.
10. If no active recognition rule pack exists, parsing/recognition returns `rule_pack_missing` and the UI tells the user to import or enable a rule pack.

## Current Runtime Baseline

Task 18 is the primary business self-test dataset.

Expected after the latest fixes:

- Parent waybills: 161
- Order rows: 164
- Special rows: 5
- Matched rows: 121
- Product unmatched: 33
- SKU unmatched: 5
- Image unmatched: 0
- Conflict: 0
- Actionable exceptions: 38

## Task 1: Runtime Delivery Audit Harness

**Files:**
- Create: `scripts/customer_delivery_audit.ps1`
- Test: runtime command output

- [ ] **Step 1: Create a read-only audit script**

Create `scripts/customer_delivery_audit.ps1`:

```powershell
$ErrorActionPreference = "Stop"

$BaseUrl = $env:CARGO_PLATFORM_API_BASE
if (-not $BaseUrl) {
  $BaseUrl = "http://127.0.0.1:8000/api/v1"
}

$FrontendUrl = $env:CARGO_PLATFORM_FRONTEND_BASE
if (-not $FrontendUrl) {
  $FrontendUrl = "http://127.0.0.1:5173"
}

$TaskId = if ($env:CARGO_PLATFORM_AUDIT_TASK_ID) { [int]$env:CARGO_PLATFORM_AUDIT_TASK_ID } else { 18 }

$Login = Invoke-RestMethod -Method Post -Uri "$BaseUrl/auth/login" -ContentType "application/json" -Body '{"username":"admin","password":"admin123"}'
$Headers = @{ Authorization = "Bearer $($Login.access_token)" }

$Health = Invoke-RestMethod -Uri "$BaseUrl/health"
$Drafts = Invoke-RestMethod -Headers $Headers -Uri "$BaseUrl/order-row-drafts/tasks/$TaskId`?limit=5000"
$Report = Invoke-RestMethod -Headers $Headers -Uri "$BaseUrl/collector-control/tasks/$TaskId/report-preview"
$PreviewBody = @{
  scope = @{ scope_type = "current_batch"; task_id = $TaskId; confirmed_by_user = $true }
  include_saved_rules = $true
  rule_ids = @()
} | ConvertTo-Json -Depth 10
$ProductPreview = Invoke-RestMethod -Method Post -Headers $Headers -ContentType "application/json" -Uri "$BaseUrl/product-sku-linking/preview" -Body $PreviewBody

$Pages = @("/", "/waybill-batches", "/exceptions", "/exports", "/admin/product-matching", "/admin/recognition-rule-packs") | ForEach-Object {
  $Url = "$FrontendUrl$_"
  try {
    $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
    [pscustomobject]@{ path = $_; status = [int]$Response.StatusCode }
  } catch {
    [pscustomobject]@{ path = $_; status = $_.Exception.Message }
  }
}

$ReportExceptions =
  [int]($Report.summary.product_unmatched ?? 0) +
  [int]($Report.summary.sku_unmatched ?? 0) +
  [int]($Report.summary.image_unmatched ?? 0) +
  [int]($Report.summary.conflict ?? 0) +
  [int]($Report.summary.pending ?? 0) +
  [int]($Report.summary.unmatched ?? 0)

$Result = [ordered]@{
  health = $Health.status
  task_id = $TaskId
  draft_parent = $Drafts.summary.parent_waybill_count
  draft_rows = $Drafts.summary.child_waybill_count
  draft_special = $Drafts.summary.special_count
  draft_review = $Drafts.summary.needs_review_count
  report_total = $Report.summary.total
  report_matched = $Report.summary.matched
  report_exceptions = $ReportExceptions
  report_special = $Report.summary.special
  product_total = $ProductPreview.summary.total
  product_matched = $ProductPreview.summary.matched
  product_product_unmatched = $ProductPreview.summary.product_unmatched
  product_sku_unmatched = $ProductPreview.summary.sku_unmatched
  product_image_unmatched = $ProductPreview.summary.image_unmatched
  product_conflict = $ProductPreview.summary.conflict
  product_special = $ProductPreview.summary.special
  pages = $Pages
}

$Result | ConvertTo-Json -Depth 8
```

- [ ] **Step 2: Run the audit script**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\customer_delivery_audit.ps1
```

Expected:

- `health` is `ok`.
- `draft_rows`, `report_total`, and `product_total` all equal `164`.
- `report_matched` and `product_matched` both equal `121`.
- `report_special` and `product_special` both equal `5`.
- All listed pages return HTTP 200.

## Task 2: Product Matching Page Must Default To Latest Task

**Files:**
- Modify: `frontend/src/views/workbench/ProductMatchingView.vue`
- Test: browser route `/admin/product-matching`

- [x] **Step 1: Reproduce**

Open `/admin/product-matching` with an authenticated browser session.

Observed before fix:

- Page auto-selected old task `#4`.
- Preview showed `1` row instead of task 18's `164` rows.

- [x] **Step 2: Fix default task selection**

Use a sorted task list by descending task id and default to the first sorted task.

Expected behavior:

- The page selects `业务采集 2026/6/16 14:23:20 #18`.
- The page auto-runs saved-rule preview.

- [x] **Step 3: Verify**

Expected visible counts:

- `121 已匹配`
- `33 商品未命中`
- `5 SKU 未命中`
- `5 特殊单`

## Task 3: Product Matching Page Must Not Show Misleading Empty State

**Files:**
- Modify: `frontend/src/views/workbench/ProductMatchingView.vue`
- Test: browser route `/admin/product-matching`

- [x] **Step 1: Reproduce**

Observed before fix:

- Page initially displayed all summary cards as `0`.
- Table showed `暂无数据` despite backend preview having 164 rows.

- [x] **Step 2: Fix**

The page must:

- Auto-run saved-rule preview when a task is selected.
- Hide summary/table until preview has actually run.
- Show an informational message instead of zero cards before preview.

- [x] **Step 3: Verify**

Expected:

- No misleading `暂无数据` on first load when task 18 has data.
- No horizontal page overflow at 1440px viewport.

## Task 4: Special Rows Must Not Become Product Exceptions

**Files:**
- Modify: `backend/app/services/product_sku_linking.py`
- Modify: `backend/app/api/routes/product_sku_linking.py`
- Modify: `frontend/src/views/workbench/ProductMatchingView.vue`
- Modify: `frontend/src/views/workbench/ExceptionsView.vue`
- Modify: `frontend/src/views/workbench/ExportCenterView.vue`
- Test: `backend/tests/test_product_sku_linking.py`

- [x] **Step 1: Write failing backend test**

Test special order rows with `_order_row_status = "special"` passed into product matching.

Expected before fix:

- The test fails because the row becomes `product_unmatched`.

- [x] **Step 2: Implement special passthrough**

Product matching must return:

- `match_status = "special"`
- `exception_reason = "特殊单不参与商品、SKU、图片匹配。"`

- [x] **Step 3: Update UI**

Product matching, exceptions, and export pages must count special rows separately.

- [x] **Step 4: Verify**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backend_test.ps1 backend\tests\test_product_sku_linking.py backend\tests\test_order_row_drafts.py backend\tests\test_recognition_report_export.py
```

Expected:

- `37 passed`.

## Task 5: Customer-Facing Navigation Audit

**Files:**
- Inspect: `frontend/src/router/clientRoutes.ts`
- Inspect: `frontend/src/router/clientAdminRoutes.ts`
- Inspect: `frontend/src/layouts/ClientLayout.vue`
- Inspect: `frontend/src/layouts/ClientAdminLayout.vue`

- [ ] **Step 1: Verify business nav**

Business nav must show:

- `业务首页`
- `采集记录`
- `面单解析`
- `异常处理`
- `导出中心`

Business nav must not show:

- `商品匹配`
- `识别规则包`

- [ ] **Step 2: Verify admin nav**

Admin nav must show:

- `商品/SKU`
- `商品匹配`
- `识别规则包`
- `导出表头`
- `系统设置`

- [ ] **Step 3: Verify redirects**

Legacy or duplicate route `/order-rows` must redirect to `/waybill-batches`.
Legacy `/product-matching` must redirect to `/admin/product-matching`.

## Task 6: Exception Reduction Loop

**Files:**
- Inspect: product assets, SKU assets, product matching learning records through API.
- Modify only when behavior is rule-pack or user-confirmed-rule controlled.

- [ ] **Step 1: Query current grouped exceptions**

Use product matching preview rows and group by:

- `match_status`
- `product`
- `sales_attr1`
- `sales_attr2`

- [ ] **Step 2: Classify exceptions**

Classify each group:

- Product asset missing
- Product matching learning record missing
- SKU asset/binding missing
- Parsing/order row issue
- Special row that should be skipped

- [ ] **Step 3: Fix only safe groups**

Allowed:

- Fix parser bugs when a row is clearly parsed wrong.
- Fix SKU matching code when an existing SKU should match but does not.
- Improve exception UI if it hides the reason.

Not allowed:

- Invent business product meaning without user-confirmed product asset or learning record.
- Add hidden default guesses.

## Task 7: Final Customer Delivery Verification

**Files:**
- Run: `scripts/customer_delivery_audit.ps1`
- Run: `scripts/frontend_typecheck.ps1`
- Run: focused backend tests
- Optional inspect: exported workbook

- [ ] **Step 1: Run frontend typecheck**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\frontend_typecheck.ps1
```

Expected: success.

- [ ] **Step 2: Run backend regression**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backend_test.ps1 backend\tests\test_product_sku_linking.py backend\tests\test_order_row_drafts.py backend\tests\test_recognition_report_export.py backend\tests\test_waybill_parser_service.py
```

Expected: success.

- [ ] **Step 3: Deploy if code changed**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy_business_containers.ps1
```

Expected:

- `cargo-platform-data` is reused.
- Backend health is OK.
- Business containers are Up.

- [ ] **Step 4: Run runtime audit**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\customer_delivery_audit.ps1
```

Expected:

- Runtime numbers reconcile.
- Pages return 200.

## Known Remaining Non-Blocking Work

- The admin product matching form is still dense. It is usable for internal/admin setup, but needs a later UX pass before non-technical customers can configure products alone.
- Remaining task 18 exceptions are mostly missing product/SKU learning records or SKU bindings. Do not silently guess these into products.
- Export currently reports 38 actionable exception rows for task 18; this is acceptable only if the customer understands they must complete matching assets/rules before final supplier reporting.

