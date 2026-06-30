# Stage81-WaybillReading-Contract-Cleanup

## Module Contract

- Module: Waybill Reading Module
- Input: `raw_capture_record`
- Output: `waybill_sample` plus selectable `text_blocks[]`
- Does not do: field definition, product/SKU/image recognition, match rule writes, Excel export, `standard_details` generation
- Does not do: automatic same-template detection, similar-waybill grouping, structure fingerprinting, generalized matching, automatic field mapping

## Direction Correction

The user has confirmed that the system cannot reliably identify same-type or similar waybills automatically. Stage81 therefore treats waybill reading as a text-block producer only.

Allowed in this module:

- Preserve display order.
- Preserve line index, source path, document id, raw record id, and other traceability metadata.
- Split raw text into blocks the user can select.
- Read many raw records in one request when scoped by `task_id`.
- Preserve explicit batch order through `record_order`, `sample_order`, and `text_blocks[].order`.
- Provide stable trace metadata such as `selector_key`, `source_path`, `source_text_order`, `source_line_index`, and `segment_index` so the Field Definition Module can build user-confirmed reusable mappings.
- Preserve original source blocks. Any split by comma, semicolon, slash, label-like text, or trailing `*1`/`x1`/`×1` is an auxiliary child candidate only and must not replace the parent block.
- Expose raw source fields from `contents[].data` and `raw_capture_record.source_columns` as `source: "raw_field"` blocks when available. Expose printed CDATA text as `source: "printed_text"` blocks.
- Filter obvious technical raw fields such as ids, tokens, timestamps, database paths, collector metadata, and machine metadata from user-selectable `raw_field` blocks.

Forbidden in this module:

- Automatic grouping by fingerprint or similarity.
- Automatic template detection.
- Automatic field mapping.
- Applying a mapping because a sample looks similar.
- Treating structure machine code, semantic hints, or generalized matching as a foundation.
- Producing product, quantity, remark, SKU, image, or export results.

If similarity is ever added around this module, it must be `suggestion_only` and must not drive automatic grouping, template judgement, or field mapping.

## Bulk Reading Model

Bulk processing does not depend on automatic same-template recognition. Stage81 supports bulk by making text-block output stable and traceable:

- `task_id` returns multiple raw records in `raw_capture_records.id` order.
- Each sample includes `record_order` and `sample_order`.
- Each block includes `line_index`, `order`, `raw_record_id`, `source`, and `source_path`.
- The response includes `batch.ordered_by` so downstream modules know the ordering contract.
- `batch.suggestion_groups` is empty by default. If candidate group metadata is added later, it must stay `suggestion_only` and must not apply mappings or declare records to be the same type.

The Field Definition Module owns user-confirmed type/scope selection, preview, and bulk mapping. The Waybill Reading Module only supplies stable selectable text blocks.

## Progressive Rule Learning Boundary

The wider product is a progressive rule learning system, but this module does not save or apply business rules. Its role is to give downstream modules stable evidence:

- `block_id` uniquely identifies a selectable block within one raw record sample.
- `trace.selector_key` identifies the block's non-semantic source position.
- `order`, `line_index`, `source`, `source_path`, and `raw_record_id` make every block inspectable.
- Field Definition may use this metadata after the user confirms a type or explicit record scope.

The reading layer must not decide that a selector means product, quantity, remark, SKU, image, or any other business concept.

## Original And Child Block Policy

Stage83B corrected the text granularity behavior:

- Every raw field value or printed text line is emitted as `block_kind: "original"`.
- Derived candidates are emitted as `block_kind: "derived_child"` and include `parent_block_id`, `parent_text`, and `split_reason`.
- Child candidates can make `*1` or delimiter-separated display fragments selectable, but they do not replace the original block.
- Label-looking fragments such as `颜色分类:` and `鞋码:` remain non-semantic display text at the reading layer.
- Field Definition decides whether to use an original block or a child block after user confirmation.

## Raw Field Filtering Policy

Stage83E limits `source: "raw_field"` blocks to useful business candidates:

- Always hide obvious technical field names from selectable blocks: `rowid`, `id`, `component_task_id`, `task_time`, `db_path`, `created_at`, `updated_at`, `collector_id`, `token`, `machine_id`, `source_path`, and related source/runtime metadata.
- Hide technical-looking values such as local database paths, ISO-like timestamps, short numeric ids, and long opaque ids/tokens.
- Keep raw fields whose path suggests order or product meaning, such as `productInfo`, `remark`, `sku`, `quantity`, `spec`, `color`, `size`, `title`, `goods`, `item`, `buyer_message`, and `seller_remark`, as long as the value is not an obvious technical value.
- Keep unknown raw fields only when their text looks user-facing, for example Chinese product/remark text.
- Filtering applies only to user-selectable `raw_field` text blocks. Filtered fields are returned as `hidden_raw_fields` metadata with `source_path`, `text`, and `filter_reason`, not as selectable blocks.
- `printed_text` original blocks and derived child blocks remain available even if no business raw field survives filtering.
- The reading layer must not deduplicate `productInfo`, `productShortInfo`, `allProductInfo`, or similar raw fields. If they pass the raw field filter, they remain available for the user to choose.
- The reading layer must not emit structured multi-item fields such as `product`, `sales_attr1`, `sales_attr2`, `quantity`, or `remark`; multi-item parsing belongs to the Field Definition Module after user confirmation.

## Reusable Text-Reading Assets

- `backend/app/services/waybill_parser.py`
  - Safe ideas to reuse: JSON payload loading, `task.documents[]` traversal, `contents[].data` access, `contents[].printXML` CDATA extraction.
  - Isolated in Stage81: calls to `parse_raw_capture_record`, `parse_raw_record`, `find_field_definition_config_for_values`, `candidate_context_for_values`, `_field_definition_values`, and all `StandardDetail` writes.
- `backend/app/services/woda_printxml_parser.py`
  - Safe idea to reuse later with care: basic line cleanup patterns.
  - Isolated in Stage81: `parse_woda_custom_structure`, because it decides product/spec/size/quantity semantics and applies saved field mappings.
- `backend/app/services/waybill_structure_recognition/text.py`
  - Safe idea to reuse: `text_value` style Unicode cleanup.
  - Stage81 implementation keeps an independent `normalize_text` to avoid importing the old recognition package.
- `backend/app/services/waybill_structure_recognition/tokenization.py`
  - Safe idea to reuse: segment splitting for user-selectable blocks.
  - Stage81 implementation keeps an independent `split_selectable_segments` to avoid package-level imports that expose fingerprint/semantic modules.
- `backend/app/services/waybill_structure_recognition/fingerprint.py`, `matching.py`, `semantics.py`, `candidates.py`, `projection.py`, `explain.py`
  - Isolated in Stage81: these belong to the old fingerprint/generalization/semantic matching direction and must not be used as the foundation of waybill reading.

## Isolated Legacy Route

The new interface does not use or recreate old APIs:

- `/api/v1/waybill-structure-*`
- `/api/v1/print-template-configs`
- `/api/v1/match-rules`
- `/api/v1/field-definitions`
- `/api/v1/key-field-sets`
- `/api/v1/field-role-configs`

It also does not query or write the cleared rule-layer tables.

The new interface also does not return template ids, structure fingerprints, similarity groups, semantic roles, recognized fields, or auto-applied mappings.

## New Interface

`GET /api/v1/waybill-reading/samples`

Exactly one locator is required:

- `raw_record_id`
- `task_id`
- `standard_detail_id` as a compatibility locator back to `field_values.raw_record_id`

Response contract:

```json
{
  "contract_version": "waybill_reading_sample_v1",
  "batch": {
    "bulk_supported": true,
    "record_count": 1,
    "sample_count": 1,
    "ordered_by": ["raw_capture_records.id", "document_sequence", "text_blocks.order"],
    "scope": "single_record | task",
    "suggestion_groups": [],
    "suggestion_policy": "suggestion_only_empty_by_default"
  },
  "input_contract": {
    "module": "collection",
    "resource": "raw_capture_record",
    "query": ["raw_record_id", "task_id", "standard_detail_id"]
  },
  "output_contract": {
    "module": "waybill_reading",
    "sample": "waybill_sample",
    "text_block_fields": [
      "block_id",
      "text",
      "source",
      "block_kind",
      "line_index",
      "order",
      "raw_record_id",
      "trace"
    ],
    "text_block_role": "selectable_text_only",
    "original_blocks_preserved": true,
    "derived_child_blocks": "auxiliary_selectable_candidates_only",
    "child_blocks_replace_parent": false,
    "hidden_raw_fields": "filtered_metadata_not_selectable_text_blocks",
    "consumer": "field_definition",
    "bulk_consumer_rule": "field_definition_must_use_user_confirmed_type_or_scope_before_bulk_mapping",
    "automatic_grouping": false,
    "automatic_template_detection": false,
    "automatic_field_mapping": false,
    "similarity_policy": "suggestion_only_not_used_by_this_endpoint"
  },
  "samples": [
    {
      "sample_id": "raw-1-sample-1",
      "raw_record_id": 1,
      "task_id": 1,
      "record_order": 0,
      "sample_order": 0,
      "document_id": "DOC-1",
      "document_sequence": 1,
      "source_component": "cainiao-cnprint",
      "source_index": "1",
      "payload_format": "json",
      "sample_text": "original readable text",
      "text_blocks": [
        {
          "block_id": "raw-1-sample-1-block-1",
          "text": "original selectable text *1",
          "source": "raw_field | printed_text | raw_payload",
          "block_kind": "original",
          "line_index": 0,
          "order": 0,
          "raw_record_id": 1,
          "trace": {
            "selector_key": "printed_text:task.documents[0].contents[0].printXML.cdata[0]:text-0:line-0:segment-original",
            "sample_id": "raw-1-sample-1",
            "raw_record_id": 1,
            "task_id": 1,
            "document_id": "DOC-1",
            "document_sequence": 1,
            "source_component": "cainiao-cnprint",
            "source_index": "1",
            "source": "printed_text",
            "source_path": "task.documents[0].contents[0].printXML.cdata[0]",
            "source_text_order": 0,
            "source_line_index": 0,
            "line_index": 0,
            "segment_index": "original",
            "order": 0
          },
          "source_path": "task.documents[0].contents[0].printXML.cdata[0]",
          "document_id": "DOC-1",
          "document_sequence": 1,
          "parent_block_id": null,
          "parent_text": null,
          "split_reason": null
        },
        {
          "block_id": "raw-1-sample-1-block-2",
          "text": "*1",
          "source": "printed_text",
          "block_kind": "derived_child",
          "line_index": 0,
          "order": 1,
          "raw_record_id": 1,
          "parent_block_id": "raw-1-sample-1-block-1",
          "parent_text": "original selectable text *1",
          "split_reason": "safe_delimiter_and_trailing_marker",
          "trace": {
            "selector_key": "printed_text:task.documents[0].contents[0].printXML.cdata[0]:text-0:line-0:segment-1",
            "parent_block_id": "raw-1-sample-1-block-1",
            "parent_text": "original selectable text *1",
            "split_reason": "safe_delimiter_and_trailing_marker"
          }
        }
      ],
      "hidden_raw_fields": [
        {
          "text": "2026-06-16 14:27:17",
          "source": "raw_field",
          "source_path": "raw_capture_record.source_columns.task_time",
          "filter_reason": "technical_field_name",
          "document_sequence": null,
          "document_id": "DOC-1"
        }
      ]
    }
  ]
}
```

## Notes For Field Definition Module

The Field Definition Module should consume `samples[].text_blocks[]` by `block_id` and save user-confirmed mappings to the five fields only within its own module boundary. The Waybill Reading Module intentionally does not label a block as product, quantity, remark, template, waybill type, or any downstream business result.
