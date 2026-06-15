import type { RecognitionFieldOption } from './waybillFieldCatalog'
import type { WodaExtractedItem } from './wodaStructure'

export type CustomWodaFieldCode =
  | 'custom_product_text'
  | 'custom_sales_attr1_text'
  | 'custom_quantity_text'
  | 'custom_sales_attr2_text'
  | 'custom_item_remark_text'

export type CustomWodaFieldMapping = {
  field_key: CustomWodaFieldCode
  field_label: string
  source: string
  source_field_key?: CustomWodaFieldCode
  line_index: number
  segment_text?: string
  extractor: string
  usage?: string
}

export type WodaItemTextField =
  | 'productText'
  | 'salesAttr1Text'
  | 'quantityText'
  | 'salesAttr2Text'
  | 'remarkText'

export const CUSTOM_WODA_FIELD_ORDER: CustomWodaFieldCode[] = [
  'custom_product_text',
  'custom_sales_attr1_text',
  'custom_quantity_text',
  'custom_sales_attr2_text',
  'custom_item_remark_text',
]

export const CUSTOM_WODA_FIELD_LABELS: Record<CustomWodaFieldCode, string> = {
  custom_product_text: '商品文字',
  custom_sales_attr1_text: '销售属性1',
  custom_quantity_text: '数量文字',
  custom_sales_attr2_text: '销售属性2',
  custom_item_remark_text: '备注字段',
}

export const WODA_FIELD_LABELS: Record<string, string> = {
  ...CUSTOM_WODA_FIELD_LABELS,
  custom_spec_text: '销售属性1',
  custom_size_text: '销售属性2',
}

export const SAMPLE_ROW_FIELD_BY_CODE: Record<CustomWodaFieldCode, WodaItemTextField> = {
  custom_product_text: 'productText',
  custom_sales_attr1_text: 'salesAttr1Text',
  custom_quantity_text: 'quantityText',
  custom_sales_attr2_text: 'salesAttr2Text',
  custom_item_remark_text: 'remarkText',
}

export const WODA_SKU_AUTO_FIELD_CODES = new Set([
  'custom_sales_attr1_text',
  'custom_sales_attr2_text',
  'custom_spec_text',
  'custom_size_text',
  'custom_item_remark_text',
])

export function isCustomWodaFieldCode(value: unknown): value is CustomWodaFieldCode {
  return CUSTOM_WODA_FIELD_ORDER.includes(value as CustomWodaFieldCode)
}

export function extractorForWodaFieldCode(code: CustomWodaFieldCode): string {
  if (code === 'custom_product_text') return 'product_text'
  if (code === 'custom_sales_attr1_text') return 'sales_attr1_text'
  if (code === 'custom_sales_attr2_text') return 'sales_attr2_text'
  if (code === 'custom_quantity_text') return 'quantity_text'
  if (code === 'custom_item_remark_text') return 'remark_text'
  return 'text'
}

export function wodaItemFieldText(item: WodaExtractedItem, code: string): string {
  if (code === 'custom_product_text') return item.productText || ''
  if (code === 'custom_sales_attr1_text' || code === 'custom_spec_text') {
    return item.salesAttr1Text || item.specText || ''
  }
  if (code === 'custom_sales_attr2_text' || code === 'custom_size_text') {
    return item.salesAttr2Text || item.sizeText || ''
  }
  if (code === 'custom_quantity_text') return item.quantityText || ''
  if (code === 'custom_item_remark_text') return item.remarkText || ''
  return ''
}

export function wodaRecognitionFieldOptions(): RecognitionFieldOption[] {
  return [
    {
      code: 'custom_product_text',
      label: CUSTOM_WODA_FIELD_LABELS.custom_product_text,
      meaning: '',
      recommended: true,
    },
    {
      code: 'custom_sales_attr1_text',
      label: CUSTOM_WODA_FIELD_LABELS.custom_sales_attr1_text,
      meaning: '',
      recommended: true,
    },
    {
      code: 'custom_sales_attr2_text',
      label: CUSTOM_WODA_FIELD_LABELS.custom_sales_attr2_text,
      meaning: '',
    },
    {
      code: 'custom_item_remark_text',
      label: CUSTOM_WODA_FIELD_LABELS.custom_item_remark_text,
      meaning: '勾选后参与商品主类识别，也作为 SKU 兜底字段',
    },
  ]
}
