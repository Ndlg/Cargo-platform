export type WaybillModeCode = 'douyin_cloud_print' | 'cainiao_direct_shop' | 'cainiao_woda_printxml'

export type RecognitionFieldOption = {
  code: string
  label: string
  meaning: string
  recommended?: boolean
}

export type WaybillModeCatalog = {
  code: WaybillModeCode
  label: string
  shortLabel: string
  description: string
  recognitionMode: 'structured' | 'custom'
  fields: RecognitionFieldOption[]
}

export const WAYBILL_MODE_CATALOGS: WaybillModeCatalog[] = [
  {
    code: 'douyin_cloud_print',
    label: '抖店 / CloudPrint',
    shortLabel: '抖店',
    description: '平台已提供商品、数量和备注，用户只维护商品识别会用到的业务字段。',
    recognitionMode: 'structured',
    fields: [
      {
        code: 'custom_product_text',
        label: '商品信息',
        meaning: '直接来自抖店 productInfo。',
        recommended: true,
      },
      {
        code: 'custom_sales_attr1_text',
        label: '销售属性1',
        meaning: '从 productInfo 模板中拆出的第一销售属性。',
        recommended: true,
      },
      {
        code: 'custom_sales_attr2_text',
        label: '销售属性2',
        meaning: '从 productInfo 模板中拆出的第二销售属性。',
      },
      {
        code: 'custom_item_remark_text',
        label: '备注字段',
        meaning: '直接来自抖店 remark。',
        recommended: true,
      },
    ],
  },
  {
    code: 'cainiao_direct_shop',
    label: '菜鸟店铺直打',
    shortLabel: '菜鸟直打',
    description: '平台可读取 ITEM_INFO、买家备注、卖家备注等结构化字段。用户可限定字段后做商品识别。',
    recognitionMode: 'structured',
    fields: [
      {
        code: 'custom_product_text',
        label: '商品信息',
        meaning: '系统从菜鸟商品信息中自动拆出的商品主类。',
        recommended: true,
      },
      {
        code: 'custom_sales_attr1_text',
        label: '销售属性1',
        meaning: '系统从菜鸟商品信息中自动拆出的颜色、规格等一级销售属性。',
        recommended: true,
      },
      {
        code: 'custom_sales_attr2_text',
        label: '销售属性2',
        meaning: '系统从菜鸟商品信息或备注中自动拆出的尺码、型号等二级销售属性。',
      },
      {
        code: 'custom_item_remark_text',
        label: '备注字段',
        meaning: '买家或卖家备注中保留的补充识别信息。',
        recommended: true,
      },
    ],
  },
  {
    code: 'cainiao_woda_printxml',
    label: '菜鸟 woda / 我打中转',
    shortLabel: '我打中转',
    description: '标准面单字段加密，平台读取自定义打印区可见文字并生成模板识别码；字段含义由用户按自己的打印模板定义。',
    recognitionMode: 'custom',
    fields: [
      {
        code: 'custom_product_text',
        label: '商品文字',
        meaning: '从 printXML 模板中拆出的商品文字。',
        recommended: true,
      },
      {
        code: 'custom_sales_attr1_text',
        label: '销售属性1',
        meaning: '从 printXML 模板中拆出的第一销售属性。',
        recommended: true,
      },
      {
        code: 'custom_sales_attr2_text',
        label: '销售属性2',
        meaning: '从 printXML 模板中拆出的第二销售属性。',
      },
      {
        code: 'custom_item_remark_text',
        label: '备注字段',
        meaning: '从 printXML 模板中拆出的备注字段。',
        recommended: true,
      },
    ],
  },
]

export const DEFAULT_WAYBILL_MODE_CODE: WaybillModeCode = 'douyin_cloud_print'

export const ALL_PRODUCT_RECOGNITION_FIELD_CODES = [
  ...new Set(WAYBILL_MODE_CATALOGS.flatMap((mode) => mode.fields.map((field) => field.code))),
]

export function modeCatalogFor(code?: string | null): WaybillModeCatalog {
  return WAYBILL_MODE_CATALOGS.find((mode) => mode.code === code) ?? WAYBILL_MODE_CATALOGS[0]
}

export function modeLabelFor(code?: string | null): string {
  return WAYBILL_MODE_CATALOGS.find((mode) => mode.code === code)?.shortLabel ?? '旧规则'
}

export function defaultRecognitionFieldsForMode(code?: string | null): string[] {
  const catalog = modeCatalogFor(code)
  const recommended = catalog.fields.filter((field) => field.recommended).map((field) => field.code)
  return recommended.length ? recommended : catalog.fields.map((field) => field.code)
}

export function fieldLabelFor(code: string): string {
  for (const catalog of WAYBILL_MODE_CATALOGS) {
    const field = catalog.fields.find((item) => item.code === code)
    if (field) return field.label
  }
  return code
}

export function fieldLabelsFor(codes: unknown): string {
  if (!Array.isArray(codes)) return '-'
  const labels = codes
    .filter((code): code is string => typeof code === 'string' && code.length > 0)
    .map(fieldLabelFor)
  return labels.length ? [...new Set(labels)].join('、') : '-'
}

export function valueText(value: unknown, fallback = ''): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function valuesTextForFields(values: Record<string, unknown>, fields: string[]): string {
  const texts = fields.map((field) => valueText(values[field])).filter(Boolean)
  return [...new Set(texts)].join('\n')
}
