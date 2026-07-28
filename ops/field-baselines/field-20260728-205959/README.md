# 现场基线 field-20260728-205959

记录时间：2026-07-28 20:59:59 +08:00

5173 已切换到 6173 验证通过的当前版本。业务前端和后端对应提交
`7bd0d9f6082eeec4f81c1ac50bd7688f34181a0f`，解析服务对应阶段 14
提交 `66d2959`。`cargo-platform-data` 原卷沿用，未删除、未重建。

## 当前镜像

| 组件 | 固定标签 | 镜像 ID |
| --- | --- | --- |
| 业务前端 | `cargo-platform-tenant-ui:field-20260728-205959` | `sha256:76470b1c27f60fbde0702c2040c7edcb0d9c281ce3c94d3ab842c8f88bb01669` |
| 后端 | `cargo-platform-backend:field-20260728-205959` | `sha256:edede5e937fcd27f50e60a5e5acd0ec9c936dd380783b9bc2ce35f87d789a099` |
| 解析服务 | `cargo-platform-waybill-parser:field-20260728-205959` | `sha256:edfb878c51f25b4b5451551dbcc1670c5e2a73f35fb3f617a6861e6e4c0a5bb5` |

## 数据备份

升级前 SQLite 在线备份保存在现场数据卷内：

`/data/backups/field-pre-stage17-20260728-205959.db`

- 大小：66,306,048 字节
- SHA-256：`BCF943202CF3DD4BC1D36954A6843B7ACDA395D737B9E82CF8EC1238D980F216`
- `PRAGMA integrity_check`：`ok`
- 备份时正在采集轮次：0
- 备份时在线采集器：3

## 验收

- 5173 页面、5173 代理健康接口、后端直连和解析服务均返回 HTTP 200。
- 后端健康版本为 `7bd0d9f`。
- 业务前端、后端和解析服务与 6173 对应镜像 ID 一致。
- `cargo-platform-data` 仍挂载到后端 `/data`。
- 数据库 `PRAGMA quick_check` 返回 `ok`。
- 3 台采集器在线并监听，三个新容器重启次数均为 0。
- 最近一个已完成轮次验证为 94 张父面单、96 个商品结果；90 行匹配、2 行特殊单、4 行可处理异常，总数完整覆盖。
- 切换后的后端和解析服务日志未发现 5xx、`ERROR` 或 `Traceback`。
- 6173 验证环境继续运行；19 个旧验证回退容器已删除，验证数据卷均保留。

## 即时回退

仅保留本次升级前的一套现场回退容器：

- `cargo-platform-field-rollback-20260728-205959-ui`
- `cargo-platform-field-rollback-20260728-205959-backend`
- `cargo-platform-field-rollback-20260728-205959-parser`

回退命令不会删除或重建 `cargo-platform-data`：

```powershell
docker stop cargo-platform-tenant-ui cargo-platform-backend cargo-platform-waybill-parser
docker rename cargo-platform-tenant-ui cargo-platform-tenant-ui-rejected-field-20260728-205959
docker rename cargo-platform-backend cargo-platform-backend-rejected-field-20260728-205959
docker rename cargo-platform-waybill-parser cargo-platform-waybill-parser-rejected-field-20260728-205959
docker rename cargo-platform-field-rollback-20260728-205959-ui cargo-platform-tenant-ui
docker rename cargo-platform-field-rollback-20260728-205959-backend cargo-platform-backend
docker rename cargo-platform-field-rollback-20260728-205959-parser cargo-platform-waybill-parser
docker start cargo-platform-waybill-parser cargo-platform-backend cargo-platform-tenant-ui
```
