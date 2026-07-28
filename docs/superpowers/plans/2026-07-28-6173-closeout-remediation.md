# 6173 业务收口整改 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不操作 `5173`、现场数据库和三台采集器的前提下，把错误产品口径、导出准入、剩余页面延迟、规则包虚假配置和采集器意外退出风险收口到 `6173` 验证环境。

**Architecture:** 保持现有实时计算链路：原始记录由独立解析服务按启用规则包生成商品行，后端每次读取时使用最新商品规则计算匹配和导出。每项整改独立提交和打标签；最终只替换 `cargo-platform-validation-*` 容器，并保留 stage 09 回退容器。

**Tech Stack:** FastAPI、SQLAlchemy、pytest、Vue 3、TypeScript、Vite、Docker。

## Global Constraints

- 不停止、重启、替换或修改 `5173`、现场后端、现场解析服务和三台采集器。
- 不删除或重建 `cargo-platform-validation-data-20260727-014134`。
- 不恢复订单行编辑、确认、排除、审批或手工应用结果。
- 同内容打印不去重；多商品面单必须拆成多个子行。
- 每次行为修改先写失败测试并确认失败，再写最小实现。
- 每项提交前运行目标测试；最终运行全量后端测试、前端类型检查、生产构建和 6173 历史覆盖扫描。

---

### Task 1: 删除手工应用路线并修正项目口径

**Files:**
- Modify: `README.md`
- Modify: `backend/app/api/routes/product_sku_linking.py`
- Modify: `backend/app/services/product_sku_linking.py`
- Modify: `frontend/src/services/api.ts`
- Modify: `backend/tests/test_product_sku_linking.py`

**Interfaces:**
- Consumes: 现有 `/product-sku-linking/preview` 和规则增删改接口。
- Produces: 只读实时预览/导出链路；`POST /product-sku-linking/apply` 返回 404。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_product_sku_linking.py` 增加真实路由测试，登录后请求：

```python
response = client.post(
    "/api/v1/product-sku-linking/apply",
    headers=auth_headers,
    json={"scope": {"scope_type": "current_batch", "task_id": 1}},
)
assert response.status_code == 404
```

同时断言 `GET /api/v1/product-sku-linking/contract` 的端点列表不再包含 `POST /product-sku-linking/apply`。

- [ ] **Step 2: 运行目标测试并确认因旧接口仍存在而失败**

Run: `scripts/backend_test.ps1 backend/tests/test_product_sku_linking.py -q`

Expected: 路由返回非 404 或合同仍声明 apply。

- [ ] **Step 3: 写最小实现**

删除 `ProductSkuLinkingApplyRequest`、`apply_product_sku_linking_results()`、合同中的 apply 端点，以及前端未被调用的 `ProductMatchingApplyResponse` 和 `applyProductMatching()`。将 `README.md` 改为规则生成只读订单行、异常修规则/资产后实时重算的现行口径。

- [ ] **Step 4: 运行目标测试和前端类型检查**

Run:

```powershell
scripts/backend_test.ps1 backend/tests/test_product_sku_linking.py -q
scripts/frontend_typecheck.ps1
```

Expected: 全部通过。

- [ ] **Step 5: 提交并固定标签**

```powershell
git commit -m "refactor: remove manual product matching apply"
git tag -a validation-stage-10-contract-closeout-20260728 -m "6173 stage 10 contract closeout"
```

---

### Task 2: 固定正常 Excel 的准入条件

**Files:**
- Modify: `backend/app/api/routes/collector_runtime.py`
- Modify: `backend/tests/test_recognition_report_export.py`

**Interfaces:**
- Consumes: `RecognitionPreviewRow.status`。
- Produces: `recognition_report_row_is_exportable(row) -> bool`；只有 `matched` 行进入正常表，缺图片和空销售属性不会被额外剔除。

- [ ] **Step 1: 写失败测试**

增加三项字面量样本：

```python
assert recognition_report_row_is_exportable({
    "status": "matched",
    "sales_attr1_text": "",
    "sales_attr2_text": "",
})
assert not recognition_report_row_is_exportable({"status": "sku_unmatched"})
assert not recognition_report_row_is_exportable({"status": "product_unmatched"})
```

- [ ] **Step 2: 运行测试并确认 matched 空属性样本失败**

Run: `scripts/backend_test.ps1 backend/tests/test_recognition_report_export.py -q`

- [ ] **Step 3: 写最小实现**

```python
def recognition_report_row_is_exportable(row: dict[str, Any]) -> bool:
    return row.get("status") == "matched"
```

- [ ] **Step 4: 运行导出与覆盖测试**

Run:

```powershell
scripts/backend_test.ps1 backend/tests/test_recognition_report_export.py backend/tests/test_regression_coverage.py -q
```

- [ ] **Step 5: 提交并固定标签**

```powershell
git commit -m "fix: export every matched product row"
git tag -a validation-stage-11-export-eligibility-20260728 -m "6173 stage 11 export eligibility"
```

---

### Task 3: 删除剩余页面的全历史重算

**Files:**
- Modify: `frontend/src/views/client/ClientHomeView.vue`
- Modify: `frontend/src/views/workbench/CaptureRecordsView.vue`
- Modify: `frontend/src/views/workbench/WaybillBatchesView.vue`
- Modify: `backend/tests/test_product_catalog_frontend_performance.py`

**Interfaces:**
- Consumes: `/capture-tasks` 的 `limit` 和 `include_waybill_counts`。
- Produces: 首页只请求最新一轮；采集记录只请求实际展示的六轮；面单解析任务选择器不计算历史面单数。

- [ ] **Step 1: 写失败回归测试**

在现有前端性能合同测试中断言：

```python
assert "/capture-tasks?limit=1" in home_source
assert "/capture-tasks?limit=6" in capture_source
assert "include_waybill_counts=false" in batches_source
```

- [ ] **Step 2: 运行测试并确认旧的 `limit=2000` 使测试失败**

Run: `scripts/backend_test.ps1 backend/tests/test_product_catalog_frontend_performance.py -q`

- [ ] **Step 3: 修改三处请求**

只替换查询参数，不增加缓存、状态层或新依赖。

- [ ] **Step 4: 运行目标测试、类型检查和构建**

Run:

```powershell
scripts/backend_test.ps1 backend/tests/test_product_catalog_frontend_performance.py -q
scripts/frontend_typecheck.ps1
cd frontend
npm run build
```

- [ ] **Step 5: 提交并固定标签**

```powershell
git commit -m "perf: bound remaining capture task queries"
git tag -a validation-stage-12-page-query-closeout-20260728 -m "6173 stage 12 page query closeout"
```

---

### Task 4: 让规则包中现有子规则真实生效

**Files:**
- Modify: `rule-packs/current-user-shoes.v1.json`
- Modify: `services/waybill-parser/service_app/main.py`
- Modify: `services/waybill-parser/service_app/order_row_engine.py`
- Modify: `frontend/src/views/workbench/RecognitionRulePacksView.vue`
- Modify: `backend/tests/test_waybill_parser_service.py`
- Modify: `backend/tests/test_order_row_drafts.py`

**Interfaces:**
- Consumes: `parser_policy.quantity`、`label_cleanup`、`size_normalization`、`manual_label_only`、`non_shoe`。
- Produces: 校验、说明和解析均报告并使用这些子规则；管理员通过业务字段编辑，不编辑原始 JSON。

- [ ] **Step 1: 写失败解析测试**

使用独立字面量规则包验证：

```python
parser_policy["quantity"]["default_if_missing"] = 2
assert parsed_row["quantity"] == 2

parser_policy["manual_label_only"]["allow_empty_product"] = False
assert parsed_row["status"] == "needs_review"

parser_policy["non_shoe"]["allow_non_numeric_sales_attr2"] = False
assert non_numeric_row["status"] == "needs_review"
```

并断言 validate/explain 的 `policy_usage.applied` 包含五个现有子规则。

- [ ] **Step 2: 运行解析服务测试并确认失败**

Run:

```powershell
scripts/backend_test.ps1 backend/tests/test_waybill_parser_service.py backend/tests/test_order_row_drafts.py -q
```

- [ ] **Step 3: 写最小策略应用函数**

在 `parse_item_text()` 的统一返回边界应用规则包：

```python
def apply_output_policy(fields, original_text, fallback_quantity_text, parser_policy):
    # 缺数量时使用 quantity.default_if_missing
    # label_cleanup.strip_prefixes 清理管理员声明的字段前缀
    # size_normalization.strip_purchase_hint 控制尺码提示清理
    # manual_label_only.allow_empty_product 控制纯属性行
    # non_shoe.allow_non_numeric_sales_attr2 控制非数字规格是否可用
    return fields
```

保留 `shoe_waybill_v1` 解析算法，不复制解析器，不增加第二套隐式兜底。

- [ ] **Step 4: 增加业务化子规则编辑**

现有弹窗增加数量默认值、字段前缀、尺码提示清理、允许纯属性单、允许非数字规格五项；保存仍走规则包导入接口并保留其他 payload 字段。

- [ ] **Step 5: 运行解析测试、前端类型检查和固定样本测试**

Run:

```powershell
scripts/backend_test.ps1 backend/tests/test_waybill_parser_service.py backend/tests/test_order_row_drafts.py backend/tests/test_woda_printxml_parser.py backend/tests/test_douyin_product_info.py -q
scripts/frontend_typecheck.ps1
```

- [ ] **Step 6: 提交并固定标签**

```powershell
git commit -m "feat: apply editable recognition subrules"
git tag -a validation-stage-13-rule-pack-subrules-20260728 -m "6173 stage 13 editable rule subrules"
```

---

### Task 5: 防止采集器因未分类异常直接退出

**Files:**
- Modify: `collector-client/client.py`
- Modify: `frontend/src/services/api.ts`
- Modify: `backend/tests/test_collector_client_runtime.py`

**Interfaces:**
- Consumes: 采集轮询中的非 HTTP、网络、SQLite 异常。
- Produces: `last_reconnect_reason="unexpected"`，写日志并继续下一轮；不弹出新窗口。

- [ ] **Step 1: 写失败单轮测试**

将一次轮询封装成可直接调用的 `poll_collector_safely(...)`，测试让真实函数接收一个抛出 `ValueError("bad payload")` 的轮询函数，断言返回原配置、状态原因为 `unexpected`，且没有向上抛出。

- [ ] **Step 2: 运行测试并确认函数不存在或异常外溢**

Run: `scripts/backend_test.ps1 backend/tests/test_collector_client_runtime.py -q`

- [ ] **Step 3: 写最小实现并复用现有分类处理**

把现有四个 `except` 分支移入 `poll_collector_safely`，最后增加：

```python
except Exception as exc:
    state.last_reconnect_reason = "unexpected"
    notice.warning("unexpected", "collector unexpected error; retrying in background: %s", exc)
    return config
```

主循环只调用该函数和 `save_state_safely`。

- [ ] **Step 4: 运行采集器测试**

Run:

```powershell
scripts/backend_test.ps1 backend/tests/test_collector_client_runtime.py backend/tests/test_collector_server_runtime.py -q
```

- [ ] **Step 5: 提交并固定标签**

```powershell
git commit -m "fix: keep collector alive after unexpected errors"
git tag -a validation-stage-14-collector-resilience-20260728 -m "6173 stage 14 collector resilience"
```

---

### Task 6: 构建、部署和验收 6173

**Files:**
- Modify: `tasks/todo.md`
- Modify: `tasks/recognition-rule-pack-audit-20260728.md`
- Modify: `ops/validation-stages/20260728-night/README.md`

**Interfaces:**
- Consumes: stage 10-14 Git 提交和镜像。
- Produces: 可逐级回退的 6173 最终环境；5173 容器身份完全不变。

- [ ] **Step 1: 记录部署前身份和备份规则包**

记录验证与现场容器 ID、镜像、启动时间；导出 6173 当前启用规则包到临时备份文件。

- [ ] **Step 2: 为每个阶段构建对应镜像**

保留 stage 09 回退容器；最终运行镜像命名使用 `stage-14-<commit>`。解析器只在 stage 13 后替换，采集器源码只进入 6173 下载包，不安装到业务机。

- [ ] **Step 3: 导入并启用新版验证规则包**

只调用 6173 后端规则包 API，导入 `rule-packs/current-user-shoes.v1.json`，保留导入前 JSON 作为数据级回退。

- [ ] **Step 4: 运行全部验证**

Run:

```powershell
scripts/backend_test.ps1 -q
scripts/frontend_typecheck.ps1
cd frontend
npm run build
python scripts/regression_coverage_scan.py
```

Expected:
- 测试、类型检查和构建退出码 0。
- 历史覆盖扫描 `ok=true`。
- 6173 页面、后端、解析服务健康接口为 200。
- 验证日志无 5xx 或异常堆栈。
- 5173 三个容器 ID 和启动时间与部署前一致。

- [ ] **Step 5: 浏览器验证**

验证任务 62 的 SKU 跳转、规则包编辑弹窗、面单解析、异常页和导出预览；不修改业务原始记录。

- [ ] **Step 6: 更新清单并推送验证分支和标签**

更新三份状态文档，提交后推送 `codex/recognition-rule-pack-audit` 及 stage 10-14 标签。不得合并 `main`。
