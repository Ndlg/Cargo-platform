# 6173 夜间整改阶段清单

本清单只管理验证环境。`5173`、现场数据库、采集器均不在切换范围内。

| 阶段 | Git 标签 | 提交 | 内容 | 6173 状态 |
| --- | --- | --- | --- | --- |
| 01 | `validation-stage-01-regression-gate-20260728` | `f96497a` | 现场基线和回归门禁 | 已留存 |
| 02 | `validation-stage-02-policy-audit-20260728` | `74c5054` | 规则字段生效审计 | 已留存 |
| 03 | `validation-stage-03-special-rules-20260728` | `6435290` | 特殊单关键词规则化 | 已部署验证 |
| 04 | `validation-stage-04-rule-status-20260728` | `2a55022` | 精确区分规则包错误 | 已随最终阶段验证 |
| 05 | `validation-stage-05-rule-contract-20260728` | `adedebe` | 强制业务约束进入规则包校验 | 已随最终阶段验证 |
| 06 | `validation-stage-06-rule-editor-20260728` | `d14cc37` | 编辑已生效的特殊单子规则 | 已随最终阶段验证 |
| 07 | `validation-stage-07-business-labels-20260728` | `7f89d21` | 业务结果不显示技术批次号 | 已随最终阶段验证 |
| 07b | `validation-stage-07b-label-sweep-20260728` | `c3f6788` | 清理其余页面的技术任务号 | 已留存 |
| 09 | `validation-stage-09-navigation-fix-20260728` | `c0b6363` | SKU 修复直达资料页、移除手工应用按钮、轻量轮次查询 | 已保留即时回退 |
| 10 | `validation-stage-10-remove-manual-apply` | `6f0e5e6` | 从接口、前端和项目口径删除手工应用结果路线 | 已随最终阶段验证 |
| 11 | `validation-stage-11-export-matched-rows` | `63260c7` | 已匹配商品行即进入正常表，缺少可选属性不降级为异常 | 已随最终阶段验证 |
| 12 | `validation-stage-12-faster-navigation` | `e42781c` | 页面只查询实际需要的采集轮次，避免全历史计数 | 已随最终阶段验证 |
| 13 | `validation-stage-13-rule-pack-subrules` | `e777a2d` | 现有五类识别子规则真实参与解析并可在页面维护 | 已随最终阶段验证 |
| 14 | `validation-stage-14-collector-resilience` | `66d2959` | 未分类轮询异常记录原因并继续后台重试 | 当前 6173 |

## 固定验证资源

- 页面：`http://127.0.0.1:6173`
- 后端：`http://127.0.0.1:18000`
- 解析服务：`http://127.0.0.1:18010`
- 数据副本卷：`cargo-platform-validation-data-20260727-014134`
- 独立网络：`cargo-platform-validation-20260727-014134`

不得删除或重新创建数据副本卷。阶段切换只替换验证容器镜像。

## 切换原则

1. 记录当前三个验证容器的镜像名。
2. 只停止并替换名称以 `cargo-platform-validation-` 开头的容器。
3. 使用目标阶段对应镜像启动验证容器。
4. 验证健康接口、页面和指定样本。
5. 不通过时恢复刚才记录的镜像。

## 当前 6173 镜像

- 页面：`cargo-platform-validation-ui:stage-14-66d2959`
- 后端：`cargo-platform-validation-backend:stage-14-66d2959`
- 解析服务：`cargo-platform-validation-parser:stage-14-66d2959`
- 启用规则包：`current-user-shoes-v1` `1.2.0`
- 采集器下载包 SHA-256：`B0C9AD3F7843DF361D6C9D75B98B4D4BFCB191A45226BBEAA01AF9F17712879D`

## 已保留的立即回退容器

- stage 03 页面：`cargo-platform-validation-ui-rollback-f96497a-night`
- stage 03 后端：`cargo-platform-validation-backend-rollback-f96497a-night`
- stage 03 解析服务：`cargo-platform-validation-parser-rollback-special-r2-night`
- stage 02 解析服务：`cargo-platform-validation-parser-rollback-policy-audit-20260728`
- stage 01 解析服务：`cargo-platform-validation-parser-rollback-20260728-005134`
- stage 07 页面：`cargo-platform-validation-ui-rollback-stage08-7f89d21`
- stage 08 页面：`cargo-platform-validation-ui-rollback-stage08-before-stage09`
- stage 08 后端：`cargo-platform-validation-backend-rollback-stage08-before-stage09`
- stage 09 页面：`cargo-platform-validation-ui-rollback-stage09-before-stage14`
- stage 09 后端：`cargo-platform-validation-backend-rollback-stage09-before-stage14`
- stage 08 解析服务：`cargo-platform-validation-parser-rollback-stage08-before-stage14`

这些容器均已停止但未删除。切换时继续使用同一个验证数据副本卷，不复制、不删除数据。

## 阶段 14 立即回退

容器级回退（只操作 6173）：

```powershell
docker stop cargo-platform-validation-ui cargo-platform-validation-backend cargo-platform-validation-parser
docker rename cargo-platform-validation-ui cargo-platform-validation-ui-rejected-stage14
docker rename cargo-platform-validation-backend cargo-platform-validation-backend-rejected-stage14
docker rename cargo-platform-validation-parser cargo-platform-validation-parser-rejected-stage14
docker rename cargo-platform-validation-ui-rollback-stage09-before-stage14 cargo-platform-validation-ui
docker rename cargo-platform-validation-backend-rollback-stage09-before-stage14 cargo-platform-validation-backend
docker rename cargo-platform-validation-parser-rollback-stage08-before-stage14 cargo-platform-validation-parser
docker start cargo-platform-validation-parser cargo-platform-validation-backend cargo-platform-validation-ui
```

只回退规则包时，向 6173 的“识别规则包”页面导入并启用：

`ops/validation-stages/20260728-night/active-rule-pack-before-stage14.json`

该文件是部署前正在启用的 `1.1.0` 完整 payload。不要删除或重建数据卷。

## 明早逐级判断

从当前 `14` 开始验证。若不通过：

1. 先按上面的命令恢复 stage 09 页面、后端和 stage 08 解析服务。
2. 若需要定位本轮具体阶段，按标签依次构建并切换 `13`、`12`、`11`、`10`；每次只切一个标签。
3. 再切页面到保留的 stage 07 页面，判断是否只是最后一轮文字收口问题。
4. 再按 Git 标签依次构建并切换 `06`、`05`、`04`；每次只切一个标签。
5. 若仍不通过，直接恢复三个 stage 03 保留容器。
6. stage 03 以下只需要替换解析服务，即可依次判断特殊单规则、规则审计、原始基线。

所有 Git 标签都是独立固定点。阶段 04 至 06 若需要运行，使用对应标签重新构建验证镜像；不切换 `main`，不操作 `5173`。

## 当前验收结果

- 后端：179 项测试通过。
- 前端：类型检查和生产构建通过。
- 历史副本：59 个已完成轮次、1979 张父面单全部覆盖，2240 个商品结果，正常 1545、异常 695，硬失败 0。
- 保守口径：任务 41 的 1 条重复子行不去重；任务 57 的 1 张高子行面单仅提示、不丢弃。
- 页面：规则包 `1.2.0` 已启用；六类业务子规则编辑项可打开；取消不保存。
- 交互：任务 62 的“维护 SKU”直达 `/admin/products?product_id=18`；无效的“应用规则到本轮”不存在。
- 导出：任务 62 报货预览为 164 张面单、212 个商品结果；Excel 接口返回 200，文件大小 1,217,855 字节。
- 性能：页面不再查询无用的全历史轮次计数；规则包页面直达加载在本次浏览器复测中小于 1 秒。
- 日志：验证页面、后端、解析服务未发现 5xx、异常堆栈。
- 现场：`5173`、现场后端、现场解析服务容器 ID 和启动时间未变化。
