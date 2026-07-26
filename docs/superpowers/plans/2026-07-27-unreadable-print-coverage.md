# 不可读打印记录覆盖修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让没有可读商品文本、也没有旧标准明细的原始打印记录仍产生一条可复核异常行。

**Architecture:** 保留识别服务现有异常占位能力和现有来源优先级，只修正后端任务读取入口的空结果分支。可读原始记录仍优先、已有标准明细仍可使用；只有即将返回空结果且存在原始记录时，才进入 `parse_raw_records_to_order_rows`。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、pytest。

## Global Constraints

- 不读取或修改现场业务数据。
- 不部署、不重启服务、不操作采集器。
- 不修改规则包、匹配和导出格式。
- 使用项目脚本和临时 SQLite 数据库运行后端测试。

---

### Task 1: 保证不可读原始打印记录生成异常行

**Files:**
- Modify: `backend/app/services/order_row_reader.py:421-442`
- Test: `backend/tests/test_product_matching_stage82b.py`

**Interfaces:**
- Consumes: `raw_records_for_task(...) -> list[RawCaptureRecord]`
- Produces: `order_rows_for_task(...) -> tuple[list[dict[str, str]], list[dict[str, Any]]]`

- [ ] **Step 1: 写失败回归测试**

在 `backend/tests/test_product_matching_stage82b.py` 增加接口测试，写入一条只有不可读加密字段的 `RawCaptureRecord`，不写入 `StandardDetail`。断言识别预览仍包含一张面单和一条异常订单行，并断言最终工作簿的 `异常面单` 表保留该行。

- [ ] **Step 2: 运行测试并确认按预期失败**

Run:

```powershell
pwsh.exe -NoProfile -File .\scripts\backend_test.ps1 backend/tests/test_product_matching_stage82b.py::test_recognition_export_keeps_unreadable_raw_print_in_exception_sheet -q
```

Expected: FAIL，因为当前识别预览的 `waybill_count` 和 `order_row_count` 都是 `0`。

- [ ] **Step 3: 写最小实现**

保留现有可读原始记录和标准明细分支，在最终 `return [], []` 前增加：

```python
if records:
    return parse_raw_records_to_order_rows(
        db, workspace_id=workspace_id, task_id=task_id, records=records
    )
```

- [ ] **Step 4: 运行单项测试并确认通过**

Run:

```powershell
pwsh.exe -NoProfile -File .\scripts\backend_test.ps1 backend/tests/test_product_matching_stage82b.py::test_recognition_export_keeps_unreadable_raw_print_in_exception_sheet -q
```

Expected: PASS。

- [ ] **Step 5: 运行相关回归测试**

Run:

```powershell
pwsh.exe -NoProfile -File .\scripts\backend_test.ps1 backend/tests/test_order_row_drafts.py backend/tests/test_recognition_report_export.py backend/tests/test_product_matching_stage82b.py -q
```

Expected: 全部通过。

- [ ] **Step 6: 提交原子补丁**

```powershell
git add backend/tests/test_product_matching_stage82b.py backend/app/services/order_row_reader.py
git commit -m "fix: keep unreadable print records reviewable"
```
