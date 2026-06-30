# Stage84-WaybillReading-LocalModel-Plan

## Module Contract

- Module: Waybill Reading Module
- Enhancement direction: local OCR / local LLM / local VLM assistance for waybill reading
- Input: `raw_capture_record` from the Collection Module
- Output: `waybill_sample` plus selectable `text_blocks[]`
- Required text block properties: text, source, stable order, source path or source asset reference, confidence where available, trace metadata
- Consumer: Field Definition Module

This module must not output final business field semantics. It must not decide `product`, `sales_attr1`, `sales_attr2`, `quantity`, or `remark`. It must not match product, SKU, or image. It must not export Excel.

The local model layer is allowed to improve readable text evidence. It is not allowed to become a hidden field-definition, product-matching, or template-recognition engine.

## Current Repository Observations

### Collection Input

- `backend/app/models/entities.py` defines `RawCaptureRecord` with `payload_format`, `raw_payload`, `source_columns`, `parsed_payload`, `document_id`, source metadata, and a compatibility pointer to `standard_detail_id`.
- `backend/app/services/collection_contract.py` defines the clean Collection Module output contract as `raw_capture_record`.
- `backend/app/api/routes/collector_runtime.py` persists collector uploads through `build_raw_capture_record(...)`. The upload endpoint deliberately stores raw records only and does not run downstream reading during upload.
- Raw upload limits currently exist at the API level:
  - raw payload max: `2_000_000` characters
  - `source_columns` max: `20_000` serialized JSON characters

### Current Waybill Reading

- `backend/app/services/waybill_reading.py` is the clean reading service.
- It reads:
  - `raw_capture_record.source_columns`
  - JSON `task.documents[].contents[].data`
  - JSON `task.documents[].contents[].printXML`
  - fallback raw payload text
- It emits:
  - `sample_id`
  - `sample_text`
  - selectable `text_blocks`
  - `hidden_raw_fields`
  - trace metadata with selector keys, source path, source text order, source line index, segment index, raw record id, document id, and document sequence
- It preserves original blocks and emits split candidates as `block_kind: "derived_child"` without replacing the original.
- It filters obvious technical raw fields from user-selectable blocks and reports them as `hidden_raw_fields`.
- It has no image OCR, no PDF parser, no HTML layout renderer, no coordinate extraction, no local model adapter, and no model confidence field today.

### Current Waybill Reading API

- `backend/app/api/routes/waybill_reading.py` exposes `GET /api/v1/waybill-reading/samples`.
- The endpoint accepts exactly one locator: `raw_record_id`, `task_id`, or compatibility `standard_detail_id`.
- It declares:
  - `automatic_grouping: false`
  - `automatic_template_detection: false`
  - `automatic_field_mapping: false`
  - `similarity_policy: suggestion_only_not_used_by_this_endpoint`
- It returns empty diagnostics instead of writing downstream results when no readable text exists.

### Field Definition Boundary

- `backend/app/services/waybill_field_mapping.py` consumes text blocks and user mappings to produce exactly five standard fields.
- `backend/app/api/routes/waybill_field_mapping.py` persists user-confirmed mapping rules and mapping results.
- Saved rule artifacts are valuable future training data:
  - `waybill_field_mapping_rules.source_sample`
  - `waybill_field_mapping_rules.block_selectors`
  - `waybill_field_mapping_rules.mapping`
  - `waybill_field_mapping_rules.application_scope`
  - `waybill_field_mapping_rules.preview_summary`
  - `waybill_field_mapping_results.standard_fields`
  - `waybill_field_mapping_results.source_trace`
- The Field Definition Module already enforces manual confirmed scope for reusable rules.

### Standard Details And Compatibility

- `StandardDetail` still exists and is retained as business data.
- Some older export and preview flows can still read `standard_details`.
- The clean route should treat `StandardDetail` as retained compatibility/storage, not as the source of new waybill-reading decisions.

### Legacy Parser And Old Rule Layer

- `backend/app/services/waybill_parser.py` is still present but mixes responsibilities:
  - parses raw capture payloads;
  - detects old waybill modes;
  - calculates template keys and fingerprints;
  - imports old `waybill_structure_recognition`;
  - applies template/config mappings;
  - writes `StandardDetail`.
- Safe ideas to reuse from it are limited to low-level traversal patterns: JSON loading, `task.documents[]`, `contents[].data`, `contents[].printXML`, and template URL discovery as source metadata.
- Unsafe as a new foundation: fingerprint matching, semantic candidate structures, field-definition config matching, product-ish normalization, and `StandardDetail` writes.
- `backend/app/services/waybill_structure_recognition/*`, `woda_template_matcher.py`, and old resource helpers remain in the repo, but the local-model plan must not build on them as a trusted route.

### OCR / Image Processing Status

- Runtime dependency list includes `pillow`, used for product/SKU image handling and Excel export.
- There is no current dependency on `pytesseract`, PaddleOCR, EasyOCR, OpenCV, ONNX Runtime, llama.cpp, Ollama, or a VLM package.
- Current raw payloads in tests are primarily JSON and `printXML`. A realistic OCR path must therefore start as optional and degradable, not mandatory.

## Three-Layer Capability Model

### Layer 1: OCR And Layout Parsing

Responsibility: convert source artifacts into text blocks and coordinates.

Inputs may include:

- text/JSON fields already in `raw_payload`;
- `printXML` text nodes and CDATA;
- embedded or referenced images if collectors provide them later;
- HTML or rendered print payloads if collectors provide them later;
- PDF or print spool artifacts if collectors provide them later.

Output should be normalized `TextBlockCandidate` objects:

```json
{
  "text": "string",
  "source": "printed_text | raw_field | image_ocr | html_text | pdf_text | model_cleaned_text",
  "source_path": "string",
  "block_kind": "original | derived_child | ocr_line | ocr_word | model_suggestion",
  "order": 0,
  "line_index": 0,
  "bbox": {"x": 0, "y": 0, "width": 0, "height": 0, "unit": "px"},
  "confidence": 0.98,
  "trace": {
    "raw_record_id": 0,
    "document_id": "string",
    "source_asset_id": "string",
    "engine": "string",
    "engine_version": "string"
  }
}
```

OCR/layout engines should never output standard business fields. They only output readable evidence.

### Layer 2: Local LLM/VLM Assistance

Responsibility: improve text-block usability without making business decisions.

Allowed:

- normalize OCR noise;
- merge broken OCR line fragments into candidate display blocks;
- suggest alternate readings with confidence and evidence;
- explain why a block is low-confidence;
- align OCR text back to source coordinates;
- generate `model_suggestion` blocks beside original evidence.

Forbidden:

- deciding that a block is product, size, color, quantity, or remark;
- writing field mapping rules;
- applying field mapping rules;
- selecting product/SKU/image;
- silently correcting source text without preserving the original.

Every model-assisted block must keep:

- parent source block ids or source coordinates;
- model name/version;
- prompt/template version if applicable;
- confidence or uncertainty note;
- original text preserved separately.

### Layer 3: User-Confirmed Rules

Responsibility: decide meaning and reuse.

This belongs outside Waybill Reading:

- Field Definition Module maps blocks to five fields.
- 商品匹配模块 maps five-field rows to product/SKU/image.
- Export Module creates workbooks.

Local models may help produce better candidates, but reusable behavior must still come from user-confirmed type/scope/mapping plus preview.

## Proposed Local Model Adapter Architecture

### Design Goals

- Optional: the app must work when no local model is installed.
- Replaceable: each engine implements a small interface.
- Inspectable: every output carries engine metadata and trace.
- Degradable: failures produce diagnostics and fall back to existing text extraction.
- Local-first: no default network calls and no automatic model weight downloads.
- Narrow: output only waybill samples and text blocks.

### Interfaces

Introduce a small service boundary under `backend/app/services/waybill_reading_adapters/` later:

```python
class WaybillReadingAdapter(Protocol):
    name: str
    version: str

    def supports(self, source: WaybillSource) -> bool:
        ...

    def extract(self, source: WaybillSource) -> WaybillReadingAdapterResult:
        ...
```

Suggested objects:

- `WaybillSource`: raw record id, tenant/workspace, document id, payload fragment, source path, optional local file path or bytes reference, MIME type.
- `WaybillReadingAdapterResult`: text block candidates, diagnostics, engine metadata, runtime cost.
- `TextBlockCandidate`: normalized text, coordinates, source, confidence, trace, parent relationship.

The existing `read_waybill_samples(record)` can become an orchestrator:

1. run built-in structured text extraction;
2. detect additional source artifacts;
3. run configured adapters with timeout and size limits;
4. merge results without deduplicating away evidence;
5. return `text_blocks` plus diagnostics.

### Adapter Tiers

Tier 0: current built-in parser

- Always enabled.
- Handles `source_columns`, JSON fields, and `printXML`.
- No external dependencies.

Tier 1: deterministic local parsers

- HTML text extraction and optional DOM bounding boxes if HTML appears in payload.
- PDF text extraction if PDF artifacts are later collected.
- `printXML` attribute parsing for coordinates, for example `<text x="120" y="80">...`.
- These should be implemented before heavier model dependencies.

Tier 2: local OCR

- Candidate engines:
  - Tesseract: smaller, mature, CPU-friendly, but weaker for mixed Chinese/e-commerce layouts unless language data is installed.
  - PaddleOCR: stronger Chinese OCR and layout support, heavier dependencies and larger install footprint.
  - ONNX Runtime OCR models: controllable deployment, more engineering effort.
- Recommendation: start with an adapter interface and a disabled-by-default Tesseract or PaddleOCR PoC, selected by config. Do not add mandatory OCR dependency to `backend/requirements.txt` in the first pass.

Tier 3: local LLM/VLM

- Candidate runtimes:
  - Ollama: easiest local service adapter, good for Qwen text models and some vision models if installed by user.
  - llama.cpp: more controllable packaging, more operational work on Windows.
  - Local Qwen/Qwen-VL through Python libraries: powerful but large dependency and GPU/VRAM concerns.
- Recommendation: first implement an HTTP adapter contract for user-managed local services, for example Ollama-compatible endpoint, disabled by default. The adapter should accept text blocks or image references and return only candidate cleanup/merge suggestions.

## Minimal Viable Local Model Plan

### Phase 0: Document And Contract Guardrails

Deliverable:

- This document.
- No code changes, no dependencies, no model downloads.

Acceptance:

- The Waybill Reading Module boundary remains `raw_capture_record -> waybill_sample + text_blocks`.
- No old APIs/tables become the foundation.

### Phase 1: Coordinate Extraction Without Models

Goal:

- Improve traceability for existing `printXML` text nodes.

Possible implementation:

- Extend `printxml_text_entries(...)` to parse text node attributes when present, such as `x`, `y`, `width`, `height`, font size, or transform.
- Add optional `bbox` / `layout` fields to text blocks.
- Preserve current API compatibility by allowing extra response fields.

Tests:

- Add fixture with `<text x="120" y="80">Plain Shoe</text>`.
- Assert text remains selectable and bbox appears in trace or block metadata.
- Assert no standard field names are emitted.

### Phase 2: Adapter Interface With No External Engine

Goal:

- Create the extension point safely.

Possible implementation:

- Add `waybill_reading_adapters/base.py`.
- Move current built-in extraction behind a `StructuredPayloadAdapter` or keep it as the orchestrator's first step.
- Add a `NullAdapter` / diagnostics-only adapter for tests.
- Add config flags:
  - `WAYBILL_READING_ADAPTERS=structured`
  - `WAYBILL_READING_MODEL_TIMEOUT_SECONDS`
  - `WAYBILL_READING_MAX_SOURCE_BYTES`

Tests:

- Existing `test_waybill_reading.py` should continue passing.
- New tests should verify adapter diagnostics are returned but no downstream results are created.

### Phase 3: Offline OCR Command PoC

Goal:

- Test OCR quality without changing production request path.

Possible implementation:

- Add a command/script that accepts a local image file and emits JSON text blocks.
- The command should not write DB rows.
- It should print engine metadata, confidence, bbox, and source image path.

Candidate command:

```text
scripts/waybill_ocr_probe.ps1 -ImagePath <path> -Engine tesseract
```

Dependencies:

- Prefer user-installed engine discovery first.
- Do not download OCR models automatically.
- Document install paths and missing-engine diagnostics.

### Phase 4: Optional OCR Adapter

Goal:

- Integrate OCR when raw records include image artifacts.

Possible implementation:

- Add `ImageOcrAdapter`.
- Support local files referenced by collector payloads or future stored attachments.
- Return OCR text blocks with bbox/confidence.
- If OCR fails, return diagnostics and leave existing text extraction intact.

Acceptance:

- Existing printXML/text records work without OCR.
- OCR blocks are selectable evidence only.
- Low-confidence OCR blocks are visible as such.

### Phase 5: Optional Local LLM Cleanup Adapter

Goal:

- Improve block usability for noisy OCR.

Possible implementation:

- Input: OCR line/word blocks, original coordinates, and source image metadata.
- Output: suggested merged/corrected blocks with parent block references.
- Runtime: user-managed Ollama-compatible local endpoint or local service URL.
- Prompt must explicitly forbid producing business fields.

Output example:

```json
{
  "source": "model_cleaned_text",
  "block_kind": "model_suggestion",
  "text": "颜色分类: 黑白; 鞋码: 42; *1",
  "confidence": 0.72,
  "trace": {
    "model": "qwen-local",
    "adapter": "ollama_text_cleanup",
    "parent_block_ids": ["raw-1-sample-1-block-5", "raw-1-sample-1-block-6"],
    "policy": "candidate_only_not_field_mapping"
  }
}
```

Acceptance:

- Original OCR blocks are still present.
- Model suggestions are visually distinguishable in the Field Definition UI.
- No field mapping or product matching happens inside the adapter.

## Training And Data Feedback Loop

### Data Sources

High-value supervised data can come from user-confirmed field mapping artifacts:

- raw input:
  - `raw_capture_records.raw_payload`
  - `raw_capture_records.source_columns`
  - future source image references
- reading output:
  - `waybill_sample.sample_text`
  - `text_blocks[]`
  - trace metadata
  - OCR bbox/confidence if available
- human labels:
  - `waybill_field_mapping_rules.mapping`
  - `waybill_field_mapping_rules.block_selectors`
  - `waybill_field_mapping_results.standard_fields`
  - `waybill_field_mapping_results.source_trace`
- scope/reuse evidence:
  - confirmed `application_scope`
  - `preview_summary`
  - revision metadata

### Dataset Types

1. OCR/layout dataset

- Input: image/HTML/PDF/print payload.
- Label: text blocks and coordinates.
- Source: manually corrected OCR output or deterministic printXML coordinates.
- Metric: character error rate, line recall, block order accuracy, bbox overlap where labeled.

2. Text cleanup dataset

- Input: noisy OCR blocks with coordinates.
- Label: user-accepted cleaned/merged block candidates.
- Metric: exact text match, edit distance, parent trace preservation, false merge rate.

3. Field mapping support dataset

- Input: text blocks.
- Label: user-confirmed selected block ids for each of five fields.
- Use: evaluation and suggestion research only.
- Important: model predictions may be shown as suggestions later, but must not auto-apply or become rules without user confirmation.

### Labeling Rules

- A label is trusted only after user confirmation and preview.
- The dataset must distinguish:
  - model suggestion;
  - user accepted;
  - user rejected;
  - user edited;
  - later disabled/revised rule.
- Disabled or superseded rules should stay in the audit trail but be excluded from "current accepted" training splits by default.
- Keep source trace, engine version, and prompt version with every example.

### Evaluation Gates

Before any model-assisted output becomes visible by default:

- OCR must beat current text-only extraction for image-only records without degrading printXML/text records.
- Low-confidence blocks must remain reviewable.
- The model must not invent text absent from source evidence.
- Any cleanup suggestion must cite parent OCR/text blocks.
- The Field Definition Module must still require user confirmation for reusable mappings.

Before any field-mapping suggestion is considered:

- Use it only as advisory UI.
- Compare against a holdout set of user-confirmed mappings.
- Report false-positive rate separately from accuracy.
- Never silently apply a suggestion because of similarity, fingerprint, or model confidence.

## Risk Register

- Dependency size: PaddleOCR/VLM stacks can substantially increase installation size and Windows setup friction.
- CPU/GPU mismatch: many VLMs are unusably slow on CPU and may require significant VRAM.
- Licensing: model weights and OCR engines may have licenses incompatible with redistribution.
- Data privacy: local-first is good, but source images and waybill data still contain sensitive order/logistics information; no default cloud calls.
- Hallucination: LLM/VLM cleanup can invent plausible product text. Preserve originals and mark suggestions.
- Trace loss: merged/corrected text without parent evidence would make later user corrections hard to audit.
- UI overload: too many original/OCR/model blocks can overwhelm mapping. Need filtering, confidence display, and source toggles later.
- Old-route regression: importing old fingerprint/semantic modules for convenience can accidentally rebuild the retired route.

## Explicit Non-Goals

- Do not recreate `/api/v1/waybill-structure-*`.
- Do not recreate old `match-rules`, `print-template-configs`, `field-definitions`, `key-field-sets`, or `field-role-configs` as the foundation.
- Do not write product/SKU/image decisions from Waybill Reading.
- Do not write Excel output from Waybill Reading.
- Do not auto-apply mappings from similarity, fingerprint, OCR confidence, LLM confidence, or VLM explanation.
- Do not download model weights automatically.
- Do not make OCR/VLM dependencies mandatory for the backend startup path.
- Do not delete or clear database tables, Docker volumes, or retained business assets.

## Recommended First Code PoC

The safest first code PoC is Phase 1 plus Phase 2:

1. Add optional bbox extraction for `printXML` text nodes.
2. Add adapter result types and diagnostics.
3. Keep all existing text behavior stable.
4. Add tests proving the endpoint still emits only selectable text blocks.

This produces immediate value for traceability and creates a clean local-model extension point without committing the project to a heavy OCR/VLM dependency too early.
