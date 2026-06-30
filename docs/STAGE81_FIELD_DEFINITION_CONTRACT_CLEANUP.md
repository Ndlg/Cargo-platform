# Stage81 Field Definition Contract Cleanup

## Module Boundary

Module: Field Definition.

Responsibility: let the user map waybill-reading text blocks into five standard waybill fields.

Input contract:

```json
{
  "waybill_sample": {
    "sample_id": "string",
    "raw_record_id": 0,
    "display_text": "string"
  },
  "text_blocks": [
    {
      "block_id": "string",
      "text": "string",
      "source": "string",
      "line_index": 0,
      "order": 0,
      "raw_record_id": 0
    }
  ],
  "mapping": {
    "product": ["block-id"],
    "sales_attr1": ["block-id"],
    "sales_attr2": ["block-id"],
    "quantity": ["block-id"],
    "remark": ["block-id"]
  },
  "application_scope": {
    "scope_type": "sample_only | waybill_type | selected_records",
    "confirmed_by_user": false,
    "waybill_type_code": "string",
    "waybill_type_name": "string",
    "selected_raw_record_ids": [1],
    "selected_sample_ids": ["sample-id"],
    "preview_impact_count": 0
  }
}
```

Output contract:

```json
{
  "product": "string",
  "sales_attr1": "string",
  "sales_attr2": "string",
  "quantity": "string",
  "remark": "string"
}
```

Forbidden responsibilities:

- Product category decisions.
- SKU recognition.
- Image matching or maintenance.
- Excel export.
- Silent same-template or similar-waybill application.
- Fingerprint, semantic hint, or similarity based automatic reuse.
- Recreating old field-definition, print-template, match-rule, key-field, or structure-fingerprint APIs.

## Stage81 Implementation

New backend route namespace:

- `GET /api/v1/waybill-field-mapping/contract`
- `POST /api/v1/waybill-field-mapping/resolve`
- `POST /api/v1/waybill-field-mapping/preview-scope`
- `POST /api/v1/waybill-field-mapping/rule-draft`

The namespace intentionally avoids old `/field-definitions` and `/waybill-structure-*` names.

Current persistence status: not implemented in Stage81. The resolve endpoint is stateless and only normalizes a user-provided mapping into the five-field payload.

Reuse policy: `manual_confirmed_scope_only`.

Bulk processing model: `confirm_waybill_type_or_selected_scope_once_preview_then_bulk_apply`.

Progressive rule learning model: `learn_user_confirmed_field_mapping_rules_only`.

Allowed application scopes:

- `sample_only`: resolve the current sample only; no reusable rule.
- `waybill_type`: apply only to a user-confirmed waybill type.
- `selected_records`: apply only to explicitly selected raw records or sample ids.

Similarity policy:

- Similar samples may be shown as suggestions.
- Suggested samples must be previewed as an affected scope before saving.
- The user must confirm the waybill type or selected record scope.
- The system must never silently apply a mapping because a sample appears similar.

Bulk processing policy:

- The user should not confirm every waybill one by one.
- The user confirms one representative waybill type or an explicit selected record scope.
- The user maps text blocks to the five standard fields once for that confirmed type or scope.
- The system previews the affected rows before saving or applying.
- The system bulk-applies only inside that confirmed type or selected scope.
- Rows outside confirmed mappings remain pending or go to exceptions.

Progressive rule learning policy:

- First use may require manual confirmation of common waybill types or selected record scopes.
- Later batches may apply enabled, user-confirmed field mapping rules when their confirmed type or scope matches.
- Similarity can suggest candidate groups, but it is not a rule source.
- New patterns, conflicts, and unmatched rows are returned for user review.
- User corrections become reusable only after preview and confirmation.

Reusable field mapping rule draft shape:

```json
{
  "rule_name": "string",
  "source_sample": {
    "sample_id": "string",
    "raw_record_id": 0,
    "text_block_ids": ["block-id"]
  },
  "mapping": {
    "product": ["block-id"],
    "sales_attr1": ["block-id"],
    "sales_attr2": ["block-id"],
    "quantity": ["block-id"],
    "remark": ["block-id"]
  },
  "application_scope": {
    "scope_type": "waybill_type | selected_records",
    "confirmed_by_user": true
  },
  "preview_summary": {
    "affected_record_count": 1,
    "affected_sample_count": 1,
    "conflict_count": 0,
    "pending_count": 0
  },
  "is_enabled": true,
  "revision_of_rule_id": 0,
  "revision_note": "string"
}
```

If persistence is added later, use a new explicit name such as `waybill_field_mapping_profiles` or `confirmed_waybill_field_mappings`. Persist only user-confirmed mappings and store:

- workspace and tenant scope
- mapping rule name
- source sample id, raw record id, and selected source block ids
- user-confirmed `waybill_type` or explicit selected record/sample scope
- selected `block_id` list for the five standard fields
- sample trace metadata
- preview impact summary shown to the user before confirmation
- enabled/disabled status
- revision metadata, including the previous rule id and revision note
- created/updated user ids

Do not persist product, SKU, image, export header, or recognition result fields in this module.

## Frontend Entry State

Stage81 adds a minimal field-definition page at `/field-mapping`.

The page supports:

- loading waybill-reading samples
- mapping selectable text blocks to the five standard fields
- choosing `sample_only`, `waybill_type`, or `selected_records` scope
- previewing affected scope
- resolving the five-field payload
- building a rule draft with source sample, preview summary, enabled status, and revision metadata

Existing legacy routes continue to redirect away from old field-definition pages:

- `/admin/field-definition` redirects to `/admin/export-headers`
- `/admin/field-definitions` redirects to `/admin/export-headers`
- `/field-definitions` redirects to `/admin/export-headers`

The page does not contain product/SKU/image binding controls and does not export Excel.

## Needed From Waybill Reading Module

The field definition module needs:

- `waybill_sample.sample_id` or another stable sample identifier
- `waybill_sample.raw_record_id` when available
- `waybill_sample.display_text` for UI display
- `text_blocks[].block_id` stable within the sample
- `text_blocks[].text`
- `text_blocks[].source`
- `text_blocks[].line_index`
- `text_blocks[].order`
- `text_blocks[].raw_record_id`

The waybill reading module should not decide standard field meaning.

## Stage82A Persistence And Bulk Apply

New tables:

- `waybill_field_mapping_rules`
- `waybill_field_mapping_results`

`waybill_field_mapping_rules` stores user-confirmed field mapping rules:

- `workspace_id` / `tenant_id`
- `rule_name`
- `scope_type`: `waybill_type` or `selected_records`
- `waybill_type_code` / `waybill_type_name`
- `source_sample`
- `block_selectors`
- `mapping`
- `application_scope`
- `preview_summary`
- `is_enabled`
- `status`
- `revision_of_rule_id`
- `revision_note`
- built-in audit timestamps and users

`waybill_field_mapping_results` stores the output of bulk application:

```json
{
  "standard_fields": {
    "product": "string",
    "sales_attr1": "string",
    "sales_attr2": "string",
    "quantity": "string",
    "remark": "string"
  },
  "source_trace": {
    "product": [
      {
        "selector": {},
        "matched_block": {},
        "trace": {}
      }
    ]
  }
}
```

This result is not a product/SKU/image match result.

Stage82A backend APIs:

- `GET /api/v1/waybill-field-mapping/rules`
- `POST /api/v1/waybill-field-mapping/rules`
- `PATCH /api/v1/waybill-field-mapping/rules/{rule_id}`
- `POST /api/v1/waybill-field-mapping/rules/{rule_id}/preview-apply`
- `POST /api/v1/waybill-field-mapping/rules/{rule_id}/apply`
- `GET /api/v1/waybill-field-mapping/results`

Bulk application behavior:

- Disabled rules cannot be applied.
- Rules only apply to the stored user-confirmed `application_scope`.
- `selected_records` uses explicit raw record or sample ids.
- `waybill_type` may use explicit selected ids and, when `waybill_type_code` is present, records whose `raw_capture_records.waybill_mode` equals that code.
- No similarity, fingerprint, or hidden grouping is used.
- Existing results for the same rule, raw record, and sample are updated rather than duplicated.
