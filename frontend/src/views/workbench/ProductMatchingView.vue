<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Check, Delete, Edit, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  deleteProductMatchingRule,
  getProductMatchingRules,
  getRecord,
  getRecords,
  saveProductMatchingRule,
  updateProductMatchingRule,
  type ApiRecord,
  type CaptureTaskRecord,
  type ProductMatchingRuleRecord,
  type ProductMatchingScope,
  type StandardWaybillFieldCode,
} from '../../services/api'

type ProductRecord = ApiRecord & {
  id: number
  name: string
  is_enabled?: boolean
}

type ProductSkuRecord = ApiRecord & {
  id: number
  product_id: number
  name: string
  image_asset_id?: number | null
  is_enabled?: boolean
}

type ImageAssetRecord = ApiRecord & {
  id: number
  name: string
  file_path?: string | null
}

const productFieldOptions: Array<{
  code: StandardWaybillFieldCode
  label: string
  description: string
  discouraged?: boolean
}> = [
  {
    code: 'product',
    label: '商品文字',
    description: '面单解析后的商品文字。',
  },
  {
    code: 'sales_attr1',
    label: '销售属性1',
    description: '颜色、系列、款式等第一属性。',
  },
  {
    code: 'sales_attr2',
    label: '销售属性2',
    description: '尺码、第二规格或补充属性。',
  },
  {
    code: 'remark',
    label: '备注字段',
    description: '适合把商品关键词打印在备注区的模板。',
  },
  {
    code: 'quantity',
    label: '数量',
    description: '通常不用于识别商品主类，仅在特殊模板中辅助判断。',
    discouraged: true,
  },
]

const skuFieldOptions: Array<{
  code: StandardWaybillFieldCode
  label: string
  description: string
}> = [
  {
    code: 'sales_attr1',
    label: '销售属性1',
    description: '常用作 SKU 款式、颜色或图片匹配字段。',
  },
  {
    code: 'sales_attr2',
    label: '销售属性2',
    description: '适合尺码、第二规格或模板里的补充 SKU 字段。',
  },
  {
    code: 'remark',
    label: '备注字段',
    description: '适合把 SKU 关键字打印在备注区的模板。',
  },
  {
    code: 'product',
    label: '商品文字',
    description: '当 SKU 信息和商品文字混在同一字段时可选。',
  },
]

const fieldLabels: Record<StandardWaybillFieldCode, string> = {
  product: '商品文字',
  sales_attr1: '销售属性1',
  sales_attr2: '销售属性2',
  quantity: '数量',
  remark: '备注字段',
}

const fieldCodes: StandardWaybillFieldCode[] = [
  'product',
  'sales_attr1',
  'sales_attr2',
  'quantity',
  'remark',
]

const route = useRoute()

const captureTasks = ref<CaptureTaskRecord[]>([])
const products = ref<ProductRecord[]>([])
const skus = ref<ProductSkuRecord[]>([])
const images = ref<ImageAssetRecord[]>([])
const rules = ref<ProductMatchingRuleRecord[]>([])
const loading = ref(false)
const saving = ref(false)
const productLoading = ref(false)
const skuLoading = ref(false)
const imageLoading = ref(false)
const imagesLoaded = ref(false)
const error = ref('')
const selectedTaskId = ref<number | null>(null)
const editingRuleId = ref<number | null>(null)
const skuSection = ref<HTMLElement | null>(null)
const draftSourceSamples = ref<Partial<Record<StandardWaybillFieldCode, string>>[] | null>(null)
const ruleSearch = ref('')
const ruleStatusFilter = ref<'all' | 'enabled' | 'disabled'>('all')
const ruleProductFilter = ref<number | 'all'>('all')
const ruleFieldFilter = ref<StandardWaybillFieldCode | 'all'>('all')
const productSearchKeyword = ref('')
const skuSearchKeyword = ref('')
const imageSearchKeyword = ref('')
let productLoadSeq = 0
let skuLoadSeq = 0
let imageLoadSeq = 0

const form = reactive({
  name: '',
  product_id: null as number | null,
  product_match_fields: ['product'] as StandardWaybillFieldCode[],
  product_keyword: '',
  product_match_type: 'contains' as 'contains' | 'exact',
  sku_match_fields: [] as StandardWaybillFieldCode[],
  sku_id: null as number | null,
  image_asset_id: null as number | null,
  revision_note: '',
})

const exceptionStatusLabels: Record<string, string> = {
  product_unmatched: '商品未命中',
  sku_unmatched: 'SKU 未命中',
  sku_ambiguous: 'SKU 多候选',
  image_unmatched: '图片未命中',
  conflict: '冲突',
  special: '特殊单',
  pending: '待处理',
  unmatched: '未匹配',
}

const selectedProduct = computed(
  () => products.value.find((product) => product.id === form.product_id) ?? null,
)
const sortedCaptureTasks = computed(() =>
  [...captureTasks.value].sort((left, right) => Number(right.id ?? 0) - Number(left.id ?? 0)),
)
const selectedProductSkus = computed(() => (form.product_id ? skus.value : []))
const editorModeText = computed(() => (editingRuleId.value ? '修订匹配记录' : '创建匹配记录'))
const enabledRuleCount = computed(() => rules.value.filter((rule) => rule.is_enabled).length)
const ruleProductOptions = computed(() => {
  const ids = new Set<number>()
  for (const rule of rules.value) {
    if (rule.product_id) ids.add(rule.product_id)
  }
  return [...ids]
    .map((productId) => ({
      id: productId,
      name: productName(productId),
    }))
    .sort((left, right) => left.name.localeCompare(right.name, 'zh-Hans-CN'))
})
const filteredRules = computed(() => {
  const keyword = normalizeRuleSearch(ruleSearch.value)
  return rules.value.filter((rule) => {
    if (ruleStatusFilter.value === 'enabled' && !rule.is_enabled) return false
    if (ruleStatusFilter.value === 'disabled' && rule.is_enabled) return false
    if (ruleProductFilter.value !== 'all' && rule.product_id !== ruleProductFilter.value) return false
    if (
      ruleFieldFilter.value !== 'all'
      && ![...(rule.product_match_fields ?? []), ...(rule.sku_match_fields ?? [])].includes(ruleFieldFilter.value)
    ) {
      return false
    }
    if (!keyword) return true
    return normalizeRuleSearch(ruleSearchText(rule)).includes(keyword)
  })
})
const ruleFilterSummary = computed(() => {
  const total = rules.value.length
  const disabled = rules.value.filter((rule) => !rule.is_enabled).length
  return `共 ${total} 条，启用 ${enabledRuleCount.value} 条，停用 ${disabled} 条，当前显示 ${filteredRules.value.length} 条`
})
const inboundFromExceptions = computed(() => queryText(route.query.from) === 'exceptions')
const inboundStatusLabel = computed(() => exceptionStatusLabels[queryText(route.query.status)] ?? '异常订单行')
const inboundSourceLabel = computed(() => queryText(route.query.source_label))
const inboundDetailLabel = computed(() => {
  const detailId = queryText(route.query.detail_id) || queryText(route.query.detail_ids)
  return detailId ? '已选择的面单商品' : '已选择的五字段结果'
})
const inboundOrderLabel = computed(() => inboundSourceLabel.value || inboundDetailLabel.value)
const inboundReasonText = computed(() => queryText(route.query.reason))
const inboundRowSummary = computed(() => {
  const row = normalizeInboundPreviewRow({
    product: queryText(route.query.product_text),
    sales_attr1: queryText(route.query.sales_attr1),
    sales_attr2: queryText(route.query.sales_attr2),
    quantity: queryText(route.query.quantity),
    remark: queryText(route.query.remark),
  })
  return fieldCodes
    .map((fieldCode) => `${fieldLabels[fieldCode]}：${row[fieldCode] || '空'}`)
    .join(' / ')
})
const inboundActionText = computed(() => {
  const status = queryText(route.query.status)
  if (status === 'product_unmatched') {
    return '选择正确商品，勾选用于识别商品的字段，填写关键词后保存学习记录。'
  }
  if (status === 'sku_unmatched') {
    return '在已选商品下选择或绑定 SKU，并确认 SKU 匹配字段。'
  }
  if (status === 'sku_ambiguous') {
    return '多个 SKU 都可能命中，请选择正确 SKU；如果候选不对，修订 SKU 关键词或 SKU 资产。'
  }
  if (status === 'image_unmatched') {
    return '给商品或 SKU 绑定图片，或选择这条学习记录要使用的图片。'
  }
  if (status === 'conflict') {
    return '如果命中多条学习记录，请停用或修订多余规则；如果只有一条记录，请修订这条记录的商品、SKU 或图片绑定。'
  }
  return '根据这行五字段补齐商品、SKU 或图片学习记录。'
})
const inboundAlertTitle = computed(() => {
  return `正在处理：${inboundOrderLabel.value} · ${inboundStatusLabel.value}`
})

function queryText(value: unknown): string {
  if (Array.isArray(value)) return value[0] === undefined || value[0] === null ? '' : String(value[0])
  if (value === undefined || value === null) return ''
  return String(value)
}

function queryPositiveInt(value: unknown): number | null {
  const parsed = Number(queryText(value))
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function ruleCode(ruleId?: number | null): string {
  if (!ruleId) return '本次记录'
  return `学习记录 ${ruleId}`
}

function productName(productId?: number | null): string {
  if (!productId) return '未选择商品'
  return products.value.find((product) => product.id === productId)?.name ?? '未命名商品'
}

function fieldSummary(fields?: StandardWaybillFieldCode[]): string {
  if (!fields?.length) return '-'
  return fields.map((fieldCode) => fieldLabels[fieldCode] ?? fieldCode).join('、')
}

function normalizeRuleSearch(value: string): string {
  return value.trim().toLocaleLowerCase()
}

function ruleSearchText(rule: ProductMatchingRuleRecord): string {
  return [
    ruleCode(rule.id),
    rule.name,
    productName(rule.product_id),
    rule.product_keyword,
    rule.product_match_type === 'exact' ? '完全相同' : '包含关键词',
    fieldSummary(rule.product_match_fields),
    fieldSummary(rule.sku_match_fields),
    rule.revision_note,
    rule.is_enabled ? '启用' : '停用',
  ].filter(Boolean).join(' ')
}

function ruleMatchTypeText(rule: ProductMatchingRuleRecord): string {
  return rule.product_match_type === 'exact' ? '完全相同' : '包含关键词'
}

function clearRuleFilters() {
  ruleSearch.value = ''
  ruleStatusFilter.value = 'all'
  ruleProductFilter.value = 'all'
  ruleFieldFilter.value = 'all'
}

function applyInboundQueryContext() {
  const taskId = queryPositiveInt(route.query.task_id)

  if (taskId) selectedTaskId.value = taskId

  const productId = queryPositiveInt(route.query.product_id)
  if (productId) form.product_id = productId

  if (inboundFromExceptions.value) {
    const inboundRow = normalizeInboundPreviewRow({
      product: queryText(route.query.product_text),
      sales_attr1: queryText(route.query.sales_attr1),
      sales_attr2: queryText(route.query.sales_attr2),
      quantity: queryText(route.query.quantity),
      remark: queryText(route.query.remark),
    })
    if (Object.values(inboundRow).some((value) => Boolean(value))) {
      draftSourceSamples.value = [inboundRow]
    }
  }
}

function applyInboundRuleContext() {
  const ruleId = queryPositiveInt(route.query.rule_id)
  if (!ruleId) return
  const rule = rules.value.find((item) => item.id === ruleId)
  if (rule) editRule(rule)
}

async function focusInboundSection() {
  if (queryText(route.query.focus) !== 'sku') return
  await nextTick()
  skuSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function normalizeInboundPreviewRow(
  row: Partial<Record<StandardWaybillFieldCode, string>>,
): Partial<Record<StandardWaybillFieldCode, string>> {
  return Object.fromEntries(
    fieldCodes.map((fieldCode) => [fieldCode, String(row[fieldCode] ?? '').trim()]),
  ) as Partial<Record<StandardWaybillFieldCode, string>>
}

function buildGlobalRuleScope(): ProductMatchingScope {
  return {
    scope_type: 'current_batch',
    task_id: selectedTaskId.value,
    confirmed_by_user: true,
    preview_impact_count: null,
  }
}

function draftRule() {
  if (!form.product_id) return null
  return {
    name: form.name.trim() || null,
    scope: buildGlobalRuleScope(),
    product_id: form.product_id,
    product_match_fields: form.product_match_fields,
    product_keyword: form.product_keyword.trim(),
    product_match_type: form.product_match_type,
    sku_match_fields: form.sku_match_fields,
    sku_id: form.sku_id || null,
    image_asset_id: form.image_asset_id || null,
    source_samples: draftSourceSamples.value ?? [],
    field_sources: Object.fromEntries(fieldCodes.map((fieldCode) => [fieldCode, `面单解析后的五字段.${fieldCode}`])),
    preview_summary: {},
    revision_note: form.revision_note.trim() || null,
    is_enabled: true,
  }
}

function requireDraft(): ReturnType<typeof draftRule> {
  const rule = draftRule()
  if (!rule) {
    error.value = '请先选择商品主类。'
    return null
  }
  if (!rule.product_match_fields.length) {
    error.value = '请选择用于识别商品的字段。'
    return null
  }
  if (!rule.product_keyword) {
    error.value = '请填写商品匹配关键词。'
    return null
  }
  return rule
}

function isProductFieldChecked(fieldCode: StandardWaybillFieldCode): boolean {
  return form.product_match_fields.includes(fieldCode)
}

function toggleProductField(fieldCode: StandardWaybillFieldCode) {
  if (isProductFieldChecked(fieldCode)) {
    form.product_match_fields = form.product_match_fields.filter((item) => item !== fieldCode)
    return
  }
  form.product_match_fields = [...form.product_match_fields, fieldCode]
}

function isSkuFieldChecked(fieldCode: StandardWaybillFieldCode): boolean {
  return form.sku_match_fields.includes(fieldCode)
}

function toggleSkuField(fieldCode: StandardWaybillFieldCode) {
  if (isSkuFieldChecked(fieldCode)) {
    form.sku_match_fields = form.sku_match_fields.filter((item) => item !== fieldCode)
    return
  }
  form.sku_match_fields = [...form.sku_match_fields, fieldCode]
}

function requiredProductIds(): number[] {
  const ids = new Set<number>()
  if (form.product_id) ids.add(form.product_id)
  for (const rule of rules.value) {
    if (rule.product_id) ids.add(rule.product_id)
  }
  return [...ids]
}

async function rowsWithRequiredProducts(rows: ProductRecord[], productIds: number[]): Promise<ProductRecord[]> {
  const seen = new Set(rows.map((product) => product.id))
  const result = [...rows]
  for (const productId of productIds) {
    if (seen.has(productId)) continue
    try {
      const product = await getRecord(`/products/${productId}`) as ProductRecord
      if (product.id && product.is_enabled !== false && !seen.has(product.id)) {
        seen.add(product.id)
        result.unshift(product)
      }
    } catch {
      // Missing or disabled product assets remain visible as 商品 #ID in summaries.
    }
  }
  return result
}

async function loadProducts(keyword = productSearchKeyword.value) {
  const seq = ++productLoadSeq
  productSearchKeyword.value = keyword
  productLoading.value = true
  try {
    const params = new URLSearchParams({ limit: '50' })
    const cleanKeyword = keyword.trim()
    if (cleanKeyword) params.set('q', cleanKeyword)
    const productRecords = await getRecords(`/products?${params.toString()}`)
    if (seq !== productLoadSeq) return
    const rows = (productRecords as ProductRecord[]).filter((product) => product.is_enabled !== false)
    products.value = await rowsWithRequiredProducts(rows, requiredProductIds())
    if (!form.product_id && products.value[0] && !inboundFromExceptions.value) {
      form.product_id = products.value[0].id
    }
  } catch (err) {
    if (seq !== productLoadSeq) return
    error.value = err instanceof Error ? err.message : '商品列表加载失败'
  } finally {
    if (seq === productLoadSeq) productLoading.value = false
  }
}

function searchProducts(keyword: string) {
  void loadProducts(keyword)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [taskRecords, ruleResponse] = await Promise.all([
      getRecords('/capture-tasks?limit=2000&include_waybill_counts=false'),
      getProductMatchingRules(true),
    ])
    captureTasks.value = taskRecords as CaptureTaskRecord[]
    rules.value = ruleResponse.rules
    applyInboundRuleContext()
    applyInboundQueryContext()
    await loadProducts(productSearchKeyword.value)
    if (!selectedTaskId.value && sortedCaptureTasks.value[0]) selectedTaskId.value = sortedCaptureTasks.value[0].id
    if (form.product_id) await loadSelectedProductSkus(form.product_id)
    await focusInboundSection()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '商品匹配配置加载失败'
  } finally {
    loading.value = false
  }
}

async function loadSelectedProductSkus(productId: number | null) {
  const seq = ++skuLoadSeq
  if (!productId) {
    skus.value = []
    form.sku_id = null
    return
  }
  skuLoading.value = true
  try {
    const params = new URLSearchParams({
      limit: '50',
      product_id: String(productId),
    })
    const keyword = skuSearchKeyword.value.trim()
    if (keyword) params.set('q', keyword)
    const skuRecords = await getRecords(`/product-skus?${params.toString()}`)
    if (seq !== skuLoadSeq) return
    const rows = (skuRecords as ProductSkuRecord[]).filter((sku) => sku.is_enabled !== false)
    skus.value = await rowsWithSelectedSku(rows, productId)
    if (form.sku_id && !skus.value.some((sku) => sku.id === form.sku_id)) {
      form.sku_id = null
    }
  } catch (err) {
    if (seq !== skuLoadSeq) return
    error.value = err instanceof Error ? err.message : 'SKU 列表加载失败'
  } finally {
    if (seq === skuLoadSeq) skuLoading.value = false
  }
}

async function rowsWithSelectedSku(rows: ProductSkuRecord[], productId: number): Promise<ProductSkuRecord[]> {
  if (!form.sku_id || rows.some((sku) => sku.id === form.sku_id)) return rows
  try {
    const selectedSku = await getRecord(`/product-skus/${form.sku_id}`) as ProductSkuRecord
    if (
      selectedSku.product_id === productId
      && selectedSku.is_enabled !== false
      && !rows.some((sku) => sku.id === selectedSku.id)
    ) {
      return [selectedSku, ...rows]
    }
  } catch {
    return rows
  }
  return rows
}

function searchSelectedProductSkus(keyword: string) {
  skuSearchKeyword.value = keyword
  void loadSelectedProductSkus(form.product_id)
}

async function loadImageAssets(keyword = imageSearchKeyword.value) {
  const seq = ++imageLoadSeq
  imageSearchKeyword.value = keyword
  imageLoading.value = true
  try {
    const params = new URLSearchParams({ limit: '50' })
    const cleanKeyword = keyword.trim()
    if (cleanKeyword) params.set('q', cleanKeyword)
    const imageRecords = await getRecords(`/image-assets?${params.toString()}`) as ImageAssetRecord[]
    if (seq !== imageLoadSeq) return
    images.value = await rowsWithSelectedImage(imageRecords)
    imagesLoaded.value = !cleanKeyword
  } catch (err) {
    if (seq !== imageLoadSeq) return
    error.value = err instanceof Error ? err.message : '图片列表加载失败'
  } finally {
    if (seq === imageLoadSeq) imageLoading.value = false
  }
}

async function ensureImagesLoaded() {
  if (imageLoading.value) return
  await loadImageAssets(imageSearchKeyword.value)
}

async function rowsWithSelectedImage(rows: ImageAssetRecord[]): Promise<ImageAssetRecord[]> {
  if (!form.image_asset_id || rows.some((image) => image.id === form.image_asset_id)) return rows
  try {
    const selectedImage = await getRecord(`/image-assets/${form.image_asset_id}`) as ImageAssetRecord
    if (selectedImage.id && !rows.some((image) => image.id === selectedImage.id)) {
      return [selectedImage, ...rows]
    }
  } catch {
    return rows
  }
  return rows
}

function searchImageAssets(keyword: string) {
  imageSearchKeyword.value = keyword
  void loadImageAssets(keyword)
}

async function saveRule() {
  const rule = requireDraft()
  if (!rule) return
  saving.value = true
  error.value = ''
  try {
    const wasEditing = Boolean(editingRuleId.value)
    const response = editingRuleId.value
      ? await updateProductMatchingRule(editingRuleId.value, rule)
      : await saveProductMatchingRule(rule)
    rules.value = [response.rule, ...rules.value.filter((item) => item.id !== response.rule.id)]
    editingRuleId.value = response.rule.id
    draftSourceSamples.value = null
    void loadProducts(productSearchKeyword.value)
    ElMessage.success(wasEditing ? '商品匹配学习记录已修订' : '商品匹配学习记录已保存')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '学习记录保存失败'
  } finally {
    saving.value = false
  }
}

function resetEditor() {
  editingRuleId.value = null
  form.name = ''
  form.product_id = products.value[0]?.id ?? null
  form.product_match_fields = ['product']
  form.product_keyword = ''
  form.product_match_type = 'contains'
  form.sku_match_fields = ['sales_attr1']
  form.sku_id = null
  form.image_asset_id = null
  form.revision_note = ''
  skuSearchKeyword.value = ''
  imageSearchKeyword.value = ''
  draftSourceSamples.value = null
}

function editRule(rule: ProductMatchingRuleRecord) {
  editingRuleId.value = rule.id
  draftSourceSamples.value = null
  form.name = rule.name ?? ''
  form.product_id = rule.product_id
  form.product_match_fields = [...(rule.product_match_fields ?? ['product'])]
  form.product_keyword = rule.product_keyword
  form.product_match_type = rule.product_match_type
  form.sku_match_fields = [...(rule.sku_match_fields ?? ['sales_attr1'])]
  form.sku_id = rule.sku_id ?? null
  form.image_asset_id = rule.image_asset_id ?? null
  form.revision_note = rule.revision_note ?? ''
  skuSearchKeyword.value = ''
  imageSearchKeyword.value = ''
  void loadProducts(productSearchKeyword.value)
  void loadSelectedProductSkus(form.product_id)
  if (form.image_asset_id) void ensureImagesLoaded()
  ElMessage.info(`正在修订 ${ruleCode(rule.id)}`)
}

async function toggleRule(rule: ProductMatchingRuleRecord, enabled: boolean) {
  error.value = ''
  try {
    const response = await updateProductMatchingRule(rule.id, {
      is_enabled: enabled,
      revision_note: enabled ? '重新启用匹配记录' : '停用匹配记录',
    })
    rules.value = rules.value.map((item) => (item.id === rule.id ? response.rule : item))
  } catch (err) {
    error.value = err instanceof Error ? err.message : '规则状态更新失败'
  }
}

async function deleteRule(rule: ProductMatchingRuleRecord) {
  try {
    await ElMessageBox.confirm(
      `确认删除 ${ruleCode(rule.id)}？删除后这条商品匹配学习记录不会再命中后续五字段。`,
      '删除商品匹配学习记录',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  error.value = ''
  try {
    await deleteProductMatchingRule(rule.id)
    rules.value = rules.value.filter((item) => item.id !== rule.id)
    if (editingRuleId.value === rule.id) resetEditor()
    ElMessage.success('商品匹配学习记录已删除')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '学习记录删除失败'
  }
}

watch(
  () => form.product_id,
  (productId, previousProductId) => {
    if (productId !== previousProductId) {
      skus.value = []
      skuSearchKeyword.value = ''
    }
    void loadSelectedProductSkus(productId)
  },
)

watch(
  () => route.query,
  async () => {
    applyInboundRuleContext()
    applyInboundQueryContext()
    await focusInboundSection()
  },
)

onMounted(load)
</script>

<template>
  <main class="product-matching-page" v-loading="loading">
    <section class="page-header">
      <div>
        <p class="eyebrow">商品匹配模块</p>
        <h1>商品 / SKU / 图片匹配</h1>
        <p>只消费已解析订单行，配置商品、SKU 和图片如何命中。</p>
      </div>
      <el-button :icon="Refresh" @click="load">刷新</el-button>
    </section>

    <el-alert v-if="error" class="page-alert" type="error" :closable="false" :title="error" />
    <el-alert
      v-if="inboundFromExceptions"
      class="page-alert"
      type="warning"
      :closable="false"
      show-icon
    >
      <template #title>
        {{ inboundAlertTitle }}
      </template>
      <template #default>
        <div class="inbound-alert-body">
          <div>
            <strong>当前行：</strong>
            <span>{{ inboundRowSummary }}</span>
          </div>
          <div v-if="inboundReasonText">
            <strong>拦截原因：</strong>
            <span>{{ inboundReasonText }}</span>
          </div>
          <div>
            <strong>下一步：</strong>
            <span>{{ inboundActionText }}</span>
          </div>
        </div>
      </template>
    </el-alert>

    <section class="layout-grid">
      <div class="rule-editor">
        <section class="form-section">
          <div class="section-title-row">
            <span class="mode-pill">{{ editorModeText }}</span>
            <el-button v-if="editingRuleId" link @click="resetEditor">新建规则</el-button>
          </div>
          <h2>1. 选择商品主类</h2>
          <p>选择这条学习记录要归到哪个商品主类。</p>
          <el-select
            v-model="form.product_id"
            class="full-control"
            filterable
            remote
            reserve-keyword
            :loading="productLoading"
            :remote-method="searchProducts"
            placeholder="输入商品名搜索要学习到的商品"
            @visible-change="(visible: boolean) => { if (visible) void loadProducts() }"
          >
            <el-option
              v-for="product in products"
              :key="product.id"
              :label="product.name"
              :value="product.id"
            />
          </el-select>
        </section>

        <section class="form-section">
          <h2>2. 选择用于匹配商品的字段</h2>
          <p>系统看到这些字段里出现关键词时，会把这行订单归到上面的商品主类。</p>
          <div class="field-card-list">
            <button
              v-for="option in productFieldOptions"
              :key="option.code"
              class="field-card"
              :class="{ selected: isProductFieldChecked(option.code), discouraged: option.discouraged }"
              type="button"
              @click="toggleProductField(option.code)"
            >
              <el-checkbox :model-value="isProductFieldChecked(option.code)" @click.stop @change="toggleProductField(option.code)" />
              <span>
                <strong>{{ option.label }}</strong>
                <small>{{ option.description }}</small>
                <em v-if="option.discouraged">不建议用于商品主类识别</em>
              </span>
            </button>
          </div>
        </section>

        <section class="form-section">
          <h2>3. 填写商品匹配关键词</h2>
          <p>这是用户确认后的学习记录，不是系统硬编码的业务猜测。</p>
          <el-input
            v-model="form.product_keyword"
            class="full-control"
            placeholder="输入选中字段里能匹配商品主类的关键词"
            clearable
          />
          <el-radio-group v-model="form.product_match_type" class="match-type">
            <el-radio-button label="contains">包含关键词</el-radio-button>
            <el-radio-button label="exact">完全相同</el-radio-button>
          </el-radio-group>
        </section>

        <section ref="skuSection" class="form-section sku-section">
          <h2>4. 关联 SKU（可选）</h2>
          <p>不选具体 SKU 时，会按下方 SKU 匹配字段自动匹配商品下的 SKU。</p>
          <el-select
            v-model="form.sku_id"
            class="full-control"
            clearable
            filterable
            remote
            reserve-keyword
            :loading="skuLoading"
            :remote-method="searchSelectedProductSkus"
            placeholder="不选则按销售属性自动匹配 SKU"
          >
            <el-option
              v-for="sku in selectedProductSkus"
              :key="sku.id"
              :label="sku.name"
              :value="sku.id"
            />
          </el-select>
          <el-select
            v-model="form.image_asset_id"
            class="full-control stacked-control"
            clearable
            filterable
            remote
            reserve-keyword
            :loading="imageLoading"
            :remote-method="searchImageAssets"
            placeholder="可选指定图片；不选则使用 SKU 图片"
            @visible-change="(visible: boolean) => { if (visible) void loadImageAssets() }"
          >
            <el-option
              v-for="image in images"
              :key="image.id"
              :label="image.name"
              :value="image.id"
            />
          </el-select>
        </section>

        <section class="form-section">
          <h2>5. SKU 匹配字段</h2>
          <p>这些字段用于后续 SKU、图片、尺码或颜色匹配。</p>
          <div class="field-card-list compact">
            <button
              v-for="option in skuFieldOptions"
              :key="option.code"
              class="field-card"
              :class="{ selected: isSkuFieldChecked(option.code) }"
              type="button"
              @click="toggleSkuField(option.code)"
            >
              <el-checkbox :model-value="isSkuFieldChecked(option.code)" @click.stop @change="toggleSkuField(option.code)" />
              <span>
                <strong>{{ option.label }}</strong>
                <small>{{ option.description }}</small>
              </span>
            </button>
          </div>
        </section>

        <section class="form-section">
          <h2>6. 保存学习记录</h2>
          <p>保存后可复用到后续订单行，命中前仍以五字段内容和商品资产为准。</p>
          <el-input v-model="form.name" class="full-control" :placeholder="selectedProduct ? `${selectedProduct.name} 匹配学习记录` : '商品匹配学习记录名称'" />
          <el-input v-model="form.revision_note" class="full-control stacked-control" placeholder="修订说明，例如：用户确认鞋类关键词" clearable />
        </section>

        <div class="editor-sticky-actions">
          <el-button :icon="Check" :loading="saving" type="primary" @click="saveRule">
            {{ editingRuleId ? '保存修订' : '保存学习记录' }}
          </el-button>
        </div>
      </div>

      <aside class="side-column">
        <section class="rules-panel">
          <div class="panel-heading">
            <div>
              <h2>商品匹配学习记录</h2>
              <p>搜索商品、关键词或字段，快速找到要修订的规则。</p>
            </div>
            <el-tag size="small" type="info">{{ ruleFilterSummary }}</el-tag>
          </div>
          <div class="rule-filter-bar">
            <el-input
              v-model="ruleSearch"
              class="rule-search"
              clearable
              placeholder="搜索规则名、商品、关键词、字段"
            />
            <el-select v-model="ruleStatusFilter" class="rule-filter-control" placeholder="状态">
              <el-option label="全部状态" value="all" />
              <el-option label="只看启用" value="enabled" />
              <el-option label="只看停用" value="disabled" />
            </el-select>
            <el-select v-model="ruleProductFilter" class="rule-filter-control" placeholder="商品">
              <el-option label="全部商品" value="all" />
              <el-option
                v-for="product in ruleProductOptions"
                :key="product.id"
                :label="product.name"
                :value="product.id"
              />
            </el-select>
            <el-select v-model="ruleFieldFilter" class="rule-filter-control" placeholder="字段">
              <el-option label="全部字段" value="all" />
              <el-option
                v-for="fieldCode in fieldCodes"
                :key="fieldCode"
                :label="fieldLabels[fieldCode]"
                :value="fieldCode"
              />
            </el-select>
            <el-button @click="clearRuleFilters">清空筛选</el-button>
          </div>
          <el-empty v-if="!filteredRules.length" description="没有符合筛选条件的学习记录" />
          <el-table v-else :data="filteredRules" size="small" border height="360" class="rules-table">
            <el-table-column label="规则" min-width="190">
              <template #default="{ row }">
                <div class="rule-name">{{ row.name || ruleCode(row.id) }}</div>
                <div class="muted">{{ ruleCode(row.id) }} · 版本 {{ row.revision }}</div>
                <el-tag size="small" :type="row.is_enabled ? 'success' : 'info'">
                  {{ row.is_enabled ? '启用中' : '已停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="匹配商品" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">
                <strong>{{ productName(row.product_id) }}</strong>
              </template>
            </el-table-column>
            <el-table-column label="命中条件" min-width="260">
              <template #default="{ row }">
                <div class="rule-condition">
                  <span class="condition-keyword">{{ row.product_keyword || '-' }}</span>
                  <el-tag size="small" effect="plain">{{ ruleMatchTypeText(row) }}</el-tag>
                </div>
                <div class="muted">商品字段：{{ fieldSummary(row.product_match_fields) }}</div>
                <div class="muted">SKU 字段：{{ fieldSummary(row.sku_match_fields) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="82">
              <template #default="{ row }">
                <el-switch
                  :model-value="row.is_enabled"
                  @change="(value: string | number | boolean) => toggleRule(row, Boolean(value))"
                />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="128" fixed="right">
              <template #default="{ row }">
                <el-button :icon="Edit" link type="primary" @click="editRule(row)">修订</el-button>
                <el-button :icon="Delete" link type="danger" @click="deleteRule(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </section>
      </aside>
    </section>
  </main>
</template>

<style scoped>
.product-matching-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: calc(100vh - 96px);
  overflow: visible;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.page-header h1 {
  margin: 4px 0;
  font-size: 24px;
  line-height: 1.25;
}

.page-header p,
.form-section p,
.panel-heading p {
  margin: 0;
  color: #5f6775;
}

.eyebrow {
  margin: 0;
  color: #2563eb;
  font-size: 13px;
  font-weight: 700;
}

.page-alert {
  margin: 0;
}

.inbound-alert-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  line-height: 1.55;
}

.inbound-alert-body strong {
  color: #1f2937;
}

.inbound-alert-body span {
  word-break: break-word;
}

.layout-grid {
  display: grid;
  grid-template-columns: minmax(420px, 0.82fr) minmax(560px, 1.18fr);
  gap: 16px;
  align-items: flex-start;
  flex: none;
  min-height: auto;
}

.rule-editor,
.rules-panel {
  border: 1px solid #d8dee8;
  border-radius: 8px;
  background: #fff;
}

.rule-editor {
  min-height: 0;
  overflow: visible;
  padding: 16px 18px;
}

.rules-panel {
  padding: 16px;
}

.side-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 0;
  overflow: visible;
  padding-right: 4px;
}

.form-section {
  padding: 0 0 16px;
  border-bottom: 1px solid #e5eaf1;
}

.form-section + .form-section {
  padding-top: 16px;
}

.form-section:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.form-section h2,
.panel-heading h2 {
  margin: 0 0 8px;
  font-size: 16px;
  line-height: 1.35;
}

.section-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.mode-pill {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 0 10px;
  border: 1px solid #bfdbfe;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 700;
}

.full-control,
.field-card-list,
.match-type {
  margin-top: 12px;
  width: 100%;
}

.stacked-control {
  margin-top: 10px;
}

.field-card-list {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
}

.field-card-list.compact {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.field-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  min-height: 62px;
  padding: 10px;
  border: 1px solid #d8dee8;
  border-radius: 8px;
  background: #fff;
  color: #1f2937;
  text-align: left;
  cursor: pointer;
}

.field-card.selected {
  border-color: #3b82f6;
  background: #eff6ff;
}

.field-card.discouraged {
  background: #fbfcfe;
}

.field-card span,
.field-card strong,
.field-card small,
.field-card em {
  display: block;
}

.field-card strong {
  font-size: 14px;
  line-height: 1.4;
}

.field-card small {
  margin-top: 4px;
  color: #667085;
  line-height: 1.45;
}

.field-card em {
  margin-top: 6px;
  color: #b45309;
  font-size: 12px;
  font-style: normal;
}

.action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.editor-sticky-actions {
  position: sticky;
  bottom: 0;
  z-index: 3;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 -18px;
  padding: 12px 18px 14px;
  border-top: 1px solid #e5eaf1;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.82), #fff 26%),
    #fff;
  box-shadow: 0 -10px 20px rgba(15, 23, 42, 0.06);
}

.panel-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  position: sticky;
  top: 0;
  z-index: 2;
  margin: -16px -16px 0;
  padding: 16px 16px 10px;
  border-bottom: 1px solid #eef2f7;
  background: #fff;
}

.panel-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.rule-filter-bar {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) repeat(3, minmax(116px, 0.45fr)) auto;
  gap: 8px;
  align-items: center;
  margin: 12px 0;
}

.rule-search,
.rule-filter-control {
  width: 100%;
}

.rules-table {
  width: 100%;
}

.rule-condition {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.condition-keyword {
  min-width: 0;
  max-width: 180px;
  overflow: hidden;
  color: #111827;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.muted {
  color: #667085;
  font-size: 12px;
}

.rule-name {
  font-weight: 700;
}

@media (max-width: 1200px) {
  .product-matching-page {
    max-height: none;
    min-height: 0;
    overflow: visible;
  }

  .layout-grid,
  .field-card-list.compact {
    grid-template-columns: 1fr;
  }

  .rule-filter-bar {
    grid-template-columns: 1fr;
  }

  .layout-grid,
  .rule-editor,
  .side-column {
    max-height: none;
    overflow: visible;
  }

  .editor-sticky-actions,
  .panel-heading {
    position: static;
  }
}
</style>
