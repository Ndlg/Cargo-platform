import {
  CUSTOM_WODA_FIELD_LABELS,
  CUSTOM_WODA_FIELD_ORDER,
  extractorForWodaFieldCode,
  isCustomWodaFieldCode,
  type CustomWodaFieldCode,
  type CustomWodaFieldMapping,
} from './wodaFields'

export type TemplateRuleFieldForm = {
  productLineIndex: number
  specLineIndex: number
  sizeLineIndex: number
  quantityLineIndex: number
  remarkLineIndex: number
  productSegmentText: string
  specSegmentText: string
  sizeSegmentText: string
  quantitySegmentText: string
  remarkSegmentText: string
}

function valueText(value: unknown, fallback = ''): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

export function configSamplePreviewValue(config: Record<string, unknown>, code: CustomWodaFieldCode): string {
  const fieldMappings = objectValue(config.field_mappings)
  const mapping = objectValue(fieldMappings[code])
  const segmentText = valueText(mapping.segment_text)
  if (segmentText) return segmentText
  const preview = objectValue(config.sample_preview)
  const previewText = valueText(preview[code])
  if (previewText) return previewText
  if (code === 'custom_product_text') return valueText(config.product_segment_text)
  if (code === 'custom_sales_attr1_text') {
    return valueText(config.sales_attr1_segment_text, valueText(config.spec_segment_text))
  }
  if (code === 'custom_sales_attr2_text') {
    return valueText(config.sales_attr2_segment_text, valueText(config.size_segment_text))
  }
  if (code === 'custom_quantity_text') return valueText(config.quantity_segment_text)
  return valueText(config.item_remark_segment_text, valueText(config.remark_segment_text))
}

export function lineIndexForWodaField(form: TemplateRuleFieldForm, code: CustomWodaFieldCode): number {
  if (code === 'custom_product_text') return form.productLineIndex
  if (code === 'custom_sales_attr1_text') return form.specLineIndex
  if (code === 'custom_sales_attr2_text') return form.sizeLineIndex
  if (code === 'custom_quantity_text') return form.quantityLineIndex
  if (code === 'custom_item_remark_text') return form.remarkLineIndex
  return -1
}

export function segmentTextForWodaField(form: TemplateRuleFieldForm, code: CustomWodaFieldCode): string {
  if (code === 'custom_product_text') return form.productSegmentText
  if (code === 'custom_sales_attr1_text') return form.specSegmentText
  if (code === 'custom_sales_attr2_text') return form.sizeSegmentText
  if (code === 'custom_quantity_text') return form.quantitySegmentText
  if (code === 'custom_item_remark_text') return form.remarkSegmentText
  return ''
}

export function sourceFieldCodeForTarget(
  assignments: Partial<Record<CustomWodaFieldCode, CustomWodaFieldCode>>,
  targetCode: CustomWodaFieldCode,
): CustomWodaFieldCode {
  return CUSTOM_WODA_FIELD_ORDER.find((sourceCode) => assignments[sourceCode] === targetCode) ?? targetCode
}

export function buildFieldMappingPayload(
  form: TemplateRuleFieldForm,
  assignments: Partial<Record<CustomWodaFieldCode, CustomWodaFieldCode>>,
): Record<CustomWodaFieldCode, CustomWodaFieldMapping> {
  const quantityLineIndex = form.quantityLineIndex >= 0
    ? form.quantityLineIndex
    : form.productLineIndex >= 0 ? form.productLineIndex : form.specLineIndex
  const sizeLineIndex = form.sizeLineIndex >= 0 ? form.sizeLineIndex : form.specLineIndex
  const mappingFor = (
    targetCode: CustomWodaFieldCode,
    fallbackLineIndex: number,
    usage?: string,
  ): CustomWodaFieldMapping => {
    const sourceCode = sourceFieldCodeForTarget(assignments, targetCode)
    const sourceLineIndex = lineIndexForWodaField(form, sourceCode)
    const segmentText = segmentTextForWodaField(form, sourceCode).trim()
    return {
      field_key: targetCode,
      field_label: CUSTOM_WODA_FIELD_LABELS[targetCode],
      source: 'custom_area_lines',
      source_field_key: sourceCode,
      line_index: sourceLineIndex >= 0 ? sourceLineIndex : fallbackLineIndex,
      ...(segmentText ? { segment_text: segmentText } : {}),
      extractor: extractorForWodaFieldCode(sourceCode),
      ...(usage ? { usage } : {}),
    }
  }
  return {
    custom_product_text: mappingFor('custom_product_text', form.productLineIndex),
    custom_sales_attr1_text: mappingFor('custom_sales_attr1_text', form.specLineIndex),
    custom_quantity_text: mappingFor('custom_quantity_text', quantityLineIndex),
    custom_sales_attr2_text: mappingFor('custom_sales_attr2_text', sizeLineIndex),
    custom_item_remark_text: mappingFor('custom_item_remark_text', form.remarkLineIndex, 'sku_fallback'),
  }
}

export function restoreFieldAssignments(
  fieldMappings: unknown,
): Partial<Record<CustomWodaFieldCode, CustomWodaFieldCode>> {
  const savedMappings = objectValue(fieldMappings)
  const assignments: Partial<Record<CustomWodaFieldCode, CustomWodaFieldCode>> = {}
  CUSTOM_WODA_FIELD_ORDER.forEach((targetCode) => {
    const mapping = objectValue(savedMappings[targetCode])
    const sourceCode = valueText(mapping.source_field_key)
    if (isCustomWodaFieldCode(sourceCode) && sourceCode !== targetCode) {
      assignments[sourceCode] = targetCode
    }
  })
  return assignments
}
