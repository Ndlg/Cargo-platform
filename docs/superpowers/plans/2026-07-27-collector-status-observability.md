# Collector Status Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有采集连接页面显示真实积压数、最后成功上传时间和最近重连原因，同时保证状态统计绝不影响采集水位与补传。

**Architecture:** 采集器继续把本机打印数据库和现有水位文件作为唯一补传队列；只在现有 `CollectorState` 增加两个可选状态值和一个积压计算方法，并通过现有心跳上报。后端继续存入 `Collector.status_payload`，前端复用现有采集器展开详情显示，不新增表、接口或页面。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、Vue 3、TypeScript、现有 JSON 状态文件与 SQLite 打印数据库。

**Implementation Record:** 已在 `codex/collector-status-observability-design` 完成；采集器、后端和前端分别保留独立提交，最终验证为后端 170 项通过、前端生产构建通过、6173 数据副本 1815/1815 面单覆盖且硬失败为 0。

## Global Constraints

- 不新增数据库表、数据库迁移、第三方依赖或监控服务。
- 状态统计失败时必须继续采集和重试，不能前移采集水位。
- `last_upload_at` 只允许在服务端确认上传成功后更新。
- 旧版 `collector-state.json` 必须可直接加载。
- 不修改面单识别、商品匹配、导出和采集任务时间窗行为。
- 不部署或重启 5173、8000、8010 现场服务和三台业务机采集器。
- 只在隔离分支、后端测试数据库和 6173 数据副本验证。

---

### Task 1: 采集器持久化真实状态

**Files:**
- Modify: `collector-client/client.py:356-433`
- Modify: `collector-client/client.py:605-730`
- Modify: `collector-client/client.py:890-908`
- Test: `backend/tests/test_collector_client_runtime.py`

**Interfaces:**
- Consumes: `PrintDbAdapter.max_rowid()`, `CollectorState.capture_watermarks`, `utc_now()`.
- Produces: `CollectorState.last_upload_at: str | None`.
- Produces: `CollectorState.last_reconnect_reason: str | None`.
- Produces: `CollectorState.pending_count(adapters: list[PrintDbAdapter]) -> int | None`.
- Produces: `reconnect_reason(exc: BaseException) -> str`.
- Produces: heartbeat JSON fields `queue_size`, `last_upload_at`, `last_reconnect_reason`.

- [ ] **Step 1: 写旧状态兼容和积压计算失败测试**

在 `backend/tests/test_collector_client_runtime.py` 增加：

```python
def test_collector_state_loads_old_file_and_reports_pending_rows(tmp_path) -> None:
    state_path = tmp_path / "collector-state.json"
    collector_client.write_json(
        state_path,
        {
            "idle_watermarks": {"cainiao-cnprint": 0},
            "capture_watermarks": {
                "57:cainiao-cnprint": 1,
                "58:cainiao-cnprint": 2,
            },
        },
    )
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("1", "{}", "2026-07-27 10:00:01"),
                ("2", "{}", "2026-07-27 10:00:02"),
                ("3", "{}", "2026-07-27 10:00:03"),
            ],
        )
    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)

    state = collector_client.CollectorState.load(state_path)

    assert state.last_upload_at is None
    assert state.last_reconnect_reason is None
    assert state.pending_count([adapter]) == 2
```

这个断言同时锁定“同一打印组件只计算一次”：两轮水位分别为 1 和 2、最大行号为 3 时，积压应为 2，而不是重复相加得到 3。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
.\scripts\backend_test.ps1 backend/tests/test_collector_client_runtime.py::test_collector_state_loads_old_file_and_reports_pending_rows
```

Expected: FAIL，`CollectorState` 尚无状态字段或 `pending_count`。

- [ ] **Step 3: 最小扩展 `CollectorState`**

在 `collector-client/client.py` 中把可选字段加入构造、加载和保存：

```python
class CollectorState:
    def __init__(
        self,
        idle_watermarks: dict[str, int] | None = None,
        capture_watermarks: dict[str, int] | None = None,
        last_upload_at: str | None = None,
        last_reconnect_reason: str | None = None,
    ) -> None:
        self.idle_watermarks = idle_watermarks or {}
        self.capture_watermarks = capture_watermarks or {}
        self.last_upload_at = last_upload_at
        self.last_reconnect_reason = last_reconnect_reason

    def pending_count(self, adapters: list[PrintDbAdapter]) -> int | None:
        adapters_by_component = {adapter.source_component: adapter for adapter in adapters}
        earliest_watermarks: dict[str, int] = {}
        for key, watermark in self.capture_watermarks.items():
            _task_id, separator, component = key.partition(":")
            if separator and component in adapters_by_component:
                earliest_watermarks[component] = min(
                    watermark,
                    earliest_watermarks.get(component, watermark),
                )
        total = 0
        for component, watermark in earliest_watermarks.items():
            status = adapters_by_component[component].get_status()
            if status.get("status") != "ready":
                if status.get("status") == "error":
                    self.last_reconnect_reason = "sqlite"
                return None
            total += max(0, int(status.get("max_rowid") or 0) - watermark)
        return total
```

`load()` 使用 `payload.get(...) or None` 读取两个新字段；`to_dict()` 原样写回。

- [ ] **Step 4: 写上传确认和异常分类失败测试**

把现有 `test_collector_retries_unacknowledged_rows_without_advancing_watermark` 扩展为：

```python
def test_collector_retries_unacknowledged_rows_without_advancing_watermark(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "print.db"
    with collector_client.sqlite3.connect(db_path) as connection:
        connection.execute("create table task (taskID text, msg text, time text)")
        connection.executemany(
            "insert into task (taskID, msg, time) values (?, ?, ?)",
            [
                ("local-1", '{"task":"one"}', "2026-07-27 10:00:01"),
                ("local-2", '{"task":"two"}', "2026-07-27 10:00:02"),
            ],
        )

    adapter = collector_client.PrintDbAdapter("cainiao-cnprint", "Cainiao", db_path)
    state = collector_client.CollectorState(idle_watermarks={"cainiao-cnprint": 0})
    attempts: list[list[str]] = []

    def upload_with_lost_first_response(_base_url, _token, _task_id, records):
        attempts.append([record["source_index"] for record in records])
        if len(attempts) == 1:
            raise ConnectionError("response lost")
        return {"inserted": 0, "skipped": len(records)}

    monkeypatch.setattr(collector_client, "upload_records", upload_with_lost_first_response)
    monkeypatch.setattr(
        collector_client,
        "utc_now",
        lambda: "2026-07-27T10:00:00+00:00",
    )

    try:
        collector_client.upload_rows_for_task(
            "http://collector.test",
            "token",
            state,
            58,
            adapter,
            100,
        )
    except ConnectionError:
        pass
    else:
        raise AssertionError("first upload must simulate a lost response")

    assert state.capture_watermarks["58:cainiao-cnprint"] == 0
    assert state.last_upload_at is None
    assert collector_client.upload_rows_for_task(
        "http://collector.test",
        "token",
        state,
        58,
        adapter,
        100,
    ) == 2
    assert attempts == [["1", "2"], ["1", "2"]]
    assert state.capture_watermarks["58:cainiao-cnprint"] == 2
    assert state.last_upload_at == "2026-07-27T10:00:00+00:00"
```

再新增异常分类测试：

```python
def test_collector_reconnect_reason_uses_stable_categories() -> None:
    auth_error = urllib.error.HTTPError("http://test", 401, "unauthorized", {}, None)
    http_error = urllib.error.HTTPError("http://test", 503, "unavailable", {}, None)

    assert collector_client.reconnect_reason(auth_error) == "auth"
    assert collector_client.reconnect_reason(http_error) == "http"
    assert collector_client.reconnect_reason(collector_client.sqlite3.OperationalError("locked")) == "sqlite"
    assert collector_client.reconnect_reason(ConnectionError("offline")) == "network"
```

- [ ] **Step 5: 运行测试并确认失败**

Run:

```powershell
.\scripts\backend_test.ps1 backend/tests/test_collector_client_runtime.py -k "collector_state_loads_old_file or collector_retries_unacknowledged or collector_reconnect_reason"
```

Expected: FAIL，尚未记录成功时间和异常类别。

- [ ] **Step 6: 上报最小状态**

实现：

```python
def reconnect_reason(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return "auth" if is_auth_http_error(exc) else "http"
    if isinstance(exc, sqlite3.Error):
        return "sqlite"
    return "network"
```

在 `upload_rows_for_task()` 收到 `upload_records()` 返回值后、前移水位时设置：

```python
state.last_upload_at = utc_now()
```

给 `heartbeat()` 增加 `state: CollectorState | None = None`，发送：

```python
"queue_size": state.pending_count(adapters) if state is not None else 0,
"last_upload_at": state.last_upload_at if state is not None else None,
"last_reconnect_reason": state.last_reconnect_reason if state is not None else None,
```

`run_sqlite_once()` 调用心跳时传入 `state=state`。三个异常分支分别设置：

```python
state.last_reconnect_reason = reconnect_reason(exc)
```

`save_state_safely()` 捕获写入异常时在内存中设置：

```python
state.last_reconnect_reason = "state_save"
```

- [ ] **Step 7: 运行采集器测试**

Run:

```powershell
.\scripts\backend_test.ps1 backend/tests/test_collector_client_runtime.py
```

Expected: PASS。

- [ ] **Step 8: 提交**

```powershell
git add collector-client/client.py backend/tests/test_collector_client_runtime.py
git commit -m "feat: report collector upload status"
```

---

### Task 2: 后端保存并返回状态字段

**Files:**
- Modify: `backend/app/api/routes/collector_runtime.py:352-360`
- Modify: `backend/app/api/routes/collector_runtime.py:2455-2463`
- Test: `backend/tests/test_collector_client_runtime.py`

**Interfaces:**
- Consumes: heartbeat JSON `queue_size: int | null`, `last_upload_at: str | null`, `last_reconnect_reason: str | null`.
- Produces: `Collector.status_payload` 中同名字段。
- Produces: `/api/v1/collector-control/status` 现有响应中的同名字段。

- [ ] **Step 1: 写后端心跳失败测试**

扩展现有心跳测试，提交：

```python
heartbeat = client.post(
    "/api/v1/collector-runtime/heartbeat",
    headers={"X-Collector-Token": token},
    json={
        "collector_id": "WAREHOUSE-PC-09",
        "runtime_status": "listening",
        "queue_size": 3,
        "last_upload_at": "2026-07-27T10:00:00+00:00",
        "last_reconnect_reason": "network",
    },
)

status_payload = heartbeat.json()["collector"]["status_payload"]
assert status_payload["queue_size"] == 3
assert status_payload["last_upload_at"] == "2026-07-27T10:00:00+00:00"
assert status_payload["last_reconnect_reason"] == "network"
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
.\scripts\backend_test.ps1 backend/tests/test_collector_client_runtime.py -k "heartbeat"
```

Expected: FAIL 或请求字段未进入 `status_payload`。

- [ ] **Step 3: 扩展现有 Pydantic 请求与状态字典**

在 `CollectorHeartbeatRequest` 增加：

```python
last_upload_at: str | None = Field(default=None, max_length=64)
last_reconnect_reason: str | None = Field(default=None, max_length=32)
```

现有 `queue_size: int | None` 保持不变：打印数据库暂时不可读时继续用 `null` 表示未知，不能谎报为 0。

在 `collector_heartbeat()` 的现有 `status_payload` 字典增加：

```python
"last_upload_at": payload.last_upload_at,
"last_reconnect_reason": payload.last_reconnect_reason,
```

不新增接口、模型或数据库字段。

- [ ] **Step 4: 运行后端相关测试**

Run:

```powershell
.\scripts\backend_test.ps1 backend/tests/test_collector_client_runtime.py
```

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/api/routes/collector_runtime.py backend/tests/test_collector_client_runtime.py
git commit -m "feat: persist collector upload status"
```

---

### Task 3: 在现有采集连接详情显示状态

**Files:**
- Modify: `frontend/src/services/api.ts:91-99`
- Modify: `frontend/src/views/workbench/CollectorConnectionsView.vue:263-285`
- Modify: `tasks/todo.md:43-49`

**Interfaces:**
- Consumes: `CollectorRecord.status_payload.last_upload_at`.
- Consumes: `CollectorRecord.status_payload.last_reconnect_reason`.
- Produces: “最后成功上传”和“最近重连原因”两条现有详情信息。

- [ ] **Step 1: 扩展前端类型**

在 `CollectorRecord.status_payload` 增加：

```typescript
last_upload_at?: string | null
last_reconnect_reason?: 'network' | 'http' | 'auth' | 'sqlite' | 'state_save' | null
```

- [ ] **Step 2: 增加稳定中文标签**

在 `CollectorConnectionsView.vue` 的现有格式化函数附近增加：

```typescript
function reconnectReasonLabel(value: unknown): string {
  const labels: Record<string, string> = {
    network: '网络中断',
    http: '服务端暂时不可用',
    auth: '采集器凭证失效',
    sqlite: '打印数据库暂时不可读',
    state_save: '本地采集状态保存失败',
  }
  return labels[String(value ?? '')] ?? '无'
}
```

- [ ] **Step 3: 复用现有展开详情**

在“本地队列”和“最近错误”附近增加：

```vue
<div class="detail-line">
  <span>本地队列</span>
  <strong>{{ textValue(row.status_payload?.queue_size, '未知') }}</strong>
</div>
<div class="detail-line">
  <span>最后成功上传</span>
  <strong>{{ formatDateTime(row.status_payload?.last_upload_at) }}</strong>
</div>
<div class="detail-line">
  <span>最近重连原因</span>
  <strong>{{ reconnectReasonLabel(row.status_payload?.last_reconnect_reason) }}</strong>
</div>
```

用这段“本地队列”替换现有默认显示 `0` 的行；保留现有“最近错误”，用于显示具体但已受长度限制的诊断信息。

- [ ] **Step 4: 更新收口清单**

在 `tasks/todo.md` 勾选：

```markdown
- [x] 记录队列积压、最后成功上传和重连原因
```

- [ ] **Step 5: 运行前端类型检查**

Run:

```powershell
.\scripts\frontend_typecheck.ps1
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add frontend/src/services/api.ts frontend/src/views/workbench/CollectorConnectionsView.vue tasks/todo.md
git commit -m "feat: show collector upload status"
```

---

### Task 4: 全量隔离验证

**Files:**
- Verify only: no production file changes.

**Interfaces:**
- Consumes: Tasks 1-3 的最终分支。
- Produces: 可合并但未部署的验证结果。

- [ ] **Step 1: 后端全量测试**

Run:

```powershell
.\scripts\backend_test.ps1
```

Expected: 全部 PASS。

- [ ] **Step 2: 前端类型检查**

Run:

```powershell
.\scripts\frontend_typecheck.ps1
```

Expected: PASS。

- [ ] **Step 3: 6173 数据副本覆盖守门**

Run:

```powershell
$source=(Get-Location).Path
$containerEnv=docker inspect cargo-platform-validation-backend --format '{{range .Config.Env}}{{println .}}{{end}}'
$names=@('DATABASE_URL','SECRET_KEY','WAYBILL_PARSER_URL','AUTO_CREATE_TABLES')
$dockerArgs=@('run','--rm','--network','cargo-platform-validation-20260727-014134','--volumes-from','cargo-platform-validation-backend','--mount',"type=bind,source=$source,target=/workspace",'-w','/workspace')
foreach($name in $names){
  $entry=$containerEnv | Where-Object { $_ -like "$name=*" } | Select-Object -First 1
  if($entry){ $dockerArgs += @('-e',$entry) }
}
$dockerArgs += @('cargo-platform-validation-backend:d131010','python','scripts/regression_coverage_scan.py')
& docker @dockerArgs
exit $LASTEXITCODE
```

Expected:

- `ok: true`
- `expected_parent_count: 1815`
- `recognized_parent_count: 1815`
- 硬失败为 0
- 不输出业务原文

- [ ] **Step 4: 检查差异与现场边界**

Run:

```powershell
git diff main...HEAD --check
git status --short
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Expected:

- Git 无空白错误和未提交文件。
- 5173、8000、8010 与三台采集器没有被重启或替换。
- 分支只包含设计、计划和 Tasks 1-3 的最小修改。
