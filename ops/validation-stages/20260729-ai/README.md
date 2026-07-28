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
