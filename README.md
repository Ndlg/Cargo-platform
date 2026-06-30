# Cargo Platform

Cargo Platform is an order-reporting tool.

Its goal is to help users turn collected order and waybill data into supplier reporting Excel workbooks.

Current direction:

1. Collect raw order/waybill records.
2. Require an active recognition rule pack.
3. Parse each raw record into one or more editable business order rows.
4. Let users review, correct, confirm, or exclude those rows.
5. Match confirmed rows to product, SKU, and image assets.
6. Export the final supplier workbook and an exception sheet.

Primary business row columns:

- `商品`
- `销售属性1`
- `图片`
- `销售属性2`
- `数量`
- `备注`
- `图片匹配文本`

Multi-product waybills are first-class input. One parent waybill may produce multiple child order rows.

Recognition behavior must come from an active rule pack. Without a rule pack, parsing should stop with a clear message telling the user to import or enable one.

See:

- `AGENTS.md`
- `spec/`
