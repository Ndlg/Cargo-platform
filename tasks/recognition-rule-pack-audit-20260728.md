# 识别规则包生效情况审计（2026-07-28）

## 边界

- 数据源：6173 独立验证环境的数据副本。
- 只统计任务、规则字段和结构，不记录商品、收件人、快递单号或面单原文。
- 本轮只更新 6173 验证规则包，不更新现场规则包，不部署正式环境。

## 固定回归样本

- 多文档：任务 55（1 条原始记录生成 8 张父面单）。
- 多商品：任务 42（已验证多商品拆分并保留来源）。
- 相同内容保留：任务 41（A、B、A 三行必须全部保留，不去重）。
- 特殊单：任务 54、56、57。
- 重连回放：任务 58。
- 不可读、已匹配但缺图片：当前副本没有可靠真实样本，继续使用已有合成回归样本。
- 高拆分观察：任务 57 单张父面单最多 11 个子行，先作为待核查样本，不把 11 行固化为正确答案。

## 当前启用规则包

- 规则包：`current-user-shoes-v1`，版本 `1.2.0`。
- 选择解析器：`order_row_parser`。
- 真正参与解析的子规则：
  - `structured_item_sources`
  - `special_text_keywords`（只读取 `keyword`、`status`、`reason`）
  - `requires_active_rule_pack`（必须为真）
  - `multi_item`（多商品拆行、保留追溯、输出商品子行必须为真）
  - `label_cleanup`
  - `manual_label_only`
  - `non_shoe`
  - `quantity`
  - `size_normalization`

上述五类子规则已在统一解析输出边界真实生效：

- `quantity.default_if_missing` 和 `manual_label_only.default_quantity_if_missing`
- `label_cleanup.strip_prefixes` 和 `label_cleanup.separator_chars`
- `size_normalization.enabled` 和 `size_normalization.strip_purchase_hint`
- `manual_label_only.allow_empty_product`
- `non_shoe.allow_non_numeric_sales_attr2`

## 隐藏解析行为

以下行为仍由 `services/waybill-parser/service_app/order_row_engine.py` 内部代码决定，而不是由可编辑子规则决定：

- 商品字段候选名称和读取优先级。
- 文本拆行、分号拆分、重复数量拆分和多商品组合方式。
- 鞋码、销售属性和显式数量的正则。
- 非业务行、取消占位行和备注行过滤。
- 抖音商品文本的专用解析顺序。

这些剩余行为仍需逐项迁移或登记，不能声称整个解析器已经完全规则化。

## 本轮整改

识别服务的校验和说明接口现在会明确返回：

- `selected`：选择了解析器的字段。
- `applied`：当前确实参与解析的字段。
- `configured_but_not_applied`：规则包已经配置但解析代码未使用的字段。
- `policy_field_not_applied` 警告。

特殊单识别已不再硬编码“微信”：只有规则包声明关键词时才生效，关键词、状态和异常原因均来自规则包。现行规则包仍声明“微信”，所以固定样本的业务结果不变。

规则包页面“编辑子规则”当前开放特殊单关键词、缺少数量默认值、字段标签清理、尺码提示清理、纯属性单和非数字规格六类业务项。
多商品拆行、原文追溯和商品子行输出属于不可关闭的业务合同，规则包校验会拒绝关闭这些约束。
缺少规则包、规则包无效、识别服务不可用现在分别返回不同状态，不再把无效规则包静默显示为零行。

后续迁移子规则时，每迁移一项，都必须先用固定样本预览并通过零漏单覆盖检查。

## 阶段 14 验证结论

- 6173 启用规则包：`current-user-shoes-v1` `1.2.0`。
- 59 个已完成轮次、1979 张父面单全部覆盖。
- 2240 个商品结果 = 1545 条正常导出 + 695 条异常导出。
- 硬失败为 0；相同内容继续保留，不做业务去重。
- 5173 和现场识别规则包未修改。
