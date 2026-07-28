# Local AI Waybill Recognition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** 在完全不改动 5173 现场的前提下，仅在 6173 建立无既有识别规则的冷启动副本，用本地 4B 模型为陌生或解析不完整的面单生成候选订单行和声明式格式规则；规则经管理员确认后固化复用，AI 掉线时已固化规则继续工作。

**Architecture:** 保留现有 `backend -> waybill-parser` 独立解析边界。新增一个 `ai-recognition` 服务，负责脱敏、格式指纹、模型结构化输出、会话和管理员确认；模型由独立 Ollama 容器承载。`waybill-parser` 只执行声明式规则并在无规则或结果不完整时调用 AI，不读取业务数据库。平台后端继续负责规则包版本、激活和业务状态展示。验证环境使用新的冷启动 SQLite 副本和独立 Docker volume，答案集只供验收程序读取，不挂载给模型。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、HTTPX、SQLite、Vue 3、Docker Compose、Ollama、`qwen3.5:4b-q4_K_M`

## Global Constraints

- 5173 的容器、数据卷、端口、配置和进程均不可修改、重启或迁移。
- 6173 使用独立数据副本；不得删除或重建 `cargo-platform-data`。
- 冷启动副本保留原始打印记录、采集轮次、档口、商品、SKU、图片和商品匹配资产；清空识别规则及解析、匹配、导出派生结果。
- 原始 `raw_payload` 必须完整穿过后端；`documents[]` 只能在 `waybill-parser` 展开。
- 无规则、规则不完整、AI 不可用、AI 结果待确认必须是不同业务状态，不得统一显示成“请启用规则包”。
- AI 只接收脱敏后的商品相关信息，不做 OCR，不读取业务数据库，不读取答案集，不维护商品/SKU/图片/档口。
- AI 候选结果不能进入正常导出；只有管理员确认并固化为声明式规则后，重跑所得确定性结果才可进入匹配和导出。
- 每个阶段先保留镜像、数据库或 Git 回退点，再切换 6173。

## Task 1: Mark The Old Local-Model Direction As Superseded

**Files:**

- Modify: `docs/Stage84-WaybillReading-LocalModel-Plan.md`
- Modify: `docs/superpowers/specs/2026-07-29-local-ai-waybill-recognition-design.md`

**Steps:**

1. 在旧 Stage84 文档首部增加弃用说明，明确它不再定义当前产品方向。
2. 在新设计中引用本实施计划，并明确新设计取代旧的“模型只清洗文本、不输出订单行”约束。
3. 检查文档中不存在 `TODO`、`TBD`、占位符或与 5173 隔离边界冲突的表述。

**Verification:**

```powershell
rg -n "superseded|已取代|5173|6173|TODO|TBD" docs/Stage84-WaybillReading-LocalModel-Plan.md docs/superpowers/specs/2026-07-29-local-ai-waybill-recognition-design.md
```

**Commit:**

```text
docs: supersede obsolete local model direction
```

## Task 2: Build A Cold-Start Validation Dataset And Read-Only Answer Set

**Files:**

- Create: `scripts/ai_validation_dataset.py`
- Create: `backend/tests/test_ai_validation_dataset.py`
- Create: `ops/validation-stages/20260729-ai/README.md`

**Contract:**

```python
def build_cold_start_database(source_db: Path, destination_db: Path) -> dict[str, object]: ...
def export_answer_set(source_db: Path, parser_url: str, output: Path) -> dict[str, object]: ...
def sha256_file(path: Path) -> str: ...
```

**Steps:**

1. 先写测试：源库只读、SQLite 在线备份、目标库 `PRAGMA integrity_check=ok`。
2. 仅保留以下业务输入和资产表：
   - `tenants`, `workspaces`, `users`, `roles`, `user_workspaces`
   - `collectors`, `capture_tasks`, `raw_capture_records`
   - `stalls`, `products`, `product_skus`, `image_assets`, `product_matching_rules`
3. 清空识别规则、解析结果、字段映射、异常、报表、导出和旧模板派生表。
4. 在副本中禁用采集器、清除 token/心跳/运行状态；保留采集器身份供历史数据展示。
5. 在副本中保留 `raw_payload` 和 `source_columns`，清除 `parsed_payload`、`standard_detail_id` 和历史 `waybill_mode`，防止旧解析结果泄漏给 AI。
6. 对数据库中未列入保留表但仍有数据的未知表直接失败，避免静默把新派生结果带进冷启动副本。
7. 答案集导出脚本通过现有解析服务逐轮读取历史原始数据，保存 JSONL 和清单 SHA-256；输出目录不挂载到模型或 AI 服务。
8. CLI 必须拒绝源文件和目标文件相同，拒绝覆盖未显式指定的现有目标。

**Minimal test cases:**

- 源数据库散列在执行前后相同。
- 冷库资产数量等于源库，识别规则和派生结果数量为 0。
- 采集器全部 disabled/offline，`token_hash IS NULL`。
- 原始记录数量和 `raw_payload` 散列保持一致。
- 未知非空表导致脚本失败。
- 答案集元数据包含源库 SHA-256、解析服务版本、采集轮次和记录覆盖数。

**Verification:**

```powershell
scripts/backend_test.ps1 backend/tests/test_ai_validation_dataset.py -q
python -m py_compile scripts/ai_validation_dataset.py
```

**Commit:**

```text
feat: create isolated AI validation dataset
```

## Task 3: Add The Minimal AI Recognition Service

**Files:**

- Create: `services/ai-recognition/requirements.txt`
- Create: `services/ai-recognition/Dockerfile`
- Create: `services/ai-recognition/service_app/__init__.py`
- Create: `services/ai-recognition/service_app/contracts.py`
- Create: `services/ai-recognition/service_app/fingerprint.py`
- Create: `services/ai-recognition/service_app/sanitizer.py`
- Create: `services/ai-recognition/service_app/store.py`
- Create: `services/ai-recognition/service_app/model_client.py`
- Create: `services/ai-recognition/service_app/main.py`
- Create: `services/ai-recognition/service_app/static/console.html`
- Create: `backend/tests/test_ai_recognition_service.py`

**HTTP contract:**

- `GET /health`
- `POST /api/v1/recognize`
- `GET /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `POST /api/v1/sessions/{session_id}/approve`
- `POST /api/v1/sessions/{session_id}/reject`
- `GET /console`

**Candidate output:**

```json
{
  "contract_version": "ai_waybill_candidate_v1",
  "format_fingerprint": "sha256:...",
  "parent_waybills": [
    {
      "source_index": 0,
      "order_rows": [
        {
          "product_text": "范74 A5J2025",
          "sales_attribute_1": "5代白金",
          "sales_attribute_2": "45",
          "quantity": 1,
          "remark": "",
          "image_match_text": "范74 A5J2025 5代白金 45"
        }
      ]
    }
  ],
  "candidate_profile": {
    "strategy": "structured_items_v1",
    "items_path": ["task", "documents", "*", "contents"]
  },
  "confidence": 0.0,
  "warnings": []
}
```

**Steps:**

1. 先写一个服务级测试文件，使用临时 SQLite 和 fake model client。
2. `fingerprint.py` 只使用字段路径、节点类型、固定键集合和受限结构特征；不把商品值、手机号、地址、单号写入指纹。
3. `sanitizer.py` 删除收件人、手机号、地址、快递单号、平台订单号等标识字段，只留下商品解析所需字段和值。
4. `store.py` 使用 stdlib `sqlite3`，保存会话输入摘要、脱敏输入、候选输出、状态、管理员反馈和时间，不保存模型思维链。
5. 同一 `format_fingerprint + sanitized_payload_hash` 的未完成请求幂等复用现有会话，避免页面刷新反复调用模型。
6. `model_client.py` 调用 Ollama `/api/chat`，使用 Pydantic JSON Schema、`stream=false`、`think=false`、`temperature=0`、`num_ctx=4096`、单次 30 秒超时。
7. 模型输出必须经过 Pydantic 校验、字段长度限制、行数限制和候选规则白名单校验；无效输出进入 `ai_parse_failed`。
8. 管理员批准时，服务通过带共享 token 的平台内部 HTTP 接口提交候选规则，不直连平台数据库。
9. `console.html` 只展示会话状态、脱敏样本、候选订单行、字段来源、候选规则、批准/拒绝/反馈；不展示或编辑商品/SKU/档口资产。

**Minimal test cases:**

- 脱敏字段不会出现在发往 fake model 的 payload。
- 指纹对同结构不同商品值稳定，对不同字段结构变化。
- 重复识别请求只调用模型一次。
- 无效模型 JSON、超时、空订单行和越界数量均返回明确状态。
- 批准请求携带内部 token 且保存响应；拒绝不会调用平台。
- 服务重启后会话仍可读取。

**Verification:**

```powershell
scripts/backend_test.ps1 backend/tests/test_ai_recognition_service.py -q
docker build -t cargo-platform-ai-recognition:test services/ai-recognition
```

**Commit:**

```text
feat: add local AI recognition service
```

## Task 4: Add A Safe Declarative Rule Executor To The Parser

**Files:**

- Create: `services/waybill-parser/service_app/declarative_rules.py`
- Modify: `services/waybill-parser/service_app/main.py`
- Create: `backend/tests/test_declarative_waybill_rules.py`
- Modify: `backend/tests/test_waybill_parser_service.py`

**Supported strategies:**

- `structured_items_v1`: 有界字段路径和数组展开。
- `text_pipeline_v1`: 字面量分隔、`extract_between`、`split`/`rsplit`、trim、去固定前后缀、正整数转换。

**Rules:**

- 不允许可执行代码。
- 不允许用户或模型提供正则表达式。
- 不允许无限递归、任意 Python 路径或数据库访问。
- 所有路径深度、步骤数、字符串长度、订单行数均有固定上限。
- 规则执行必须生成每个字段的 `source_trace`。

**Core functions:**

```python
def structural_fingerprint(payload: dict[str, object], source_component: str) -> str: ...
def validate_format_profile(profile: dict[str, object]) -> list[str]: ...
def parse_with_format_profile(payload: dict[str, object], profile: dict[str, object]) -> dict[str, object]: ...
def check_parent_completeness(parent: dict[str, object]) -> tuple[bool, list[str]]: ...
```

**Steps:**

1. 先写真实形态 fixture：`documents[]` 多面单、`packageItemDetail[]` 多商品、`ITEM_INFO`、`productInfo` 和纯文本 `customContent`。
2. 复用现有路径遍历和订单行 contract；不复制 `order_row_engine.py` 的结构化读取能力。
3. 在规则包校验中支持 `parser_policy.order_row_parser = "declarative_v1"` 和 `format_profiles`。
4. 指纹命中后执行对应 profile；结果必须经过完整性检查：
   - 至少一个父面单。
   - 每个父面单至少一条商品行。
   - 每条行必须有 `product_text` 和正整数 `quantity`。
   - 多商品源不得被折叠成一个拼接文本字段。
5. 保留现有 `shoe_waybill_v1` 兼容能力，但冷启动副本不带该规则包。

**Minimal test cases:**

- `documents[]` 产生多个父面单，不能在后端预拆。
- 多商品产生多个子行。
- 同内容打印两次保留两条来源，不去重。
- 指纹命中但商品或数量缺失时完整性失败。
- 越界路径、未知操作、正则或可执行内容被拒绝。
- source trace 可以定位原始字段路径。

**Verification:**

```powershell
scripts/backend_test.ps1 backend/tests/test_declarative_waybill_rules.py backend/tests/test_waybill_parser_service.py backend/tests/test_independent_parser_boundary.py -q
```

**Commit:**

```text
feat: execute declarative waybill format rules
```

## Task 5: Trigger AI Only For Unknown Or Incomplete Formats

**Files:**

- Create: `services/waybill-parser/service_app/ai_client.py`
- Modify: `services/waybill-parser/service_app/main.py`
- Modify: `services/waybill-parser/requirements.txt`
- Modify: `backend/tests/test_waybill_parser_service.py`

**State flow:**

```text
known fingerprint + complete deterministic rows -> parsed
known fingerprint + incomplete rows            -> ai_rule_pending / ai_unavailable / ai_parse_failed
unknown fingerprint                             -> ai_rule_pending / ai_unavailable / ai_parse_failed
no active pack + AI enabled                     -> ai_rule_pending / ai_unavailable / ai_parse_failed
no active pack + AI disabled                    -> rule_pack_missing
```

**Steps:**

1. 将 `workspace_id` 作为不透明上下文从请求传给 AI 服务；解析器仍不读取平台数据库。
2. 先尝试激活规则包中的指纹 profile。
3. 规则未命中或结果不完整时才调用 AI；商品/SKU/图片匹配失败不得触发 AI。
4. AI 候选订单行只用于独立会话预览，不返回为可导出的正常 `order_rows`。
5. 返回 `ai_session_id`、`ai_console_url`、失败原因和原始记录 source trace。
6. HTTP 超时、服务不可达、无效响应均转成业务状态，不回退到隐藏内置解析。

**Minimal test cases:**

- 已固化完整规则不会调用 AI。
- 同指纹但完整性失败会调用 AI。
- 没有规则包时 AI 可创建待确认会话。
- AI 不可用时未知格式明确异常，已知规则照常完成。
- 候选 AI 行不会进入正常 parser rows。

**Verification:**

```powershell
scripts/backend_test.ps1 backend/tests/test_waybill_parser_service.py backend/tests/test_independent_parser_boundary.py -q
```

**Commit:**

```text
feat: route unknown waybill formats to local AI
```

## Task 6: Add Rule Approval And Revision In The Platform Backend

**Files:**

- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/waybill_parser_client.py`
- Modify: `backend/app/services/order_row_reader.py`
- Modify: `backend/app/services/recognition_rule_packs.py`
- Create: `backend/app/api/routes/ai_recognition.py`
- Modify: `backend/app/api/router.py`
- Create: `backend/tests/test_ai_recognition_backend.py`

**Internal approval endpoint:**

```text
POST /api/internal/ai-recognition/approve
X-AI-Recognition-Token: value from AI_RECOGNITION_INTERNAL_TOKEN
```

**Steps:**

1. 先写 API tests：token 缺失/错误为 401，未知 workspace 为 404，无效候选规则为 422。
2. `order_row_reader` 在 AI 启用时不再因“无激活规则包”提前返回；原始记录仍完整传给 parser。
3. 批准候选规则时：
   - 校验候选 profile 和指纹。
   - 读取当前激活的声明式规则包；不存在则从空 `format_profiles` 开始。
   - 合并或替换该指纹 profile。
   - 创建新 revision，保留旧 revision 但设为 inactive。
   - 通过 parser `/validate` 校验后才激活。
   - 记录管理员确认摘要和来源 AI session id。
4. 每次批准生成新的不可变 revision code；不得覆盖旧规则包记录。
5. 批准成功后，AI 控制台提示刷新对应采集轮次即可触发确定性重算；不持久化人工订单行覆盖。
6. AI disabled 时维持当前 `rule_pack_missing` 行为。

**Minimal test cases:**

- 无规则包 + AI enabled 时后端调用 parser。
- 批准第一个 profile 创建 revision 1 并激活。
- 批准第二个指纹创建 revision 2、包含两个 profiles、revision 1 保留 inactive。
- parser validation 失败不激活新 revision。
- 旧规则可回滚激活。
- 原始 payload 中 `documents[]` 未在后端拆分。

**Verification:**

```powershell
scripts/backend_test.ps1 backend/tests/test_ai_recognition_backend.py backend/tests/test_independent_parser_boundary.py backend/tests/test_order_row_reader.py -q
```

**Commit:**

```text
feat: approve AI format rules as revisions
```

## Task 7: Surface AI States Without Adding A Manual Order-Row Workflow

**Files:**

- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/views/workbench/WaybillBatchesView.vue`
- Modify: `frontend/src/views/workbench/ExceptionsView.vue`
- Modify: related frontend tests if present

**Steps:**

1. 使用 CodeGraph 定位 `rule_pack_missing`、解析异常和操作按钮的统一渲染点。
2. 增加以下业务文案：
   - `ai_rule_pending`: `新格式待管理员确认`
   - `ai_unavailable`: `AI 识别服务不可用，已固化规则仍可继续使用`
   - `ai_parse_failed`: `AI 未能生成完整候选规则`
3. 仅在这三种 AI 状态显示“查看 AI 识别会话”链接，打开独立 console URL。
4. 不增加订单行编辑、确认、排除、批量确认或逐行批准。
5. 页面切换继续复用现有路由和数据请求，不增加阻塞式预加载。

**Verification:**

```powershell
scripts/frontend_typecheck.ps1
Set-Location frontend
npm run build
```

**Commit:**

```text
feat: expose local AI recognition states
```

## Task 8: Compose The 6173-Only AI Runtime

**Files:**

- Create: `ops/validation-stages/20260729-ai/docker-compose.override.yml`
- Create: `ops/validation-stages/20260729-ai/.env.example`
- Modify: `ops/validation-stages/20260729-ai/README.md`

**Services:**

- `cargo-platform-validation-local-model`: `ollama/ollama`, Nvidia GPU, persistent model volume.
- `cargo-platform-validation-ai-recognition`: newly built service, persistent session SQLite volume.
- Existing validation parser/backend receive AI URLs and shared token through the override.
- AI console uses an unused localhost-only port determined at deployment time and recorded in `.env`; it does not bind to the production network or 5173.

**Steps:**

1. Record production container IDs, images, restart counts and port ownership before any validation change.
2. Render merged Compose config and fail if it contains production container names, `5173`, or `cargo-platform-data`.
3. Build versioned parser/backend/AI images; do not use floating application tags for rollback.
4. Pull `qwen3.5:4b-q4_K_M` inside the local-model container.
5. Mount the cold-start database volume only into validation backend.
6. Do not mount the answer-set directory into local-model or ai-recognition.
7. Start AI/model services first, verify health, then switch only validation parser/backend. Rebuild validation UI only if Task 7 changed it.
8. Record exact previous/new image IDs, DB SHA-256, volume names and rollback commands.

**Verification:**

```powershell
docker compose -f ops/validation-stages/20260728-night/docker-compose.yml -f ops/validation-stages/20260729-ai/docker-compose.override.yml config
docker ps --format "{{.Names}}|{{.Image}}|{{.ID}}|{{.Ports}}"
docker exec cargo-platform-validation-local-model ollama list
Invoke-RestMethod http://127.0.0.1:8011/health
Invoke-WebRequest http://127.0.0.1:6173/ -UseBasicParsing
```

**Rollback:**

```text
Stop the AI override services, restore the recorded validation images and the previous validation data volume, then verify 6173. Never change 5173 or cargo-platform-data.
```

**Commit:**

```text
ops: add isolated 6173 AI recognition runtime
```

## Task 9: Run Cold-Start Acceptance And Failure Drills

**Files:**

- Create: `scripts/verify_ai_recognition_e2e.py`
- Create: `backend/tests/test_ai_recognition_e2e_contract.py`
- Modify: `ops/validation-stages/20260729-ai/README.md`

**Required scenarios:**

1. 冷库中识别规则数为 0、历史解析和导出结果为 0，原始记录和业务资产数量与清单一致。
2. 选择至少三种格式：
   - 结构化单商品。
   - 结构化多商品。
   - 纯文本或历史特殊格式。
3. 首次读取生成 AI 待确认会话，不产生正常可导出行。
4. 管理员确认后产生新规则 revision；刷新同轮次后由声明式规则生成订单行。
5. 相同指纹的新商品值不再次调用模型。
6. 相同指纹但缺少关键字段时再次进入 AI，而不是输出空白或误导行。
7. 停止 AI 容器后：
   - 已固化格式继续正常解析。
   - 陌生格式进入 `ai_unavailable` 异常。
8. 多商品面单拆成多行；同内容打印记录不去重。
9. 与只读答案集比较父面单覆盖和商品行关键字段；差异形成报告，不把答案集喂给模型。
10. 对每个验证轮次检查：

```text
采集打印覆盖 = 正常解析覆盖 + 明确异常覆盖
```

11. 后端、parser、AI 会话库均通过完整性/健康检查；容器无异常重启和持续错误日志。
12. 再次核对 5173 的容器 ID、镜像、重启次数和数据卷均与前置快照一致。

**Verification:**

```powershell
scripts/backend_test.ps1 -q
scripts/frontend_typecheck.ps1
Set-Location frontend
npm run build
Set-Location ..
python scripts/verify_ai_recognition_e2e.py --base-url http://127.0.0.1:18001 --ai-url http://127.0.0.1:18111 --answer-set ops/validation-stages/20260729-ai/runtime/answer-set.jsonl
```

The concrete answer-set path is written to the validation README and the generated manifest; it is not committed.

**Commit:**

```text
test: verify cold-start local AI recognition
```

## Completion Gate

Implementation is complete only when:

- Every target test and the existing backend/frontend checks pass.
- 6173 demonstrates cold-start -> AI candidate -> administrator approval -> deterministic rule reuse.
- AI-down behavior is proven.
- No candidate AI row enters normal export before rule approval.
- No captured print disappears without either a normal parsed result or a named exception.
- The answer-set comparison report, runtime image IDs, database hashes and rollback commands are recorded.
- 5173 before/after runtime snapshots match.
