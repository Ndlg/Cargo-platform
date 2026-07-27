# 面单覆盖回归守门实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and superpowers:verification-before-completion.

**Goal:** 提供一个可重复执行、只读且不泄露业务原文的面单覆盖检查命令。

**Architecture:** 用一个纯检查函数判断覆盖关系；命令层只负责从现有数据库和识别函数取数量并汇总结果。复用现有读取与识别能力，不复制业务解析逻辑。

**Tech Stack:** Python 3.12、SQLAlchemy、pytest。

## 约束

- 不修改现场、业务数据、规则包或识别结果。
- 不增加 UI、API、数据库结构或新依赖。
- 不做任何自动去重。

## 清单

### Task 1: 固化覆盖判定

**Files:**
- Create: `backend/app/services/regression_coverage.py`
- Create: `backend/tests/test_regression_coverage.py`

- [ ] 先写一条失败测试，覆盖硬失败与非阻断告警。
- [ ] 确认测试因检查模块不存在而失败。
- [ ] 写最小纯函数并让单项测试通过。

### Task 2: 提供只读命令

**Files:**
- Create: `scripts/regression_coverage_scan.py`

- [ ] 复用现有任务、原始记录、面单拆分和识别结果函数。
- [ ] 默认扫描已结束任务；支持重复传入 `--task-id`。
- [ ] 只输出计数和任务 ID；硬失败返回退出码 1。
- [ ] 运行 `--help` 和测试数据库冒烟检查。

### Task 3: 全量验证与收口

- [ ] 运行完整后端测试。
- [ ] 在 6173 数据副本扫描全部历史已结束任务。
- [ ] 检查输出不含业务原文。
- [ ] 更新 `tasks/todo.md` 和扫描记录。
- [ ] 保留隔离分支，未获确认前不合并、不部署。
