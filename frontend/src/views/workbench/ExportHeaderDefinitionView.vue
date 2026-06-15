<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, Check, Delete, Plus, Refresh, RefreshLeft } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  fetchImageAssetBlob,
  getCaptureTaskRecognitionPreview,
  getRecords,
  type ApiRecord,
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
  reportColumnPixelWidth,
  saveSavedReportLayouts,
  saveReportLayout,
  type ReportFieldKey,
  type ReportLayout,
  type ReportLayoutColumn,
  type ReportOutputMode,
  type ReportPreviewRow,
  type SavedReportLayout,
  visibleReportColumns,
} from './reportExportLayout'

type StandardDetailRecord = ApiRecord & {
  id: number
  field_values?: Record<string, unknown> | null
}

const session = useSessionStore()
const captureTasks = ref<CaptureTaskRecord[]>([])
const standardDetails = ref<StandardDetailRecord[]>([])
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
const visibleColumns = computed(() => visibleReportColumns(layout.value))
const exceptionCount = computed(
  () =>
    Number(recognitionPreview.value?.summary.product_unmatched ?? 0)
    + Number(recognitionPreview.value?.summary.sku_unmatched ?? 0)
    + Number(recognitionPreview.value?.summary.conflict ?? 0),
)

const detailCounts = computed(() => {
  const counts = new Map<number, number>()
  standardDetails.value.forEach((detail) => {
    const taskId = Number(detail.field_values?.capture_task_id)
    if (!Number.isInteger(taskId) || taskId <= 0) return
    counts.set(taskId, (counts.get(taskId) ?? 0) + 1)
  })
  return counts
})

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
  const count = detailCounts.value.get(task.id) ?? 0
  return `${round}：${formatTaskTime(task.started_at)} ${taskStatusLabel(task.status)} / ${count} 张`
}

function ensureSelectedTask() {
  const taskIds = new Set(sortedTasks.value.map((task) => task.id))
  if (selectedTaskId.value && taskIds.has(selectedTaskId.value)) return

  const taskIdsWithDetails = new Set<number>()
  detailCounts.value.forEach((count, taskId) => {
    if (count > 0) taskIdsWithDetails.add(taskId)
  })
  selectedTaskId.value = sortedTasks.value.find((task) => taskIdsWithDetails.has(task.id))?.id
    ?? sortedTasks.value[0]?.id
    ?? null
}

function fieldDescription(key: ReportFieldKey): string {
  return REPORT_FIELD_DEFINITIONS.find((field) => field.key === key)?.description ?? ''
}

function moveColumn(index: number, direction: -1 | 1) {
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

function resetDefault() {
  layout.value = defaultReportLayout()
  activeSavedLayoutId.value = ''
  ElMessage.success('已恢复默认版式')
}

function selectOutputMode(mode: ReportOutputMode) {
  layout.value = {
    ...layout.value,
    outputMode: mode,
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
    const [tasks, details] = await Promise.all([
      getRecords('/capture-tasks?limit=2000'),
      getRecords('/standard-details?limit=2000'),
    ])
    captureTasks.value = tasks as CaptureTaskRecord[]
    standardDetails.value = details as StandardDetailRecord[]
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
    width: `${layout.value.imageWidth}px`,
    height: `${layout.value.imageHeight}px`,
  }
}

function imageElementStyle() {
  return {
    maxWidth: `${Math.max(layout.value.imageWidth - 8, 24)}px`,
    maxHeight: `${Math.max(layout.value.imageHeight - 8, 24)}px`,
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
onBeforeUnmount(() => revokeSkuImageUrls())
</script>

<template>
  <section class="page-header">
    <div>
      <h1>导出表头</h1>
      <p>先选择一个报货表预设，再按需要调整字段顺序、表头名称、列宽、行高和图片大小。</p>
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
      <h2>字段和参数</h2>
      <span class="muted-line">左侧维护 Excel 字段，右侧维护输出、汇总和尺寸。</span>
    </div>

    <div class="layout-editor-grid">
      <el-table :data="layout.columns" row-key="key" border class="layout-column-table">
        <el-table-column label="启用" width="80" align="center">
          <template #default="{ row }">
            <el-checkbox v-model="row.visible" />
          </template>
        </el-table-column>
        <el-table-column label="顺序" width="110" align="center">
          <template #default="{ $index }">
            <div class="layout-order-buttons">
              <el-button :disabled="$index === 0" :icon="ArrowUp" size="small" @click="moveColumn($index, -1)" />
              <el-button
                :disabled="$index === layout.columns.length - 1"
                :icon="ArrowDown"
                size="small"
                @click="moveColumn($index, 1)"
              />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="业务字段" min-width="150">
          <template #default="{ row }">
            <strong>{{ REPORT_FIELD_DEFINITIONS.find((field) => field.key === row.key)?.label }}</strong>
            <span class="muted-line">{{ fieldDescription(row.key) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Excel 表头名" min-width="180">
          <template #default="{ row }">
            <el-input v-model="row.label" />
          </template>
        </el-table-column>
        <el-table-column label="列宽" width="130" align="center">
          <template #default="{ row }">
            <el-input-number v-model="row.width" :max="60" :min="8" controls-position="right" />
          </template>
        </el-table-column>
      </el-table>

      <div class="layout-size-panel">
        <div class="layout-panel-title-row">
          <h3>导出参数</h3>
          <el-button :icon="RefreshLeft" link type="primary" @click="resetDefault">恢复默认</el-button>
        </div>
        <h4>输出方式</h4>
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
        <p class="muted-text">
          {{ REPORT_OUTPUT_MODE_OPTIONS.find((option) => option.value === layout.outputMode)?.description }}
        </p>
        <el-divider />
        <h4>汇总方式</h4>
        <div class="layout-toggle-card">
          <div>
            <strong>按销售属性1汇总</strong>
            <span>开启后，同商品、同销售属性1/SKU 合并成一行；关闭后按采集到的商品项分行。</span>
          </div>
          <el-switch v-model="layout.stackSalesAttr1" />
        </div>
        <div class="layout-toggle-card">
          <div>
            <strong>销售属性2去重</strong>
            <span>关闭时按数量重复显示并排序；开启后相同销售属性2只显示一次。</span>
          </div>
          <el-switch v-model="layout.stackSalesAttr2" />
        </div>
        <el-divider />
        <h4>整体尺寸</h4>
        <el-form label-position="top">
          <el-form-item label="表头行高">
            <el-input-number v-model="layout.headerRowHeight" :max="80" :min="18" controls-position="right" />
          </el-form-item>
          <el-form-item label="内容行高">
            <el-input-number v-model="layout.rowHeight" :max="180" :min="24" controls-position="right" />
          </el-form-item>
          <el-form-item label="图片宽度">
            <el-input-number v-model="layout.imageWidth" :max="220" :min="32" controls-position="right" />
          </el-form-item>
          <el-form-item label="图片高度">
            <el-input-number v-model="layout.imageHeight" :max="220" :min="32" controls-position="right" />
          </el-form-item>
        </el-form>
        <p class="muted-text">销售属性2默认不去重：尺码会按每件商品重复显示并排序，数量等于尺码个数。</p>
        <p class="muted-text">宽度单位按 Excel 列宽，行高和图片尺寸按像素近似换算。</p>
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
      <span class="muted-line">{{ selectedTask ? taskLabel(selectedTask, sortedTasks.indexOf(selectedTask)) : '未选择批次' }}</span>
    </div>

    <el-alert
      v-if="exceptionCount"
      :closable="false"
      :title="`还有 ${exceptionCount} 条商品识别异常，下载报货 Excel 时会放到“异常明细”表。`"
      type="warning"
    />

    <el-table
      v-if="reportRows.length"
      v-loading="previewLoading"
      :data="reportRows"
      :row-style="{ height: `${layout.rowHeight}px` }"
      row-key="key"
      height="520"
      stripe
    >
      <el-table-column
        v-for="column in visibleColumns"
        :key="column.key"
        :label="column.label"
        :width="reportColumnPixelWidth(column)"
        align="center"
      >
        <template #default="{ row }">
          <div v-if="column.key === 'sku_image'" class="report-image-cell" :style="imageCellStyle()">
            <img
              v-if="skuImageUrl(row)"
              :src="skuImageUrl(row)"
              :alt="row.sales_attr1_text || 'SKU图片'"
              :style="imageElementStyle()"
            />
            <el-tag v-else-if="skuImageLoading(row)" type="info">加载中</el-tag>
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
