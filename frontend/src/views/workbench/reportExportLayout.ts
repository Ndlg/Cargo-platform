import type { RecognitionPreviewRow } from '../../services/api'

export type ReportFieldKey =
  | 'product_name'
  | 'stall_name'
  | 'sales_attr1'
  | 'sku_image'
  | 'sales_attr2'
  | 'quantity'
  | 'remark'
  | 'image_match_text'
export type ReportOutputMode = 'merged_sheet' | 'stall_sheet' | 'stall_workbooks'

export type ReportLayoutColumn = {
  key: ReportFieldKey
  label: string
  visible: boolean
  width: number
}

export type ReportLayout = {
  presetId: string
  columns: ReportLayoutColumn[]
  headerRowHeight: number
  rowHeight: number
  imageWidth: number
  imageHeight: number
  imageOffsetX: number
  imageOffsetY: number
  stackSalesAttr1: boolean
  stackSalesAttr2: boolean
  outputMode: ReportOutputMode
}

export type ReportPreviewRow = {
  key: string
  product_name: string
  stall_name: string
  sales_attr1_text: string
  sales_attr2_text: string
  image_label: string
  quantity: number
  remark_text: string
  image_match_text: string
  sku_id?: number | null
  sku_image_asset_id?: number | null
}

export type ReportLayoutPreset = {
  id: string
  name: string
  description: string
  layout: ReportLayout
}

export type SavedReportLayout = {
  id: string
  name: string
  description: string
  layout: ReportLayout
  updatedAt: string
}

const STORAGE_KEY_PREFIX = 'cargo-platform-report-layout-v1'
const STYLE_STORAGE_KEY_PREFIX = 'cargo-platform-report-layout-styles-v1'
export const REPORT_COLUMN_WIDTH_PIXEL_RATIO = 9

export const REPORT_FIELD_DEFINITIONS: Array<{
  key: ReportFieldKey
  label: string
  description: string
  defaultWidth: number
}> = [
  { key: 'product_name', label: '商品', description: '商品匹配输出的商品', defaultWidth: 16 },
  { key: 'stall_name', label: '档口', description: 'SKU 档口优先，未设置时使用商品默认档口', defaultWidth: 14 },
  { key: 'sales_attr1', label: '销售属性1', description: '规格、颜色、款式等第一销售属性', defaultWidth: 24 },
  { key: 'sku_image', label: '图片', description: 'SKU 绑定的报货图', defaultWidth: 18 },
  { key: 'sales_attr2', label: '销售属性2', description: '第二销售属性或补充 SKU 字段', defaultWidth: 18 },
  { key: 'quantity', label: '数量', description: '同 SKU 汇总后的数量', defaultWidth: 12 },
  { key: 'remark', label: '备注', description: '五字段结果中的备注文本', defaultWidth: 18 },
  { key: 'image_match_text', label: '图片匹配文本', description: '商品匹配输出的图片追溯文本', defaultWidth: 42 },
]

const availableColumns: ReportFieldKey[] = [
  'product_name',
  'stall_name',
  'sales_attr1',
  'sku_image',
  'sales_attr2',
  'quantity',
  'remark',
  'image_match_text',
]
const standardColumns: ReportFieldKey[] = [
  'product_name',
  'sales_attr1',
  'sku_image',
  'sales_attr2',
  'quantity',
  'remark',
  'image_match_text',
]

const LEGACY_DEFAULT_LABELS: Partial<Record<ReportFieldKey, string[]>> = {
  product_name: ['商品名称'],
  sku_image: ['SKU图片'],
}

export const REPORT_OUTPUT_MODE_OPTIONS: Array<{
  value: ReportOutputMode
  label: string
  description: string
}> = [
  { value: 'merged_sheet', label: '合并Sheet', description: '所有报货行放在同一个工作表。' },
  { value: 'stall_sheet', label: '鞋款档口Sheet', description: '按档口拆成多个工作表，适合现场分档口发货。' },
  { value: 'stall_workbooks', label: '鞋款档口文档', description: '按档口拆成多个 Excel 文件，并打包下载。' },
]

function fieldDefinition(key: ReportFieldKey) {
  return REPORT_FIELD_DEFINITIONS.find((field) => field.key === key) ?? REPORT_FIELD_DEFINITIONS[0]
}

function makeColumns(keys: ReportFieldKey[], hiddenKeys: ReportFieldKey[] = []): ReportLayoutColumn[] {
  const orderedKeys = [...keys, ...availableColumns.filter((key) => !keys.includes(key))]
  return orderedKeys.map((key) => {
    const field = fieldDefinition(key)
    return {
      key,
      label: field.label,
      visible: !hiddenKeys.includes(key),
      width: field.defaultWidth,
    }
  })
}

export function defaultReportLayout(): ReportLayout {
  return {
    presetId: 'standard',
    columns: makeColumns(standardColumns),
    headerRowHeight: 26,
    rowHeight: 86,
    imageWidth: 88,
    imageHeight: 88,
    imageOffsetX: 0,
    imageOffsetY: 0,
    stackSalesAttr1: false,
    stackSalesAttr2: false,
    outputMode: 'stall_sheet',
  }
}

export const REPORT_LAYOUT_PRESETS: ReportLayoutPreset[] = [
  {
    id: 'standard',
    name: '标准报货',
    description: '商品、规格、图片、销售属性2、数量、备注和图片匹配文本都输出。',
    layout: defaultReportLayout(),
  },
  {
    id: 'image_first',
    name: '图片靠前',
    description: '先看商品和图片，再看规格属性。',
    layout: {
      ...defaultReportLayout(),
      presetId: 'image_first',
      columns: makeColumns([
        'product_name',
        'sku_image',
        'sales_attr1',
        'sales_attr2',
        'quantity',
        'remark',
        'image_match_text',
      ]),
    },
  },
  {
    id: 'compact_no_image',
    name: '无图简版',
    description: '只导出文字字段，适合临时核对。',
    layout: {
      ...defaultReportLayout(),
      presetId: 'compact_no_image',
      columns: makeColumns(standardColumns, ['sku_image']),
      rowHeight: 32,
    },
  },
]

function clampNumber(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(Math.max(Math.round(parsed), min), max)
}

export function reportColumnWidthFromPixels(value: number): number {
  return clampNumber(value / REPORT_COLUMN_WIDTH_PIXEL_RATIO, 12, 8, 60)
}

function storageKey(workspaceId?: number | string | null): string {
  return `${STORAGE_KEY_PREFIX}:${workspaceId || 'default'}`
}

function styleStorageKey(workspaceId?: number | string | null): string {
  return `${STYLE_STORAGE_KEY_PREFIX}:${workspaceId || 'default'}`
}

function createStyleId(): string {
  return `style-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

export function presetReportLayout(presetId: string): ReportLayout {
  const preset = REPORT_LAYOUT_PRESETS.find((item) => item.id === presetId) ?? REPORT_LAYOUT_PRESETS[0]
  return normalizeReportLayout(JSON.parse(JSON.stringify(preset.layout)) as Partial<ReportLayout>)
}

export function normalizeReportLayout(raw: Partial<ReportLayout> | null | undefined): ReportLayout {
  const base = defaultReportLayout()
  const sourceColumns = Array.isArray(raw?.columns) ? raw.columns : []
  const outputMode = REPORT_OUTPUT_MODE_OPTIONS.some((option) => option.value === raw?.outputMode)
    ? (raw?.outputMode as ReportOutputMode)
    : base.outputMode
  const usedKeys = new Set<ReportFieldKey>()
  const columns: ReportLayoutColumn[] = []

  sourceColumns.forEach((column) => {
    const key = column?.key as ReportFieldKey
    if (!availableColumns.includes(key) || usedKeys.has(key)) return
    const field = fieldDefinition(key)
    usedKeys.add(key)
    const rawLabel = String(column.label || field.label).trim() || field.label
    columns.push({
      key,
      label: LEGACY_DEFAULT_LABELS[key]?.includes(rawLabel) ? field.label : rawLabel,
      visible: column.visible !== false,
      width: clampNumber(column.width, field.defaultWidth, 8, 60),
    })
  })

  availableColumns.forEach((key) => {
    if (usedKeys.has(key)) return
    const field = fieldDefinition(key)
    columns.push({
      key,
      label: field.label,
      visible: standardColumns.includes(key),
      width: field.defaultWidth,
    })
  })

  if (!columns.some((column) => column.visible)) {
    columns.forEach((column) => {
      column.visible = true
    })
  }

  return {
    presetId: String(raw?.presetId || base.presetId),
    columns,
    headerRowHeight: clampNumber(raw?.headerRowHeight, base.headerRowHeight, 18, 80),
    rowHeight: clampNumber(raw?.rowHeight, base.rowHeight, 24, 180),
    imageWidth: clampNumber(raw?.imageWidth, base.imageWidth, 32, 220),
    imageHeight: clampNumber(raw?.imageHeight, base.imageHeight, 32, 220),
    imageOffsetX: clampNumber(raw?.imageOffsetX, base.imageOffsetX, 0, 220),
    imageOffsetY: clampNumber(raw?.imageOffsetY, base.imageOffsetY, 0, 220),
    stackSalesAttr1: raw?.stackSalesAttr1 === true,
    stackSalesAttr2: raw?.stackSalesAttr2 === true,
    outputMode,
  }
}

export function loadReportLayout(workspaceId?: number | string | null): ReportLayout {
  try {
    const raw = localStorage.getItem(storageKey(workspaceId))
    if (!raw) return defaultReportLayout()
    return normalizeReportLayout(JSON.parse(raw) as Partial<ReportLayout>)
  } catch {
    return defaultReportLayout()
  }
}

export function saveReportLayout(layout: ReportLayout, workspaceId?: number | string | null) {
  localStorage.setItem(storageKey(workspaceId), JSON.stringify(normalizeReportLayout(layout)))
}

export function loadSavedReportLayouts(workspaceId?: number | string | null): SavedReportLayout[] {
  try {
    const raw = localStorage.getItem(styleStorageKey(workspaceId))
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .map((item): SavedReportLayout | null => {
        const name = String(item?.name || '').trim()
        if (!name) return null
        return {
          id: String(item?.id || createStyleId()),
          name,
          description: String(item?.description || '').trim(),
          layout: normalizeReportLayout(item?.layout as Partial<ReportLayout>),
          updatedAt: String(item?.updatedAt || new Date().toISOString()),
        }
      })
      .filter((item): item is SavedReportLayout => item !== null)
  } catch {
    return []
  }
}

export function saveSavedReportLayouts(
  layouts: SavedReportLayout[],
  workspaceId?: number | string | null,
): SavedReportLayout[] {
  const normalized = layouts.map((item) => ({
    ...item,
    id: item.id || createStyleId(),
    name: item.name.trim(),
    description: item.description.trim(),
    layout: normalizeReportLayout(item.layout),
    updatedAt: item.updatedAt || new Date().toISOString(),
  })).filter((item) => item.name)
  localStorage.setItem(styleStorageKey(workspaceId), JSON.stringify(normalized))
  return normalized
}

export function makeSavedReportLayout(
  name: string,
  description: string,
  layout: ReportLayout,
): SavedReportLayout {
  return {
    id: createStyleId(),
    name: name.trim(),
    description: description.trim(),
    layout: normalizeReportLayout(layout),
    updatedAt: new Date().toISOString(),
  }
}

export function visibleReportColumns(layout: ReportLayout): ReportLayoutColumn[] {
  return normalizeReportLayout(layout).columns.filter((column) => column.visible)
}

export function reportLayoutDownloadPayload(layout: ReportLayout) {
  const normalized = normalizeReportLayout(layout)
  return {
    columns: normalized.columns,
    header_row_height: normalized.headerRowHeight,
    row_height: normalized.rowHeight,
    image_width: normalized.imageWidth,
    image_height: normalized.imageHeight,
    image_offset_x: normalized.imageOffsetX,
    image_offset_y: normalized.imageOffsetY,
    stack_sales_attr1: normalized.stackSalesAttr1,
    stack_sales_attr2: normalized.stackSalesAttr2,
    output_mode: normalized.outputMode,
  }
}

function valueText(value: unknown, fallback = ''): string {
  if (value === null || value === undefined || value === '') return fallback
  return String(value).trim()
}

function stripProductPrefix(text: unknown, productName: unknown): string {
  const value = valueText(text)
  const prefix = valueText(productName)
  if (prefix && value.startsWith(prefix)) {
    return value.slice(prefix.length).replace(/^[\s\-_/，、|]+/, '')
  }
  return value
}

function reportSpecText(row: RecognitionPreviewRow): string {
  const productName = row.product_name || ''
  const values = [row.sku_name, row.sales_attr1_text, row.product_text]
  for (const value of values) {
    const text = stripProductPrefix(value, productName)
    if (text) return text
  }
  return '-'
}

function reportSalesAttr2Values(value: unknown): string[] {
  const text = valueText(value)
  if (!text) return ['-']
  return [text]
}

function naturalSortValue(value: unknown): [number, number | string, string] {
  const text = valueText(value, '-').toLowerCase()
  const match = text.match(/\d+(?:\.\d+)?/)
  if (match) return [0, Number(match[0]), text]
  return [1, text, text]
}

function compareNatural(left: unknown, right: unknown): number {
  const a = naturalSortValue(left)
  const b = naturalSortValue(right)
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] === b[index]) continue
    if (typeof a[index] === 'number' && typeof b[index] === 'number') {
      return (a[index] as number) - (b[index] as number)
    }
    return String(a[index]).localeCompare(String(b[index]), 'zh-CN', { numeric: true })
  }
  return 0
}

function sortedUniqueValues(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort(compareNatural)
}

function sortedValues(values: string[]): string[] {
  return values.filter(Boolean).sort(compareNatural)
}

function uniqueJoinedValues(values: unknown[], separator = '\n'): string {
  const seen = new Set<string>()
  const unique: string[] = []
  values.forEach((value) => {
    const text = valueText(value)
    if (!text || seen.has(text)) return
    seen.add(text)
    unique.push(text)
  })
  return unique.join(separator)
}

function expandedSalesAttr2Values(row: RecognitionPreviewRow): string[] {
  const tokens = reportSalesAttr2Values(row.sales_attr2_text)
  const quantity = reportQuantity(row.quantity_text)
  if (tokens.length > 1) return tokens
  return Array.from({ length: quantity }, () => tokens[0] || '-')
}

function reportQuantity(value: unknown): number {
  const match = valueText(value).match(/\d+/)
  const parsed = match ? Number(match[0]) : 1
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1
}

export function buildReportRows(
  rows: RecognitionPreviewRow[],
  layout?: Partial<ReportLayout> | null,
): ReportPreviewRow[] {
  const normalizedLayout = normalizeReportLayout(layout)
  const grouped = new Map<
    string,
    ReportPreviewRow & {
      salesAttr1Values: string[]
      salesAttr2Values: string[]
      remarkValues: string[]
      imageMatchTextValues: string[]
    }
  >()
  rows.forEach((row) => {
    if (row.status !== 'matched') return
    const productName = row.product_name || row.product_text || '-'
    const stallName = row.stall_name || '未设置档口'
    const salesAttr1Text = reportSpecText(row)
    const key = [
      row.stall_id || stallName,
      row.product_id || productName,
      row.sku_id || salesAttr1Text,
      row.sku_image_asset_id || 0,
      normalizedLayout.stackSalesAttr1
        ? 'grouped'
        : row.candidate_key || `${row.detail_id}:${row.item_index || 0}:${salesAttr1Text}:${row.sales_attr2_text}`,
    ].join(':')

    if (!grouped.has(key)) {
      grouped.set(key, {
        key,
        product_name: productName,
        stall_name: stallName,
        sales_attr1_text: salesAttr1Text,
        sales_attr2_text: '-',
        image_label: '',
        quantity: 0,
        remark_text: '',
        image_match_text: '',
        sku_id: row.sku_id,
        sku_image_asset_id: row.sku_image_asset_id,
        salesAttr1Values: [],
        salesAttr2Values: [],
        remarkValues: [],
        imageMatchTextValues: [],
      })
    }

    const line = grouped.get(key)
    if (!line) return
    line.salesAttr1Values.push(salesAttr1Text)
    line.salesAttr2Values.push(...expandedSalesAttr2Values(row))
    line.remarkValues.push(row.remark_text)
    line.imageMatchTextValues.push(row.image_match_text)
    line.quantity += reportQuantity(row.quantity_text)
  })

  return [...grouped.values()].map((line) => {
    const { salesAttr1Values, salesAttr2Values, remarkValues, imageMatchTextValues, ...row } = line
    return {
      ...row,
      sales_attr1_text: normalizedLayout.stackSalesAttr1
        ? sortedUniqueValues(salesAttr1Values).join(' ') || '-'
        : row.sales_attr1_text,
      sales_attr2_text: normalizedLayout.stackSalesAttr2
        ? sortedUniqueValues(salesAttr2Values).join(' ') || '-'
        : sortedValues(salesAttr2Values).join(' ') || '-',
      remark_text: uniqueJoinedValues(remarkValues),
      image_match_text: uniqueJoinedValues(imageMatchTextValues),
    }
  }).sort((left, right) =>
    compareNatural(left.stall_name, right.stall_name)
    || compareNatural(left.product_name, right.product_name)
    || compareNatural(left.sales_attr1_text, right.sales_attr1_text)
    || compareNatural(left.sales_attr2_text, right.sales_attr2_text)
  )
}

export function reportCellText(row: ReportPreviewRow, key: ReportFieldKey): string | number {
  if (key === 'product_name') return row.product_name || '-'
  if (key === 'stall_name') return row.stall_name || '未设置档口'
  if (key === 'sales_attr1') return row.sales_attr1_text || '-'
  if (key === 'sales_attr2') return row.sales_attr2_text || '-'
  if (key === 'quantity') return row.quantity
  if (key === 'remark') return row.remark_text || ''
  if (key === 'image_match_text') return row.image_match_text || ''
  return ''
}

export function reportColumnPixelWidth(column: ReportLayoutColumn): number {
  return Math.max(82, column.width * REPORT_COLUMN_WIDTH_PIXEL_RATIO)
}
