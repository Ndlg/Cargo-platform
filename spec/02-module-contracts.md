# 02 Module Contracts

Each module owns one contract. A module must not reach backward into another module's internals.

## Collection Module

Input:

- collector payload
- source machine/component metadata

Output:

- raw capture record

Must not:

- parse rows
- match product/SKU/image
- export Excel

## Waybill Parsing Module

Input:

- raw capture record
- active recognition rule pack

Output:

- parent waybill sample
- `order_rows[]`
- parse status
- exception reason when needed
- source trace

Order row fields:

- `product`
- `sales_attr1`
- `sales_attr2`
- `quantity`
- `remark`
- `image_match_text`
- `parent_label`
- `child_index`
- `child_count`
- `status`

Must:

- split multi-product waybills into child rows
- preserve enough source trace for audit
- normalize quantity and size when rule-pack controlled
- classify special orders without hiding them

Must not:

- choose product assets
- choose SKU assets
- export Excel

## 面单解析结果模块

Input:

- parsed order rows

Output:

- rule-generated order rows ready for matching
- actionable parse exceptions

Must:

- make row status and source trace obvious
- keep rejected or uncertain rows reviewable

Must not:

- edit, confirm, exclude, or approve individual order rows
- store manual row overrides
- repair parsing outside a rule-pack revision

## 商品匹配模块

Input:

- rule-generated order rows
- product/SKU/image assets
- active matching rules

Output:

- product result
- SKU result
- image result
- match status
- exception reason

Must:

- consume order rows only
- preview impact before saving rules
- group unmatched/conflict rows by actionable reason
- leave missing matches as exceptions

Must not:

- parse raw waybills
- mutate parser output
- export Excel

## Export Module

Input:

- product/SKU/image matching output

Output:

- Excel workbook

Must:

- preserve one row per product item
- write normal rows to the main sheet
- write unresolved rows to `异常面单`
- keep workbook counts explainable

Must not:

- infer product meaning
- repair parser output
- hide missing rows
