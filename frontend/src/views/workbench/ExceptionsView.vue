<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, Right } from '@element-plus/icons-vue'

import {
  getCaptureTaskRecognitionPreview,
  getRecords,
  type ApiRecord,
  type CaptureTaskRecord,
  type RecognitionPreviewResponse,
  type RecognitionPreviewRow,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

type StandardDetailRecord = ApiRecord & {
  id: number
  field_values?: Record<string, unknown> | null
}

type ExceptionStatus = 'product_unmatched' | 'sku_unmatched' | 'conflict'

const router = useRouter()
const route = useRoute()
const session = useSessionStore()

const SELECTED_TASK_STORAGE_KEY = 'cargo-platform-exceptions-task-id'

const captureTasks = ref<CaptureTaskRecord[]>([])
const standardDetails = ref<StandardDetailRecord[]>([])
const selectedTaskId = ref<number | null>(null)
const recognitionPreview = ref<RecognitionPreviewResponse | null>(null)
const loading = ref(false)
const previewLoading = ref(false)
const error = ref('')

const sortedTasks = computed(() => [...captureTasks.value].sort((a, b) => b.id - a.id))
const selectedTask = computed(
  () => sortedTasks.value.find((task) => task.id === selectedTaskId.value) ?? null,
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

const recognitionRows = computed<RecognitionPreviewRow[]>(() => recognitionPreview.value?.rows ?? [])
const exceptionRows = computed(() =>
  recognitionRows.value.filter((row) => row.status !== 'matched'),
)
const exceptionTypes = computed(() => [
  {
    key: 'product_unmatched' as ExceptionStatus,
    label: '商品未命中',
    count: exceptionCountByStatus('product_unmatched'),
    action: '去商品识别里补商品主类规则',
  },
  {
    key: 'sku_unmatched' as ExceptionStatus,
    label: 'SKU未命中',
    count: exceptionCountByStatus('sku_unmatched'),
    action: '补 SKU 关键词或 SKU 图片资料',
  },
  {
    key: 'conflict' as ExceptionStatus,
    label: '冲突',
    count: exceptionCountByStatus('conflict'),
    action: '检查多个规则是否命中同一面单',
  },
])

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

function selectedTaskFromSavedState(): number | null {
  const queryValue = Array.isArray(route.query.task_id)
    ? route.query.task_id[0]
    : route.query.task_id
  const rawValue = queryValue ?? localStorage.getItem(SELECTED_TASK_STORAGE_KEY)
  const parsed = Number(rawValue)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function persistSelectedTask(taskId: number | null) {
  const nextQuery = { ...route.query }

  if (!taskId) {
    localStorage.removeItem(SELECTED_TASK_STORAGE_KEY)
    if ('task_id' in nextQuery) {
      delete nextQuery.task_id
      void router.replace({ query: nextQuery })
    }
    return
  }

  localStorage.setItem(SELECTED_TASK_STORAGE_KEY, String(taskId))
  const queryTaskId = Array.isArray(route.query.task_id)
    ? route.query.task_id[0]
    : route.query.task_id
  if (queryTaskId === String(taskId)) return

  void router.replace({
    query: {
      ...nextQuery,
      task_id: String(taskId),
    },
  })
}

function ensureSelectedTask() {
  const taskIds = new Set(sortedTasks.value.map((task) => task.id))
  if (selectedTaskId.value && taskIds.has(selectedTaskId.value)) return

  const savedTaskId = selectedTaskFromSavedState()
  if (savedTaskId && taskIds.has(savedTaskId)) {
    selectedTaskId.value = savedTaskId
    return
  }

  const taskIdsWithDetails = new Set<number>()
  detailCounts.value.forEach((count, taskId) => {
    if (count > 0) taskIdsWithDetails.add(taskId)
  })
  selectedTaskId.value = sortedTasks.value.find((task) => taskIdsWithDetails.has(task.id))?.id
    ?? sortedTasks.value[0]?.id
    ?? null
}

function exceptionCountByStatus(status: ExceptionStatus): number {
  return recognitionRows.value.filter((row) => row.status === status).length
}

function statusLabel(status: string): string {
  if (status === 'product_unmatched') return '商品未命中'
  if (status === 'sku_unmatched') return 'SKU未命中'
  if (status === 'conflict') return '冲突'
  if (status === 'matched') return '已匹配'
  return status || '-'
}

function statusTag(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'matched') return 'success'
  if (status === 'conflict') return 'danger'
  if (status === 'product_unmatched' || status === 'sku_unmatched') return 'warning'
  return 'info'
}

function itemLabel(row: RecognitionPreviewRow): string {
  if (row.item_index && row.item_count > 1) {
    return `第 ${row.item_index}/${row.item_count} 个商品`
  }
  return '单商品'
}

function valueText(value: unknown, fallback = '-'): string {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function repairTarget(row: RecognitionPreviewRow): string {
  if (row.status === 'product_unmatched') return '补商品主类识别规则'
  if (row.status === 'sku_unmatched' && row.product_id) return '补 SKU 图片资料'
  if (row.status === 'sku_unmatched') return '补 SKU 识别规则'
  if (row.status === 'conflict') return '检查重复命中的商品规则'
  return '查看识别结果'
}

function repairRoute(row: RecognitionPreviewRow) {
  if (row.status === 'sku_unmatched' && row.product_id) {
    return {
      path: '/admin/products',
      query: {
        product_id: String(row.product_id),
      },
    }
  }

  return {
    path: '/admin/matching',
    query: selectedTaskId.value ? { task_id: String(selectedTaskId.value) } : undefined,
  }
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
    error.value = err instanceof Error ? err.message : '异常明细加载失败'
  } finally {
    previewLoading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [tasks, details] = await Promise.all([
      getRecords('/capture-tasks?limit=2000'),
      getRecords('/standard-details?limit=2000'),
    ])
    captureTasks.value = tasks as CaptureTaskRecord[]
    standardDetails.value = details as StandardDetailRecord[]
    const previousTaskId = selectedTaskId.value
    ensureSelectedTask()
    if (selectedTaskId.value === previousTaskId) {
      await loadRecognitionPreview()
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '异常处理页面加载失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => session.currentWorkspaceId,
  () => {
    selectedTaskId.value = null
    recognitionPreview.value = null
    void load()
  },
)

watch(selectedTaskId, () => {
  persistSelectedTask(selectedTaskId.value)
  recognitionPreview.value = null
  void loadRecognitionPreview()
})

onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>异常处理</h1>
      <p>这里显示会进入导出 Excel“异常明细”表的商品识别异常，先修这里，再导出报货表。</p>
    </div>
    <div class="header-actions">
      <el-button :icon="Refresh" :loading="loading" plain @click="load">刷新</el-button>
      <el-button :icon="Right" type="primary" @click="router.push('/exports')">
        下一步：导出中心
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
      <span class="muted-line">会保留当前选择；刷新后仍查看这一轮。</span>
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
      <strong>{{ recognitionPreview?.summary.total ?? 0 }}</strong>
      <small>一个商品项对应一行</small>
    </div>
    <div class="stat-tile">
      <span>可导出</span>
      <strong>{{ recognitionPreview?.summary.matched ?? 0 }}</strong>
      <small>已经匹配商品和 SKU</small>
    </div>
    <div class="stat-tile">
      <span>异常</span>
      <strong>{{ exceptionRows.length }}</strong>
      <small>会写入 Excel 异常明细</small>
    </div>
  </section>

  <section class="workflow-grid">
    <div class="work-surface">
      <div class="section-title-row">
        <h2>异常列表</h2>
        <span class="muted-line">和导出 Excel 的“异常明细”同源</span>
      </div>

      <el-table
        v-if="exceptionRows.length"
        v-loading="previewLoading"
        :data="exceptionRows"
        row-key="candidate_key"
        height="520"
        stripe
      >
        <el-table-column label="面单" min-width="150">
          <template #default="{ row }">
            <strong>{{ row.source_label }}</strong>
            <div class="muted-line">{{ itemLabel(row) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="异常" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="商品文字" min-width="170">
          <template #default="{ row }">{{ valueText(row.product_text) }}</template>
        </el-table-column>
        <el-table-column label="销售属性1" min-width="170">
          <template #default="{ row }">{{ valueText(row.sales_attr1_text) }}</template>
        </el-table-column>
        <el-table-column label="销售属性2" width="120">
          <template #default="{ row }">{{ valueText(row.sales_attr2_text) }}</template>
        </el-table-column>
        <el-table-column label="数量" width="90">
          <template #default="{ row }">{{ valueText(row.quantity_text) }}</template>
        </el-table-column>
        <el-table-column label="原因" min-width="220">
          <template #default="{ row }">{{ valueText(row.reason) }}</template>
        </el-table-column>
        <el-table-column label="处理" width="160" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="router.push(repairRoute(row))">
              {{ repairTarget(row) }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-else
        v-loading="previewLoading"
        description="当前批次没有会进入导出异常明细的商品识别异常"
      />
    </div>

    <div class="work-surface">
      <h2>异常类型</h2>
      <div class="tag-list">
        <el-tag
          v-for="type in exceptionTypes"
          :key="type.key"
          :type="type.count ? 'warning' : 'info'"
          size="large"
        >
          {{ type.label }}：{{ type.count }}
        </el-tag>
      </div>
      <el-divider />
      <div class="info-list">
        <p v-for="type in exceptionTypes" :key="type.key">
          <strong>{{ type.label }}</strong>
          <span>{{ type.action }}</span>
        </p>
      </div>
      <el-alert
        :closable="false"
        title="面单模板无法识别、导出配置缺失这类异常暂时没有独立落库；目前以商品识别预览作为导出异常来源。"
        type="info"
      />
    </div>
  </section>
</template>
