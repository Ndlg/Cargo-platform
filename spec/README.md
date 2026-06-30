# cargo-platform Spec Index

This spec describes the current rebuild direction for `cargo-platform`.

The product is an order-reporting assistant. Its job is to turn collected waybill/order data into supplier Excel workbooks.

The current route is:

1. Collect raw records.
2. Require an active recognition rule pack.
3. Send raw records plus the active rule pack to the independent recognition engine service.
4. Receive editable business order rows from the engine.
5. Let the user review, correct, confirm, or exclude rows.
6. Match confirmed rows to product, SKU, and image assets.
7. Export normal rows plus reviewable exceptions.

Current independent recognition runtime:

- parser service source: `services/waybill-parser`
- parser service container: `cargo-platform-waybill-parser`
- backend adapter: `backend/app/services/waybill_parser_client.py`
- backend configuration: `WAYBILL_PARSER_URL`
- rule-pack contract: active packs must include `parser_policy.order_row_parser`

If the parser service is unavailable or the active pack is invalid, the platform must show a clear business error instead of falling back to old embedded parsing.

Primary row columns:

- `商品`
- `销售属性1`
- `图片`
- `销售属性2`
- `数量`
- `备注`
- `图片匹配文本`

The system must not hide multiple products in one field. A multi-product waybill becomes multiple child rows.

## Files

- `00-current-direction-reset.md`: hard reset and forbidden rebuild behavior.
- `01-product-goal.md`: business goal and success criteria.
- `02-module-contracts.md`: module input/output contracts.
- `03-user-workflows-ui.md`: UI and workflow requirements.
- `04-rule-pack-learning-model.md`: rule pack and learning record design.
- `05-multi-item-and-exceptions.md`: multi-product and exception model.
- `06-acceptance-roadmap.md`: practical acceptance plan.
- `07-recognition-rule-packs.md`: switchable recognition rule pack contract and lifecycle.
- `08-usability-audit-and-remediation-plan.md`: usability audit, system risks, target data flow, page responsibilities, and staged remediation plan.
- `09-independent-recognition-engine.md`: independent recognition engine boundary, API contract, rule-pack editor direction, migration plan, and acceptance criteria.
