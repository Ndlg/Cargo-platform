# Codex Working Notes

## Current Product Goal

`cargo-platform` exists to help the user turn collected order/waybill data into supplier order-reporting Excel workbooks.

The current system is being rewritten because the previous technical workflow became confusing, fragile, and difficult to use in real business. Future work must be centered on business order rows, not on technical intermediate screens.

Primary business row contract:

- `商品`
- `销售属性1`
- `图片`
- `销售属性2`
- `数量`
- `备注`
- `图片匹配文本`

Multi-product waybills must become multiple child order rows. Never hide several products inside one text field.

## Hard Direction Reset

Do not rebuild or improve the retired technical mapping/workbench route as the primary product.

The platform should work like this:

1. Collect raw records from collector clients.
2. Send raw records plus the active recognition rule pack to an independent recognition engine service.
3. Receive one or more editable order rows from that engine.
4. Let the user inspect and correct rows in a table-like workflow.
5. Convert user-confirmed corrections into inspectable learning records or updated rule packs.
6. Match confirmed rows to product, SKU, and image assets.
7. Export the final supplier workbook and an exception sheet.

If no active recognition rule pack exists, parsing/recognition must return a business-readable missing-rule-pack response. Do not silently run hidden built-in recognition.

## Independent Recognition Engine Direction

Recognition must become an independent module/service, not business logic embedded in the main platform.

Current implementation status:

- Parser service code lives under `services/waybill-parser`.
- Runtime container name: `cargo-platform-waybill-parser`.
- Runtime health endpoint: `GET http://127.0.0.1:8010/health`.
- Main backend reaches it only through `WAYBILL_PARSER_URL`.
- Docker compose wires backend to `http://waybill-parser:8010`.
- Backend parser calls should go through `backend/app/services/waybill_parser_client.py`.
- Backend may keep `backend/app/services/order_row_contract.py` as a DTO/contract-version module only; it must not contain parsing heuristics.
- Backend routes must not import `services/waybill-parser/service_app/*` or duplicate parser internals.

Operational rule:

- If parsing behavior changes but the HTTP contract does not change, rebuild/restart only the parser service.
- If the backend adapter contract changes, rebuild backend and parser together.
- If only a recognition rule pack changes, import/activate the pack through the rule-pack API/UI; do not rebuild containers.
- If `WAYBILL_PARSER_URL` is missing or the parser service is down, the platform must show "识别服务不可用" or equivalent business wording. It must not fall back to old embedded parsing.

The main platform may:

- store rule packs and rule-pack revisions
- activate, deactivate, import, export, and delete rule packs
- pass raw capture records and an active rule pack to the recognition engine
- store and display the engine output
- let users correct rows and save inspectable learning records

The main platform must not:

- contain hidden default recognition rules
- hard-code shoe-specific parsing behavior
- keep business-specific parser functions as the source of truth
- silently parse when no active rule pack exists

The recognition engine service may:

- validate a rule pack
- explain a rule pack in business-readable sections
- preview raw waybills against a rule pack
- parse raw records into order rows and diagnostics

Current parser service endpoints:

- `GET /health`
- `POST /api/v1/rule-packs/validate`
- `POST /api/v1/rule-packs/explain`
- `POST /api/v1/parse/preview`
- `POST /api/v1/parse/batch`

The recognition engine service must not:

- connect to the business database
- know product/SKU/image assets
- export Excel
- decide final product matching
- mutate platform data

Rule packs must be declarative scenario assets. They configure the recognition engine; they must not contain executable business code.

Rule-pack status language must be precise:

- no active pack: `rule_pack_missing`
- active pack exists but cannot be used by the parser: `rule_pack_invalid`
- parser service unreachable or not configured: parser service unavailable

Do not show all three cases to the user as "请启用规则包". That hides the real problem and makes debugging impossible.

## Module Boundaries

### 1. Collection Module

Responsibility: receive and store raw collector payloads.

Input: collector/printer payload.

Output: raw capture record.

Must not parse order rows, match products, maintain assets, or export Excel.

### 2. Independent Recognition Engine / Waybill Parsing Service

Responsibility: convert one raw capture record into business order row candidates by applying a provided rule pack.

Input: raw capture record plus recognition rule pack payload.

Output:

- parent waybill sample
- one or more child order rows
- parse status and exception reason when needed
- source trace for each row

Must support special waybills and multi-product waybills. If parsing cannot produce a reliable business row, return a reviewable exception instead of blank or misleading fields.

Must be callable as a separate service. It must not read or write platform business tables.

### 3. Order Row Review Module

Responsibility: let the user review, edit, confirm, exclude, or revise parsed order rows.

Input: parsed order row candidates.

Output: confirmed order rows ready for product matching.

The UI should show business labels such as `第1批-第24单-子1`, not raw database IDs as the primary label.

### 4. 商品匹配模块

Responsibility: consume confirmed order rows and match them to product, SKU, and image assets.

Input: confirmed order rows.

Output:

- product result
- SKU result
- image result
- match status
- exception reason

Must not parse waybills or change upstream row parsing. It may use user-confirmed product/SKU/image rules and maintained assets.

### 5. Export Module

Responsibility: generate the supplier workbook from final matched results.

Input: product/SKU/image matching output.

Output:

- normal workbook sheet with the seven business columns
- `异常面单` sheet with one `图片匹配文本` column

Must not parse waybills or infer product rules.

### 6. Rule Pack Editor Module

Responsibility: help users maintain recognition rule packs without editing raw JSON directly.

Input: recognition rule pack payload, sample raw records, and optional user edits.

Output:

- validated rule pack
- business-readable rule sections
- preview result and impact summary
- revised rule-pack version after user confirmation

Must not directly parse orders through hidden platform code. Preview and validation must go through the independent recognition engine contract.

## Recognition Rule Packs

Rule packs are scenario assets. They must be importable, exportable, activatable, disableable, deleteable, inspectable, and revisable.

A rule pack may include:

- parser policy
- special-waybill policy
- multi-product split policy
- field cleanup and normalization policy
- product/SKU/image matching policy
- exception policy

Current shoe-waybill behavior should be packaged as a user-selectable rule pack, not hidden as a default.

The rule pack UI/editor must make clear that a pack configures the independent recognition engine. If a rule is not represented in the pack or in a user-visible learning record, it should not affect recognition.

Agents changing parsing, matching, or export must state whether the behavior is controlled by a rule pack. If not, treat that as technical debt.

## UI Direction

The product UI should follow the business flow:

1. `采集记录`: collector status and collected waybill count.
2. `面单解析`: raw waybill, parsed child rows, special-waybill status, parse errors.
3. `订单行整理`: editable business rows and batch confirmation.
4. `商品匹配`: product/SKU/image matching and exceptions.
5. `异常处理`: unresolved rows grouped by actionable reason.
6. `导出中心`: workbook preview, row counts, download.
7. `规则包`: import, export, activate, revise, and inspect recognition rule packs.

Avoid exposing source paths, JSON traces, internal IDs, or technical parse artifacts unless the user opens diagnostics.

## Development Rules

- Before editing, identify the module and its input/output contract.
- Keep changes scoped to the responsible module.
- Prefer simple, inspectable data structures over hidden inference.
- Use real task data to verify that parsed rows, matched rows, exceptions, and exported rows add up.
- If a row is uncertain, keep it reviewable. Do not silently guess.
- User-confirmed corrections should become visible learning records or rule-pack revisions.
- Do not delete the database, Docker volumes, or `cargo-platform-data` without explicit user authorization.
- Preserve collector data, raw records, product/SKU/image/stall assets, and export/download foundation.

## Runtime And Test Notes

- Project path: `C:\Users\ndlgx\Documents\Projects\GitHub\Ndlg\cargo-platform`.
- Frontend uses npm with `frontend/package-lock.json`. Do not use pnpm or yarn, and do not commit `node_modules`.
- If `node` or `npm` is missing from PATH, use the project scripts; they add `C:\Program Files\nodejs` when available.
- Frontend setup must use `npm ci` from `frontend`; type checking must use `npm run typecheck` or `scripts/frontend_typecheck.ps1`.
- Backend uses Python 3.12 in `.venv`.
- Run backend tests through `scripts/backend_test.ps1 <pytest args>` or `scripts/backend_test.bat <pytest args>`.
- `scripts/backend_test.ps1` defaults pytest to a temporary SQLite database under `.pytest_cache`; set `DATABASE_URL` only when intentionally testing MySQL.
- Windows PowerShell is configured for UTF-8 text in this workspace. If command output is garbled, work around it silently.

## Current Clean Module Agent Threads

- Collection Module Agent: `019ed62f-f90f-7ad2-86e8-a71bc9dcf257`
- Waybill Parsing Module Agent: `019ed630-1b0a-7c73-900e-200182d58cbe`
- Order Row Review Module Agent: `019ed630-4fd2-72b3-971e-b8c22ed535e7`
- 商品匹配模块 Agent: `019ed630-7198-7641-8dd2-5d9d76a78de7`
- Export Module Agent: `019ed630-9377-77b1-811b-0d6bc6413050`
