export type RecognitionTemplateConfig = {
  id: number
  template_key: string
  template_label?: string | null
  config?: Record<string, unknown> | null
}

export type RecognitionSku = {
  id: number
  product_id: number
  name: string
}

type BuildMatchValuesInput = {
  modeCode: string
  selectedTemplateConfig: RecognitionTemplateConfig | null
  productId: number | null
  productName: string
  productKeyword: string
  sku: RecognitionSku | undefined
  fields: string[]
  skuAutoFields: string[]
}

function valueText(value: unknown, fallback = ''): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

export function wodaConfigSourceTemplateKey(config: RecognitionTemplateConfig): string {
  const payload = objectValue(config.config)
  const match = objectValue(payload.template_match)
  return valueText(match.source_template_key, valueText(payload.source_template_key, config.template_key))
}

export function buildProductRecognitionMatchValues(input: BuildMatchValuesInput): Record<string, unknown> {
  const selectedTemplate = input.selectedTemplateConfig
  const isWoda = input.modeCode === 'cainiao_woda_printxml'
  return {
    mode_code: input.modeCode,
    print_template_config_id: selectedTemplate?.id ?? null,
    print_template_key: selectedTemplate?.template_key ?? null,
    print_template_source_key: selectedTemplate ? wodaConfigSourceTemplateKey(selectedTemplate) : null,
    print_template_label: selectedTemplate?.template_label ?? null,
    product_id: input.productId,
    product_name: input.productName,
    product_field: null,
    product_keyword: input.productKeyword,
    sku_id: input.sku?.id ?? null,
    sku_name: input.sku?.name ?? null,
    sku_field: null,
    sku_fallback_field: null,
    sku_fallback_keyword: null,
    sku_lock: Boolean(input.sku),
    sku_auto_match: !input.sku,
    sku_auto_fields: isWoda ? input.skuAutoFields : [],
    keyword: input.productKeyword,
    fields: input.fields,
  }
}
