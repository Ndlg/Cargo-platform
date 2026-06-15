<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Download, Refresh, View } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import {
  downloadCaptureTaskRecognitionReport,
  fetchImageAssetBlob,
  getCaptureTaskRecognitionPreview,
  getRecords,
  saveBlob,
  type ApiRecord,
  type CaptureTaskRecord,
  type RecognitionPreviewResponse,
  type RecognitionPreviewRow,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'
import {
  buildReportRows,
  loadReportLayout,
  reportCellText,
  reportColumnPixelWidth,
  reportLayoutDownloadPayload,
  type ReportFieldKey,
  type ReportLayout,
  type ReportPreviewRow,
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
const reportLayout = ref<ReportLayout>(loadReportLayout(session.currentWorkspaceId))
const loading = ref(false)
const previewLoading = ref(false)
const downloading = ref(false)
const error = ref('')
const skuImageUrls = ref<Record<number, string>>({})
const skuImageLoadingIds = ref<Set<number>>(new Set())

const sortedTasks = computed(() => [...captureTasks.value].sort((a, b) => b.id - a.id))
const selectedTask = computed(
  () => sortedTasks.value.find((task) => task.id === selectedTaskId.value) ?? null,
)
const recognitionRows = computed<RecognitionPreviewRow[]>(() => recognitionPreview.value?.rows ?? [])
const recognitionSummary = computed(() => recognitionPreview.value?.summary ?? {})
const reportRows = computed<ReportPreviewRow[]>(() => buildReportRows(recognitionRows.value, reportLayout.value))
const visibleColumns = computed(() => visibleReportColumns(reportLayout.value))
const exceptionCount = computed(
  () => summaryValue('product_unmatched') + summaryValue('sku_unmatched') + summaryValue('conflict'),
)
const boundImageCount = computed(
  () => reportRows.value.filter((row) => row.sku_image_asset_id).length,
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

function summaryValue(key: string): number {
  return Number(recognitionSummary.value[key] ?? 0)
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
    error.value = err instanceof Error ? err.message : '报货预览加载失败'
  } finally {
    previewLoading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  reportLayout.value = loadReportLayout(session.currentWorkspaceId)
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
    error.value = err instanceof Error ? err.message : '导出中心加载失败'
  } finally {
    loading.value = false
  }
}

async function downloadReport() {
  if (!selectedTaskId.value) {
    error.value = '请先选择监听批次。'
    return
  }
  downloading.value = true
  error.value = ''
  try {
    const { blob, filename } = await downloadCaptureTaskRecognitionReport(
      selectedTaskId.value,
      reportLayoutDownloadPayload(reportLayout.value),
    )
    saveBlob(blob, filename)
    ElMessage.success(reportLayout.value.outputMode === 'stall_workbooks' ? '报货档口文档已生成' : '报货 Excel 已生成')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '报货 Excel 下载失败'
  } finally {
    downloading.value = false
  }
}

function imageCellStyle() {
  return {
    width: `${reportLayout.value.imageWidth}px`,
    height: `${reportLayout.value.imageHeight}px`,
  }
}

function imageElementStyle() {
  return {
    maxWidth: `${Math.max(reportLayout.value.imageWidth - 8, 24)}px`,
    maxHeight: `${Math.max(reportLayout.value.imageHeight - 8, 24)}px`,
  }
}

function previewCellText(row: ReportPreviewRow, key: ReportFieldKey): string | number {
  return reportCellText(row, key)
}

watch(
  () => session.currentWorkspaceId,
  () => {
    reportLayout.value = loadReportLayout(session.currentWorkspaceId)
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
      <h1>导出中心</h1>
      <p>按监听批次生成报货 Excel，表头、列宽、图片尺寸读取“导出表头”里的版式设置。</p>
    </div>
    <div class="header-actions">
      <el-button :icon="Refresh" :loading="loading" plain @click="load">刷新</el-button>
      <el-button
        :disabled="!selectedTaskId"
        :icon="View"
        :loading="previewLoading"
        plain
        @click="loadRecognitionPreview"
      >
        预览
      </el-button>
      <el-button
        :disabled="!selectedTaskId"
        :icon="Download"
        :loading="downloading"
        type="primary"
        @click="downloadReport"
      >
        下载报货文件
      </el-button>
    </div>
  </section>

  <section class="work-surface">
    <div class="capture-control-bar">
      <strong>监听批次</strong>
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

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="stat-grid">
    <div class="stat-tile">
      <span>面单</span>
      <strong>{{ recognitionPreview?.detail_count ?? 0 }}</strong>
      <small>{{ selectedTask ? taskLabel(selectedTask, sortedTasks.indexOf(selectedTask)) : '未选择批次' }}</small>
    </div>
    <div class="stat-tile">
      <span>商品行</span>
      <strong>{{ summaryValue('total') }}</strong>
      <small>一行对应一个商品项</small>
    </div>
    <div class="stat-tile">
      <span>报货行</span>
      <strong>{{ reportRows.length }}</strong>
      <small>SKU图片 {{ boundImageCount }} 张 / {{ reportLayout.outputMode === 'merged_sheet' ? '合并Sheet' : '按档口输出' }}</small>
    </div>
    <div class="stat-tile">
      <span>异常</span>
      <strong>{{ exceptionCount }}</strong>
      <small>商品 / SKU 未命中或冲突</small>
    </div>
  </section>

  <section class="work-surface">
    <div class="section-title-row">
      <h2>报货表预览</h2>
      <span class="muted-line">下载 Excel 的第一张表会按这里的列顺序和尺寸输出。</span>
    </div>

    <el-alert
      v-if="exceptionCount"
      :closable="false"
      :title="`还有 ${exceptionCount} 条商品识别异常，下载的 Excel 会放到“异常明细”表。`"
      type="warning"
    />

    <el-table
      v-if="reportRows.length"
      v-loading="previewLoading"
      :data="reportRows"
      :row-style="{ height: `${reportLayout.rowHeight}px` }"
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
      description="当前批次还没有已匹配的报货行。"
    />
  </section>
</template>
