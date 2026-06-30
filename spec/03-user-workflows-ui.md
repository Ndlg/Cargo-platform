# 03 User Workflows And UI

The UI should be organized around the business workflow.

## Page Flow

1. `采集记录`
   - collector online/offline state
   - collected waybill count
   - latest task status

2. `面单解析`
   - raw waybill preview
   - parsed child rows
   - special-order status
   - parse errors
   - active rule pack indicator

3. `订单行整理`
   - editable table of business rows
   - confirm/exclude/revise actions
   - row status filters
   - keyboard navigation

4. `商品匹配`
   - product/SKU/image matching status
   - unmatched groups
   - conflict groups
   - rule preview and save

5. `异常处理`
   - rows needing review
   - reason groups
   - quick jump back to the responsible workflow

6. `导出中心`
   - normal row count
   - exception row count
   - workbook preview/download

7. `规则包`
   - import
   - export
   - activate
   - disable
   - inspect revision history

## UI Rules

- Show business labels first, such as `第1批-第24单-子1`.
- Keep raw IDs in diagnostics only.
- Avoid giant unstructured text dumps.
- Put parsed fields in a table where errors are easy to scan.
- Provide "show more" or pagination for large batches.
- Preview impact before applying changes.
- A button should always explain what it will affect.
- Do not make users hunt for hidden technical state.

## Review States

Rows should use clear states:

- `可进入商品匹配`
- `需要复核`
- `特殊单`
- `已排除`
- `商品未命中`
- `SKU未命中`
- `图片未命中`
- `冲突`

Each state must have a next action.
