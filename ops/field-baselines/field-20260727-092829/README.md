# 现场基线 field-20260727-092829

记录时间：2026-07-27 09:28:29 +08:00

这是当前 5173 现场版本的不可变回退节点。现场后端已部署 Git 提交
`d13101004cc74054b83452fa37c6cdbe6bcb7974`，Git 标签为
`field-20260727-092829`。本次只替换后端，识别服务、业务前端、管理前端和
`cargo-platform-data` 数据卷均沿用原现场版本。

## 精确镜像

| 组件 | 本地回退标签 | 镜像 ID |
| --- | --- | --- |
| 后端 | `cargo-platform-backend:field-20260727-092829` | `sha256:038224cd1a621f276d9a5aa2e225ec23c3ab3543d2d9621e26c251ca965a3622` |
| 识别服务 | `cargo-platform-waybill-parser:field-20260727-092829` | `sha256:cfec30ba5af48a8a6b7ff4ad9f1854689dacca2c7b3a42370c744220c948c7b4` |
| 业务前端 | `cargo-platform-tenant-ui:field-20260727-092829` | `sha256:13f1345c0da7d72d82bef21d75b71d94693a4e6769b3b72d69ef57287f8b2458` |
| 管理前端 | `cargo-platform-admin-ui:field-20260727-092829` | `sha256:6e257681e7cdb98d8b13c5122080f9d929d185a0edd8f73c4b727749cc0c51f2` |

离线镜像包：

`C:\Users\ndlgx\Documents\Projects\GitHub\Ndlg\cargo-platform-field-baselines\field-20260727-092829\cargo-platform-images.tar`

SHA-256：

`095EE07B328261694003C40EA29CC78E2342BFDC8B63C9957F7C1DF779705376`

## 数据备份

停止采集后的 SQLite 在线备份：

`C:\Users\ndlgx\Documents\Projects\GitHub\Ndlg\cargo-platform-deploy-backups\pre-d131010-stopped-20260727-092745\cargo-platform.db`

- 大小：65,245,184 字节
- SHA-256：`F74A85D6B7B59D0616AD0599F776FF100C6D4C169F0B814D05CECB46FA4FFDC4`
- `PRAGMA integrity_check`：`ok`

## 现场验收

- 5173 页面返回 HTTP 200。
- 后端直连和 5173 代理健康检查均返回 `status=ok`、`version=d131010`。
- 独立识别服务健康检查返回 `ok`。
- 3 台采集器均为在线、监听中。
- 数据库 `PRAGMA quick_check` 返回 `ok`。
- 任务 61 共 10 张面单：8 行正常报货表、2 行特殊单保留在异常面单。
- 后端切换后近期日志未发现 `ERROR`、`Traceback` 或未捕获异常。

## 回退

回退会重建业务容器，只能在停止采集并明确获准的维护窗口执行：

```powershell
docker load --input "C:\Users\ndlgx\Documents\Projects\GitHub\Ndlg\cargo-platform-field-baselines\field-20260727-092829\cargo-platform-images.tar"
docker compose -f docker-compose.yml -f ops/field-baselines/field-20260727-092829/docker-compose.override.yml config --quiet
docker compose -f docker-compose.yml -f ops/field-baselines/field-20260727-092829/docker-compose.override.yml up -d --no-build --no-deps waybill-parser backend tenant-ui platform-admin-ui
```

恢复本次升级前的现场版本时，使用
`ops/field-baselines/field-20260727-004852`。两条回退路径都不会删除或重建
`cargo-platform-data`。
