<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Delete, Refresh, Right, Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createRecord,
  deleteRecord,
  getCaptureTaskRecognitionPreview,
  getRecords,
  type ApiRecord,
  type CaptureTaskRecord,
  type RecognitionPreviewResponse,
  type RecognitionPreviewRow,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'
import {
  DEFAULT_WAYBILL_MODE_CODE,
  WAYBILL_MODE_CATALOGS as FALLBACK_WAYBILL_MODE_CATALOGS,
  type RecognitionFieldOption,
  type WaybillModeCatalog,
  type WaybillModeCode,
} from './waybillFieldCatalog'
import {
  analyzeWodaStructure,
  wodaStructureLabel,
  type WodaExtractedItem,
  type WodaStructureKind,
  type WodaStructureResult,
} from './wodaStructure'
import {
  WODA_FIELD_LABELS,
  WODA_SKU_AUTO_FIELD_CODES,
  wodaItemFieldText,
  wodaRecognitionFieldOptions,
} from './wodaFields'
import {
  buildProductRecognitionMatchValues,
  wodaConfigSourceTemplateKey,
} from './productRecognitionRulePayload'

type KeyFieldSetRecord = ApiRecord & {
  id: number
  name: string
  purpose: string
  field_codes: string[]
}

type MatchRuleRecord = ApiRecord & {
  id: number
  key_field_set_id: number
  match_values: Record<string, unknown>
  target_type: string
  target_id?: number | null
  target_name?: string | null
  is_enabled?: boolean
}

type ProductRecord = ApiRecord & {
  id: number
  name: string
  keywords?: string[] | null
}

type ProductSkuRecord = ApiRecord & {
  id: number
  product_id: number
  name: string
  keywords?: string[] | null
}

type StandardDetailRecord = ApiRecord & {
  id: number
  waybill_mode?: string | null
  field_values?: Record<string, unknown> | null
}

type PrintTemplateConfigRecord = ApiRecord & {
  id: number
  waybill_mode: string
  template_key: string
  template_label?: string | null
  config?: Record<string, unknown> | null
  is_enabled?: boolean
}

type WaybillModeRecord = ApiRecord & {
  id: number
  name: string
  code: WaybillModeCode
  is_enabled?: boolean
}

type WaybillTemplateRecord = ApiRecord & {
  id: number
  waybill_mode_id: number
  name: string
  extraction_rules?: Record<string, unknown> | null
  is_enabled?: boolean
}

type WaybillTemplateFieldRecord = ApiRecord & {
  id: number
  waybill_template_id: number
  target_field_code: string
  extraction_config?: Record<string, unknown> | null
  sort_order?: number
}

type CollectedCandidate = {
  candidateKey: string
  id: number
  itemIndex: number | null
  itemCount: number
  printTemplateConfigId: number | null
  sourceLabel: string
  text: string
  keywordText: string
  specText: string
  templateLabel: string
  productText: string
  skuKeywordText: string
  skuFallbackKeywordText: string
}

type CandidateRecognitionFilter = 'unmatched' | 'matched' | 'all'

const PRODUCT_IDENTIFY_SET_NAME = '平台解析商品字段'
const SUPPORTED_PRODUCT_MODE_CODES = new Set(['douyin_cloud_print', 'cainiao_direct_shop', 'cainiao_woda_printxml'])

const session = useSessionStore()
const keySets = ref<KeyFieldSetRecord[]>([])
const rules = ref<MatchRuleRecord[]>([])
const products = ref<ProductRecord[]>([])
const skus = ref<ProductSkuRecord[]>([])
const standardDetails = ref<StandardDetailRecord[]>([])
const captureTasks = ref<CaptureTaskRecord[]>([])
const waybillModes = ref<WaybillModeRecord[]>([])
const waybillTemplates = ref<WaybillTemplateRecord[]>([])
const waybillTemplateFields = ref<WaybillTemplateFieldRecord[]>([])
const printTemplateConfigs = ref<PrintTemplateConfigRecord[]>([])
const selectedCaptureTaskId = ref<number | null>(null)
const recognitionPreview = ref<RecognitionPreviewResponse | null>(null)
const recognitionPreviewLoading = ref(false)
const loading = ref(false)
const saving = ref(false)
const deletingRuleId = ref<number | null>(null)
const error = ref('')
const ruleSearch = ref('')
const ruleModeFilter = ref('')
const ruleTargetFilter = ref('')
const ruleStatusFilter = ref('all')
const candidateSearch = ref('')
const candidateRecognitionFilter = ref<CandidateRecognitionFilter>('unmatched')

const form = reactive({
  mode_code: DEFAULT_WAYBILL_MODE_CODE as WaybillModeCode,
  print_template_config_id: null as number | null,
  fields: [] as string[],
  product_keyword: '',
  keyword: '',
  sku_fallback_keyword: '',
  product_id: null as number | null,
  sku_id: null as number | null,
})

function valueText(value: unknown, fallback = ''): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function detailCaptureTaskId(detail: StandardDetailRecord): number | null {
  const taskId = Number(detail.field_values?.capture_task_id)
  return Number.isFinite(taskId) && taskId > 0 ? taskId : null
}

function captureTaskStatusLabel(status?: string | null): string {
  if (status === 'collecting') return '采集中'
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '失败'
  return status || '-'
}

function formatCaptureTaskTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const sortedCaptureTasks = computed(() => [...captureTasks.value].sort((a, b) => b.id - a.id))
const selectedCaptureTask = computed(
  () => sortedCaptureTasks.value.find((task) => task.id === selectedCaptureTaskId.value) ?? null,
)
const captureTaskDetailCounts = computed(() => {
  const counts = new Map<number, number>()
  standardDetails.value.forEach((detail) => {
    const taskId = detailCaptureTaskId(detail)
    if (!taskId) return
    counts.set(taskId, (counts.get(taskId) ?? 0) + 1)
  })
  return counts
})
const archivedStandardDetails = computed(() => {
  if (!selectedCaptureTaskId.value) return []
  return standardDetails.value.filter((detail) => detailCaptureTaskId(detail) === selectedCaptureTaskId.value)
})
const captureTaskDetailNumbers = computed(() => {
  const numbers = new Map<number, number>()
  archivedStandardDetails.value
    .slice()
    .sort((a, b) => a.id - b.id)
    .forEach((detail, index) => {
      numbers.set(detail.id, index + 1)
    })
  return numbers
})

function captureTaskLabel(task: CaptureTaskRecord, index = 0): string {
  const round = index <= 0 ? '最近一轮' : `上一轮 ${index}`
  const count = captureTaskDetailCounts.value.get(task.id) ?? 0
  return `${round}：${formatCaptureTaskTime(task.started_at)} ${captureTaskStatusLabel(task.status)} / ${count} 张`
}

function ensureSelectedCaptureTask() {
  const taskIds = new Set(sortedCaptureTasks.value.map((task) => task.id))
  if (selectedCaptureTaskId.value && taskIds.has(selectedCaptureTaskId.value)) return
  const taskIdsWithDetails = new Set<number>()
  standardDetails.value.forEach((detail) => {
    const taskId = detailCaptureTaskId(detail)
    if (taskId) taskIdsWithDetails.add(taskId)
  })
  selectedCaptureTaskId.value =
    sortedCaptureTasks.value.find((task) => taskIdsWithDetails.has(task.id))?.id
    ?? sortedCaptureTasks.value[0]?.id
    ?? null
}

function isRecommendedField(code: string): boolean {
  return /product|spec|sales_attr|remark|memo|custom_area|quantity/i.test(code)
}

function modeShortLabel(name: string): string {
  if (name.includes('抖店')) return '抖店'
  if (name.includes('店铺直接')) return '菜鸟直打'
  if (name.includes('woda') || name.includes('我打')) return '我打'
  return name
}

function extractionFieldMap(template: WaybillTemplateRecord): Record<string, unknown> {
  const fields = template.extraction_rules?.fields
  return fields && typeof fields === 'object' ? fields as Record<string, unknown> : {}
}

function platformFieldOption(field: WaybillTemplateFieldRecord, template: WaybillTemplateRecord): RecognitionFieldOption {
  const extractionConfig = field.extraction_config ?? {}
  const rawField = valueText(extractionFieldMap(template)[field.target_field_code], field.target_field_code)
  return {
    code: field.target_field_code,
    label: valueText(extractionConfig.name, field.target_field_code),
    meaning: `原始字段：${rawField}`,
    recommended: isRecommendedField(field.target_field_code),
  }
}

const waybillModeCatalogs = computed<WaybillModeCatalog[]>(() => {
  const enabledCodes = new Set(
    waybillModes.value
      .filter((mode) => SUPPORTED_PRODUCT_MODE_CODES.has(mode.code) && mode.is_enabled !== false)
      .map((mode) => mode.code),
  )
  const staticCatalogs = FALLBACK_WAYBILL_MODE_CATALOGS
    .filter((catalog) => catalog.code !== 'cainiao_woda_printxml')
    .filter((catalog) => !enabledCodes.size || enabledCodes.has(catalog.code))

  return staticCatalogs.length
    ? staticCatalogs
    : FALLBACK_WAYBILL_MODE_CATALOGS.filter((catalog) => catalog.code !== 'cainiao_woda_printxml')
})

const wodaModeCatalog = computed<WaybillModeCatalog | null>(() => {
  const mode = waybillModes.value.find((item) => item.code === 'cainiao_woda_printxml')
  if (!mode || mode.is_enabled === false) return null
  return {
    code: 'cainiao_woda_printxml',
    label: mode.name,
    shortLabel: modeShortLabel(mode.name),
    description: '先选择客户已维护的我打模板，再使用该模板拆分出的字段做商品识别。',
    recognitionMode: 'custom',
    fields: [],
  }
})

const selectableModeCatalogs = computed<WaybillModeCatalog[]>(() => {
  const catalogs = [...waybillModeCatalogs.value]
  if (wodaModeCatalog.value) catalogs.push(wodaModeCatalog.value)
  return catalogs
})

const wodaTemplateConfigs = computed(() =>
  printTemplateConfigs.value.filter((item) =>
    item.waybill_mode === 'cainiao_woda_printxml'
    && item.is_enabled !== false
    && item.config?.rule_type === 'custom_text_lines_v1',
  ),
)

const selectedWodaTemplateConfig = computed(() =>
  wodaTemplateConfigs.value.find((item) => item.id === form.print_template_config_id) ?? null,
)

function customConfigFieldOptions(config: PrintTemplateConfigRecord | null): RecognitionFieldOption[] {
  if (!config) return []
  return wodaRecognitionFieldOptions()
}

const activeModeCatalog = computed(() =>
  selectableModeCatalogs.value.find((mode) => mode.code === form.mode_code)
    ?? selectableModeCatalogs.value[0]
    ?? FALLBACK_WAYBILL_MODE_CATALOGS[0],
)
const activeRecognitionFields = computed(() =>
  form.mode_code === 'cainiao_woda_printxml'
    ? customConfigFieldOptions(selectedWodaTemplateConfig.value)
    : activeModeCatalog.value.fields,
)
const allProductRecognitionFieldCodes = computed(() => [
  ...new Set([
    ...selectableModeCatalogs.value.flatMap((mode) => mode.fields.map((field) => field.code)),
    ...wodaTemplateConfigs.value.flatMap((config) => customConfigFieldOptions(config).map((field) => field.code)),
  ]),
])

const productIdentifyKeySet = computed(() =>
  keySets.value.find((item) => item.purpose === 'product_identify')
  ?? keySets.value.find((item) => item.name === PRODUCT_IDENTIFY_SET_NAME)
  ?? null,
)
const productRules = computed(() =>
  rules.value.filter((rule) => ['product', 'sku'].includes(rule.target_type)),
)
const ruleStats = computed(() => ({
  total: productRules.value.length,
  enabled: productRules.value.filter((rule) => rule.is_enabled !== false).length,
  sku: productRules.value.filter((rule) => rule.target_type === 'sku').length,
  product: productRules.value.filter((rule) => rule.target_type === 'product').length,
}))

function productName(productId?: number | null): string {
  if (!productId) return '-'
  return products.value.find((product) => product.id === productId)?.name ?? '-'
}

function skuName(skuId?: number | null): string {
  if (!skuId) return '-'
  return skus.value.find((sku) => sku.id === skuId)?.name ?? '-'
}

function skuTargetName(skuId?: number | null): string {
  const sku = skus.value.find((item) => item.id === skuId)
  if (!sku) return '-'
  return `${productName(sku.product_id)} / ${sku.name}`
}

function targetName(rule: MatchRuleRecord): string {
  if (rule.target_type === 'sku') {
    const fromTarget = skuTargetName(rule.target_id)
    if (fromTarget !== '-') return fromTarget
    const product = valueText(rule.match_values?.product_name)
    const sku = valueText(rule.match_values?.sku_name)
    return [product, sku].filter(Boolean).join(' / ') || valueText(rule.target_name, '-')
  }
  return productName(rule.target_id)
}

const selectedProductSkus = computed(() =>
  form.product_id ? skus.value.filter((sku) => sku.product_id === form.product_id) : [],
)

function onProductChange() {
  if (!selectedProductSkus.value.some((sku) => sku.id === form.sku_id)) {
    form.sku_id = null
  }
}

function onModeChange() {
  if (form.mode_code !== 'cainiao_woda_printxml') {
    form.print_template_config_id = null
  } else if (!form.print_template_config_id && wodaTemplateConfigs.value[0]) {
    form.print_template_config_id = wodaTemplateConfigs.value[0].id
  }
  form.fields = defaultRecognitionFieldsForMode(form.mode_code)
}

function onWodaTemplateChange() {
  form.fields = defaultRecognitionFieldsForMode(form.mode_code)
}

function ruleModeLabel(rule: MatchRuleRecord): string {
  const code = rule.match_values?.mode_code as string | undefined
  if (!code) return '未绑定模式'
  return selectableModeCatalogs.value.find((mode) => mode.code === code)?.shortLabel ?? '未绑定模式'
}

function ruleModeCode(rule: MatchRuleRecord): string {
  return valueText(rule.match_values?.mode_code)
}

function ruleFieldsLabel(rule: MatchRuleRecord): string {
  const fieldsValue = rule.match_values?.fields
  const fieldCodes = Array.isArray(fieldsValue)
    ? fieldsValue
    : typeof fieldsValue === 'string'
      ? fieldsValue.split(',')
      : []
  const labels = fieldCodes
    .filter((code): code is string => typeof code === 'string' && code.length > 0)
    .map((code) => code.trim())
    .filter(Boolean)
    .map((code) => {
      for (const catalog of waybillModeCatalogs.value) {
        const field = catalog.fields.find((item) => item.code === code)
        if (field) return field.label
      }
      if (WODA_FIELD_LABELS[code]) return WODA_FIELD_LABELS[code]
      return code
    })
  return labels.length ? [...new Set(labels)].join('、') : '-'
}

function ruleKeywordLabel(rule: MatchRuleRecord): string {
  const keyword = valueText(rule.match_values?.product_keyword, valueText(rule.match_values?.keyword, '-'))
  const fallback = valueText(rule.match_values?.sku_fallback_keyword)
  return fallback ? `${keyword} / 备注兜底：${fallback}` : keyword
}

function ruleKeywordParts(rule: MatchRuleRecord): string[] {
  return ruleKeywordLabel(rule).split(/\s*\/\s*/).filter(Boolean)
}

function ruleTargetTypeLabel(rule: MatchRuleRecord): string {
  return rule.target_type === 'sku' ? 'SKU规则' : '商品规则'
}

function ruleTemplateLabel(rule: MatchRuleRecord): string {
  const label = rule.match_values?.print_template_label
  if (typeof label === 'string' && label) return label
  const configId = Number(rule.match_values?.print_template_config_id)
  if (!Number.isNaN(configId)) {
    const configLabel = printTemplateConfigs.value.find((item) => item.id === configId)?.template_label
    if (configLabel) return configLabel
  }
  const sourceKey = valueText(rule.match_values?.print_template_source_key)
  if (sourceKey) return `版式指纹 ${sourceKey}`
  const templateKey = valueText(rule.match_values?.print_template_key)
  if (templateKey) return `模板记录 ${templateKey}`
  return '-'
}

function ruleSearchText(rule: MatchRuleRecord): string {
  return [
    ruleModeLabel(rule),
    ruleTemplateLabel(rule),
    ruleFieldsLabel(rule),
    ruleKeywordLabel(rule),
    targetName(rule),
    ruleTargetTypeLabel(rule),
    rule.is_enabled === false ? '停用' : '启用',
  ].join('\n').toLowerCase()
}

const ruleModeOptions = computed(() =>
  selectableModeCatalogs.value
    .map((mode) => ({
      code: mode.code,
      label: mode.shortLabel,
      count: productRules.value.filter((rule) => ruleModeCode(rule) === mode.code).length,
    }))
    .filter((item) => item.count > 0),
)

const filteredProductRules = computed(() => {
  const search = ruleSearch.value.trim().toLowerCase()
  return productRules.value.filter((rule) => {
    if (ruleModeFilter.value && ruleModeCode(rule) !== ruleModeFilter.value) return false
    if (ruleTargetFilter.value && rule.target_type !== ruleTargetFilter.value) return false
    if (ruleStatusFilter.value === 'enabled' && rule.is_enabled === false) return false
    if (ruleStatusFilter.value === 'disabled' && rule.is_enabled !== false) return false
    return !search || ruleSearchText(rule).includes(search)
  })
})

function resetRuleFilters() {
  ruleSearch.value = ''
  ruleModeFilter.value = ''
  ruleTargetFilter.value = ''
  ruleStatusFilter.value = 'all'
}

function defaultRecognitionFieldsForMode(code?: string | null): string[] {
  if (code === 'cainiao_woda_printxml') {
    const fields = customConfigFieldOptions(selectedWodaTemplateConfig.value)
    const recommended = fields.filter((field) => field.recommended).map((field) => field.code)
    return recommended.length ? recommended : fields.map((field) => field.code)
  }
  const catalog = waybillModeCatalogs.value.find((mode) => mode.code === code) ?? waybillModeCatalogs.value[0]
  const recommended = catalog?.fields.filter((field) => field.recommended).map((field) => field.code) ?? []
  return recommended.length ? recommended : catalog?.fields.map((field) => field.code) ?? []
}

function fieldLabelFor(code: string): string {
  const field = activeRecognitionFields.value.find((item) => item.code === code)
  return field?.label ?? WODA_FIELD_LABELS[code] ?? code
}

function detailFieldText(detail: StandardDetailRecord, code: string): string {
  return valueText(detail.field_values?.[code])
}

function selectedFieldCodes(): string[] {
  return form.fields.length ? form.fields : defaultRecognitionFieldsForMode(form.mode_code)
}

function selectedWodaSkuAutoFieldCodes(): string[] {
  return selectedFieldCodes().filter((code) => WODA_SKU_AUTO_FIELD_CODES.has(code))
}

function modeLabelForDetail(detail: StandardDetailRecord): string {
  const catalog = selectableModeCatalogs.value.find((item) => item.code === detail.waybill_mode)
  if (catalog) return catalog.shortLabel
  return valueText(detail.field_values?.source_platform, valueText(detail.waybill_mode, '未知模式'))
}

function detailDisplayNumber(detail: StandardDetailRecord): number {
  return captureTaskDetailNumbers.value.get(detail.id) ?? detail.id
}

function detailSourceLabel(detail: StandardDetailRecord, itemIndex?: number | null, itemCount = 1): string {
  const detailNumber = detailDisplayNumber(detail)
  const displayId = itemIndex && itemCount > 1 ? `${detailNumber}-${itemIndex}` : String(detailNumber)
  return `面单 ${displayId} / ${modeLabelForDetail(detail)}`
}

function wodaTextForDetail(detail: StandardDetailRecord): string {
  const values = detail.field_values ?? {}
  return valueText(values.custom_area_raw_text, valueText(values.product_full_text))
}

function wodaItemFromParsedPayload(value: unknown, fallbackRawText: string): WodaExtractedItem | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const item = value as Record<string, unknown>
  const productText = valueText(item.product_text)
  const specText = valueText(item.spec_text)
  const sizeText = valueText(item.size_text)
  const salesAttr1Text = valueText(item.sales_attr1_text, specText)
  const salesAttr2Text = valueText(item.sales_attr2_text, sizeText)
  const quantityText = valueText(item.quantity_text)
  const remarkText = valueText(item.remark_text)
  const rawText = valueText(item.raw_text, fallbackRawText)
  if (!productText && !salesAttr1Text && !salesAttr2Text && !quantityText && !remarkText) return null
  return {
    productText,
    salesAttr1Text,
    salesAttr2Text,
    remarkText,
    specText: specText || salesAttr1Text,
    sizeText: sizeText || salesAttr2Text,
    quantityText,
    rawText,
  }
}

function wodaStructureForDetail(detail: StandardDetailRecord): WodaStructureResult {
  const rawText = wodaTextForDetail(detail)
  const analyzed = analyzeWodaStructure(rawText)
  const values = detail.field_values ?? {}
  const parsedItems = Array.isArray(values.custom_items)
    ? values.custom_items
        .map((item) => wodaItemFromParsedPayload(item, rawText))
        .filter((item): item is WodaExtractedItem => item !== null)
    : []
  if (!parsedItems.length) return analyzed

  const kind = (valueText(values.woda_structure_kind) as WodaStructureKind) || analyzed.kind
  return {
    ...analyzed,
    kind,
    label: valueText(values.woda_structure_label, wodaStructureLabel(kind)),
    itemCount: parsedItems.length,
    items: parsedItems,
    reason: valueText(values.woda_structure_reason, analyzed.reason),
  }
}

function detailHasParsedItems(detail: StandardDetailRecord): boolean {
  const customItems = detail.field_values?.custom_items
  return Array.isArray(customItems) && customItems.some((item) => item && typeof item === 'object')
}

function wodaConfigMatchesDetail(config: PrintTemplateConfigRecord, detail: StandardDetailRecord): boolean {
  const values = detail.field_values ?? {}
  const configId = Number(values.print_template_config_id)
  if (!Number.isNaN(configId) && configId === config.id) return true
  const detailTemplateKey = valueText(values.print_template_key)
  const configTemplateKey = wodaConfigSourceTemplateKey(config)
  return Boolean(detailTemplateKey && configTemplateKey && detailTemplateKey === configTemplateKey)
}

function uniqueWodaTemplateConfigForDetail(detail: StandardDetailRecord): PrintTemplateConfigRecord | null {
  const matches = wodaTemplateConfigs.value.filter((config) => wodaConfigMatchesDetail(config, detail))
  return matches.length === 1 ? matches[0] : null
}

function wodaTemplateConfigForDetail(detail: StandardDetailRecord): PrintTemplateConfigRecord | null {
  const values = detail.field_values ?? {}
  const configId = Number(values.print_template_config_id)
  if (!Number.isNaN(configId)) {
    const config = wodaTemplateConfigs.value.find((item) => item.id === configId)
    if (config) return config
  }
  return uniqueWodaTemplateConfigForDetail(detail)
}

function detailMatchesCurrentMode(detail: StandardDetailRecord): boolean {
  if (detail.waybill_mode !== form.mode_code) return false

  if (form.mode_code === 'cainiao_woda_printxml') {
    if (!selectedWodaTemplateConfig.value) return true
    return wodaConfigMatchesDetail(selectedWodaTemplateConfig.value, detail)
  }

  return true
}

function candidateText(detail: StandardDetailRecord): string {
  if (form.mode_code === 'cainiao_woda_printxml') {
    return wodaTextForDetail(detail)
  }

  const selectedFields = selectedFieldCodes()
  const textFields = selectedFields.length > 1
    ? selectedFields.filter((code) => code !== 'quantity')
    : selectedFields
  const texts = textFields
    .map((code) => detailFieldText(detail, code))
    .filter(Boolean)
  return [...new Set(texts)].join('\n')
}

function inferQuantity(text: string): string {
  const quantities = [...text.matchAll(/(?:\*|x|X|×)\s*(\d+)|(\d+)\s*件/g)]
    .map((match) => Number(match[1] || match[2]))
    .filter((value) => Number.isFinite(value) && value > 0)
  if (!quantities.length) return ''
  if (quantities.length === 1) return String(quantities[0])
  return String(quantities.reduce((sum, value) => sum + value, 0))
}

function inferSize(text: string): string {
  const sizes = new Set<string>()
  const pushMatches = (pattern: RegExp) => {
    for (const match of text.matchAll(pattern)) {
      const size = match[1]
      if (size) sizes.add(size)
    }
  }

  pushMatches(/(?:鞋码|尺码|码)[:：\s]*([2-4]\d(?:\.5)?|50)(?!\d)/g)
  pushMatches(/(?:^|[^\d])([2-4]\d(?:\.5)?|50)\s*(?=(?:\*|x|X|×)\s*\d)/g)
  pushMatches(/[，,]\s*([2-4]\d(?:\.5)?|50)\s*(?=$|\r?\n|[，,;；])/g)

  return [...sizes].join('、')
}

function wodaCandidateMetaText(detail: StandardDetailRecord): string {
  const structure = wodaStructureForDetail(detail)
  const salesAttr1 = [...new Set(structure.items.map((item) => item.salesAttr1Text || item.specText).filter(Boolean))].join('、')
  const salesAttr2 = [...new Set(structure.items.map((item) => item.salesAttr2Text || item.sizeText).filter(Boolean))].join('、')
  const quantityValues = structure.items
    .map((item) => Number(item.quantityText))
    .filter((value) => Number.isFinite(value) && value > 0)
  const quantity = quantityValues.length
    ? String(quantityValues.reduce((sum, value) => sum + value, 0))
    : ''
  const parts = [
    structure.itemCount > 1 ? `${structure.itemCount}个商品` : '',
    salesAttr1 ? `属性1 ${salesAttr1}` : '',
    salesAttr2 ? `属性2 ${salesAttr2}` : '',
    quantity ? `数量 ${quantity}` : '',
  ].filter(Boolean)
  return parts.join(' / ')
}

function wodaTemplateLabelForDetail(
  detail: StandardDetailRecord,
  _structure: WodaStructureResult,
  config: PrintTemplateConfigRecord | null,
): string {
  const selectedConfig = selectedWodaTemplateConfig.value
  if (selectedConfig && wodaConfigMatchesDetail(selectedConfig, detail)) {
    return selectedConfig.template_label || selectedConfig.template_key
  }
  if (config) return config.template_label || config.template_key
  return '-'
}

function candidateSpecText(detail: StandardDetailRecord): string {
  if (form.mode_code === 'cainiao_woda_printxml') {
    return wodaCandidateMetaText(detail)
  }

  const values = detail.field_values ?? {}
  if (detailHasParsedItems(detail)) {
    return [
      valueText(values.custom_sales_attr1_text),
      valueText(values.custom_sales_attr2_text),
      valueText(values.custom_quantity_text, valueText(values.quantity)),
    ].filter(Boolean).join(' / ')
  }

  return [
    valueText(values.spec_text),
    valueText(values.quantity),
    valueText(values.buyer_remark),
    valueText(values.seller_remark),
  ].filter(Boolean).join(' / ')
}

function wodaItemDisplayText(item: WodaExtractedItem): string {
  return [
    item.productText ? `商品名称：${item.productText}` : '',
    item.salesAttr1Text ? `销售属性1：${item.salesAttr1Text}` : '',
    item.salesAttr2Text ? `销售属性2：${item.salesAttr2Text}` : '',
    item.quantityText ? `商品数量：${item.quantityText}` : '',
    item.remarkText ? `备注字段：${item.remarkText}` : '',
  ].filter(Boolean).join('\n')
}

function standardItemFromDetail(detail: StandardDetailRecord): WodaExtractedItem | null {
  const values = detail.field_values ?? {}
  const parsedItems = Array.isArray(values.custom_items)
    ? values.custom_items
        .map((item) => wodaItemFromParsedPayload(item, valueText(values.product_full_text)))
        .filter((item): item is WodaExtractedItem => item !== null)
    : []
  if (parsedItems[0]) return normalizeStandardItemForDisplay(parsedItems[0], values)

  const productText = valueText(values.custom_product_text)
  const salesAttr1Text = valueText(values.custom_sales_attr1_text)
  const salesAttr2Text = valueText(values.custom_sales_attr2_text)
  const quantityText = valueText(values.custom_quantity_text, valueText(values.quantity))
  if (!productText && !salesAttr1Text && !salesAttr2Text && !quantityText) return null
  return normalizeStandardItemForDisplay({
    productText,
    salesAttr1Text,
    salesAttr2Text,
    specText: salesAttr1Text,
    sizeText: salesAttr2Text,
    quantityText,
    rawText: valueText(values.product_short_text, valueText(values.product_full_text)),
  }, values)
}

function splitBracketedProduct(value: string): { title: string; suffix: string } | null {
  const text = value.trim()
  const match = text.match(/^【([^】]+)】(.+)$/)
  if (!match) return null
  return {
    title: match[1].trim(),
    suffix: match[2].trim(),
  }
}

function normalizeStandardItemForDisplay(
  item: WodaExtractedItem,
  values: Record<string, unknown>,
): WodaExtractedItem {
  const fullTitle = valueText(values.product_full_text).replace(/^【|】$/g, '').trim()
  const productSplit = splitBracketedProduct(item.productText)
  const salesSplit = splitBracketedProduct(item.salesAttr1Text || '')
  const title = productSplit?.title || fullTitle || item.productText
  let salesAttr1Text = salesSplit?.suffix || productSplit?.suffix || item.salesAttr1Text || ''

  if (fullTitle && salesAttr1Text.startsWith(fullTitle)) {
    salesAttr1Text = salesAttr1Text.slice(fullTitle.length).trim()
  }
  if (title && salesAttr1Text.startsWith(title)) {
    salesAttr1Text = salesAttr1Text.slice(title.length).trim()
  }
  salesAttr1Text = salesAttr1Text.replace(/^】/, '').trim()

  return {
    ...item,
    productText: title || item.productText,
    salesAttr1Text: salesAttr1Text || item.salesAttr1Text,
    specText: salesAttr1Text || item.specText,
  }
}

function standardItemDisplayText(item: WodaExtractedItem): string {
  return [
    item.productText,
    item.salesAttr1Text && item.salesAttr1Text !== item.productText ? `规格：${item.salesAttr1Text}` : '',
    item.salesAttr2Text ? `尺码：${item.salesAttr2Text}` : '',
    item.quantityText ? `数量：${item.quantityText}` : '',
  ].filter(Boolean).join('\n')
}

function standardCandidateForDetail(detail: StandardDetailRecord): CollectedCandidate {
  const item = standardItemFromDetail(detail)
  const text = item ? standardItemDisplayText(item) : candidateText(detail)
  const productText = item?.productText ?? ''
  const skuKeywordText = [
    item?.salesAttr1Text,
    item?.salesAttr2Text,
  ].filter(Boolean).join('\n') || candidateText(detail)
  return {
    candidateKey: detailHasParsedItems(detail) ? `${detail.id}:1` : `${detail.id}:0`,
    id: detail.id,
    itemIndex: null,
    itemCount: 1,
    printTemplateConfigId: null,
    sourceLabel: detailSourceLabel(detail),
    text,
    keywordText: productText || candidateText(detail),
    productText,
    skuKeywordText,
    skuFallbackKeywordText: item?.rawText ?? '',
    specText: candidateSpecText(detail),
    templateLabel: selectedFieldCodes().map(fieldLabelFor).join('、'),
  }
}

function wodaItemKeywordText(item: WodaExtractedItem, fieldCodes = selectedFieldCodes()): string {
  const texts = fieldCodes
    .map((code) => wodaItemFieldText(item, code).trim())
    .filter(Boolean)
  return [...new Set(texts)].join('\n')
}

function wodaItemRemarkText(item: WodaExtractedItem): string {
  if (item.remarkText) return item.remarkText
  const remarkPattern = /(颜色分类|商品名称|商品名|买家备注|卖家备注|卖家留言|备注|留言|color|sku)/i
  const lines = valueText(item.rawText)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  const remarkLines = lines.filter((line) => remarkPattern.test(line))
  if (remarkLines.length) return remarkLines.join(' ')
  return lines.length > 1 ? lines.slice(1).join(' ') : ''
}

function keywordMatchesRecord(record: { name: string; keywords?: string[] | null }, keyword: string): boolean {
  const normalized = keyword.trim().toLowerCase()
  if (!normalized) return false
  const names = [record.name, ...(record.keywords ?? [])]
    .map((item) => valueText(item).trim().toLowerCase())
    .filter(Boolean)
  return names.some((item) => item === normalized || item.includes(normalized) || normalized.includes(item))
}

function wodaItemMetaText(structure: WodaStructureResult, item: WodaExtractedItem, index: number): string {
  return [
    structure.itemCount > 1 ? `第 ${index + 1}/${structure.itemCount} 个商品` : '',
    item.salesAttr1Text ? `属性1 ${item.salesAttr1Text}` : '',
    item.salesAttr2Text ? `属性2 ${item.salesAttr2Text}` : '',
    item.quantityText ? `数量 ${item.quantityText}` : '',
    item.remarkText ? `备注字段 ${item.remarkText}` : '',
  ].filter(Boolean).join(' / ')
}

function wodaConfigForCandidate(
  detail: StandardDetailRecord,
  _structure: WodaStructureResult,
): PrintTemplateConfigRecord | null {
  const detailConfig = wodaTemplateConfigForDetail(detail)
  if (selectedWodaTemplateConfig.value) {
    if (detailConfig) {
      return detailConfig.id === selectedWodaTemplateConfig.value.id ? detailConfig : null
    }
    return wodaConfigMatchesDetail(selectedWodaTemplateConfig.value, detail) ? selectedWodaTemplateConfig.value : null
  }
  return detailConfig
}

function wodaCandidateForItem(
  detail: StandardDetailRecord,
  parentStructure: WodaStructureResult,
  item: WodaExtractedItem,
  index: number,
): CollectedCandidate | null {
  const matchedConfig = wodaConfigForCandidate(detail, parentStructure)
  if (selectedWodaTemplateConfig.value && !matchedConfig) return null
  const itemCount = parentStructure.itemCount || 1
  const sourceLabel = detailSourceLabel(detail, index + 1, itemCount)
  const skuKeywordText = wodaItemKeywordText(item, selectedWodaSkuAutoFieldCodes())
  const skuFallbackKeywordText = wodaItemRemarkText(item)
  const candidateKey = detailHasParsedItems(detail) ? `${detail.id}:${index + 1}` : `${detail.id}:0`
  return {
    candidateKey,
    id: detail.id,
    itemIndex: index,
    itemCount,
    printTemplateConfigId: matchedConfig?.id ?? null,
    sourceLabel,
    text: wodaItemDisplayText(item),
    keywordText: wodaItemKeywordText(item),
    productText: item.productText,
    skuKeywordText,
    skuFallbackKeywordText,
    specText: wodaItemMetaText(parentStructure, item, index),
    templateLabel: matchedConfig ? matchedConfig.template_label || matchedConfig.template_key : '-',
  }
}

function wodaCollectedCandidates(): CollectedCandidate[] {
  return archivedStandardDetails.value
    .filter((detail) => detail.waybill_mode === 'cainiao_woda_printxml')
    .flatMap((detail) => {
      const structure = wodaStructureForDetail(detail)
      const items = structure.items.length
        ? structure.items
        : [{
            productText: candidateText(detail),
            salesAttr1Text: '',
            salesAttr2Text: '',
            specText: '',
            sizeText: '',
            quantityText: '',
            rawText: candidateText(detail),
          }]
      return items
        .map((item, index) => wodaCandidateForItem(detail, structure, item, index))
        .filter((item): item is CollectedCandidate => item !== null)
    })
}

const collectedCandidates = computed<CollectedCandidate[]>(() => {
  const candidates = form.mode_code === 'cainiao_woda_printxml'
    ? wodaCollectedCandidates()
    : archivedStandardDetails.value
      .filter(detailMatchesCurrentMode)
      .map(standardCandidateForDetail)

  return candidates
    .filter((item) => item.text.trim().length > 0)
    .sort((a, b) => b.id - a.id || Number(a.itemIndex ?? 0) - Number(b.itemIndex ?? 0))
})

function candidateSearchText(row: CollectedCandidate): string {
  return [
    row.sourceLabel,
    row.text,
    row.keywordText,
    row.specText,
    row.templateLabel,
    row.productText,
  ].join('\n').toLowerCase()
}

const filteredCollectedCandidates = computed(() => {
  const search = candidateSearch.value.trim().toLowerCase()
  return collectedCandidates.value.filter((row) => {
    if (candidateRecognitionFilter.value === 'matched' && candidateRecognitionStatus(row) !== 'matched') return false
    if (candidateRecognitionFilter.value === 'unmatched' && candidateRecognitionStatus(row) === 'matched') return false
    return !search || candidateSearchText(row).includes(search)
  })
})

function candidateMainText(row: CollectedCandidate): string {
  return row.productText || row.keywordText.split(/\r?\n/).find(Boolean) || row.text.split(/\r?\n/).find(Boolean) || '-'
}

function candidateDetailLines(row: CollectedCandidate): string[] {
  const lines = row.text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  const main = candidateMainText(row)
  return lines.filter((line) => line !== main).slice(0, 4)
}

const collectedEmptyDescription = computed(() => {
  if (!selectedCaptureTask.value) {
    return '请先选择一个监听批次。'
  }
  if (form.mode_code === 'cainiao_woda_printxml' && selectedWodaTemplateConfig.value) {
    const templateName = selectedWodaTemplateConfig.value.template_label || selectedWodaTemplateConfig.value.template_key
    return `当前监听批次还没有命中“${templateName}”指纹定义的已采集面单。`
  }
  return '当前监听批次在这个面单模式下还没有可读取的采集面单。'
})

const filteredCollectedEmptyDescription = computed(() => {
  if (!collectedCandidates.value.length) return collectedEmptyDescription.value
  if (candidateSearch.value.trim()) return '当前筛选下没有匹配的样本面单。'
  if (candidateRecognitionFilter.value === 'unmatched') return '当前批次没有未识别样本。'
  if (candidateRecognitionFilter.value === 'matched') return '当前批次没有已识别样本。'
  return '当前筛选下没有样本面单。'
})

const recognitionPreviewRows = computed<RecognitionPreviewRow[]>(() => recognitionPreview.value?.rows ?? [])
const recognitionPreviewSummary = computed(() => recognitionPreview.value?.summary ?? {})
const recognitionRowsByCandidateKey = computed(() => {
  const rows = new Map<string, RecognitionPreviewRow>()
  recognitionPreviewRows.value.forEach((row) => {
    rows.set(row.candidate_key, row)
  })
  return rows
})

const candidateRecognitionCounts = computed(() => {
  const counts = { all: 0, matched: 0, unmatched: 0 }
  collectedCandidates.value.forEach((row) => {
    counts.all += 1
    if (candidateRecognitionStatus(row) === 'matched') {
      counts.matched += 1
    } else {
      counts.unmatched += 1
    }
  })
  return counts
})

const recognitionPreviewStatusFilters = computed(() =>
  ['matched', 'product_unmatched', 'sku_unmatched', 'conflict']
    .map((status) => {
      const count = recognitionPreviewRows.value.filter((row) => row.status === status).length
      return {
        text: `${recognitionStatusLabel(status)} ${count}`,
        value: status,
      }
    })
    .filter((item) => !item.text.endsWith(' 0')),
)

const recognitionPreviewProductFilters = computed(() =>
  previewFilterOptions(recognitionPreviewRows.value.map((row) => row.product_name || '未命中商品')),
)

const recognitionPreviewSkuFilters = computed(() =>
  previewFilterOptions(recognitionPreviewRows.value.map((row) => row.sku_name || '未命中SKU')),
)

function recognitionSummaryValue(key: string): number {
  return Number(recognitionPreviewSummary.value[key] ?? 0)
}

const naturalTextCollator = new Intl.Collator('zh-CN', {
  numeric: true,
  sensitivity: 'base',
})

function previewFilterOptions(values: string[]) {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))]
    .sort((a, b) => naturalTextCollator.compare(a, b))
    .slice(0, 80)
    .map((value) => ({ text: value, value }))
}

function comparePreviewText(left: unknown, right: unknown): number {
  return naturalTextCollator.compare(valueText(left, '-'), valueText(right, '-'))
}

function recognitionStatusOrder(status: string): number {
  if (status === 'matched') return 1
  if (status === 'product_unmatched') return 2
  if (status === 'sku_unmatched') return 3
  if (status === 'conflict') return 4
  return 9
}

function sortRecognitionPreviewBySource(left: RecognitionPreviewRow, right: RecognitionPreviewRow): number {
  return Number(left.detail_id ?? 0) - Number(right.detail_id ?? 0)
    || Number(left.item_index ?? 0) - Number(right.item_index ?? 0)
    || comparePreviewText(left.source_label, right.source_label)
}

function sortRecognitionPreviewByProductText(left: RecognitionPreviewRow, right: RecognitionPreviewRow): number {
  return comparePreviewText(recognitionPreviewMainText(left), recognitionPreviewMainText(right))
    || comparePreviewText(left.sales_attr1_text, right.sales_attr1_text)
    || comparePreviewText(left.sales_attr2_text, right.sales_attr2_text)
}

function sortRecognitionPreviewByProduct(left: RecognitionPreviewRow, right: RecognitionPreviewRow): number {
  return comparePreviewText(left.product_name || '未命中商品', right.product_name || '未命中商品')
}

function sortRecognitionPreviewBySku(left: RecognitionPreviewRow, right: RecognitionPreviewRow): number {
  return comparePreviewText(left.sku_name || '未命中SKU', right.sku_name || '未命中SKU')
}

function sortRecognitionPreviewByMatch(left: RecognitionPreviewRow, right: RecognitionPreviewRow): number {
  return comparePreviewText(recognitionMatchLabel(left), recognitionMatchLabel(right))
}

function sortRecognitionPreviewByStatus(left: RecognitionPreviewRow, right: RecognitionPreviewRow): number {
  return recognitionStatusOrder(left.status) - recognitionStatusOrder(right.status)
    || comparePreviewText(left.reason, right.reason)
}

function sortRecognitionPreviewByReason(left: RecognitionPreviewRow, right: RecognitionPreviewRow): number {
  return comparePreviewText(left.reason, right.reason)
}

function filterRecognitionPreviewByStatus(value: string, row: RecognitionPreviewRow): boolean {
  return row.status === value
}

function filterRecognitionPreviewByProduct(value: string, row: RecognitionPreviewRow): boolean {
  return (row.product_name || '未命中商品') === value
}

function filterRecognitionPreviewBySku(value: string, row: RecognitionPreviewRow): boolean {
  return (row.sku_name || '未命中SKU') === value
}

function recognitionStatusLabel(status: string): string {
  if (status === 'matched') return '已匹配'
  if (status === 'product_unmatched') return '商品未命中'
  if (status === 'sku_unmatched') return 'SKU未命中'
  if (status === 'conflict') return '冲突'
  return status || '-'
}

function recognitionStatusTag(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'matched') return 'success'
  if (status === 'conflict') return 'danger'
  if (status === 'product_unmatched' || status === 'sku_unmatched') return 'warning'
  return 'info'
}

function candidateRecognitionStatus(row: CollectedCandidate): string {
  return candidateRecognitionRow(row)?.status
    ?? (recognitionPreview.value ? 'not_previewed' : 'pending')
}

function candidateRecognitionRow(row: CollectedCandidate): RecognitionPreviewRow | null {
  return recognitionRowsByCandidateKey.value.get(row.candidateKey) ?? null
}

function candidateRecognitionLabel(row: CollectedCandidate): string {
  const status = candidateRecognitionStatus(row)
  if (status === 'matched') return '已识别'
  if (status === 'product_unmatched') return '商品未识别'
  if (status === 'sku_unmatched') return 'SKU未识别'
  if (status === 'conflict') return '冲突'
  if (status === 'not_previewed') return '未进入预览'
  return recognitionPreviewLoading.value ? '识别中' : '待识别'
}

function candidateRecognitionTag(row: CollectedCandidate): 'success' | 'warning' | 'danger' | 'info' {
  const status = candidateRecognitionStatus(row)
  if (status === 'matched') return 'success'
  if (status === 'conflict') return 'danger'
  if (status === 'product_unmatched' || status === 'sku_unmatched') return 'warning'
  return 'info'
}

function candidateRecognitionReason(row: CollectedCandidate): string {
  const previewRow = candidateRecognitionRow(row)
  if (!previewRow || previewRow.status === 'matched') return ''
  return previewRow.reason || ''
}

function recognitionMatchLabel(row: RecognitionPreviewRow): string {
  if (row.match_type === 'forced') return '规则锁定'
  if (row.match_type === 'auto_fallback') return `备注自动：${row.match_keyword}`
  if (row.match_type === 'auto') return `自动：${row.match_keyword}`
  if (row.match_type === 'fallback') return `备注兜底：${row.match_keyword}`
  if (row.match_keyword) return `${row.match_keyword}`
  return '-'
}

function recognitionPreviewMainText(row: RecognitionPreviewRow): string {
  return normalizedRecognitionPreviewText(row).productText
}

function recognitionPreviewDetailLines(row: RecognitionPreviewRow): string[] {
  const normalized = normalizedRecognitionPreviewText(row)
  return [
    normalized.salesAttr1Text && normalized.salesAttr1Text !== normalized.productText ? `规格：${normalized.salesAttr1Text}` : '',
    row.sales_attr2_text ? `尺码：${row.sales_attr2_text}` : '',
    row.quantity_text ? `数量：${row.quantity_text}` : '',
  ].filter(Boolean)
}

function normalizedRecognitionPreviewText(row: RecognitionPreviewRow): { productText: string; salesAttr1Text: string } {
  const productText = row.product_text || ''
  const productSplit = splitBracketedProduct(productText)
  const salesSplit = splitBracketedProduct(row.sales_attr1_text || '')
  const title = productSplit?.title || productText || row.sales_attr1_text || '-'
  let salesAttr1Text = salesSplit?.suffix || productSplit?.suffix || row.sales_attr1_text || ''

  if (title && salesAttr1Text.startsWith(title)) {
    salesAttr1Text = salesAttr1Text.slice(title.length).trim()
  }
  salesAttr1Text = salesAttr1Text.replace(/^】/, '').trim()

  return {
    productText: title,
    salesAttr1Text,
  }
}

async function loadRecognitionPreview() {
  if (!selectedCaptureTaskId.value) {
    error.value = '请先选择监听批次。'
    return
  }
  recognitionPreviewLoading.value = true
  error.value = ''
  try {
    recognitionPreview.value = await getCaptureTaskRecognitionPreview(selectedCaptureTaskId.value)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '本批次识别预览加载失败'
  } finally {
    recognitionPreviewLoading.value = false
  }
}

function useCollectedCandidate(row: CollectedCandidate) {
  if (form.mode_code === 'cainiao_woda_printxml' && row.printTemplateConfigId) {
    const wasSameTemplate = form.print_template_config_id === row.printTemplateConfigId
    form.print_template_config_id = row.printTemplateConfigId
    if (!wasSameTemplate && !form.fields.length) {
      form.fields = defaultRecognitionFieldsForMode(form.mode_code)
    }
  }
  if (form.mode_code === 'cainiao_woda_printxml') {
    form.product_keyword = (row.keywordText || row.productText).trim()
    form.keyword = ''
    form.sku_fallback_keyword = ''
    const product = products.value.find((item) =>
      keywordMatchesRecord(item, row.productText) || keywordMatchesRecord(item, row.keywordText),
    )
    if (product) {
      form.product_id = product.id
      form.sku_id = null
    }
    return
  }
  form.keyword = (row.keywordText || row.text).trim()
  form.sku_fallback_keyword = ''
}

async function ensureProductIdentifyKeySet(): Promise<KeyFieldSetRecord> {
  if (productIdentifyKeySet.value) return productIdentifyKeySet.value
  const created = (await createRecord('/key-field-sets', {
    name: PRODUCT_IDENTIFY_SET_NAME,
    purpose: 'product_identify',
    field_codes: allProductRecognitionFieldCodes.value,
    priority: 10,
    is_enabled: true,
  })) as KeyFieldSetRecord
  keySets.value = [created, ...keySets.value]
  return created
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [
      keyFieldSets,
      matchRules,
      productRecords,
      skuRecords,
      detailRecords,
      captureTaskRecords,
      platformModes,
      platformTemplates,
      platformTemplateFields,
      templateConfigs,
    ] = await Promise.all([
      getRecords('/key-field-sets?limit=2000'),
      getRecords('/match-rules?limit=2000'),
      getRecords('/products?limit=2000'),
      getRecords('/product-skus?limit=2000'),
      getRecords('/standard-details?limit=2000'),
      getRecords('/capture-tasks?limit=2000'),
      getRecords('/waybill-modes?limit=2000'),
      getRecords('/waybill-templates?limit=2000'),
      getRecords('/waybill-template-fields?limit=2000'),
      getRecords('/print-template-configs?limit=2000'),
    ])
    keySets.value = keyFieldSets as KeyFieldSetRecord[]
    rules.value = matchRules as MatchRuleRecord[]
    products.value = productRecords as ProductRecord[]
    skus.value = skuRecords as ProductSkuRecord[]
    standardDetails.value = detailRecords as StandardDetailRecord[]
    captureTasks.value = captureTaskRecords as CaptureTaskRecord[]
    waybillModes.value = platformModes as WaybillModeRecord[]
    waybillTemplates.value = platformTemplates as WaybillTemplateRecord[]
    waybillTemplateFields.value = platformTemplateFields as WaybillTemplateFieldRecord[]
    printTemplateConfigs.value = templateConfigs as PrintTemplateConfigRecord[]
    const previousCaptureTaskId = selectedCaptureTaskId.value
    ensureSelectedCaptureTask()
    if (!selectableModeCatalogs.value.some((mode) => mode.code === form.mode_code)) {
      form.mode_code = selectableModeCatalogs.value[0]?.code ?? DEFAULT_WAYBILL_MODE_CODE
    }
    if (form.mode_code === 'cainiao_woda_printxml' && !selectedWodaTemplateConfig.value && wodaTemplateConfigs.value[0]) {
      form.print_template_config_id = wodaTemplateConfigs.value[0].id
    }
    if (!form.fields.length) {
      form.fields = defaultRecognitionFieldsForMode(form.mode_code)
    }
    if (selectedCaptureTaskId.value && selectedCaptureTaskId.value === previousCaptureTaskId) {
      await loadRecognitionPreview()
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '商品识别规则加载失败'
  } finally {
    loading.value = false
  }
}

async function saveRule() {
  const keyword = form.keyword.trim()
  const productKeyword = form.mode_code === 'cainiao_woda_printxml'
    ? form.product_keyword.trim()
    : keyword
  if (!productKeyword) {
    error.value = '商品主类关键词不能为空。'
    return
  }
  if (!form.fields.length) {
    error.value = '请至少选择一个参与识别的字段。'
    return
  }
  if (form.mode_code === 'cainiao_woda_printxml' && !selectedWodaTemplateConfig.value) {
    error.value = '请先选择一个我打模板。'
    return
  }
  if (!form.product_id) {
    error.value = '请先选择要关联的商品。'
    return
  }
  if (form.mode_code === 'cainiao_woda_printxml' && !form.product_keyword.trim()) {
    error.value = '商品主类关键词不能为空。'
    return
  }

  saving.value = true
  error.value = ''
  try {
    const keySet = await ensureProductIdentifyKeySet()
    const sku = skus.value.find((item) => item.id === form.sku_id)
    const targetType = sku ? 'sku' : 'product'
    const targetId = sku ? sku.id : form.product_id

    await createRecord('/match-rules', {
      key_field_set_id: keySet.id,
      match_values: buildProductRecognitionMatchValues({
        modeCode: form.mode_code,
        selectedTemplateConfig: selectedWodaTemplateConfig.value,
        productId: form.product_id,
        productName: productName(form.product_id),
        productKeyword,
        sku,
        fields: form.fields,
        skuAutoFields: selectedWodaSkuAutoFieldCodes(),
      }),
      target_type: targetType,
      target_id: targetId,
      target_name: sku ? `${productName(sku.product_id)} / ${sku.name}` : productName(form.product_id),
      priority: 100,
      is_enabled: true,
    })
    form.product_keyword = ''
    form.keyword = ''
    form.sku_fallback_keyword = ''
    form.fields = defaultRecognitionFieldsForMode(form.mode_code)
    form.product_id = null
    form.sku_id = null
    await load()
    if (selectedCaptureTaskId.value) {
      await loadRecognitionPreview()
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '商品识别规则保存失败'
  } finally {
    saving.value = false
  }
}

async function removeRule(row: MatchRuleRecord) {
  const keyword = row.match_values?.keyword ?? '该规则'
  try {
    await ElMessageBox.confirm(
      `确定删除商品识别规则“${keyword}”吗？`,
      '删除规则',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  deletingRuleId.value = row.id
  error.value = ''
  try {
    await deleteRecord(`/match-rules/${row.id}`)
    ElMessage.success('商品识别规则已删除')
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '商品识别规则删除失败'
  } finally {
    deletingRuleId.value = null
  }
}

watch(selectedCaptureTaskId, () => {
  recognitionPreview.value = null
  candidateSearch.value = ''
  if (selectedCaptureTaskId.value) {
    void loadRecognitionPreview()
  }
})

watch(
  () => session.currentWorkspaceId,
  () => {
    selectedCaptureTaskId.value = null
    recognitionPreview.value = null
    void load()
  },
)
onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>商品识别</h1>
      <p>平台先解析面单字段，客户管理员再指定“哪个面单模式、哪些字段、什么关键词”对应自己的商品。</p>
    </div>
    <div class="action-row">
      <el-button :icon="Refresh" :loading="loading" plain @click="load">刷新</el-button>
      <el-button :icon="Right" type="primary" @click="$router.push('/exceptions')">
        查看业务异常
      </el-button>
    </div>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="workflow-grid">
    <div class="work-surface">
      <h2>新增商品识别规则</h2>
      <el-form label-position="top" class="inline-config-form">
        <el-form-item label="面单模式">
          <el-select v-model="form.mode_code" placeholder="选择面单模式" @change="onModeChange">
            <el-option
              v-for="mode in selectableModeCatalogs"
              :key="mode.code"
              :label="mode.label"
              :value="mode.code"
            />
          </el-select>
          <p class="muted-text">{{ activeModeCatalog.description }}</p>
        </el-form-item>
        <el-form-item v-if="form.mode_code === 'cainiao_woda_printxml'" label="我打模板">
          <el-select
            v-model="form.print_template_config_id"
            clearable
            filterable
            placeholder="选择已维护的我打模板"
            @change="onWodaTemplateChange"
          >
            <el-option
              v-for="template in wodaTemplateConfigs"
              :key="template.id"
              :label="template.template_label || template.template_key"
              :value="template.id"
            />
          </el-select>
          <p class="muted-text">
            这里读取“打印模板规则”中保存的我打客户模板；先维护模板，再用它做商品识别。
          </p>
        </el-form-item>
        <el-alert
          v-if="form.mode_code === 'cainiao_woda_printxml' && !wodaTemplateConfigs.length"
          :closable="false"
          title="还没有我打模板定义，请先到“打印模板规则”新建模板。"
          type="warning"
        />
        <el-form-item label="商品主类识别字段">
          <el-checkbox-group v-model="form.fields" class="field-check-list">
            <el-checkbox
              v-for="field in activeRecognitionFields"
              :key="field.code"
              :label="field.code"
            >
              {{ field.label }}
              <small v-if="field.meaning" class="muted-line">{{ field.meaning }}</small>
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item v-if="form.mode_code === 'cainiao_woda_printxml'" label="商品主类关键词">
          <el-input v-model="form.product_keyword" placeholder="可填商品文字或销售属性里的主类关键词" />
        </el-form-item>
        <el-form-item v-else label="商品主类关键词">
          <el-input v-model="form.keyword" placeholder="输入选中字段里能识别商品主类的关键词" />
        </el-form-item>
        <el-form-item label="关联商品">
          <el-select v-model="form.product_id" clearable filterable placeholder="选择商品" @change="onProductChange">
            <el-option v-for="product in products" :key="product.id" :label="product.name" :value="product.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联SKU（可选）">
          <el-select
            v-model="form.sku_id"
            clearable
            filterable
            :disabled="!form.product_id"
            placeholder="不选则按销售属性自动匹配 SKU"
          >
            <el-option v-for="sku in selectedProductSkus" :key="sku.id" :label="sku.name" :value="sku.id" />
          </el-select>
        </el-form-item>
        <div class="action-row">
          <el-button type="primary" :loading="saving" @click="saveRule">保存规则</el-button>
        </div>
      </el-form>

      <div class="collected-preview-panel collected-reader-panel">
        <div class="panel-heading-row">
          <div>
            <h3>读取已采集面单</h3>
            <p class="muted-text">从监听批次中挑选商品文字，快速生成识别关键词。</p>
          </div>
          <div class="panel-metrics">
            <span>{{ archivedStandardDetails.length }} 张面单</span>
            <strong>{{ candidateRecognitionCounts.unmatched }} 条未识别</strong>
          </div>
        </div>

        <div class="candidate-toolbar">
          <label>
            <span>监听批次</span>
            <el-select
              v-model="selectedCaptureTaskId"
              clearable
              filterable
              placeholder="请选择监听批次"
            >
              <el-option
                v-for="(task, index) in sortedCaptureTasks"
                :key="task.id"
                :label="captureTaskLabel(task, index)"
                :value="task.id"
              />
            </el-select>
          </label>
          <label class="candidate-status-filter">
            <span>识别状态</span>
            <el-radio-group v-model="candidateRecognitionFilter" size="small">
              <el-radio-button label="unmatched">
                未识别 {{ candidateRecognitionCounts.unmatched }}
              </el-radio-button>
              <el-radio-button label="matched">
                已识别 {{ candidateRecognitionCounts.matched }}
              </el-radio-button>
              <el-radio-button label="all">
                全部 {{ candidateRecognitionCounts.all }}
              </el-radio-button>
            </el-radio-group>
          </label>
          <label>
            <span>查找文字</span>
            <el-input
              v-model="candidateSearch"
              clearable
              :prefix-icon="Search"
              placeholder="面单号、商品、规格、模板"
            />
          </label>
        </div>

        <div v-if="selectedCaptureTask" class="candidate-context-strip">
          <span>{{ captureTaskStatusLabel(selectedCaptureTask.status) }}</span>
          <strong>{{ formatCaptureTaskTime(selectedCaptureTask.started_at) }}</strong>
          <span>显示 {{ filteredCollectedCandidates.length }} / {{ collectedCandidates.length }}</span>
          <span>未识别 {{ candidateRecognitionCounts.unmatched }}</span>
          <span>已识别 {{ candidateRecognitionCounts.matched }}</span>
        </div>

        <el-table
          v-if="filteredCollectedCandidates.length"
          class="collected-candidate-table professional-table"
          :data="filteredCollectedCandidates"
          row-key="candidateKey"
          height="380"
          stripe
        >
          <el-table-column label="面单" width="150">
            <template #default="{ row }">
              <div class="candidate-source-cell">
                <strong>{{ row.sourceLabel.split(' / ')[0] }}</strong>
                <span>{{ row.sourceLabel.split(' / ')[1] || '-' }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="170">
            <template #default="{ row }">
              <div class="candidate-status-cell" :title="candidateRecognitionReason(row)">
                <el-tag :type="candidateRecognitionTag(row)" size="small">
                  {{ candidateRecognitionLabel(row) }}
                </el-tag>
                <small v-if="candidateRecognitionReason(row)">
                  {{ candidateRecognitionReason(row) }}
                </small>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="商品文字" min-width="340">
            <template #default="{ row }">
              <div class="candidate-main-cell">
                <strong>{{ candidateMainText(row) }}</strong>
                <span
                  v-for="line in candidateDetailLines(row)"
                  :key="line"
                >
                  {{ line }}
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="规格/数量" min-width="220">
            <template #default="{ row }">
              <span class="candidate-meta-text">{{ row.specText || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="form.mode_code === 'cainiao_woda_printxml' ? '模板' : '字段'" width="190">
            <template #default="{ row }">
              <span class="candidate-meta-text">{{ row.templateLabel }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="88" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="useCollectedCandidate(row)">使用</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty
          v-else
          :description="filteredCollectedEmptyDescription"
        />
      </div>

      <div class="collected-preview-panel">
        <div class="section-title-row">
          <h3>本批次识别预览</h3>
          <el-button
            type="primary"
            plain
            :loading="recognitionPreviewLoading"
            :disabled="!selectedCaptureTaskId"
            @click="loadRecognitionPreview"
          >
            预览本批次识别结果
          </el-button>
        </div>
        <p class="muted-text">按当前已保存商品/SKU规则执行，不会写入数据；用于检查这一批面单是否能自动命中。</p>

        <div v-if="recognitionPreview" class="template-preview-grid">
          <div>
            <span>商品行</span>
            <strong>{{ recognitionSummaryValue('total') }}</strong>
          </div>
          <div>
            <span>已匹配</span>
            <strong>{{ recognitionSummaryValue('matched') }}</strong>
          </div>
          <div>
            <span>备注兜底</span>
            <strong>{{ recognitionSummaryValue('fallback_matched') }}</strong>
          </div>
          <div>
            <span>异常</span>
            <strong>
              {{
                recognitionSummaryValue('product_unmatched')
                + recognitionSummaryValue('sku_unmatched')
                + recognitionSummaryValue('conflict')
              }}
            </strong>
          </div>
        </div>

        <el-table
          v-if="recognitionPreviewRows.length"
          class="recognition-preview-table professional-table"
          :data="recognitionPreviewRows"
          row-key="candidate_key"
          height="360"
          stripe
        >
          <el-table-column
            label="面单"
            width="120"
            sortable
            :sort-method="sortRecognitionPreviewBySource"
          >
            <template #default="{ row }">{{ row.source_label }}</template>
          </el-table-column>
          <el-table-column
            label="商品行文字"
            min-width="280"
            sortable
            :sort-method="sortRecognitionPreviewByProductText"
          >
            <template #default="{ row }">
              <div class="candidate-main-cell">
                <strong>{{ recognitionPreviewMainText(row) }}</strong>
                <span
                  v-for="line in recognitionPreviewDetailLines(row)"
                  :key="line"
                >
                  {{ line }}
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            label="商品"
            min-width="140"
            sortable
            :sort-method="sortRecognitionPreviewByProduct"
            :filters="recognitionPreviewProductFilters"
            :filter-method="filterRecognitionPreviewByProduct"
            filter-placement="bottom-start"
          >
            <template #default="{ row }">{{ row.product_name || '-' }}</template>
          </el-table-column>
          <el-table-column
            label="SKU"
            min-width="150"
            sortable
            :sort-method="sortRecognitionPreviewBySku"
            :filters="recognitionPreviewSkuFilters"
            :filter-method="filterRecognitionPreviewBySku"
            filter-placement="bottom-start"
          >
            <template #default="{ row }">{{ row.sku_name || '-' }}</template>
          </el-table-column>
          <el-table-column
            label="命中"
            min-width="160"
            sortable
            :sort-method="sortRecognitionPreviewByMatch"
          >
            <template #default="{ row }">{{ recognitionMatchLabel(row) }}</template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="120"
            sortable
            :sort-method="sortRecognitionPreviewByStatus"
            :filters="recognitionPreviewStatusFilters"
            :filter-method="filterRecognitionPreviewByStatus"
            filter-placement="bottom-start"
          >
            <template #default="{ row }">
              <el-tag :type="recognitionStatusTag(row.status)">
                {{ recognitionStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="原因"
            min-width="220"
            sortable
            :sort-method="sortRecognitionPreviewByReason"
          >
            <template #default="{ row }">{{ row.reason }}</template>
          </el-table-column>
        </el-table>
        <el-empty
          v-else-if="recognitionPreview"
          description="当前批次没有可预览的商品行。"
        />
      </div>
    </div>

    <div class="work-surface rule-maintenance-surface">
      <div class="panel-heading-row">
        <div>
          <h2>已有商品识别规则</h2>
          <p class="muted-text">按模式、目标和关键词维护规则。</p>
        </div>
        <el-button :icon="Refresh" plain @click="load">刷新</el-button>
      </div>

      <div class="rule-stat-strip">
        <div>
          <span>全部规则</span>
          <strong>{{ ruleStats.total }}</strong>
        </div>
        <div>
          <span>启用中</span>
          <strong>{{ ruleStats.enabled }}</strong>
        </div>
        <div>
          <span>SKU规则</span>
          <strong>{{ ruleStats.sku }}</strong>
        </div>
        <div>
          <span>商品规则</span>
          <strong>{{ ruleStats.product }}</strong>
        </div>
      </div>

      <div class="rule-filter-bar">
        <el-input
          v-model="ruleSearch"
          clearable
          :prefix-icon="Search"
          placeholder="查找关键词、商品、SKU、模板、字段"
        />
        <el-select v-model="ruleModeFilter" clearable placeholder="全部模式">
          <el-option
            v-for="mode in ruleModeOptions"
            :key="mode.code"
            :label="`${mode.label}（${mode.count}）`"
            :value="mode.code"
          />
        </el-select>
        <el-select v-model="ruleTargetFilter" clearable placeholder="全部对象">
          <el-option label="商品规则" value="product" />
          <el-option label="SKU规则" value="sku" />
        </el-select>
        <el-select v-model="ruleStatusFilter" placeholder="全部状态">
          <el-option label="全部状态" value="all" />
          <el-option label="启用" value="enabled" />
          <el-option label="停用" value="disabled" />
        </el-select>
        <el-button plain @click="resetRuleFilters">重置</el-button>
      </div>

      <el-table
        v-if="productRules.length"
        class="professional-table"
        :data="filteredProductRules"
        height="520"
        row-key="id"
        stripe
      >
        <el-table-column label="规则" min-width="230">
          <template #default="{ row }">
            <div class="rule-main-cell">
              <div>
                <el-tag size="small" effect="plain">{{ ruleModeLabel(row) }}</el-tag>
                <el-tag size="small" :type="row.target_type === 'sku' ? 'primary' : 'info'" effect="plain">
                  {{ ruleTargetTypeLabel(row) }}
                </el-tag>
              </div>
              <strong>{{ ruleTemplateLabel(row) }}</strong>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="识别条件" min-width="300">
          <template #default="{ row }">
            <div class="rule-condition-cell">
              <span>{{ ruleFieldsLabel(row) }}</span>
              <div class="rule-keyword-list">
                <el-tag
                  v-for="part in ruleKeywordParts(row)"
                  :key="part"
                  size="small"
                  type="warning"
                  effect="light"
                >
                  {{ part }}
                </el-tag>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="关联对象" min-width="210">
          <template #default="{ row }">
            <strong class="rule-target-name">{{ targetName(row) }}</strong>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="92">
          <template #default="{ row }">
            <el-tag :type="row.is_enabled ? 'success' : 'info'">
              {{ row.is_enabled ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="96" fixed="right">
          <template #default="{ row }">
            <el-button
              :icon="Delete"
              :loading="deletingRuleId === row.id"
              link
              type="danger"
              @click="removeRule(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="没有符合筛选条件的规则。" />
        </template>
      </el-table>
      <el-empty v-else description="还没有商品识别规则。" />
    </div>
  </section>
</template>
