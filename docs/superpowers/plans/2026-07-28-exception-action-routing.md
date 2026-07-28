# Exception Action Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every exception action button state-specific, correctly named, and routed to the page that can actually resolve that exception.

**Architecture:** Keep exception action policy in one explicit frontend map inside `ExceptionsView.vue`. Pass the current row and matched rule identity through route query parameters; `ProductMatchingView.vue` opens that existing reusable rule for SKU ambiguity instead of showing a generic blank editor.

**Tech Stack:** Vue 3, TypeScript, Vue Router, Element Plus, pytest source-contract regression, Docker/Nginx validation UI.

## Global Constraints

- Implement and deploy only on `6173`.
- Do not update `5173` or restart production UI, backend, parser, or collectors.
- Do not add per-row editing, confirmation, exclusion, approval, or result overrides.
- Unknown exception statuses must not receive a default navigation button.
- Keep the previous `6173` UI container and a Git tag for immediate rollback.

---

### Task 1: Centralize exception status, advice, action label, and target

**Files:**
- Modify: `frontend/src/views/workbench/ExceptionsView.vue`
- Test: `backend/tests/test_product_catalog_frontend_performance.py`

**Interfaces:**
- Consumes: `RecognitionPreviewRow.status`, `product_id`, `rule_id`, and the current `selectedTaskId`.
- Produces: `exceptionDefinition(status)`, `repairRoute(row)`, and `goToRepair(row)` used by the exception table.

- [ ] **Step 1: Add a failing source-contract test**

```python
def test_exception_actions_have_explicit_status_routes_without_default_jump() -> None:
    source = EXCEPTIONS_VIEW.read_text(encoding="utf-8")

    assert "sku_ambiguous" in source
    assert "指定 SKU 匹配" in source
    assert "暂无处理入口" in source
    assert "return '查看识别结果'" not in source
    assert "path: '/admin/product-matching'" in source
    assert "path: '/admin/products'" in source
    assert "path: '/waybill-batches'" in source
```

- [ ] **Step 2: Run the targeted test and confirm it fails**

Run:

```powershell
pwsh.exe -NoLogo -NoProfile -File scripts/backend_test.ps1 backend/tests/test_product_catalog_frontend_performance.py -q
```

Expected: failure because `sku_ambiguous` is not an explicit exception action and the fallback “查看识别结果” still exists.

- [ ] **Step 3: Replace independent branches with one explicit definition map**

Use an `ExceptionDefinition` record containing:

```ts
type RepairTarget = 'product-matching' | 'product-assets' | 'order-rows' | null

type ExceptionDefinition = {
  label: string
  advice: string
  actionLabel: string
  target: RepairTarget
}
```

The exact mapping must be:

```ts
const exceptionDefinitions: Record<ExceptionStatus, ExceptionDefinition> = {
  product_unmatched: {
    label: '商品未命中',
    advice: '补商品关键词或商品匹配规则',
    actionLabel: '补商品规则',
    target: 'product-matching',
  },
  sku_unmatched: {
    label: 'SKU未命中',
    advice: '维护当前商品的 SKU 关键词、绑定和规格字段',
    actionLabel: '维护 SKU',
    target: 'product-assets',
  },
  sku_ambiguous: {
    label: 'SKU多候选',
    advice: '为当前商品行指定可复用的 SKU 匹配规则',
    actionLabel: '指定 SKU 匹配',
    target: 'product-matching',
  },
  image_unmatched: {
    label: '图片未命中',
    advice: '为当前商品或 SKU 补图片',
    actionLabel: '补 SKU 图片',
    target: 'product-assets',
  },
  conflict: {
    label: '冲突',
    advice: '检查并修订同时命中的匹配规则',
    actionLabel: '检查冲突规则',
    target: 'product-matching',
  },
  pending: {
    label: '待处理',
    advice: '检查当前采集轮次的面单解析结果',
    actionLabel: '检查解析结果',
    target: 'order-rows',
  },
  unmatched: {
    label: '未匹配',
    advice: '补当前商品行的商品匹配规则',
    actionLabel: '补商品规则',
    target: 'product-matching',
  },
  special: {
    label: '特殊单',
    advice: '特殊单无需处理',
    actionLabel: '',
    target: null,
  },
}
```

`repairRoute(row)` returns `null` for unknown statuses and for product-asset targets without `product_id`. The template displays `暂无处理入口` instead of a button when no target is available.

- [ ] **Step 4: Pass the matched reusable rule identity**

Add `rule_id` to `repairQuery(row)` when present:

```ts
if (row.rule_id) query.rule_id = String(row.rule_id)
if (row.status === 'sku_ambiguous') query.focus = 'sku'
```

- [ ] **Step 5: Run the targeted test**

Run the command from Step 2.

Expected: all tests in `test_product_catalog_frontend_performance.py` pass.

### Task 2: Open the relevant reusable rule for SKU ambiguity

**Files:**
- Modify: `frontend/src/views/workbench/ProductMatchingView.vue`
- Test: `backend/tests/test_product_catalog_frontend_performance.py`

**Interfaces:**
- Consumes: route query `rule_id` and `focus=sku` from Task 1.
- Produces: an editor loaded with the matched existing rule rather than a blank rule.

- [ ] **Step 1: Extend the regression test**

Add these assertions to the test from Task 1:

```python
matching_source = PRODUCT_MATCHING_VIEW.read_text(encoding="utf-8")
assert "route.query.rule_id" in matching_source
assert "route.query.focus" in matching_source
assert "sku-section" in matching_source
```

- [ ] **Step 2: Run the targeted test and confirm it fails**

Run:

```powershell
pwsh.exe -NoLogo -NoProfile -File scripts/backend_test.ps1 backend/tests/test_product_catalog_frontend_performance.py -q
```

Expected: failure because the matching page does not yet consume `rule_id` or `focus`.

- [ ] **Step 3: Load the route-selected rule after rules are available**

After `rules.value = ruleResponse.rules`, locate `queryPositiveInt(route.query.rule_id)`. If the rule exists, call the existing `editRule(rule)` so the editor becomes a revision of that reusable rule.

Do not create a manual row override and do not save automatically.

- [ ] **Step 4: Focus the SKU section**

Give the existing SKU section a stable `ref="skuSection"` and, when `route.query.focus === 'sku'`, call `scrollIntoView({ behavior: 'smooth', block: 'start' })` after the selected rule and its SKU options load.

- [ ] **Step 5: Run targeted regression and frontend checks**

Run:

```powershell
pwsh.exe -NoLogo -NoProfile -File scripts/backend_test.ps1 backend/tests/test_product_catalog_frontend_performance.py -q
Set-Location frontend
npm run typecheck
npm run build
```

Expected: targeted tests pass, Vue TypeScript reports zero errors, and the production frontend build exits `0`.

### Task 3: Deploy and verify only the isolated validation UI

**Files:**
- Modify: `ops/validation-stages/20260728-night/README.md`

**Interfaces:**
- Consumes: committed frontend source from Tasks 1 and 2.
- Produces: a tagged validation UI image on `6173`, with the stage 15 UI retained as an exited rollback container.

- [ ] **Step 1: Commit and tag the implementation**

Commit the implementation and create an annotated stage tag at that commit.

- [ ] **Step 2: Build the tenant UI image**

Build only `frontend/Dockerfile` with `BUILD_COMMAND=build:tenant` and `DIST_DIR=dist`.

- [ ] **Step 3: Replace only `cargo-platform-validation-ui`**

Stop and rename the current validation UI, then start the new image on `127.0.0.1:6173` in network `cargo-platform-validation-20260727-014134`.

Do not replace `cargo-platform-validation-backend`, `cargo-platform-validation-parser`, or either validation data volume.

- [ ] **Step 4: Browser-test every action mapping**

On `6173`, verify:

- `SKU多候选` shows `指定 SKU 匹配`, opens the existing rule, and focuses the SKU section.
- `SKU未命中` and `图片未命中` open the selected product in 商品/SKU.
- `商品未命中`, `冲突`, and `未匹配` open 商品匹配 with current-row context.
- `待处理` opens the selected collection round in 面单解析.
- Unknown status has no button and displays `暂无处理入口`.

- [ ] **Step 5: Verify isolation and record rollback**

Confirm production UI, backend, and parser container IDs and start times are unchanged. Record the current validation UI image, rollback container, Git tag, test totals, and browser result in the validation-stage README, then commit and push the documentation.
