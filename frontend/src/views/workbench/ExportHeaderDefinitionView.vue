<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight, Check, Delete, Hide, Plus, Refresh, RefreshLeft } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  fetchImageAssetBlob,
  getCaptureTaskRecognitionPreview,
  getRecords,
  type CaptureTaskRecord,
  type RecognitionPreviewResponse,
  type RecognitionPreviewRow,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'
import {
  REPORT_FIELD_DEFINITIONS,
  REPORT_LAYOUT_PRESETS,
  REPORT_OUTPUT_MODE_OPTIONS,
  buildReportRows,
  defaultReportLayout,
  loadReportLayout,
  loadSavedReportLayouts,
  makeSavedReportLayout,
  normalizeReportLayout,
  presetReportLayout,
  reportCellText,
  reportColumnWidthFromPixels,
  reportColumnPixelWidth,
  saveSavedReportLayouts,
  saveReportLayout,
  type ReportFieldKey,
  type ReportLayout,
  type ReportOutputMode,
  type ReportPreviewRow,
  type SavedReportLayout,
  visibleReportColumns,
} from './reportExportLayout'

const session = useSessionStore()
const captureTasks = ref<CaptureTaskRecord[]>([])
const selectedTaskId = ref<number | null>(null)
const recognitionPreview = ref<RecognitionPreviewResponse | null>(null)
const layout = ref<ReportLayout>(loadReportLayout(session.currentWorkspaceId))
const savedLayouts = ref<SavedReportLayout[]>(loadSavedReportLayouts(session.currentWorkspaceId))
const activeSavedLayoutId = ref('')
const loading = ref(false)
const previewLoading = ref(false)
const error = ref('')
const skuImageUrls = ref<Record<number, string>>({})
const skuImageLoadingIds = ref<Set<number>>(new Set())
const styleForm = reactive({
  name: '',
  description: '',
})

const sortedTasks = computed(() => [...captureTasks.value].sort((a, b) => b.id - a.id))
const selectedTask = computed(
  () => sortedTasks.value.find((task) => task.id === selectedTaskId.value) ?? null,
)
const recognitionRows = computed<RecognitionPreviewRow[]>(() => recognitionPreview.value?.rows ?? [])
const reportRows = computed<ReportPreviewRow[]>(() => buildReportRows(recognitionRows.value, layout.value))
const visibleColumns = computed(() => layout.value.columns.filter((column) => column.visible))
const hiddenColumns = computed(() => layout.value.columns.filter((column) => !column.visible))
const exceptionCount = computed(
  () =>
    Number(recognitionPreview.value?.summary.product_unmatched ?? 0)
    + Number(recognitionPreview.value?.summary.sku_unmatched ?? 0)
    + Number(recognitionPreview.value?.summary.conflict ?? 0),
)

const imageResizeState = ref<{
  startX: number
  startY: number
  startSize: number
} | null>(null)
const imageMoveState = ref<{
  startX: number
  startY: number
  startOffsetX: number
  startOffsetY: number
} | null>(null)

function formatTaskTime(value?: string | null): string {
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

function taskStatusLabel(status?: string | null): string {
  if (status === 'collecting') return '采集中'
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '失败'
  return status || '-'
}

function taskLabel(task: CaptureTaskRecord, index = 0): string {
  const round = index <= 0 ? '最近一轮' : `上一轮 ${index}`
  return `${round}：${formatTaskTime(task.started_at)} ${taskStatusLabel(task.status)} / #${task.id}`
}

function ensureSelectedTask() {
  const taskIds = new Set(sortedTasks.value.map((task) => task.id))
  if (selectedTaskId.value && taskIds.has(selectedTaskId.value)) return

  selectedTaskId.value = sortedTasks.value[0]?.id ?? null
}

function fieldDescription(key: ReportFieldKey): string {
  return REPORT_FIELD_DEFINITIONS.find((field) => field.key === key)?.description ?? ''
}

function fieldLabel(key: ReportFieldKey): string {
  return REPORT_FIELD_DEFINITIONS.find((field) => field.key === key)?.label ?? key
}

function columnIndex(key: ReportFieldKey): number {
  return layout.value.columns.findIndex((column) => column.key === key)
}

function moveColumnByKey(key: ReportFieldKey, direction: -1 | 1) {
  const index = columnIndex(key)
  if (index < 0) return
  const nextIndex = index + direction
  if (nextIndex < 0 || nextIndex >= layout.value.columns.length) return
  const nextColumns = [...layout.value.columns]
  const [column] = nextColumns.splice(index, 1)
  nextColumns.splice(nextIndex, 0, column)
  layout.value = {
    ...layout.value,
    presetId: 'custom',
    columns: nextColumns,
  }
}

function updateColumnLabel(key: ReportFieldKey, label: string) {
  const index = columnIndex(key)
  if (index < 0) return
  const nextColumns = [...layout.value.columns]
  nextColumns[index] = {
    ...nextColumns[index],
    label,
  }
  layout.value = {
    ...layout.value,
    presetId: 'custom',
    columns: nextColumns,
  }
}

function setColumnVisible(key: ReportFieldKey, visible: boolean) {
  const index = columnIndex(key)
  if (index < 0) return
  const nextColumns = [...layout.value.columns]
  nextColumns[index] = {
    ...nextColumns[index],
    visible,
  }
  layout.value = {
    ...layout.value,
    presetId: 'custom',
    columns: nextColumns,
  }
}

function updateColumnWidth(key: ReportFieldKey, pixelWidth: number) {
  const index = columnIndex(key)
  if (index < 0) return
  const nextColumns = [...layout.value.columns]
  nextColumns[index] = {
    ...nextColumns[index],
    width: reportColumnWidthFromPixels(pixelWidth),
  }
  const nextLayout: ReportLayout = {
    ...layout.value,
    presetId: 'custom',
    columns: nextColumns,
  }
  if (key === 'sku_image') {
    const imageSize = clampPreviewSize(pixelWidth - 28, 32, 220)
    nextLayout.imageWidth = imageSize
    nextLayout.imageHeight = imageSize
    nextLayout.rowHeight = Math.max(32, imageSize + 16)
    nextLayout.imageOffsetX = clampPreviewSize(nextLayout.imageOffsetX, 0, Math.max(0, pixelWidth - 24 - imageSize))
    nextLayout.imageOffsetY = clampPreviewSize(nextLayout.imageOffsetY, 0, Math.max(0, nextLayout.rowHeight - 14 - imageSize))
  }
  layout.value = nextLayout
}

function resetDefault() {
  layout.value = defaultReportLayout()
  activeSavedLayoutId.value = ''
  ElMessage.success('已恢复默认版式')
}

function selectOutputMode(mode: ReportOutputMode) {
  layout.value = {
    ...layout.value,
    presetId: 'custom',
    outputMode: mode,
  }
}

function setStackSalesAttr1(value: boolean) {
  layout.value = {
    ...layout.value,
    presetId: 'custom',
    stackSalesAttr1: value,
  }
}

function setStackSalesAttr2(value: boolean) {
  layout.value = {
    ...layout.value,
    presetId: 'custom',
    stackSalesAttr2: value,
  }
}

function outputModeControlLabel(mode: ReportOutputMode): string {
  if (mode === 'merged_sheet') return '合并 Sheet'
  if (mode === 'stall_sheet') return '档口 Sheet'
  return '档口文档'
}

function saveCurrentLayout() {
  layout.value = normalizeReportLayout(layout.value)
  saveReportLayout(layout.value, session.currentWorkspaceId)
  ElMessage.success('当前版式已应用到导出')
}

function saveStyles(nextLayouts: SavedReportLayout[]) {
  savedLayouts.value = saveSavedReportLayouts(nextLayouts, session.currentWorkspaceId)
}

function saveAsStyle() {
  const name = styleForm.name.trim()
  if (!name) {
    ElMessage.warning('请输入方案名称')
    return
  }
  const currentLayout = normalizeReportLayout({
    ...layout.value,
    presetId: 'custom',
  })
  const style = makeSavedReportLayout(name, styleForm.description, currentLayout)
  saveStyles([style, ...savedLayouts.value])
  activeSavedLayoutId.value = style.id
  layout.value = style.layout
  saveReportLayout(layout.value, session.currentWorkspaceId)
  ElMessage.success('已保存为新方案并应用到导出')
}

function applySavedStyle(style: SavedReportLayout) {
  layout.value = normalizeReportLayout(style.layout)
  activeSavedLayoutId.value = style.id
  styleForm.name = style.name
  styleForm.description = style.description
  ElMessage.success(`已套用方案“${style.name}”`)
}

function updateSavedStyle(style: SavedReportLayout) {
  const nextLayouts = savedLayouts.value.map((item) => {
    if (item.id !== style.id) return item
    return {
      ...item,
      layout: normalizeReportLayout({
        ...layout.value,
        presetId: 'custom',
      }),
      updatedAt: new Date().toISOString(),
    }
  })
  saveStyles(nextLayouts)
  activeSavedLayoutId.value = style.id
  ElMessage.success(`已更新方案“${style.name}”`)
}

async function removeSavedStyle(style: SavedReportLayout) {
  try {
    await ElMessageBox.confirm(
      `确定删除导出版式方案“${style.name}”吗？`,
      '删除版式方案',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  saveStyles(savedLayouts.value.filter((item) => item.id !== style.id))
  if (activeSavedLayoutId.value === style.id) activeSavedLayoutId.value = ''
  ElMessage.success('版式方案已删除')
}

function applyPreset(presetId: string) {
  const preset = REPORT_LAYOUT_PRESETS.find((item) => item.id === presetId)
  layout.value = presetReportLayout(presetId)
  activeSavedLayoutId.value = ''
  if (preset) {
    styleForm.name = preset.name
    styleForm.description = preset.description
  }
  ElMessage.success('已套用预设，可继续微调后保存')
}

function formatStyleTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function layoutOutputLabel(targetLayout: ReportLayout): string {
  return REPORT_OUTPUT_MODE_OPTIONS.find((option) => option.value === targetLayout.outputMode)?.label ?? '输出方式'
}

function layoutStyleSummary(targetLayout: ReportLayout): string {
  const columnCount = visibleReportColumns(targetLayout).length
  const groupMode = targetLayout.stackSalesAttr1 ? '按销售属性1汇总' : '逐商品项'
  return `${layoutOutputLabel(targetLayout)} / ${groupMode} / ${columnCount} 列`
}

function clampPreviewSize(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min
  return Math.min(Math.max(Math.round(value), min), max)
}

function skuImageColumn() {
  return layout.value.columns.find((column) => column.key === 'sku_image')
}

function imageCellPixelWidth(): number {
  const column = skuImageColumn()
  return Math.max(32, column ? reportColumnPixelWidth(column) - 24 : layout.value.imageWidth)
}

function imageCellPixelHeight(): number {
  return Math.max(32, layout.value.rowHeight - 14)
}

function imageOffsetBounds() {
  return {
    maxX: Math.max(0, imageCellPixelWidth() - layout.value.imageWidth),
    maxY: Math.max(0, imageCellPixelHeight() - layout.value.imageHeight),
  }
}

function applyImageSize(size: number) {
  const imageSize = clampPreviewSize(size, 32, 220)
  const currentImageColumnWidth = skuImageColumn() ? reportColumnPixelWidth(skuImageColumn()!) : imageSize + 28
  const nextImageColumnWidth = Math.max(currentImageColumnWidth, imageSize + 28)
  const nextColumns = layout.value.columns.map((column) => {
    if (column.key !== 'sku_image') return column
    return {
      ...column,
      width: reportColumnWidthFromPixels(nextImageColumnWidth),
    }
  })
  const nextLayout = {
    ...layout.value,
    presetId: 'custom',
    columns: nextColumns,
    imageWidth: imageSize,
    imageHeight: imageSize,
    rowHeight: Math.max(32, imageSize + 16),
  }
  const maxX = Math.max(0, nextImageColumnWidth - 24 - imageSize)
  const maxY = Math.max(0, nextLayout.rowHeight - 14 - imageSize)
  nextLayout.imageOffsetX = clampPreviewSize(nextLayout.imageOffsetX, 0, maxX)
  nextLayout.imageOffsetY = clampPreviewSize(nextLayout.imageOffsetY, 0, maxY)
  layout.value = nextLayout
}

function applyImageOffset(offsetX: number, offsetY: number) {
  const bounds = imageOffsetBounds()
  layout.value = {
    ...layout.value,
    presetId: 'custom',
    imageOffsetX: clampPreviewSize(offsetX, 0, bounds.maxX),
    imageOffsetY: clampPreviewSize(offsetY, 0, bounds.maxY),
  }
}

function startImageMove(event: PointerEvent) {
  event.preventDefault()
  event.stopPropagation()
  imageMoveState.value = {
    startX: event.clientX,
    startY: event.clientY,
    startOffsetX: layout.value.imageOffsetX,
    startOffsetY: layout.value.imageOffsetY,
  }
  window.addEventListener('pointermove', moveImageFromPointer)
  window.addEventListener('pointerup', stopImageMove, { once: true })
}

function moveImageFromPointer(event: PointerEvent) {
  const state = imageMoveState.value
  if (!state) return
  applyImageOffset(
    state.startOffsetX + event.clientX - state.startX,
    state.startOffsetY + event.clientY - state.startY,
  )
}

function stopImageMove() {
  imageMoveState.value = null
  window.removeEventListener('pointermove', moveImageFromPointer)
}

function startImageResize(event: PointerEvent) {
  event.preventDefault()
  event.stopPropagation()
  imageResizeState.value = {
    startX: event.clientX,
    startY: event.clientY,
    startSize: layout.value.imageWidth,
  }
  window.addEventListener('pointermove', resizeImageFromPointer)
  window.addEventListener('pointerup', stopImageResize, { once: true })
}

function resizeImageFromPointer(event: PointerEvent) {
  const state = imageResizeState.value
  if (!state) return
  const delta = Math.max(event.clientX - state.startX, event.clientY - state.startY)
  applyImageSize(state.startSize + delta)
}

function stopImageResize() {
  imageResizeState.value = null
  window.removeEventListener('pointermove', resizeImageFromPointer)
}

function handlePreviewColumnResize(newWidth: number, _oldWidth: number, column: { property?: string }) {
  const key = column.property as ReportFieldKey | undefined
  if (!key) return
  updateColumnWidth(key, newWidth)
}

function skuImageAssetId(row: { sku_image_asset_id?: number | null }): number | null {
  const id = Number(row.sku_image_asset_id)
  return Number.isInteger(id) && id > 0 ? id : null
}

function skuImageUrl(row: { sku_image_asset_id?: number | null }): string {
  const id = skuImageAssetId(row)
  return id ? skuImageUrls.value[id] ?? '' : ''
}

function skuImageLoading(row: { sku_image_asset_id?: number | null }): boolean {
  const id = skuImageAssetId(row)
  return id ? skuImageLoadingIds.value.has(id) : false
}

function revokeSkuImageUrls(keepIds = new Set<number>()) {
  const nextUrls: Record<number, string> = {}
  Object.entries(skuImageUrls.value).forEach(([rawId, url]) => {
    const id = Number(rawId)
    if (keepIds.has(id)) {
      nextUrls[id] = url
      return
    }
    URL.revokeObjectURL(url)
  })
  skuImageUrls.value = nextUrls
}

async function loadSkuImagePreviews() {
  const imageIds = new Set<number>()
  reportRows.value.forEach((row) => {
    const id = skuImageAssetId(row)
    if (id) imageIds.add(id)
  })
  revokeSkuImageUrls(imageIds)

  const missingIds = [...imageIds].filter((id) => !skuImageUrls.value[id])
  if (!missingIds.length) return

  skuImageLoadingIds.value = new Set([...skuImageLoadingIds.value, ...missingIds])
  const loaded = await Promise.all(
    missingIds.map(async (id) => {
      try {
        const blob = await fetchImageAssetBlob(id)
        return { id, url: URL.createObjectURL(blob) }
      } catch {
        return { id, url: '' }
      }
    }),
  )

  const nextUrls = { ...skuImageUrls.value }
  loaded.forEach(({ id, url }) => {
    if (url) nextUrls[id] = url
  })
  skuImageUrls.value = nextUrls
  skuImageLoadingIds.value = new Set([...skuImageLoadingIds.value].filter((id) => !missingIds.includes(id)))
}

async function loadRecognitionPreview() {
  if (!selectedTaskId.value) {
    recognitionPreview.value = null
    return
  }
  previewLoading.value = true
  error.value = ''
  try {
    recognitionPreview.value = await getCaptureTaskRecognitionPreview(selectedTaskId.value)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '报货表预览加载失败'
  } finally {
    previewLoading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  layout.value = loadReportLayout(session.currentWorkspaceId)
  savedLayouts.value = loadSavedReportLayouts(session.currentWorkspaceId)
  try {
    const tasks = await getRecords('/capture-tasks?limit=2000')
    captureTasks.value = tasks as CaptureTaskRecord[]
    ensureSelectedTask()
    await loadRecognitionPreview()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '导出表头加载失败'
  } finally {
    loading.value = false
  }
}

function imageCellStyle() {
  return {
    width: `${imageCellPixelWidth()}px`,
    height: `${imageCellPixelHeight()}px`,
  }
}

function imageElementStyle() {
  return {
    left: `${layout.value.imageOffsetX}px`,
    top: `${layout.value.imageOffsetY}px`,
    width: `${layout.value.imageWidth}px`,
    height: `${layout.value.imageHeight}px`,
  }
}

function previewCellText(row: ReportPreviewRow, key: ReportFieldKey): string | number {
  return reportCellText(row, key)
}

watch(
  () => session.currentWorkspaceId,
  () => {
    layout.value = loadReportLayout(session.currentWorkspaceId)
    savedLayouts.value = loadSavedReportLayouts(session.currentWorkspaceId)
    activeSavedLayoutId.value = ''
    selectedTaskId.value = null
    recognitionPreview.value = null
    void load()
  },
)

watch(selectedTaskId, () => {
  recognitionPreview.value = null
  void loadRecognitionPreview()
})

watch(reportRows, () => {
  void loadSkuImagePreviews()
})

onMounted(load)
onBeforeUnmount(() => {
  revokeSkuImageUrls()
  window.removeEventListener('pointermove', resizeImageFromPointer)
  window.removeEventListener('pointermove', moveImageFromPointer)
})
</script>

<template>
  <section class="page-header">
    <div>
      <h1>导出表头</h1>
      <p>在报货表预览里维护列和图片位置，保存后导出中心直接使用当前版式。</p>
    </div>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="work-surface layout-style-surface">
    <div class="section-title-row">
      <div>
        <h2>版式方案</h2>
        <span class="muted-line">按商品或发货场景保存多个导出版式，切换后再应用到导出。</span>
      </div>
      <div class="header-actions">
        <el-button :icon="Refresh" :loading="loading" plain @click="load">刷新</el-button>
        <el-button :icon="Check" type="primary" @click="saveCurrentLayout">应用到导出</el-button>
      </div>
    </div>

    <div class="layout-style-manager">
      <div class="layout-style-save-panel">
        <h3>保存当前设置</h3>
        <el-input v-model="styleForm.name" placeholder="方案名称，例如：鞋款档口图文版" />
        <el-input
          v-model="styleForm.description"
          :rows="2"
          placeholder="适用商品、档口或场景，可不填"
          type="textarea"
        />
        <el-button :icon="Plus" type="primary" @click="saveAsStyle">保存为新方案</el-button>
      </div>

      <div class="layout-style-library">
        <div class="layout-style-group">
          <h3>系统预设</h3>
          <div class="layout-style-card-grid">
            <button
              v-for="preset in REPORT_LAYOUT_PRESETS"
              :key="preset.id"
              class="layout-style-card"
              :class="{ active: layout.presetId === preset.id && !activeSavedLayoutId }"
              type="button"
              @click="applyPreset(preset.id)"
            >
              <strong>{{ preset.name }}</strong>
              <span>{{ preset.description }}</span>
              <small>{{ layoutStyleSummary(preset.layout) }}</small>
            </button>
          </div>
        </div>

        <div class="layout-style-group">
          <h3>自定义方案</h3>
          <div v-if="savedLayouts.length" class="layout-style-card-grid">
            <article
              v-for="style in savedLayouts"
              :key="style.id"
              class="layout-style-card saved"
              :class="{ active: activeSavedLayoutId === style.id }"
            >
              <strong>{{ style.name }}</strong>
              <span>{{ style.description || '未填写备注' }}</span>
              <small>{{ layoutStyleSummary(style.layout) }} / {{ formatStyleTime(style.updatedAt) }}</small>
              <div class="layout-style-card-actions">
                <el-button link type="primary" @click="applySavedStyle(style)">使用</el-button>
                <el-button link type="primary" @click="updateSavedStyle(style)">更新</el-button>
                <el-button :icon="Delete" link type="danger" @click="removeSavedStyle(style)">删除</el-button>
              </div>
            </article>
          </div>
          <el-empty v-else description="还没有自定义版式方案" />
        </div>
      </div>
    </div>
  </section>

  <section class="work-surface">
    <div class="section-title-row">
      <h2>导出方式</h2>
      <el-button :icon="RefreshLeft" link type="primary" @click="resetDefault">恢复默认</el-button>
    </div>

    <div class="direct-export-options">
      <div class="output-mode-control" role="radiogroup" aria-label="输出方式">
        <button
          v-for="option in REPORT_OUTPUT_MODE_OPTIONS"
          :key="option.value"
          class="output-mode-button"
          :class="{ active: layout.outputMode === option.value }"
          type="button"
          role="radio"
          :aria-checked="layout.outputMode === option.value"
          @click="selectOutputMode(option.value)"
        >
          {{ outputModeControlLabel(option.value) }}
        </button>
      </div>
      <div class="direct-export-switches">
        <el-checkbox :model-value="layout.stackSalesAttr1" @update:model-value="setStackSalesAttr1(Boolean($event))">
          按销售属性1汇总
        </el-checkbox>
        <el-checkbox :model-value="layout.stackSalesAttr2" @update:model-value="setStackSalesAttr2(Boolean($event))">
          销售属性2去重
        </el-checkbox>
      </div>
    </div>
  </section>

  <section class="work-surface">
    <div class="capture-control-bar">
      <strong>预览批次</strong>
      <el-select
        v-model="selectedTaskId"
        class="task-select"
        filterable
        placeholder="选择监听批次"
      >
        <el-option
          v-for="(task, index) in sortedTasks"
          :key="task.id"
          :label="taskLabel(task, index)"
          :value="task.id"
        />
      </el-select>
    </div>
  </section>

  <section class="work-surface export-preview-panel">
    <div class="section-title-row">
      <h2>Excel 预览</h2>
      <div class="header-actions">
        <span class="muted-line">{{ selectedTask ? taskLabel(selectedTask, sortedTasks.indexOf(selectedTask)) : '未选择批次' }}</span>
        <el-button :icon="Check" type="primary" @click="saveCurrentLayout">应用到导出</el-button>
      </div>
    </div>

    <el-alert
      v-if="exceptionCount"
      :closable="false"
      :title="`还有 ${exceptionCount} 条商品匹配异常，下载报货 Excel 时会放到“异常明细”表。`"
      type="warning"
    />

    <div v-if="hiddenColumns.length" class="hidden-column-row">
      <span>隐藏字段</span>
      <el-tooltip
        v-for="column in hiddenColumns"
        :key="column.key"
        :content="fieldDescription(column.key)"
        placement="top"
      >
        <el-button size="small" @click="setColumnVisible(column.key, true)">
          {{ fieldLabel(column.key) }}
        </el-button>
      </el-tooltip>
    </div>

    <el-table
      v-if="reportRows.length"
      v-loading="previewLoading"
      :data="reportRows"
      :fit="false"
      :row-style="{ height: `${layout.rowHeight}px` }"
      allow-drag-last-column
      border
      class="editable-report-table"
      row-key="key"
      height="520"
      stripe
      @header-dragend="handlePreviewColumnResize"
    >
      <el-table-column
        v-for="column in visibleColumns"
        :key="column.key"
        :prop="column.key"
        :resizable="true"
        :width="reportColumnPixelWidth(column)"
        align="center"
      >
        <template #header>
          <div class="editable-report-header">
            <el-input
              :model-value="column.label"
              size="small"
              @update:model-value="updateColumnLabel(column.key, String($event))"
            />
            <div class="editable-report-header-actions">
              <el-tooltip content="左移" placement="top">
                <el-button
                  :disabled="columnIndex(column.key) === 0"
                  :icon="ArrowLeft"
                  circle
                  size="small"
                  @click.stop="moveColumnByKey(column.key, -1)"
                />
              </el-tooltip>
              <el-tooltip content="右移" placement="top">
                <el-button
                  :disabled="columnIndex(column.key) === layout.columns.length - 1"
                  :icon="ArrowRight"
                  circle
                  size="small"
                  @click.stop="moveColumnByKey(column.key, 1)"
                />
              </el-tooltip>
              <el-tooltip content="隐藏" placement="top">
                <el-button
                  :disabled="visibleColumns.length <= 1"
                  :icon="Hide"
                  circle
                  size="small"
                  @click.stop="setColumnVisible(column.key, false)"
                />
              </el-tooltip>
            </div>
          </div>
        </template>
        <template #default="{ row }">
          <div
            v-if="column.key === 'sku_image'"
            class="report-image-cell editable-report-image-cell"
            :style="imageCellStyle()"
          >
            <img
              v-if="skuImageUrl(row)"
              :src="skuImageUrl(row)"
              :alt="row.sales_attr1_text || 'SKU图片'"
              draggable="false"
              :style="imageElementStyle()"
              @pointerdown="startImageMove"
            />
            <el-tag v-else-if="skuImageLoading(row)" type="info">加载中</el-tag>
            <button
              v-if="skuImageUrl(row)"
              aria-label="调整图片大小"
              class="report-image-resize-handle"
              type="button"
              @pointerdown="startImageResize"
            />
          </div>
          <span v-else>{{ previewCellText(row, column.key) }}</span>
        </template>
      </el-table-column>
    </el-table>

    <el-empty
      v-else
      v-loading="previewLoading"
      description="当前批次还没有已匹配的商品 SKU。"
    />
  </section>
</template>
