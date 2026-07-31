# Implementation Plan: 6173 纯 AI 自适应识别闭环

## Global Constraints

- 5173 现场容器、数据库、进程、代码和端口只读，任何阶段都不得重启或写入。
- 所有实现、数据冷启动和运行验证只发生在隔离 worktree 与 6173。
- 不删除或重建现有数据卷；新阶段使用新卷，旧卷作为回退点。
- 5173 标准答案不得挂载、复制或传入 backend、parser、AI、规则编译器和管理员确认链路。
- 标准答案只允许在整轮完成后由独立比较器读取。
- 每张打印面单必须进入正常结果或明确异常；不按内容去重，多商品必须拆多行。
- AI 内部可以选择脱敏片段编号以降低幻觉；平台和用户只接收五字段，不暴露片段编号。
- 管理员修正形成学习样本，不直接覆盖业务订单行；最终行必须由新规则重跑产生。

## Task 1: 未授权字段和未知指纹失败关闭

**Description:** 阻止未授权、未选择字段或系统不认识的指纹进入 AI；已授权指纹的新文本格式仍可进入学习。

**Acceptance criteria:**
- [ ] 没有租户字段选择时不使用 catalog 默认字段，不调用 AI。
- [ ] `UNKNOWN` 指纹返回可读的 `fingerprint_adapter_required` 异常，不建立 AI 会话。
- [ ] 已授权指纹缺少 profile 时仍进入 AI 候选。

**Verification:**
- [ ] 新增测试先失败，再以最小修改通过。
- [ ] 相关 parser/backend 测试通过。

**Dependencies:** None

**Files likely touched:**
- `services/waybill-parser/service_app/main.py`
- `services/waybill-parser/service_app/evidence.py`
- `backend/app/api/routes/order_row_drafts.py`
- `backend/tests/test_waybill_parser_ai_fallback.py`

**Estimated scope:** Medium

## Task 2: AI 会话保留完整来源与管理员审计

**Description:** 分开保存模型原始候选、管理员五字段修正和规则编译结果，禁止管理员修正覆盖模型原始输出；确认只能经已认证的平台后端完成，内部 AI 写接口不直接暴露给浏览器。

**Acceptance criteria:**
- [ ] 会话同时保留 `model_candidate`、`administrator_rows` 和 `compiler_result`。
- [ ] 旧 SQLite 自动兼容迁移；现有会话仍能读取。
- [ ] 管理员确认经过平台 `require_write` 权限，并记录用户、会话、模型候选 hash、修正结果 hash 和时间。
- [ ] AI 内部反馈/确认写接口要求平台内部凭据，6173 浏览器不能直接绕过平台提交。
- [ ] API 和 AI 会话页面明确展示三个阶段，用户只看到五字段业务值。

**Verification:**
- [ ] 新增持久化与 API 契约失败测试。
- [ ] 未认证平台请求和缺少内部凭据的 AI 写请求均被拒绝。
- [ ] AI service 定向测试通过。
- [ ] 浏览器页面能同时看到原始候选和管理员修改。

**Dependencies:** Task 1

**Files likely touched:**
- `services/ai-recognition/service_app/store.py`
- `services/ai-recognition/service_app/main.py`
- `services/ai-recognition/service_app/static/console.html`
- `backend/app/api/routes/ai_recognition.py`
- `backend/tests/test_ai_recognition_service.py`

**Estimated scope:** Medium

## Task 3: 规则复用来源与业务化规则页面

**Description:** 让每个自动解析结果能证明来自哪条已确认规则，并把技术拆分步骤降级为折叠诊断。

**Acceptance criteria:**
- [ ] 规则命中结果带 `compiled_rule` 来源、学习会话、profile/strategy 和 AI 调用为 0 的事实。
- [ ] 面单解析页显示“已确认规则自动复用”，不把采集器来源冒充识别来源。
- [ ] 规则页面默认只显示业务摘要、确认样本和校验状态；技术步骤默认折叠。
- [ ] 指纹配置文案准确说明“仅已选字段传给 AI”。

**Verification:**
- [ ] parser/backend 契约测试覆盖来源 trace。
- [ ] 前端类型检查通过。
- [ ] 浏览器检查规则页和解析页。

**Dependencies:** Task 2

**Files likely touched:**
- `services/waybill-parser/service_app/declarative_rules.py`
- `backend/app/services/recognition_rule_packs.py`
- `frontend/src/views/workbench/WaybillBatchesView.vue`
- `frontend/src/components/recognition/RecognitionProfileEditor.vue`
- `frontend/src/views/workbench/FingerprintSettingsView.vue`

**Estimated scope:** Medium

## Task 4: 无 oracle 的冷启动回归

**Description:** 用纯测试 payload 固定完整闭环，不导入任何现场或验证标准答案。

**Acceptance criteria:**
- [ ] 陌生格式产生模型候选，管理员修正五字段后编译规则。
- [ ] 同格式留出样本命中规则且 AI 调用次数为 0。
- [ ] 不同 grammar 不误命中；多商品和重复打印均保留。
- [ ] 运行时模块不导入 `scripts/ai_validation_dataset.py` 或验证产物。

**Verification:**
- [ ] 新增一个端到端测试，先失败后通过。
- [ ] 规则合成、AI fallback、审批和声明式规则测试全部通过。

**Dependencies:** Tasks 1-3

**Files likely touched:**
- `backend/tests/test_ai_rule_pack_approval.py`
- `backend/tests/test_waybill_parser_ai_fallback.py`
- `backend/tests/test_declarative_waybill_rules.py`

**Estimated scope:** Medium

## Task 5: 建立真正零规则的 6173

**Description:** 保存污染基线后创建全新 validation 数据卷和 AI 会话卷，以事务方式清理 workspace 1 的规则、修订、会话和派生结果，并为当前租户写入五种已授权指纹的默认字段选择。

**Acceptance criteria:**
- [ ] 旧卷和现有快照保留，标记 `oracle_seeded_not_ai`。
- [ ] 新卷中 pack/revision/session/派生结果为 0。
- [ ] 3 tasks、102 raw、23 products、2004 SKUs、2360 images、6 stalls、37 matching rules 和资产 hash 不变。
- [ ] 指纹配置页显示五种授权指纹及原始英文路径。
- [ ] 5173 容器 ID 和 restart count 不变。

**Verification:**
- [ ] 两份 SQLite `integrity_check=ok` 且保存 SHA-256。
- [ ] 6173 API 返回无活动规则包、无 AI 会话和 `rule_pack_missing`。
- [ ] 创建 compose 覆盖和逐阶段恢复说明。

**Dependencies:** Task 4

**Files likely touched:**
- `ops/validation-stages/20260729-ai/runtime/`

**Estimated scope:** Medium

## Task 6: 真实 4B 盲测与规则学习

**Description:** 在 6173 通过真实页面运行未知格式；盲测管理员只看已选字段和模型候选，不访问标准答案，确认后验证同格式自动复用。

**Acceptance criteria:**
- [ ] 输入页面逐字段显示实际传给 AI 的路径、名称和值。
- [ ] 首条陌生格式产生五字段候选或可编辑失败状态。
- [ ] 确认后生成规则包修订，业务结果由规则重跑产生。
- [ ] 同格式留出样本 AI 调用为 0。
- [ ] 模型、管理员、编译器和复用四阶段来源均可审计。

**Verification:**
- [ ] 浏览器完整走通至少一个简单格式、一个 XML 格式和一个多商品格式。
- [ ] 再批量覆盖其余已授权 grammar，不使用 oracle 修正。

**Dependencies:** Task 5

**Files likely touched:** None unless真实运行暴露缺陷

**Estimated scope:** Medium

## Task 7: 事后评分、完整回归与发布候选

**Description:** 学习阶段结束后才启用独立比较器读取冻结基准，完成业务结果、覆盖、离线和回退验收。

**Acceptance criteria:**
- [ ] 输出 AI 原始候选正确率、管理员修改率、编译成功率和留出复用正确率。
- [ ] 任务 64/65/66 满足全部面单覆盖守恒。
- [ ] 正常供应商 Excel 的业务字段、行数、图片与冻结基准一致；差异逐条解释。
- [ ] AI 服务断开时已学格式继续工作，陌生格式进入明确异常。
- [ ] 6173 最终镜像、Git、数据库和工作簿均有阶段回退点。

**Verification:**
- [ ] 后端全量测试、前端类型检查、容器健康和浏览器验收通过。
- [ ] 独立代码审查无阻塞项。
- [ ] 5173 前后容器 ID、restart count 和健康状态一致。

**Dependencies:** Task 6

**Files likely touched:**
- `ops/validation-stages/20260729-ai/runtime/`

**Estimated scope:** Medium

## Deliberate Deferral

- 规则已经严格绑定 fingerprint + grammar，当前不先增加管理员手工维护“负样本”界面。
- 只有真实盲测出现跨 grammar 误命中时，才接通已有 negative replay 能力；避免增加一套用户维护负担。
