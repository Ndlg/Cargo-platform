<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Delete, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createRecord,
  deleteRecord,
  getRecords,
  updateRecord,
  type ApiRecord,
  type CaptureTaskRecord,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'
import {
  CUSTOM_WODA_FIELD_LABELS,
  CUSTOM_WODA_FIELD_ORDER,
  type CustomWodaFieldCode,
} from './wodaFields'
import {
  buildFieldMappingPayload,
  configSamplePreviewValue,
  restoreFieldAssignments,
} from './printTemplateRuleMapping'

type StandardDetailRecord = ApiRecord & {
  id: number
  waybill_mode?: string | null
  field_values?: Record<string, unknown>
}

type PrintTemplateConfigRecord = ApiRecord & {
  id: number
  waybill_mode: string
  template_key: string
  template_label?: string | null
  template_source?: string | null
  parse_status?: string
  config?: Record<string, unknown> | null
  is_enabled?: boolean
}

type WaybillModeRecord = ApiRecord & {
  id: number
  name: string
  code: string
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

type TemplateGroup = {
  groupKey: string
  componentCategory: ComponentCategory
  modeCode: string
  modeName: string
  templateKey: string
  platformTemplateId: number
  platformTemplateName: string
  templateSource: string
  parseStatus: 'platform_structured' | 'custom_required'
  details: StandardDetailRecord[]
  savedConfig: PrintTemplateConfigRecord | null
  savedConfigCount: number
}

type FieldSample = {
  code: string
  label: string
  rawField: string
}

type SegmentToken = {
  id: string
  lineIndex: number
  text: string
  hint: string
}

type PreviewToken = SegmentToken & {
  kind: string
}

type PreviewLine = {
  id: string
  index: number
  tokens: PreviewToken[]
}

type PreviewItemGroup = {
  id: string
  label: string
  tokens: PreviewToken[]
  canAssign: boolean
}

type SampleItemPreviewRow = {
  index: number
  productText: string
  salesAttr1Text: string
  salesAttr2Text: string
  quantityText: string
  remarkText: string
}

type ComponentCategory = 'douyin' | 'cainiao'

const SUPPORTED_MODES = new Set(['douyin_cloud_print', 'cainiao_direct_shop', 'cainiao_woda_printxml'])
const CUSTOM_RULE_MODES = new Set(['douyin_cloud_print', 'cainiao_woda_printxml'])
const DOUYIN_ASSIGNABLE_FIELD_ORDER: CustomWodaFieldCode[] = [
  'custom_sales_attr1_text',
  'custom_sales_attr2_text',
]

const session = useSessionStore()
const details = ref<StandardDetailRecord[]>([])
const captureTasks = ref<CaptureTaskRecord[]>([])
const configs = ref<PrintTemplateConfigRecord[]>([])
const waybillModes = ref<WaybillModeRecord[]>([])
const waybillTemplates = ref<WaybillTemplateRecord[]>([])
const waybillTemplateFields = ref<WaybillTemplateFieldRecord[]>([])
const selectedGroupKey = ref('')
const activeComponent = ref<ComponentCategory>('douyin')
const selectedCaptureTaskId = ref<number | null>(null)
const selectedSampleId = ref<number | null>(null)
const selectedConfigId = ref<number | null>(null)
const loading = ref(false)
const saving = ref(false)
const deletingId = ref<number | null>(null)
const creatingDraft = ref(false)
const error = ref('')
const manualFieldAssignments = ref<Partial<Record<CustomWodaFieldCode, CustomWodaFieldCode>>>({})

const form = reactive({
  templateLabel: '',
  productLineIndex: -1,
  specLineIndex: -1,
  sizeLineIndex: -1,
  quantityLineIndex: -1,
  remarkLineIndex: -1,
  productSegmentText: '',
  specSegmentText: '',
  sizeSegmentText: '',
  quantitySegmentText: '',
  remarkSegmentText: '',
  remark: '',
})

function valueText(value: unknown, fallback = ''): string {
  if (value === null || value === undefined || value === '') return fallback
  if (Array.isArray(value)) return value.map((item) => valueText(item)).filter(Boolean).join('\n')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function configKey(modeCode: string, templateKey: string): string {
  return `${modeCode}::${templateKey}`
}

function isCustomRuleMode(modeCode: string): boolean {
  return CUSTOM_RULE_MODES.has(modeCode)
}

function customRuleTypeForMode(modeCode: string): string {
  return modeCode === 'douyin_cloud_print' ? 'douyin_product_info_v1' : 'custom_text_lines_v1'
}

function customModeName(modeCode: string): string {
  if (modeCode === 'douyin_cloud_print') return '抖店'
  if (modeCode === 'cainiao_woda_printxml') return '我打'
  return modeCode
}

function componentCategoryForMode(modeCode: string): ComponentCategory {
  return modeCode === 'douyin_cloud_print' ? 'douyin' : 'cainiao'
}

function componentLabel(category: ComponentCategory): string {
  return category === 'douyin' ? '抖店打印组件' : '菜鸟打印组件'
}

function fieldValues(detail: StandardDetailRecord): Record<string, unknown> {
  return detail.field_values ?? {}
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
  details.value.forEach((detail) => {
    const taskId = detailCaptureTaskId(detail)
    if (!taskId) return
    counts.set(taskId, (counts.get(taskId) ?? 0) + 1)
  })
  return counts
})
const archivedDetails = computed(() => {
  if (!selectedCaptureTaskId.value) return []
  return details.value.filter((detail) => detailCaptureTaskId(detail) === selectedCaptureTaskId.value)
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
  details.value.forEach((detail) => {
    const taskId = detailCaptureTaskId(detail)
    if (taskId) taskIdsWithDetails.add(taskId)
  })
  selectedCaptureTaskId.value =
    sortedCaptureTasks.value.find((task) => taskIdsWithDetails.has(task.id))?.id
    ?? sortedCaptureTasks.value[0]?.id
    ?? null
}

const configMap = computed(() => {
  const map = new Map<string, PrintTemplateConfigRecord>()
  configs.value.forEach((item) => {
    map.set(configKey(item.waybill_mode, item.template_key), item)
  })
  return map
})

const modeById = computed(() => {
  const map = new Map<number, WaybillModeRecord>()
  waybillModes.value.forEach((mode) => {
    map.set(mode.id, mode)
  })
  return map
})

const detailsByMode = computed(() => {
  const map = new Map<string, StandardDetailRecord[]>()
  archivedDetails.value.forEach((detail) => {
    const modeCode = detail.waybill_mode || ''
    if (!SUPPORTED_MODES.has(modeCode)) return
    const items = map.get(modeCode) ?? []
    items.push(detail)
    map.set(modeCode, items)
  })
  return map
})

function platformTemplateKey(template: WaybillTemplateRecord): string {
  return `platform-template:${template.id}`
}

function configsForCustomMode(modeCode: string): PrintTemplateConfigRecord[] {
  return configs.value.filter((item) => item.waybill_mode === modeCode && item.is_enabled !== false)
}

function configSourceTemplateKeyValue(config: PrintTemplateConfigRecord): string {
  const payload = objectValue(config.config)
  const match = objectValue(payload.template_match)
  return valueText(match.source_template_key, valueText(payload.source_template_key, config.template_key))
}

function configsForGroup(modeCode: string, templateKey: string): PrintTemplateConfigRecord[] {
  return configsForCustomMode(modeCode).filter((config) => configSourceTemplateKeyValue(config) === templateKey)
}

function configForGroup(modeCode: string, templateKey: string): PrintTemplateConfigRecord | null {
  return configsForGroup(modeCode, templateKey)[0]
    ?? configMap.value.get(configKey(modeCode, templateKey))
    ?? null
}

function modeNameForCode(modeCode: string): string {
  return waybillModes.value.find((mode) => mode.code === modeCode)?.name
    ?? (modeCode === 'douyin_cloud_print' ? '抖店 / CloudPrint' : '菜鸟 woda / 我打中转')
}

function platformTemplateIdForMode(modeCode: string): number {
  const mode = waybillModes.value.find((item) => item.code === modeCode)
  if (!mode) return 0
  return waybillTemplates.value.find((template) => template.waybill_mode_id === mode.id)?.id ?? 0
}

function templateDisplayName(modeCode: string, templateKey: string): string {
  if (modeCode === 'douyin_cloud_print') return `抖店版式 ${templateKeyName(templateKey)}`
  if (modeCode === 'cainiao_woda_printxml') return `我打模板 ${templateKey}`
  return templateKey
}

function templateKeyName(templateKey: string): string {
  const text = valueText(templateKey)
  const withoutPrefix = text.startsWith('url:') ? text.slice(4) : text
  const firstKey = withoutPrefix.split('|')[0] ?? withoutPrefix
  return firstKey.split('/').filter(Boolean).pop() || text || '-'
}

const allTemplateGroups = computed<TemplateGroup[]>(() => {
  const groups: TemplateGroup[] = []
  const map = new Map<string, TemplateGroup>()
  archivedDetails.value.forEach((detail) => {
    const modeCode = detail.waybill_mode || ''
    if (!isCustomRuleMode(modeCode)) return
    const values = fieldValues(detail)
    const templateKey = valueText(values.print_template_key, valueText(values.template_code, modeCode))
    if (!templateKey) return
    const groupKey = configKey(modeCode, templateKey)
    const existing = map.get(groupKey)
    if (existing) {
      existing.details.push(detail)
      return
    }
    const modeName = modeNameForCode(modeCode)
    const platformTemplateId = platformTemplateIdForMode(modeCode)
    const group: TemplateGroup = {
      groupKey,
      componentCategory: componentCategoryForMode(modeCode),
      modeCode,
      modeName,
      templateKey,
      platformTemplateId,
      platformTemplateName: templateDisplayName(modeCode, templateKey),
      templateSource: valueText(
        values.print_template_source,
        modeCode === 'douyin_cloud_print' ? 'douyin_template_url' : 'printxml_layout',
      ),
      parseStatus: 'custom_required',
      details: [detail],
      savedConfig: configForGroup(modeCode, templateKey),
      savedConfigCount: configsForGroup(modeCode, templateKey).length,
    }
    map.set(groupKey, group)
  })
  groups.push(...map.values())
  return groups.sort((a, b) => {
    const componentCompare = componentLabel(a.componentCategory).localeCompare(componentLabel(b.componentCategory), 'zh-CN')
    if (componentCompare) return componentCompare
    return a.platformTemplateName.localeCompare(b.platformTemplateName, 'zh-CN')
  })
})

const templateGroups = computed<TemplateGroup[]>(() =>
  allTemplateGroups.value.filter((group) => group.componentCategory === activeComponent.value),
)

const componentTabs = computed(() => {
  const counts: Record<ComponentCategory, number> = { douyin: 0, cainiao: 0 }
  allTemplateGroups.value.forEach((group) => {
    counts[group.componentCategory] += group.details.length
  })
  return [
    { value: 'douyin' as ComponentCategory, label: componentLabel('douyin'), count: counts.douyin },
    { value: 'cainiao' as ComponentCategory, label: componentLabel('cainiao'), count: counts.cainiao },
  ]
})

const selectedGroup = computed(
  () => templateGroups.value.find((group) => group.groupKey === selectedGroupKey.value) ?? templateGroups.value[0] ?? null,
)

const customGroups = computed(() => templateGroups.value.filter((group) => group.parseStatus === 'custom_required'))
const defaultCustomGroup = computed(() => customGroups.value[0] ?? null)
const definedTemplateConfigs = computed(() => {
  const customModeCodes = new Set(customGroups.value.map((group) => group.modeCode))
  return configs.value
    .filter((item) => customModeCodes.has(item.waybill_mode) && item.is_enabled !== false)
    .sort((a, b) => {
      const modeCompare = customModeName(a.waybill_mode).localeCompare(customModeName(b.waybill_mode), 'zh-CN')
      if (modeCompare) return modeCompare
      return b.id - a.id
    })
})
const unsavedCustomCount = computed(() => customGroups.value.filter((group) => !group.savedConfig).length)
const customDefinitionCount = computed(() => customGroups.value.reduce((sum, group) => sum + group.savedConfigCount, 0))
const wodaSampleCount = computed(() => customGroups.value.reduce((sum, group) => sum + group.details.length, 0))
const selectedGroupDetails = computed(() =>
  [...(selectedGroup.value?.details ?? [])].sort((a, b) => a.id - b.id),
)
const selectedGroupIndex = computed(() => {
  if (!selectedGroup.value) return 0
  const index = templateGroups.value.findIndex((group) => group.groupKey === selectedGroup.value?.groupKey)
  return index >= 0 ? index + 1 : 0
})

const selectedSample = computed(() => {
  if (selectedSampleId.value === null) return selectedGroupDetails.value[0] ?? null
  return selectedGroupDetails.value.find((detail) => detail.id === selectedSampleId.value) ?? selectedGroupDetails.value[0] ?? null
})
const selectedValues = computed(() => fieldValues(selectedSample.value ?? ({} as StandardDetailRecord)))
const selectedSampleTemplateKey = computed(() => {
  if (!selectedSample.value) return ''
  return valueText(selectedValues.value.print_template_key)
})
const selectedTemplateKey = computed(() => {
  const group = selectedGroup.value
  if (!group) return ''
  if (group.parseStatus !== 'custom_required') return group.templateKey
  return selectedSampleTemplateKey.value
})
const selectedCustomConfigs = computed(() => {
  const group = selectedGroup.value
  if (!group || group.parseStatus !== 'custom_required') return []
  return configsForCustomMode(group.modeCode)
})
const activeConfig = computed(() => {
  const group = selectedGroup.value
  if (!group) return null
  if (group.parseStatus === 'custom_required') {
    const selected = selectedCustomConfigs.value.find((item) => item.id === selectedConfigId.value)
    if (selected) return selected
    if (!selectedTemplateKey.value) return null
    const sampleConfig = configMap.value.get(configKey(group.modeCode, selectedTemplateKey.value))
    return sampleConfig ?? null
  }
  return configMap.value.get(configKey(group.modeCode, group.templateKey)) ?? null
})
const activeConfigPayload = computed(() => objectValue(activeConfig.value?.config))

const sampleOptions = computed(() =>
  selectedGroupDetails.value.map((detail, index) => ({
    id: detail.id,
    label: `样本 ${index + 1} / ${valueText(fieldValues(detail).logistics_no, valueText(fieldValues(detail).raw_document_id, '-'))}`,
  })),
)
const selectedSampleLabel = computed(() =>
  sampleOptions.value.find((option) => option.id === selectedSampleId.value)?.label ?? '样本面单',
)

const customLines = computed(() => {
  if (selectedGroup.value?.modeCode === 'douyin_cloud_print') {
    const productInfo = valueText(
      selectedValues.value.product_full_text,
      valueText(selectedValues.value.custom_product_text),
    ).trim()
    return productInfo ? [productInfo] : []
  }
  const lines = selectedValues.value.custom_area_lines
  if (Array.isArray(lines)) {
    const parsed = lines.map((line) => valueText(line).trim()).filter(Boolean)
    if (parsed.length) return parsed
  }
  return valueText(selectedValues.value.custom_area_raw_text)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
})

const previewWaybillNo = computed(() =>
  valueText(
    selectedValues.value.logistics_no,
    valueText(selectedValues.value.raw_document_id, 'YT7626784139743'),
  ),
)

function isDouyinSelectedGroup(): boolean {
  return selectedGroup.value?.modeCode === 'douyin_cloud_print'
}

function douyinAutoFieldText(code: CustomWodaFieldCode): string {
  if (code === 'custom_product_text') {
    return valueText(selectedValues.value.product_full_text, valueText(selectedValues.value.custom_product_text))
  }
  if (code === 'custom_quantity_text') {
    return valueText(selectedValues.value.product_count_text, valueText(selectedValues.value.custom_quantity_text))
  }
  if (code === 'custom_item_remark_text') {
    return valueText(selectedValues.value.seller_remark, valueText(selectedValues.value.custom_item_remark_text))
  }
  return ''
}

const assignableFieldOrder = computed<CustomWodaFieldCode[]>(() =>
  isDouyinSelectedGroup() ? DOUYIN_ASSIGNABLE_FIELD_ORDER : CUSTOM_WODA_FIELD_ORDER,
)

function waybillPersonText(value: unknown, fallback: string): string {
  const payload = objectValue(value)
  if (Object.keys(payload).length) {
    const address = objectValue(payload.address)
    return [
      valueText(payload.name),
      valueText(payload.mobile, valueText(payload.phone)),
      valueText(address.city),
      valueText(address.district),
      valueText(address.town),
    ].filter(Boolean).join(' ') || fallback
  }
  return valueText(value, fallback)
}

const previewRecipientText = computed(() =>
  waybillPersonText(selectedValues.value.recipient_masked, '三多 14735148033-9479'),
)

const previewSenderText = computed(() =>
  waybillPersonText(selectedValues.value.sender_masked, '小* 1*****5420'),
)

function extractionFieldMap(template: WaybillTemplateRecord | undefined): Record<string, unknown> {
  const rules = template?.extraction_rules
  if (!rules || typeof rules !== 'object') return {}
  const fields = (rules as Record<string, unknown>).fields
  return fields && typeof fields === 'object' ? fields as Record<string, unknown> : {}
}

const structuredSamples = computed<FieldSample[]>(() => {
  const group = selectedGroup.value
  if (!group) return []
  const template = waybillTemplates.value.find((item) => item.id === group.platformTemplateId)
  const extractionFields = extractionFieldMap(template)
  return waybillTemplateFields.value
    .filter((field) => field.waybill_template_id === group.platformTemplateId)
    .sort((a, b) => Number(a.sort_order ?? 0) - Number(b.sort_order ?? 0))
    .map((field) => ({
      code: field.target_field_code,
      label: valueText(field.extraction_config?.name, field.target_field_code),
      rawField: valueText(extractionFields[field.target_field_code], field.target_field_code),
    }))
})

function segmentKind(text: string): string {
  if (/^(?:[2-4]\d(?:\.5)?|50)$/.test(text)) return '疑似属性2'
  if (/^\d+$/.test(text)) return '数字'
  if (/(?:\*|x|X|×)\s*\d+|\d+\s*件/.test(text)) return '含数量'
  return '文字'
}

function splitLeadingSizeProduct(text: string): { sizeText: string; productText: string } | null {
  const match = text.trim().match(/^([2-4]\d(?:\.5)?|50)\s+(.+)$/)
  if (!match?.[1] || !match?.[2]) return null
  return {
    sizeText: match[1].trim(),
    productText: match[2].trim(),
  }
}

function splitSegmentsFromLine(line: string, lineIndex: number): SegmentToken[] {
  const tokens: SegmentToken[] = []
  const seen = new Set<string>()
  const push = (text: string, hint = segmentKind(text)) => {
    const trimmed = text.trim()
    if (!trimmed || seen.has(trimmed)) return
    seen.add(trimmed)
    tokens.push({
      id: `${lineIndex}-${tokens.length}-${trimmed}`,
      lineIndex,
      text: trimmed,
      hint,
    })
  }

  push(line, '整行')
  line.split(/[，,；;|｜\n]+/).forEach((part) => {
    const trimmed = part.trim()
    if (!trimmed) return
    const quantityTail = trimmed.match(/^(.+?)\s*(?:\*|x|X|×)\s*(\d+)\s*$/)
    const body = (quantityTail?.[1] ?? trimmed).trim()
    const leadingSizeProduct = splitLeadingSizeProduct(body)
    if (leadingSizeProduct) {
      push(leadingSizeProduct.sizeText, '疑似属性2')
      push(leadingSizeProduct.productText)
    } else {
      push(body)
    }
    if (quantityTail?.[2]) push(quantityTail[2], '疑似数量')
  })

  for (const match of line.matchAll(/(?:^|[^\d])([2-4]\d(?:\.5)?|50)\s*(?:\*|x|X|×)\s*(\d+)(?!\d)/g)) {
    push(match[1], '疑似属性2')
    push(match[2], '疑似数量')
  }
  for (const match of line.matchAll(/(?:\*|x|X|×)\s*(\d+)/g)) {
    push(match[1], '疑似数量')
  }

  return tokens
}

const segmentTokens = computed(() =>
  customLines.value.flatMap((line, index) => splitSegmentsFromLine(line, index)),
)

function douyinProductInfoParts(text: string): {
  productText: string
  salesAttr1Text: string
  salesAttr2Text: string
  quantityText: string
} {
  const normalized = valueText(text).replace(/\s+/g, ' ').trim()
  if (!normalized) {
    return { productText: '', salesAttr1Text: '', salesAttr2Text: '', quantityText: '' }
  }
  const lastBracketIndex = normalized.lastIndexOf('】')
  const productText = lastBracketIndex >= 0 ? normalized.slice(0, lastBracketIndex + 1).trim() : normalized
  const tail = (lastBracketIndex >= 0 ? normalized.slice(lastBracketIndex + 1) : normalized).trim()
  const quantityMatch = tail.match(/^(.*?)\s+(\d+)\s*件\s*$/)
  const withoutQuantity = (quantityMatch?.[1] ?? tail).trim()
  const quantityText = quantityMatch?.[2] ?? ''
  const sizePattern = '(?:[2-4]\\d(?:\\.5)?|50|XS|S|M|L|XL|XXL|XXXL)'
  const sizeMatch = withoutQuantity.match(new RegExp(`^(.*?)\\s+(${sizePattern})$`, 'i'))
  const salesAttr1Text = (sizeMatch?.[1] ?? withoutQuantity).trim()
  const salesAttr2Text = (sizeMatch?.[2] ?? '').trim()
  return { productText, salesAttr1Text, salesAttr2Text, quantityText }
}

function splitDouyinPreviewTokensFromLine(line: string, lineIndex: number): PreviewToken[] {
  const parts = douyinProductInfoParts(line)
  const tokens: PreviewToken[] = []
  const push = (text: string, hint: string) => {
    const trimmed = text.trim()
    if (!trimmed) return
    tokens.push({
      id: `${lineIndex}-${tokens.length}-${trimmed}`,
      lineIndex,
      text: trimmed,
      hint,
      kind: hint,
    })
  }
  push(parts.productText, '商品原文')
  push(parts.salesAttr1Text, '疑似属性1')
  push(parts.salesAttr2Text, '疑似属性2')
  push(parts.quantityText, '数量')
  return tokens.length ? tokens : [{ id: `${lineIndex}-0-${line}`, lineIndex, text: line, hint: '文字', kind: '文字' }]
}

function splitPreviewTokensFromLine(line: string, lineIndex: number): PreviewToken[] {
  if (isDouyinSelectedGroup()) return splitDouyinPreviewTokensFromLine(line, lineIndex)
  const tokens: PreviewToken[] = []
  const push = (text: string, hint = segmentKind(text)) => {
    const trimmed = text.trim()
    if (!trimmed) return
    tokens.push({
      id: `${lineIndex}-${tokens.length}-${trimmed}`,
      lineIndex,
      text: trimmed,
      hint,
      kind: hint,
    })
  }

  valueText(line)
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean)
    .forEach((subline) => {
      const parts = subline
        .replace(/[；;]/g, '，')
        .split(/[，,、]+/)
        .map((part) => part.trim())
        .filter(Boolean)
      const parsedParts = parts.length ? parts : [subline]
      parsedParts.forEach((part) => {
        const quantityTail = part.match(/^(.+?)\s*(?:\*|x|X|×)\s*(\d+)\s*$/)
        const beforeQuantity = (quantityTail?.[1] ?? part).trim()
        const quantity = quantityTail?.[2]?.trim() ?? ''
        const leadingSizeProduct = splitLeadingSizeProduct(beforeQuantity)
        if (leadingSizeProduct) {
          push(leadingSizeProduct.sizeText, '疑似属性2')
          push(leadingSizeProduct.productText)
        } else if (beforeQuantity) {
          push(beforeQuantity)
        }
        if (quantity) push(quantity, '疑似数量')
      })
    })

  return tokens.length ? tokens : [{ id: `${lineIndex}-0-${line}`, lineIndex, text: line, hint: '文字', kind: '文字' }]
}

const waybillPreviewLines = computed<PreviewLine[]>(() =>
  customLines.value.map((line, index) => ({
    id: `${index}-${line}`,
    index,
    tokens: splitPreviewTokensFromLine(line, index),
  })),
)

function templateTitle(group: TemplateGroup | null): string {
  if (!group) return '-'
  return group.platformTemplateName
}

function parseStatusLabel(group: TemplateGroup): string {
  if (group.parseStatus === 'platform_structured') return '平台已解析'
  if (group.savedConfigCount > 1) return `已配置 ${group.savedConfigCount}`
  if (group.savedConfigCount === 1) return '已配置'
  if (group.modeCode === 'douyin_cloud_print') return '系统默认'
  return '待配置'
}

function parseStatusTag(group: TemplateGroup): 'success' | 'warning' | 'info' {
  if (group.parseStatus === 'platform_structured') return 'success'
  if (group.savedConfigCount) return 'success'
  return group.modeCode === 'douyin_cloud_print' ? 'info' : 'warning'
}

function canEditConfig(group: TemplateGroup | null): boolean {
  return group?.parseStatus === 'custom_required'
}

function canDeleteConfig(group: TemplateGroup | null): boolean {
  return Boolean(group && group.parseStatus === 'custom_required' && group.savedConfigCount > 0)
}

function sourceLabel(source: string): string {
  if (source === 'platform_template') return '平台模板'
  if (source === 'douyin_template_url') return '模板地址'
  if (source === 'template_code' || source === 'douyin_template_code') return '模板编码'
  if (source === 'template_url') return '模板地址'
  if (source === 'printxml_layout') return '打印布局'
  if (source === 'legacy_custom_area') return '旧样本'
  return source || '-'
}

function shortTemplateKey(value: string): string {
  const text = valueText(value, '-')
  return text.length > 28 ? `${text.slice(0, 18)}...${text.slice(-8)}` : text
}

function configBoundLayoutLabel(config: PrintTemplateConfigRecord): string {
  const sourceKey = configSourceTemplateKeyValue(config)
  if (!sourceKey) return '-'
  return templateDisplayName(config.waybill_mode, sourceKey)
}

function selectTemplateGroup(row: TemplateGroup) {
  selectedGroupKey.value = row.groupKey
  selectedSampleId.value = null
  selectedConfigId.value = null
  creatingDraft.value = false
}

function startNewConfig(row: TemplateGroup | null = defaultCustomGroup.value) {
  if (!row || row.parseStatus !== 'custom_required') return
  selectedGroupKey.value = row.groupKey
  selectedSampleId.value = null
  selectedConfigId.value = null
  creatingDraft.value = true
  resetForm(null)
}

function editConfig(config: PrintTemplateConfigRecord) {
  const group = customGroups.value.find((item) => item.modeCode === config.waybill_mode) ?? defaultCustomGroup.value
  if (group) selectedGroupKey.value = group.groupKey
  selectedSampleId.value = null
  selectedConfigId.value = config.id
  creatingDraft.value = false
  resetForm(config)
}

function removeSavedConfig(config: PrintTemplateConfigRecord) {
  const group = customGroups.value.find((item) => item.modeCode === config.waybill_mode) ?? defaultCustomGroup.value
  if (group) selectedGroupKey.value = group.groupKey
  selectedConfigId.value = config.id
  void removeConfig(group ?? selectedGroup.value)
}

function setSpecLine(index: number) {
  form.specLineIndex = index
}

function setRemarkLine(index: number) {
  form.remarkLineIndex = index
}

function clearField(code: CustomWodaFieldCode) {
  if (code === 'custom_product_text') {
    form.productSegmentText = ''
    form.productLineIndex = -1
  }
  if (code === 'custom_sales_attr1_text') {
    form.specSegmentText = ''
    form.specLineIndex = -1
  }
  if (code === 'custom_sales_attr2_text') {
    form.sizeSegmentText = ''
    form.sizeLineIndex = -1
  }
  if (code === 'custom_quantity_text') {
    form.quantitySegmentText = ''
    form.quantityLineIndex = -1
  }
  if (code === 'custom_item_remark_text') {
    form.remarkSegmentText = ''
    form.remarkLineIndex = -1
  }
}

function setSegment(role: 'product' | 'spec' | 'size' | 'quantity' | 'remark', token: SegmentToken) {
  const fieldCodeByRole: Record<'product' | 'spec' | 'size' | 'quantity' | 'remark', CustomWodaFieldCode> = {
    product: 'custom_product_text',
    spec: 'custom_sales_attr1_text',
    size: 'custom_sales_attr2_text',
    quantity: 'custom_quantity_text',
    remark: 'custom_item_remark_text',
  }
  setFieldFromToken(fieldCodeByRole[role], { ...token, kind: token.hint })
}

function setFieldFromToken(code: CustomWodaFieldCode, token: PreviewToken) {
  manualFieldAssignments.value = {}
  CUSTOM_WODA_FIELD_ORDER.forEach((fieldCode) => {
    if (fieldCode !== code && tokenMatchesField(token, fieldCode)) {
      clearField(fieldCode)
    }
  })
  if (code === 'custom_product_text') {
    form.productSegmentText = token.text
    form.productLineIndex = token.lineIndex
  }
  if (code === 'custom_sales_attr1_text') {
    form.specSegmentText = token.text
    form.specLineIndex = token.lineIndex
  }
  if (code === 'custom_sales_attr2_text') {
    form.sizeSegmentText = token.text
    form.sizeLineIndex = token.lineIndex
  }
  if (code === 'custom_quantity_text') {
    form.quantitySegmentText = token.text
    form.quantityLineIndex = token.lineIndex
  }
  if (code === 'custom_item_remark_text') {
    form.remarkSegmentText = token.text
    form.remarkLineIndex = token.lineIndex
  }
}

function normalizedTokenText(value: string): string {
  return value.replace(/\s+/g, '').toLowerCase()
}

function selectedSegmentText(code: CustomWodaFieldCode): string {
  if (code === 'custom_product_text') return form.productSegmentText
  if (code === 'custom_sales_attr1_text') return form.specSegmentText
  if (code === 'custom_sales_attr2_text') return form.sizeSegmentText
  if (code === 'custom_quantity_text') return form.quantitySegmentText
  if (code === 'custom_item_remark_text') return form.remarkSegmentText
  return ''
}

function selectedLineIndex(code: CustomWodaFieldCode): number {
  if (code === 'custom_product_text') return form.productLineIndex
  if (code === 'custom_sales_attr1_text') return form.specLineIndex
  if (code === 'custom_sales_attr2_text') return form.sizeLineIndex
  if (code === 'custom_quantity_text') return form.quantityLineIndex
  if (code === 'custom_item_remark_text') return form.remarkLineIndex
  return -1
}

function tokenMatchesField(token: PreviewToken, code: CustomWodaFieldCode): boolean {
  const selectedText = normalizedTokenText(selectedSegmentText(code))
  if (!selectedText || selectedText !== normalizedTokenText(token.text)) return false
  const lineIndex = selectedLineIndex(code)
  return lineIndex < 0 || lineIndex === token.lineIndex
}

function tokenFieldCode(token: PreviewToken): CustomWodaFieldCode | '' {
  return CUSTOM_WODA_FIELD_ORDER.find((code) => tokenMatchesField(token, code)) ?? ''
}

function tokenTitle(token: PreviewToken): string {
  const code = tokenFieldCode(token)
  if (!code) return '点击选择这个文字框对应的字段'
  return `已选择：${CUSTOM_WODA_FIELD_LABELS[code]}`
}

function tokenFieldLabel(token: PreviewToken): string {
  const code = tokenFieldCode(token)
  return code ? CUSTOM_WODA_FIELD_LABELS[code] : ''
}

function tokenClass(token: PreviewToken): string[] {
  const code = tokenFieldCode(token)
  return code ? ['is-assigned', `is-${code}`] : []
}

function isDouyinAssignableToken(token: PreviewToken): boolean {
  return !isDouyinSelectedGroup() || token.kind === '疑似属性1' || token.kind === '疑似属性2'
}

function lineText(index: number): string {
  if (index < 0) return customLines.value.join('\n')
  return customLines.value[index] ?? ''
}

function inferQuantity(text: string): string {
  const match = text.match(/(?:\*|x|X|×)\s*(\d+)|(\d+)\s*件/)
  return match?.[1] || match?.[2] || ''
}

function inferSize(text: string): string {
  const match = text.match(/(?:^|[^\d])([2-4]\d(?:\.5)?|50)(?!\d)/)
  return match?.[1] || ''
}

function stripQuantitySuffixForPreview(text: string): string {
  return text
    .replace(/[，,、\s]*(?:\*|x|X|×)\s*\d+\s*$/g, '')
    .replace(/[，,、；;:\s]+$/g, '')
    .trim()
}

function stripSizeSuffixForPreview(text: string, size: string): string {
  const cleaned = stripQuantitySuffixForPreview(text)
  if (!size) return cleaned
  return cleaned
    .replace(new RegExp(`[，,、\\s]*${size.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*$`), '')
    .replace(/[，,、；;:\s]+$/g, '')
    .trim()
}

function selectedFieldText(code: CustomWodaFieldCode): string {
  const directText = selectedSegmentText(code).trim()
  if (directText) return directText
  if (isDouyinSelectedGroup()) {
    const parts = douyinProductInfoParts(customLines.value[0] ?? '')
    if (code === 'custom_sales_attr1_text') return parts.salesAttr1Text
    if (code === 'custom_sales_attr2_text') return parts.salesAttr2Text
  }
  const lineIndex = selectedLineIndex(code)
  if (lineIndex < 0) return ''
  const text = lineText(lineIndex).trim()
  if (!text) return ''
  if (code === 'custom_product_text') return stripQuantitySuffixForPreview(text)
  if (code === 'custom_sales_attr1_text') return stripSizeSuffixForPreview(text, inferSize(text))
  if (code === 'custom_sales_attr2_text') return inferSize(text)
  if (code === 'custom_quantity_text') return inferQuantity(text)
  return text
}

const hasTemplateFieldSelection = computed(() =>
  CUSTOM_WODA_FIELD_ORDER.some((code) => selectedSegmentText(code).trim() || selectedLineIndex(code) >= 0),
)

const sampleItemPreviewRows = computed<SampleItemPreviewRow[]>(() => {
  if (isDouyinSelectedGroup() && selectedSample.value) {
    return [{
      index: 1,
      productText: douyinAutoFieldText('custom_product_text'),
      salesAttr1Text: selectedFieldText('custom_sales_attr1_text'),
      salesAttr2Text: selectedFieldText('custom_sales_attr2_text'),
      quantityText: douyinAutoFieldText('custom_quantity_text'),
      remarkText: douyinAutoFieldText('custom_item_remark_text'),
    }]
  }
  if (!hasTemplateFieldSelection.value) return []
  return [{
    index: 1,
    productText: selectedFieldText('custom_product_text'),
    salesAttr1Text: selectedFieldText('custom_sales_attr1_text'),
    salesAttr2Text: selectedFieldText('custom_sales_attr2_text'),
    quantityText: selectedFieldText('custom_quantity_text'),
    remarkText: selectedFieldText('custom_item_remark_text'),
  }]
})

const waybillPreviewGroups = computed<PreviewItemGroup[]>(() => {
  return waybillPreviewLines.value.map((line) => ({
    id: line.id,
    label: `原文第 ${line.index + 1} 行`,
    canAssign: true,
    tokens: line.tokens,
  }))
})

function uniqueJoined(values: string[]): string {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))].join('、')
}

function quantitySummary(values: string[]): string {
  const normalized = values.map((value) => value.trim()).filter(Boolean)
  if (!normalized.length) return ''
  const numbers = normalized.map((value) => Number(value)).filter((value) => Number.isFinite(value))
  const joined = uniqueJoined(normalized)
  if (normalized.length <= 1) return joined
  if (numbers.length === normalized.length) return `共 ${numbers.reduce((sum, value) => sum + value, 0)} / ${normalized.length} 个商品`
  return `${joined} / ${normalized.length} 个商品`
}

const samplePreviewByCode = computed<Record<CustomWodaFieldCode, string>>(() => {
  const rows = sampleItemPreviewRows.value
  if (!rows.length) {
    return {
      custom_product_text: '',
      custom_sales_attr1_text: '',
      custom_quantity_text: '',
      custom_sales_attr2_text: '',
      custom_item_remark_text: '',
    }
  }
  return {
    custom_product_text: uniqueJoined(rows.map((row) => row.productText)),
    custom_sales_attr1_text: uniqueJoined(rows.map((row) => row.salesAttr1Text)),
    custom_quantity_text: quantitySummary(rows.map((row) => row.quantityText)),
    custom_sales_attr2_text: uniqueJoined(rows.map((row) => row.salesAttr2Text)),
    custom_item_remark_text: uniqueJoined(rows.map((row) => row.remarkText)),
  }
})

function samplePreviewPayload(): Record<CustomWodaFieldCode, string> {
  return samplePreviewByCode.value
}

function extractorLabel(extractor: string): string {
  if (extractor === 'product_text') return '取商品文字'
  if (extractor === 'sales_attr1_text') return '取销售属性1'
  if (extractor === 'sales_attr2_text') return '取销售属性2'
  if (extractor === 'quantity_text') return '取数量'
  if (extractor === 'remark_text') return '取备注整行'
  return extractor || '-'
}

function lineSourceLabel(index: number, extractor: string): string {
  if (index >= 0 && extractor === 'remark_text') return `第 ${index + 1} 行作为备注兜底`
  if (index >= 0) return `第 ${index + 1} 行整行拆分`
  if (extractor === 'remark_text') return '未设置备注行'
  return '随整行拆分自动识别'
}

const fieldMappingRows = computed(() => {
  const savedMappings = objectValue(activeConfigPayload.value.field_mappings)
  const currentMappings = buildFieldMappingPayload(form, manualFieldAssignments.value)
  const preview = samplePreviewPayload()
  return CUSTOM_WODA_FIELD_ORDER.map((code) => {
    const saved = objectValue(savedMappings[code])
    const fallback = currentMappings[code]
    const lineIndex = Number(saved.line_index ?? fallback.line_index)
    const extractor = valueText(saved.extractor, fallback.extractor)
    const autoPreview = isDouyinSelectedGroup() ? douyinAutoFieldText(code) : ''
    return {
      code,
      label: CUSTOM_WODA_FIELD_LABELS[code],
      sourceLabel: lineSourceLabel(Number.isFinite(lineIndex) ? lineIndex : -1, extractor),
      extractorLabel: extractorLabel(extractor),
      preview: autoPreview || preview[code],
    }
  })
})

const selectedSampleMatchingConfig = computed(() => {
  const group = selectedGroup.value
  const sourceKey = selectedSampleTemplateKey.value
  if (!group || !sourceKey) return null
  const candidates = configsForCustomMode(group.modeCode)
    .filter((config) => configSourceTemplateKeyValue(config) === sourceKey)
  return candidates.find((config) => config.id === activeConfig.value?.id) ?? candidates[0] ?? null
})

const templateMatchRows = computed(() => {
  const payload = activeConfigPayload.value
  const savedMatch = objectValue(payload.template_match)
  const sourceTemplateKey =
    selectedSampleTemplateKey.value
    || valueText(savedMatch.source_template_key)
    || valueText(payload.source_template_key)
    || valueText(activeConfig.value?.template_key)
  const matchedConfig = selectedSampleMatchingConfig.value
  const matchedLabel = matchedConfig
    ? `${matchedConfig.template_label || '未命名模板'}`
    : sourceTemplateKey
      ? selectedGroup.value?.modeCode === 'douyin_cloud_print' ? '系统默认读取规则' : '未配置读取规则'
      : '-'
  const itemCount = sampleItemPreviewRows.value.length
  const defaultMatchLabel = selectedGroup.value?.modeCode === 'douyin_cloud_print'
    ? '抖店模板地址相同'
    : '打印布局指纹相同'
  return [
    { label: '识别依据', value: valueText(savedMatch.match_label, defaultMatchLabel) },
    { label: '版式指纹', value: sourceTemplateKey || '-' },
    { label: '商品项数量', value: itemCount ? `${itemCount} 个商品项` : '-' },
    { label: '读取规则', value: matchedLabel },
  ]
})

function resetForm(saved: PrintTemplateConfigRecord | null) {
  manualFieldAssignments.value = {}
  const config = saved?.config ?? {}
  form.templateLabel = saved?.template_label || ''
  form.productLineIndex = Number(config.product_line_index ?? -1)
  form.specLineIndex = Number(config.spec_line_index ?? -1)
  form.sizeLineIndex = Number(config.sales_attr2_line_index ?? config.size_line_index ?? -1)
  form.quantityLineIndex = Number(config.quantity_line_index ?? -1)
  form.remarkLineIndex = Number(config.remark_line_index ?? -1)
  form.productSegmentText = configSamplePreviewValue(config, 'custom_product_text')
  form.specSegmentText = configSamplePreviewValue(config, 'custom_sales_attr1_text')
  form.sizeSegmentText = configSamplePreviewValue(config, 'custom_sales_attr2_text')
  form.quantitySegmentText = configSamplePreviewValue(config, 'custom_quantity_text')
  form.remarkSegmentText = configSamplePreviewValue(config, 'custom_item_remark_text')
  form.remark = valueText(saved?.remark)
  manualFieldAssignments.value = restoreFieldAssignments(config.field_mappings)
}

function sourceTemplateKeyForSave(existingConfig: PrintTemplateConfigRecord | null): string {
  return selectedSampleTemplateKey.value || valueText(selectedGroup.value?.templateKey) || valueText(existingConfig?.config?.source_template_key)
}

function sampleDetailIdForSave(existingConfig: PrintTemplateConfigRecord | null): number | null {
  if (selectedSample.value) return selectedSample.value.id
  const savedSampleId = existingConfig?.config?.sample_detail_id
  return typeof savedSampleId === 'number' ? savedSampleId : null
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [
      standardDetails,
      captureTaskRecords,
      printTemplateConfigs,
      platformModes,
      platformTemplates,
      platformTemplateFields,
    ] = await Promise.all([
      getRecords('/standard-details?limit=2000'),
      getRecords('/capture-tasks?limit=2000'),
      getRecords('/print-template-configs?limit=2000'),
      getRecords('/waybill-modes?limit=2000'),
      getRecords('/waybill-templates?limit=2000'),
      getRecords('/waybill-template-fields?limit=2000'),
    ])
    details.value = standardDetails as StandardDetailRecord[]
    captureTasks.value = captureTaskRecords as CaptureTaskRecord[]
    configs.value = printTemplateConfigs as PrintTemplateConfigRecord[]
    waybillModes.value = platformModes as WaybillModeRecord[]
    waybillTemplates.value = platformTemplates as WaybillTemplateRecord[]
    waybillTemplateFields.value = platformTemplateFields as WaybillTemplateFieldRecord[]
    ensureSelectedCaptureTask()
    if (selectedConfigId.value && !configs.value.some((item) => item.id === selectedConfigId.value)) {
      selectedConfigId.value = null
    }
    if (!templateGroups.value.length && allTemplateGroups.value[0]) {
      activeComponent.value = allTemplateGroups.value[0].componentCategory
    }
    if (!templateGroups.value.some((group) => group.groupKey === selectedGroupKey.value) && templateGroups.value[0]) {
      selectedGroupKey.value = templateGroups.value[0].groupKey
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '版式与字段规则加载失败'
  } finally {
    loading.value = false
  }
}

function newCustomTemplateKey(group: TemplateGroup): string {
  return `custom:${group.modeCode}:${Date.now()}`
}

function fieldMappingFormForSave() {
  if (!isDouyinSelectedGroup()) return form
  return {
    ...form,
    specLineIndex: form.specLineIndex >= 0 ? form.specLineIndex : 0,
    sizeLineIndex: form.sizeLineIndex >= 0 ? form.sizeLineIndex : 0,
    specSegmentText: form.specSegmentText.trim() || selectedFieldText('custom_sales_attr1_text'),
    sizeSegmentText: form.sizeSegmentText.trim() || selectedFieldText('custom_sales_attr2_text'),
  }
}

async function saveConfig() {
  const group = selectedGroup.value
  if (!group) return
  if (!canEditConfig(group)) {
    error.value = '平台已解析版式不需要自定义读取规则。'
    return
  }
  const templateLabel = form.templateLabel.trim()
  if (!templateLabel) {
    error.value = '规则名称不能为空。'
    return
  }

  saving.value = true
  error.value = ''
  const existingConfig = group.parseStatus === 'custom_required'
    ? creatingDraft.value ? null : activeConfig.value
    : group.savedConfig
  const sourceTemplateKey = group.parseStatus === 'custom_required'
    ? sourceTemplateKeyForSave(existingConfig)
    : group.templateKey
  if (group.parseStatus === 'custom_required' && !sourceTemplateKey) {
    error.value = '请先选择一个已采集面单样本，再保存读取规则。'
    saving.value = false
    return
  }
  const templateKey = existingConfig?.template_key
    ?? (group.parseStatus === 'custom_required' ? newCustomTemplateKey(group) : group.templateKey)
  const payload = {
    waybill_mode: group.modeCode,
    template_key: templateKey,
    template_label: templateLabel,
    template_source: group.parseStatus === 'custom_required'
      ? valueText(selectedValues.value.print_template_source, valueText(existingConfig?.template_source, 'printxml_layout'))
      : group.templateSource,
    parse_status: group.parseStatus,
    config: group.parseStatus === 'custom_required'
      ? {
          rule_type: customRuleTypeForMode(group.modeCode),
          platform_template_key: group.templateKey,
          source_template_key: sourceTemplateKey,
          sample_detail_id: sampleDetailIdForSave(existingConfig),
          item_schema: group.modeCode === 'douyin_cloud_print' ? 'single_product_info_v1' : 'repeated_items_v1',
          sample_item_count: sampleItemPreviewRows.value.length,
          schema_version: 2,
          template_match: {
            match_type: group.modeCode === 'douyin_cloud_print' ? 'douyin_template_url' : 'printxml_layout_fingerprint',
            match_label: group.modeCode === 'douyin_cloud_print' ? '抖店模板地址相同' : '打印布局指纹相同',
            source_template_key: sourceTemplateKey,
          },
          field_mappings: buildFieldMappingPayload(fieldMappingFormForSave(), manualFieldAssignments.value),
          sample_preview: samplePreviewPayload(),
          product_line_index: form.productLineIndex,
          spec_line_index: form.specLineIndex,
          sales_attr2_line_index: form.sizeLineIndex,
          quantity_line_index: form.quantityLineIndex,
          remark_line_index: form.remarkLineIndex,
        }
      : {
          rule_type: 'platform_structured_v1',
        },
    is_enabled: true,
    remark: form.remark.trim(),
  }

  try {
    if (existingConfig) {
      await updateRecord(`/print-template-configs/${existingConfig.id}`, payload)
    } else {
      const created = await createRecord('/print-template-configs', payload)
      selectedConfigId.value = Number(created.id)
    }
    creatingDraft.value = false
    ElMessage.success('读取规则已保存')
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '读取规则保存失败'
  } finally {
    saving.value = false
  }
}

function configToRemove(group: TemplateGroup | null): PrintTemplateConfigRecord | null {
  if (!group || group.parseStatus !== 'custom_required') return null
  if (group.groupKey === selectedGroup.value?.groupKey && activeConfig.value) return activeConfig.value
  return configsForCustomMode(group.modeCode)[0] ?? null
}

async function removeConfig(group: TemplateGroup | null = selectedGroup.value) {
  const removableConfig = configToRemove(group)
  if (!removableConfig) return
  try {
    await ElMessageBox.confirm(
      `确定删除“${removableConfig.template_label || removableConfig.template_key}”这条读取规则吗？`,
      '删除读取规则',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  deletingId.value = removableConfig.id
  error.value = ''
  try {
    await deleteRecord(`/print-template-configs/${removableConfig.id}`)
    creatingDraft.value = false
    ElMessage.success('读取规则已删除')
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '读取规则删除失败'
  } finally {
    deletingId.value = null
  }
}

watch(
  activeComponent,
  () => {
    selectedGroupKey.value = templateGroups.value[0]?.groupKey ?? ''
    selectedSampleId.value = null
    selectedConfigId.value = null
    creatingDraft.value = false
  },
)
watch(
  selectedGroup,
  () => {
    selectedSampleId.value = null
    selectedConfigId.value = null
  },
  { immediate: true },
)
watch(
  activeConfig,
  (config) => {
    if (!creatingDraft.value) resetForm(config)
  },
  { immediate: true },
)
watch(
  selectedCaptureTaskId,
  () => {
    if (selectedSampleId.value && !sampleOptions.value.some((option) => option.id === selectedSampleId.value)) {
      selectedSampleId.value = null
    }
  },
)
watch(
  selectedSampleId,
  () => {
    manualFieldAssignments.value = {}
  },
)
watch(
  () => session.currentWorkspaceId,
  () => {
    selectedGroupKey.value = ''
    selectedCaptureTaskId.value = null
    creatingDraft.value = false
    selectedConfigId.value = null
    void load()
  },
)
onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>版式与字段规则</h1>
      <p>系统自动识别面单版式；默认可直接读取，只有字段拆分不准时再调整读取规则。</p>
    </div>
    <div class="action-row">
      <el-button :icon="Refresh" :loading="loading" plain @click="load">刷新</el-button>
    </div>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="stat-grid">
    <div class="stat-tile">
      <span>已识别版式</span>
      <strong>{{ templateGroups.length }}</strong>
      <small>按组件和版式指纹归组</small>
    </div>
    <div class="stat-tile">
      <span>自定义规则</span>
      <strong>{{ customDefinitionCount }}</strong>
      <small>当前工作区字段读取规则</small>
    </div>
    <div class="stat-tile">
      <span>可用样本</span>
      <strong>{{ wodaSampleCount }}</strong>
      <small>已采集可维护面单</small>
    </div>
  </section>

  <section class="component-tabs">
    <el-radio-group v-model="activeComponent">
      <el-radio-button
        v-for="item in componentTabs"
        :key="item.value"
        :label="item.value"
      >
        {{ item.label }} {{ item.count }}
      </el-radio-button>
    </el-radio-group>
  </section>

  <section class="template-rule-layout">
    <div class="work-surface template-rule-list">
      <div class="section-title-row">
        <div>
          <h2>已识别版式</h2>
          <p class="muted-line">系统自动归组；这里不用手动创建模板。</p>
        </div>
        <el-tag type="info">{{ templateGroups.length }} 个版式组</el-tag>
      </div>
      <el-table
        v-if="templateGroups.length"
        :current-row-key="selectedGroup?.groupKey"
        :data="templateGroups"
        height="300"
        highlight-current-row
        row-key="groupKey"
        stripe
        @row-click="selectTemplateGroup"
      >
        <el-table-column label="版式" min-width="200">
          <template #default="{ row }">
            <strong>{{ templateTitle(row) }}</strong>
            <small class="muted-line">{{ sourceLabel(row.templateSource) }}：{{ shortTemplateKey(row.templateKey) }}</small>
          </template>
        </el-table-column>
        <el-table-column label="样本" width="80" align="center">
          <template #default="{ row }">
            <strong>{{ row.details.length }}</strong>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="parseStatusTag(row)">{{ parseStatusLabel(row) }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="当前组件没有可维护样本。" />

      <div class="section-divider" />

      <div class="section-title-row">
        <div>
          <h2>自定义读取规则</h2>
          <p class="muted-line">可选；默认读取不准时再维护。</p>
        </div>
        <el-button :icon="Plus" plain type="primary" @click="startNewConfig()">
          新增规则
        </el-button>
      </div>
      <el-table
        v-if="definedTemplateConfigs.length"
        :data="definedTemplateConfigs"
        height="560"
        highlight-current-row
        stripe
        @row-click="editConfig"
        >
        <el-table-column label="规则名称" min-width="180">
          <template #default="{ row }">
            <strong>{{ row.template_label || row.template_key }}</strong>
            <small class="muted-line">{{ customModeName(row.waybill_mode) }} / 字段读取规则</small>
          </template>
        </el-table-column>
        <el-table-column label="绑定版式" min-width="170">
          <template #default="{ row }">
            <span>{{ configBoundLayoutLabel(row) }}</span>
            <small class="muted-line">{{ shortTemplateKey(configSourceTemplateKeyValue(row)) }}</small>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag type="success">已启用</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <div class="line-action-buttons">
              <el-button link type="primary" @click.stop="editConfig(row)">编辑</el-button>
              <el-button
                :icon="Delete"
                :loading="deletingId === row.id"
                link
                type="danger"
                @click.stop="removeSavedConfig(row)"
              >
                删除
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="当前没有自定义读取规则，系统会使用默认读取。">
        <el-button :icon="Plus" type="primary" @click="startNewConfig()">新增规则</el-button>
      </el-empty>
    </div>

    <div class="work-surface">
      <template v-if="selectedGroup">
        <div class="template-rule-header">
          <div>
            <h2>{{ templateTitle(selectedGroup) }}</h2>
            <p>{{ selectedGroup.modeName }} / {{ sourceLabel(selectedGroup.templateSource) }}</p>
            <p v-if="activeConfig?.template_label && !creatingDraft" class="muted-text">
              当前自定义规则：{{ activeConfig.template_label }}
            </p>
          </div>
          <div class="action-row">
            <el-tag :type="parseStatusTag(selectedGroup)">{{ parseStatusLabel(selectedGroup) }}</el-tag>
            <el-button
              v-if="canEditConfig(selectedGroup)"
              :icon="Plus"
              plain
              type="primary"
              @click="startNewConfig(selectedGroup)"
            >
              新增规则
            </el-button>
            <el-button
              v-if="activeConfig && !creatingDraft"
              :icon="Delete"
              :loading="deletingId === activeConfig.id"
              plain
              type="danger"
              @click="removeConfig(selectedGroup)"
            >
              删除规则
            </el-button>
          </div>
        </div>

        <el-form label-position="top" class="inline-config-form">
          <el-form-item v-if="canEditConfig(selectedGroup)" label="规则名称">
            <el-input v-model="form.templateLabel" placeholder="例如：抖店常规读取、我打多商品读取" />
          </el-form-item>

          <template v-if="selectedGroup.parseStatus === 'platform_structured'">
            <el-alert
              :closable="false"
              title="这个版式由平台解析，不需要维护字段读取规则。"
              type="success"
              show-icon
            />
            <div class="template-sample-grid">
              <div>
                <h3>平台字段</h3>
                <el-table :data="structuredSamples" height="360" stripe>
                  <el-table-column label="字段" prop="label" width="130" />
                  <el-table-column label="原始字段">
                    <template #default="{ row }">
                      <code>{{ row.rawField }}</code>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
              <div class="template-preview-panel">
                <h3>处理结果</h3>
                <el-result
                  icon="success"
                  title="平台已解析"
                  sub-title="客户后续直接使用这些字段做商品识别和导出表头。"
                />
              </div>
            </div>
          </template>

          <template v-else>
            <el-alert
              :closable="false"
              title="系统已按版式归组；默认可直接读取，需要修正字段时再保存自定义读取规则。"
              type="info"
              show-icon
            />
            <div class="sample-source-bar">
              <span>监听批次</span>
              <el-select
                v-model="selectedCaptureTaskId"
                clearable
                filterable
                placeholder="请选择监听批次"
                style="width: 280px"
              >
                <el-option
                  v-for="(task, index) in sortedCaptureTasks"
                  :key="task.id"
                  :label="captureTaskLabel(task, index)"
                  :value="task.id"
                />
              </el-select>
              <span>样本来源：选中批次已采集面单</span>
              <el-select
                v-model="selectedSampleId"
                clearable
                filterable
                placeholder="请选择样本"
                style="width: 180px"
              >
                <el-option
                  v-for="option in sampleOptions"
                  :key="option.id"
                  :label="option.label"
                  :value="option.id"
                />
              </el-select>
              <span v-if="selectedCaptureTask">
                当前模板 {{ selectedGroupDetails.length }} 张
              </span>
              <span v-if="selectedCaptureTask">
                当前组件 {{ wodaSampleCount }} 张 / 整批 {{ archivedDetails.length }} 张
              </span>
            </div>
            <div class="template-classify-strip">
              <div>
                <span>版式归类</span>
                <strong>
                  第 {{ selectedGroupIndex || '-' }} / {{ templateGroups.length }} 个版式组
                </strong>
              </div>
              <div v-for="row in templateMatchRows" :key="row.label">
                <span>{{ row.label }}</span>
                <strong>{{ row.value }}</strong>
              </div>
            </div>

            <div class="template-definition-grid">
              <div class="sample-reader-panel">
                <div class="section-title-row">
                  <h3>样本面单预览</h3>
                  <el-tag v-if="selectedSample" type="info">{{ selectedSampleLabel }}</el-tag>
                </div>
                <p class="field-pick-hint">
                  多商品模板只用商品项 1 定义字段位置，后续商品项按同一套位置同步读取。
                </p>
                <div v-if="selectedSample" class="waybill-preview-card">
                  <div class="waybill-preview-meta">
                    <span>{{ previewWaybillNo }}</span>
                    <span>第1/8个</span>
                  </div>
                  <div class="waybill-route-code">160-J73-00 060</div>
                  <div class="waybill-barcode-row">
                    <div class="fake-barcode" />
                    <div class="fake-barcode fake-barcode--side" />
                  </div>
                  <div class="waybill-preview-address">
                    <strong>驿 AA389</strong>
                    <p>{{ previewRecipientText }}</p>
                    <p>{{ previewSenderText }}</p>
                  </div>
                  <div class="waybill-token-area">
                    <div v-for="group in waybillPreviewGroups" :key="group.id" class="waybill-item-group">
                      <span class="waybill-item-group__title">{{ group.label }}</span>
                      <div class="waybill-preview-line">
                        <el-popover
                          v-for="token in group.tokens"
                          :key="token.id"
                          placement="top"
                          trigger="click"
                          width="260"
                        >
                          <template #reference>
                            <button
                              class="waybill-token-box"
                              :class="tokenClass(token)"
                              :disabled="!group.canAssign || !isDouyinAssignableToken(token)"
                              type="button"
                              :title="group.canAssign && isDouyinAssignableToken(token) ? tokenTitle(token) : '这个字段由平台字段自动提供，不需要手动定义'"
                            >
                              <span>{{ token.text }}</span>
                              <small v-if="tokenFieldLabel(token)">{{ tokenFieldLabel(token) }}</small>
                            </button>
                          </template>
                          <div class="token-assign-panel">
                            <strong>{{ token.text }}</strong>
                            <span>{{ tokenTitle(token) }}</span>
                            <div class="token-assign-buttons">
                              <el-button
                              v-for="code in assignableFieldOrder"
                                :key="code"
                                :disabled="!group.canAssign"
                                size="small"
                                plain
                                @click="setFieldFromToken(code, token)"
                              >
                                {{ CUSTOM_WODA_FIELD_LABELS[code] }}
                              </el-button>
                            </div>
                          </div>
                        </el-popover>
                      </div>
                    </div>
                  </div>
                  <span class="waybill-checked">已验视</span>
                </div>
                <el-empty v-else description="先选择一个面单样本。" />
              </div>

              <div class="field-pick-panel">
                <div class="section-title-row">
                  <h3>当前已选字段</h3>
                  <span class="muted-line">点击左侧文字框可随时调整</span>
                </div>
                <div class="field-chip-list">
                  <div
                    v-for="row in fieldMappingRows"
                    :key="row.label"
                    class="field-chip-row"
                    :class="{ 'is-empty': !row.preview }"
                  >
                    <span>{{ row.label }}</span>
                    <strong>{{ row.preview || '未选择' }}</strong>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="selectedSample" class="field-result-preview">
              <div class="section-title-row">
                <h3>保存后读取预览</h3>
                <span class="muted-line">同版式面单会按下面商品行读取</span>
              </div>
              <el-table :data="sampleItemPreviewRows" border stripe>
                <el-table-column label="商品" width="70">
                  <template #default="{ row }">
                    <strong>第 {{ row.index }} 个</strong>
                  </template>
                </el-table-column>
                <el-table-column label="商品文字" min-width="160">
                  <template #default="{ row }">
                    <pre class="goods-text">{{ row.productText || '-' }}</pre>
                  </template>
                </el-table-column>
                <el-table-column label="销售属性1" min-width="150">
                  <template #default="{ row }">
                    <pre class="goods-text">{{ row.salesAttr1Text || '-' }}</pre>
                  </template>
                </el-table-column>
                <el-table-column label="销售属性2" width="110">
                  <template #default="{ row }">
                    <pre class="goods-text">{{ row.salesAttr2Text || '-' }}</pre>
                  </template>
                </el-table-column>
                <el-table-column label="数量" width="80">
                  <template #default="{ row }">
                    <strong>{{ row.quantityText || '-' }}</strong>
                  </template>
                </el-table-column>
                <el-table-column label="备注字段" min-width="180">
                  <template #default="{ row }">
                    <pre class="goods-text">{{ row.remarkText || '-' }}</pre>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <el-collapse v-if="selectedSample && segmentTokens.length" class="manual-segment-collapse">
              <el-collapse-item title="高级：手动修正字段片段" name="segments">
                <div class="segment-token-list">
                  <div v-for="token in segmentTokens" :key="token.id" class="segment-token">
                    <div>
                      <strong>{{ token.text }}</strong>
                      <small>{{ token.hint }}</small>
                    </div>
                    <div class="line-action-buttons">
                      <el-button v-if="!isDouyinSelectedGroup()" size="small" plain @click="setSegment('product', token)">
                        商品
                      </el-button>
                      <el-button size="small" plain @click="setSegment('spec', token)">属性1</el-button>
                      <el-button size="small" plain @click="setSegment('size', token)">属性2</el-button>
                      <el-button v-if="!isDouyinSelectedGroup()" size="small" plain @click="setSegment('quantity', token)">
                        数量
                      </el-button>
                      <el-button v-if="!isDouyinSelectedGroup()" size="small" plain @click="setSegment('remark', token)">
                        备注
                      </el-button>
                    </div>
                  </div>
                </div>
              </el-collapse-item>
            </el-collapse>
          </template>

          <el-form-item v-if="canEditConfig(selectedGroup)" label="备注">
            <el-input v-model="form.remark" type="textarea" :rows="2" />
          </el-form-item>

          <div v-if="canEditConfig(selectedGroup)" class="action-row">
            <el-button type="primary" :loading="saving" @click="saveConfig">
              {{ activeConfig && !creatingDraft ? '保存规则' : '新增规则' }}
            </el-button>
            <el-button
              v-if="activeConfig && !creatingDraft"
              :icon="Delete"
              :loading="deletingId === activeConfig.id"
              plain
              type="danger"
              @click="removeConfig(selectedGroup)"
            >
              删除规则
            </el-button>
          </div>
        </el-form>
      </template>
      <el-empty v-else description="请选择一个已识别版式。" />
    </div>
  </section>
</template>
