# 6173 本地 AI 冷启动验证

本目录只管理 `6173` 验证环境。`5173` 现场、`cargo-platform-data` 和三台业务采集器不在变更范围内。

## 阶段 1：数据边界

冷启动数据库与只读答案集由 `scripts/ai_validation_dataset.py` 生成：

```powershell
python scripts/ai_validation_dataset.py `
  --source-db ops/validation-stages/20260729-ai/runtime/source.db `
  --answer-set ops/validation-stages/20260729-ai/runtime/answer-set.jsonl `
  --parser-url http://127.0.0.1:18010 `
  --cold-db ops/validation-stages/20260729-ai/runtime/cold-start.db
```

`runtime/` 不进入 Git。运行前必须先从当前 6173 数据卷做 SQLite 在线备份得到
`source.db`，不得直接处理现场数据卷。

冷启动副本保留：

- 登录、租户和工作空间最小数据
- 采集器身份、采集轮次和原始打印 payload
- 档口、商品、SKU、图片和商品匹配规则

冷启动副本清除：

- 采集器 token、心跳和在线状态
- 识别规则包
- 历史解析、字段映射、异常、报表和导出派生结果
- 原始记录上的历史 `parsed_payload`、`standard_detail_id` 和 `waybill_mode`

答案集先由当前已验证 parser 生成，包含源库 SHA-256、parser 健康信息、任务覆盖和输出散列。
答案集不挂载给 Ollama、AI 识别服务或冷启动业务后端，只供最终验收脚本比较。

## 回退点

- 原 6173 数据卷：`cargo-platform-validation-data-20260728-165426-latest`
- 原 6173 后端：`cargo-platform-validation-backend:stage-17-7bd0d9f`
- 原 6173 parser：`cargo-platform-validation-parser:stage-14-66d2959`
- 原 6173 UI：`cargo-platform-validation-ui:stage-17-7bd0d9f`

任何阶段失败时只恢复上述验证资源。不得停止、替换或重建 5173 相关容器。

## 阶段 2：6173 独立 AI 运行时

- 冷启动数据卷：`cargo-platform-validation-ai-data-20260729-110409`
- 冷库 SHA-256：`8c7828ca3044ef50e500dc94a054b081dbc40f38f8dc8506c932dee2a5f9b16d`
- 源副本 SHA-256：`70fcfc31421eed111e04a691d9fb79560233edd006b17f77154737f82ab90a6f`
- 答案集 SHA-256：`10b67d3b8dfa96edd93fce49e3ccbf616190559bf5e95c45e2dcc32a0d4bd2f5`
- 原始记录：`1843`
- 冷库识别规则：`0`
- AI 控制台：`http://127.0.0.1:18111/console`

启动时在当前 PowerShell 会话设置一个随机验证 token，然后合并两个 Compose 文件：

```powershell
$env:AI_RECOGNITION_INTERNAL_TOKEN = [Convert]::ToHexString(
  [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
).ToLowerInvariant()

docker compose `
  -f ops/validation-stages/20260728-night/docker-compose.yml `
  -f ops/validation-stages/20260729-ai/docker-compose.override.yml `
  up -d
```

模型首次下载：

```powershell
docker exec cargo-platform-validation-local-model ollama pull qwen3.5:4b-q4_K_M
```

回退只针对 6173：

```powershell
docker compose `
  -f ops/validation-stages/20260728-night/docker-compose.yml `
  -f ops/validation-stages/20260729-ai/docker-compose.override.yml `
  down

docker compose -f ops/validation-stages/20260728-night/docker-compose.yml up -d
```

回退后应恢复原数据卷和 `stage-17`/`stage-14` 镜像；AI 的模型卷、会话卷和冷库卷保留，不删除。

## 当前 6173 镜像

- backend：`cargo-platform-validation-backend:ai-5d5ec7b`
- parser：`cargo-platform-validation-parser:ai-1cb51cc`
- UI：`cargo-platform-validation-ui:ai-287ca3b`
- AI 识别：`cargo-platform-ai-recognition:ai-98ebbd8`
- 本地模型：`qwen3.5:4b-q4_K_M`

## 验收结果

2026-07-29 使用 `scripts/verify_ai_recognition_e2e.py` 完成冷启动验收，报告保存在
`runtime/acceptance-report.json`：

- 冷库完整性通过，原始记录 `1843`，识别规则和历史派生表初始均为 `0`。
- 结构化单商品任务 `66`：`1` 张面单生成 `1` 行。
- 结构化多商品且重复打印任务 `67`：`2` 张相同面单均保留，共生成 `4` 行，没有去重。
- 文本格式任务 `70`：首次 AI 候选不进入正常行；管理员确认后生成
  `ai-cold-start-r0002`，重跑得到 `1` 行，刷新不再调用模型。
- 不完整陌生格式任务 `68`：无正常行，保留明确异常。
- 真实 1688 单文档任务 `71`：生成 `ai_rule_pending` 会话，正常行仍为 `0`；
  候选的商品、销售属性 1、销售属性 2、数量与只读答案集对应样本一致。
- 答案集覆盖 `39` 条原始记录、`118` 张父面单、`121` 条商品行。
- 对每个验收任务均满足 `采集打印覆盖 = 正常解析覆盖 + 明确异常覆盖`。

AI 故障演练只停止 `cargo-platform-validation-ai-recognition`：

- 已固化任务 `66`、`67`、`70` 仍返回 `parsed`，耗时均小于 `0.03` 秒。
- 陌生任务 `68` 返回 `ai_unavailable`，保留 `1` 张异常面单、`0` 条正常行。
- 演练后 AI 服务已恢复，容器重启次数为 `0`。

执行完整验收：

```powershell
python scripts/verify_ai_recognition_e2e.py `
  --base-url http://127.0.0.1:18001 `
  --ai-url http://127.0.0.1:18111 `
  --answer-set ops/validation-stages/20260729-ai/runtime/answer-set.jsonl
```

## 5173 未变快照

验收前后均为：

- backend：容器 `eb42223185ae`，镜像
  `cargo-platform-backend:stage-17-7bd0d9f`，重启 `0`，数据卷
  `cargo-platform-data:/data`
- tenant UI：容器 `5ed09f8a74fc`，镜像
  `cargo-platform-tenant-ui:stage-17-7bd0d9f`，重启 `0`
- parser：容器 `5d76b24c5040`，镜像
  `cargo-platform-waybill-parser:stage-14-66d2959`，重启 `0`
