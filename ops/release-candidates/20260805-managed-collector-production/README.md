# Cargo Platform 现场版本收口

收口日期：`2026-08-05`

## 版本基线

- 源码提交：`7d73683dab0204280c94a8552af441e23a8922d3`
- `origin/main` 与源码提交一致。
- 生产端口：业务页面 `5173`，管理页面 `5174`。
- 验证端口：业务页面 `6173`，管理页面 `6174`。
- 生产和验证环境的 backend、tenant-ui、admin-ui、waybill-parser 均为
  `1.0.0-rc.6`。
- 三台采集器均在线，使用采集协议 v2；采集器版本保持
  `1.0.0-rc.5+c0e541d3af91`，本次 rc.6 未修改采集器代码。

## 本次收口能力

- 导出表头可隐藏不需要的列，并保证至少保留一列。
- 隐藏状态同时作用于预览和实际 Excel；生产、验证环境均已用真实任务验证。
- 当前租户已启用 5 个面单指纹：`CN-ITEM-INFO`、`CN-PRINT-XML`、
  `CN-CUSTOM-CONTENT`、`CN-PACKAGE-ITEMS`、`CLOUD-PRODUCT-INFO`。
- 指纹配置页可查看中文字段名和原始英文字段名。

## 验证结果

- 生产和验证环境共 8 个业务容器全部 healthy，8 个页面或健康端点均返回
  HTTP 200。
- 后端全量测试：`438 passed`；导出专项测试：`16 passed`。
- 前端类型检查和生产构建通过；独立代码复核无 Critical / Important 问题。
- 生产数据库 `integrity_check=ok`。
- 生产库保留原始采集记录 2194 条、采集任务 72 个；其中各有 3 条软删除记录，
  当前活动采集任务为 0。
- 当前租户启用指纹数为 5。
- 真实 Excel 已验证隐藏 `图片匹配文本` 后仅导出：
  `商品、销售属性1、图片、销售属性2、数量、备注`。

## 镜像

| 模块 | 镜像 | 本地镜像 ID |
| --- | --- | --- |
| backend | `ghcr.io/ndlg/cargo-platform-backend:1.0.0-rc.6` | `sha256:05f0fe6bb7c7e4ce3b519059b0f9c96a28178903ec5cf5433408e5f80b3bde33` |
| tenant-ui | `ghcr.io/ndlg/cargo-platform-tenant-ui:1.0.0-rc.6` | `sha256:47c2ab8b81af811aa26320920cf8d0163d0d26183da3b613897f408239e13273` |
| admin-ui | `ghcr.io/ndlg/cargo-platform-admin-ui:1.0.0-rc.6` | `sha256:7c53dd8cf3f978b109bc65069a8d82d4a6556c0aed9a517814550e95a9957870` |
| waybill-parser | `ghcr.io/ndlg/cargo-platform-waybill-parser:1.0.0-rc.6` | `sha256:72a31d6f00f472f850310f150398b308d4ce88682bc498d24c5acb8dc4479f92` |

## 回退资产

- 生产 rc.6 发布前数据库：
  `cargo-platform-deploy-backups/production-rc6-20260805-211559/cargo-platform-data-20260805-211600078.db`
  (`SHA256 097214820368CA50675ACB8F6A91B90A181DEFDB0CE8BAE72536910E96BED43F`)
- 生产指纹配置前数据库：
  `cargo-platform-deploy-backups/production-fingerprint-20260805-211200/cargo-platform-data-20260805-211200589.db`
  (`SHA256 7A79241FE22E37A9D1B1EFAF0A95131CF7CDB01F5F96665C71F6960A97B0E4C0`)
- 验证 rc.6 发布前数据库：
  `cargo-platform-deploy-backups/validation-rc6-20260805-210620/cargo-platform-validation-no-model-20260801-153819-20260805-210620710.db`
  (`SHA256 5E7176D4E3CE05E277D1B014F72733540247996C7D08703A94A71CFB6675E5DB`)
- 应用回退优先把四个业务镜像统一切回 `1.0.0-rc.5`；只有数据库完整性或数据变更
  确实异常时才恢复数据库快照。

## 保留项

- 保留 `cargo-platform-data` 和当前 6173 验证数据卷。
- 保留 rc.5、rc.6 镜像及上述快照，直到现场版本完成业务验收。
- 旧 Redis 属于原业务环境，不纳入本次清理。
- 当前没有停止状态的临时容器；未删除历史验证卷或历史回退镜像。
