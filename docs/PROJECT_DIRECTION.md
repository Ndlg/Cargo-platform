# Cargo Platform Project Direction

## One-Sentence Goal

Cargo Platform helps users turn collected order and waybill data into a supplier order reporting Excel workbook.

The project should be judged by one question: does this make it faster and more reliable for the user to produce the reporting spreadsheet?

## Current Reset State

The previous route became too complex and mixed several responsibilities together. It has been intentionally cleared.

Cleared or downscoped:

- Old print-template rule page.
- Old product-recognition rule page.
- Old structure fingerprint / generalized matching API route.
- Old field-definition and match-rule data.
- Old module agent threads.

Retained:

- Collector client and collector runtime.
- Raw capture records.
- Parsed standard detail records.
- Product library.
- SKU library.
- Image assets.
- Stall library.
- Export/download foundation.
- User, workspace, and admin shell.

The next build must not patch the old route. It must rebuild a simple modular workflow.

## Target Workflow

1. The user starts collection.
2. Collector clients upload raw order/waybill records.
3. The system reads each waybill into selectable text blocks.
4. The user maps those text blocks into five standard fields.
5. The 商品匹配模块 consumes only those five fields and lets the user configure product, SKU, and image matching rules.
6. The export module consumes final results and creates the reporting workbook.
7. Anything unresolved goes to exceptions for review.

## Module Contracts

### 1. Collection Module

Purpose: get raw data into the system.

Input:

- Printer component output.
- Collector runtime payload.

Output:

- `raw_capture_record`

Allowed responsibilities:

- Register collector clients.
- Receive heartbeats.
- Start/stop capture tasks.
- Store raw records.
- Preserve enough source metadata for later traceability.

Forbidden responsibilities:

- Defining fields.
- Guessing products.
- Matching SKU or images.
- Exporting Excel.

### 2. Waybill Reading Module

Purpose: convert a raw record into a readable sample and selectable text blocks.

Input:

- `raw_capture_record`

Output:

- `waybill_sample`
- `text_blocks[]`

Suggested text block shape:

```json
{
  "block_id": "string",
  "text": "string",
  "source": "string",
  "line_index": 0,
  "order": 0,
  "raw_record_id": 0
}
```

Allowed responsibilities:

- Parse raw payload format.
- Preserve original display order.
- Split text into user-selectable blocks.
- Keep traceability back to the raw record.

Forbidden responsibilities:

- Deciding which block is product.
- Saving field rules.
- Matching product/SKU/image.
- Exporting Excel.

### 3. Field Definition Module

Purpose: let the user define what the waybill text means.

Input:

- `waybill_sample`
- `text_blocks[]`

Output:

Five standard fields:

```json
{
  "product": "string",
  "sales_attr1": "string",
  "sales_attr2": "string",
  "quantity": "string",
  "remark": "string"
}
```

Allowed responsibilities:

- Show the waybill sample and text blocks.
- Let the user map blocks to the five fields.
- Save a reusable mapping only after user confirmation.
- Apply confirmed mappings only to a user-confirmed waybill type or user-selected record scope.

Forbidden responsibilities:

- Product category decisions.
- SKU recognition.
- Image matching.
- Exporting Excel.

Important rule:

The five fields are not the final Excel business result. They are the standardized waybill fields that downstream modules consume.

### 4. 商品匹配模块

Purpose: consume five standard fields and match those field values to product, SKU, and image results according to user-confirmed rules.

Input:

```json
{
  "product": "string",
  "sales_attr1": "string",
  "sales_attr2": "string",
  "quantity": "string",
  "remark": "string"
}
```

Output:

```json
{
  "product": "string",
  "sku": "string",
  "image": "string",
  "match_status": "matched | product_unmatched | sku_unmatched | image_unmatched | conflict",
  "exception_reason": "string"
}
```

Allowed responsibilities:

- Let the user choose which of the five fields are used to define product meaning.
- Let the user bind product, SKU, and image rules.
- Preview matches before saving.
- Mark unresolved rows as exceptions.

Forbidden responsibilities:

- Splitting waybill raw text.
- Changing field definitions.
- Maintaining the field-definition UI.
- Exporting Excel.

Important rule:

Do not hard-code business guesses. For example, `4.0` in one field does not always mean product `4.0`; the user decides which field or field combination defines the product.

### 5. Export Module

Purpose: generate the supplier reporting workbook.

Input:

- 商品匹配结果.

Output:

Normal sheet columns:

```text
商品 / 销售属性1 / 图片 / 销售属性2 / 数量 / 备注 / 图片匹配文本
```

Exception sheet:

```text
Sheet: 异常面单
Column: 图片匹配文本
```

Allowed responsibilities:

- Group rows into the target workbook structure.
- Insert image references or image content according to the chosen export behavior.
- Put unresolved rows into the exception sheet.

Forbidden responsibilities:

- Parsing waybill raw text.
- Defining the five fields.
- Guessing product/SKU/image matches.

## Non-Negotiable Architecture Rules

- Every module must expose clear input and output.
- Downstream modules consume upstream output; they must not reach into upstream internals.
- User confirmation belongs to the module that owns the decision.
- A module must not silently save rules for another module.
- Unknown or ambiguous data must become an exception, not a hidden guess.
- Do not rebuild the old complex rule-learning workbench unless the user explicitly requests it.
- Do not rely on automatic same-template or similar-waybill recognition as a trusted foundation. Similarity is advisory only; reusable behavior must be confirmed by the user.

## Waybill Similarity Policy

Previous attempts showed that the system cannot accurately and reliably decide which waybills are the same type or merely similar. Therefore, similarity matching must not be the core rule engine.

The product should use this policy:

1. The system may show possible similar samples.
2. The user decides whether those samples belong to the same reusable waybill type.
3. The user maps text blocks into the five standard fields for that type.
4. The system previews affected rows before saving.
5. Only confirmed mappings become reusable rules.
6. Anything uncertain remains pending or becomes an exception.

This keeps the system semi-automatic: it can suggest and batch-apply, but it must not silently decide.

## Bulk Processing Model

Bulk processing must not mean hidden automatic recognition, and it must not mean one-by-one manual work.

The correct model is:

1. The system reads many records into text blocks.
2. The system may show representative records and possible candidate groups.
3. The user confirms a waybill type or selects an explicit record scope.
4. The user maps the text blocks into the five standard fields once for that type or scope.
5. The system previews the affected rows before saving.
6. After confirmation, the mapping is applied in bulk to that confirmed type or selected scope.
7. 商品匹配模块 then consumes the resulting five-field rows in bulk.
8. Rows outside confirmed mappings stay pending or go to exceptions.

This gives the user batch processing without trusting unreliable automatic similarity matching.

## Progressive Rule Learning Model

The system should become easier to use over time.

The first use may require more setup:

1. The user confirms field mappings for common waybill types or selected record scopes.
2. The user confirms which five-field values link to which product, SKU, and image.
3. The system stores those confirmations as inspectable reusable rules.

Later uses should require less work:

1. New records are read into text blocks.
2. Confirmed field mapping rules are applied where their confirmed scope matches.
3. Confirmed 商品匹配规则 consume the resulting five fields.
4. The system shows counts for matched, pending, conflicting, and exception rows.
5. The user only reviews new patterns, conflicts, and unmatched rows.
6. User corrections become new confirmed rules after preview and confirmation.

This is learning by user-confirmed rules, not learning by hidden guesses. Every reusable rule should have a clear source, scope, preview result, and safe way to disable or revise it.

## Implementation Direction

The next development phase should create a minimal working path:

1. Rebuild waybill reading as a simple text-block producer.
2. Rebuild field definition as a five-field mapping UI.
3. Rebuild 商品匹配模块 as a five-field consumer.
4. Reconnect export to the new product result contract.
5. Add exceptions only after the basic path works.

Performance, clustering, machine-code matching, or generalized inference can be considered later, only after the basic workflow is stable and inspectable.

