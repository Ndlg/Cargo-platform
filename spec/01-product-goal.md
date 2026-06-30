# 01 Product Goal

## Goal

Help users prepare supplier order-reporting spreadsheets from collected waybill/order data.

The final workbook must contain normal rows and exception rows. Normal rows must be ready for supplier reporting. Exception rows must be easy to review and fix.

## Target Output

Normal sheet columns:

| Column | Meaning |
| --- | --- |
| 商品 | Supplier-facing product/category result |
| 销售属性1 | Color, style, version, or first business attribute |
| 图片 | Matched product/SKU image label or asset |
| 销售属性2 | Size or second business attribute |
| 数量 | Normalized numeric quantity |
| 备注 | Business remark |
| 图片匹配文本 | Text used for image/product traceability |

Exception sheet:

| Column | Meaning |
| --- | --- |
| 图片匹配文本 | Enough text for the user to identify the unresolved order |

## Real-World Requirements

- Some waybills contain one product.
- Some waybills contain multiple products and must split into several rows.
- Some waybills are special manual/message orders and should remain visible with a clear status.
- Some rows have missing quantity; default quantity may be `1` only when the active rule pack says that is safe.
- Attribute labels such as color/size labels should be normalized away when they are not useful in the final row.
- Size values should be normalized to clean size text when recognized.

## Success Criteria

- The user can see how many waybills were collected.
- The user can see how many order rows were parsed.
- Multi-product rows are split and counted.
- Rows that cannot be parsed safely are visible, not blank.
- Product/SKU/image matching consumes parsed rows only.
- Export row counts add up: normal rows plus exception rows cover the parsed/exportable set.
