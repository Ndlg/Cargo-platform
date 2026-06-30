<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, Right } from '@element-plus/icons-vue'

import {
  getCaptureTaskRecognitionPreview,
  getRecords,
  type CaptureTaskRecord,
  type RecognitionPreviewResponse,
  type RecognitionPreviewRow,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

type ExceptionStatus =
  | 'product_unmatched'
  | 'sku_unmatched'
  | 'image_unmatched'
  | 'conflict'
  | 'pending'
  | 'unmatched'
  | 'special'

const router = useRouter()
const route = useRoute()
const session = useSessionStore()

const SELECTED_TASK_STORAGE_KEY = 'cargo-platform-exceptions-task-id'

const captureTasks = ref<CaptureTaskRecord[]>([])
const selectedTaskId = ref<number | null>(null)
const recognitionPreview = ref<RecognitionPreviewResponse | null>(null)
const loading = ref(false)
const previewLoading = ref(false)
const error = ref('')
const statusFilter = ref<'all' | ExceptionStatus>('all')

const sortedTasks = computed(() => [...captureTasks.value].sort((a, b) => b.id - a.id))
const selectedTask = computed(
  () => sortedTasks.value.find((task) => task.id === selectedTaskId.value) ?? null,
)

const recognitionRows = computed<RecognitionPreviewRow[]>(() => recognitionPreview.value?.rows ?? [])
const recognitionWaybillCount = computed(
  () => recognitionPreview.value?.waybill_count ?? recognitionPreview.value?.detail_count ?? 0,
)
const exceptionRows = computed(() =>
  recognitionRows.value.filter((row) => row.status !== 'matched' && row.status !== 'special'),
)
const specialRows = computed(() => recognitionRows.value.filter((row) => row.status === 'special'))
const visibleExceptionRows = computed(() => {
  if (statusFilter.value === 'all') return exceptionRows.value
  return exceptionRows.value.filter((row) => row.status === statusFilter.value)
})
const exceptionTypes = computed(() => [
  {
    key: 'product_unmatched' as ExceptionStatus,
    label: '商品未命中',
    count: exceptionCountByStatus('product_unmatched'),
    action: '让管理员补商品规则或确认商品库关键词',
  },
  {
    key: 'sku_unmatched' as ExceptionStatus,
    label: 'SKU未命中',
    count: exceptionCountByStatus('sku_unmatched'),
    action: '补 SKU 关键词、SKU 绑定或规格匹配字段',
  },
  {
    key: 'image_unmatched' as ExceptionStatus,
    label: '图片未命中',
    count: exceptionCountByStatus('image_unmatched'),
    action: '补 SKU 图片或图片资产绑定',
  },
  {
    key: 'conflict' as ExceptionStatus,
    label: '冲突',
    count: exceptionCountByStatus('conflict'),
    action: '让管理员检查同时命中的商品规则或 SKU',
  },
  {
    key: 'pending' as ExceptionStatus,
    label: '待处理',
    count: exceptionCountByStatus('pending'),
    action: '回面单解析查看规则包解析结果，必要时更新识别规则包',
  },
  {
    key: 'unmatched' as ExceptionStatus,
    label: '未匹配',
    count: exceptionCountByStatus('unmatched'),
    action: '让管理员复核商品规则覆盖',
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
  return `${round}：${formatTaskTime(task.started_at)} ${taskStatusLabel(task.status)} / #${task.id}`
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

  selectedTaskId.value = sortedTasks.value[0]?.id ?? null
}

function exceptionCountByStatus(status: ExceptionStatus): number {
  return recognitionRows.value.filter((row) => row.status === status).length
}

function statusLabel(status: string): string {
  if (status === 'product_unmatched') return '商品未命中'
  if (status === 'sku_unmatched') return 'SKU未命中'
  if (status === 'image_unmatched') return '图片未命中'
  if (status === 'conflict') return '冲突'
  if (status === 'pending') return '待处理'
  if (status === 'unmatched') return '未匹配'
  if (status === 'special') return '特殊单'
  if (status === 'matched') return '已匹配'
  return status || '-'
}

function statusTag(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'matched') return 'success'
  if (status === 'conflict') return 'danger'
  if (status === 'special') return 'info'
  if (status === 'product_unmatched' || status === 'sku_unmatched' || status === 'image_unmatched') return 'warning'
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

function exceptionAdvice(row: RecognitionPreviewRow): string {
  if (row.status === 'product_unmatched') return '让管理员补商品资产关键词或学习记录'
  if (row.status === 'sku_unmatched') return '补 SKU 关键词、SKU 绑定或规格匹配字段'
  if (row.status === 'image_unmatched') return '补 SKU 图片或图片资产绑定'
  if (row.status === 'conflict') return '检查同时命中的商品资产、SKU 或学习记录'
  if (row.status === 'pending') return '回面单解析查看规则包解析结果'
  if (row.status === 'unmatched') return '让管理员复核商品规则覆盖'
  if (row.status === 'special') return '特殊单不参与商品、SKU、图片匹配'
  return '查看订单行和匹配结果'
}

function repairTarget(row: RecognitionPreviewRow): string {
  if (row.status === 'product_unmatched') return '补商品匹配'
  if (row.status === 'sku_unmatched') return '补 SKU 匹配'
  if (row.status === 'image_unmatched') return '补图片绑定'
  if (row.status === 'conflict') return '处理匹配冲突'
  if (row.status === 'pending') return '查看订单行'
  if (row.status === 'special') return '无需处理'
  return '查看识别结果'
}

function repairQuery(row: RecognitionPreviewRow): Record<string, string> {
  const query: Record<string, string> = {
    from: 'exceptions',
    status: row.status,
    detail_id: String(row.detail_id),
    source_label: row.source_label,
  }

  if (selectedTaskId.value) query.task_id = String(selectedTaskId.value)
  if (row.product_id) query.product_id = String(row.product_id)
  if (row.product_text) query.product_text = row.product_text
  if (row.sales_attr1_text) query.sales_attr1 = row.sales_attr1_text
  if (row.sales_attr2_text) query.sales_attr2 = row.sales_attr2_text
  if (row.quantity_text) query.quantity = row.quantity_text
  if (row.remark_text) query.remark = row.remark_text
  if (row.image_match_text) query.image_match_text = row.image_match_text
  if (row.reason) query.reason = row.reason
  return query
}

function repairRoute(row: RecognitionPreviewRow) {
  if (row.status === 'pending') {
    return {
      path: '/waybill-batches',
      query: selectedTaskId.value ? { task_id: String(selectedTaskId.value) } : {},
    }
  }

  if (
    row.status === 'product_unmatched'
    || row.status === 'sku_unmatched'
    || row.status === 'image_unmatched'
    || row.status === 'conflict'
  ) {
    return {
      path: '/admin/product-matching',
      query: repairQuery(row),
    }
  }

  return {
    path: '/admin/product-matching',
    query: repairQuery(row),
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
    const tasks = await getRecords('/capture-tasks?limit=2000')
    captureTasks.value = tasks as CaptureTaskRecord[]
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
      <p>这里显示会进入导出 Excel“异常面单”表的待处理行，先修这里，再导出报货表。</p>
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
      <strong>{{ recognitionWaybillCount }}</strong>
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
      <small>需要补规则或回解析处理</small>
    </div>
    <div class="stat-tile">
      <span>特殊单</span>
      <strong>{{ specialRows.length }}</strong>
      <small>正常跳过商品匹配</small>
    </div>
  </section>

  <section class="work-surface exception-surface">
    <div class="section-title-row">
      <div>
        <h2>异常列表</h2>
        <p>只显示需要处理的订单行；特殊单已单独计数，不要求补商品或 SKU。</p>
      </div>
      <el-alert
        class="exception-source-alert"
        :closable="false"
        title="规则包未解析、商品未命中、SKU/图片未命中和冲突都会在这里复核；特殊单属于正常跳过。"
        type="info"
      />
    </div>

    <div class="exception-filter-bar">
      <button
        class="exception-filter"
        :class="{ active: statusFilter === 'all' }"
        type="button"
        @click="statusFilter = 'all'"
      >
        <strong>全部异常</strong>
        <span>{{ exceptionRows.length }}</span>
      </button>
      <button
        v-for="type in exceptionTypes"
        :key="type.key"
        class="exception-filter"
        :class="{ active: statusFilter === type.key, empty: type.count === 0 }"
        type="button"
        @click="statusFilter = type.key"
      >
        <strong>{{ type.label }}</strong>
        <span>{{ type.count }}</span>
      </button>
    </div>

    <el-table
      v-if="exceptionRows.length"
      v-loading="previewLoading"
      :data="visibleExceptionRows"
      row-key="candidate_key"
      height="560"
      stripe
      class="exception-table"
    >
      <el-table-column type="expand" width="42">
        <template #default="{ row }">
          <div class="exception-detail-grid">
            <div>
              <span>商品文字</span>
              <strong>{{ valueText(row.product_text, '空') }}</strong>
            </div>
            <div>
              <span>销售属性1</span>
              <strong>{{ valueText(row.sales_attr1_text, '空') }}</strong>
            </div>
            <div>
              <span>销售属性2</span>
              <strong>{{ valueText(row.sales_attr2_text, '空') }}</strong>
            </div>
            <div>
              <span>数量</span>
              <strong>{{ valueText(row.quantity_text, '空') }}</strong>
            </div>
            <div>
              <span>备注</span>
              <strong>{{ valueText(row.remark_text, '空') }}</strong>
            </div>
            <div>
              <span>图片匹配文本</span>
              <strong>{{ valueText(row.image_match_text, '空') }}</strong>
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="面单" width="160">
        <template #default="{ row }">
          <strong>{{ row.source_label }}</strong>
          <div class="muted-line">{{ itemLabel(row) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="异常" width="132">
        <template #default="{ row }">
          <el-tag :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="订单行摘要" min-width="520">
        <template #default="{ row }">
          <div class="order-row-summary">
            <strong>{{ valueText(row.product_text, '未读到商品') }}</strong>
            <span>
              {{ valueText(row.sales_attr1_text, '空') }}
              /
              {{ valueText(row.sales_attr2_text, '空') }}
              /
              数量 {{ valueText(row.quantity_text, '空') }}
            </span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="原因与建议" min-width="320">
        <template #default="{ row }">
          <div class="exception-reason">
            <strong>{{ valueText(row.reason, '未给出原因') }}</strong>
            <span>{{ exceptionAdvice(row) }}</span>
          </div>
        </template>
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
      description="当前批次没有会进入异常面单的待处理行"
    />
  </section>
</template>

<style scoped>
.exception-surface {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.exception-source-alert {
  max-width: 520px;
}

.exception-filter-bar {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  gap: 10px;
}

.exception-filter {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 48px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-fill-color-blank);
  color: var(--el-text-color-regular);
  cursor: pointer;
  text-align: left;
}

.exception-filter.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.exception-filter.empty {
  color: var(--el-text-color-secondary);
}

.exception-filter span {
  font-size: 20px;
  font-weight: 700;
}

.exception-table :deep(.el-table__cell) {
  vertical-align: top;
}

.order-row-summary,
.exception-reason {
  display: flex;
  flex-direction: column;
  gap: 6px;
  line-height: 1.45;
}

.order-row-summary strong,
.exception-reason strong {
  color: var(--el-text-color-primary);
  word-break: break-word;
}

.order-row-summary span,
.exception-reason span {
  color: var(--el-text-color-secondary);
  word-break: break-word;
}

.exception-detail-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(180px, 1fr));
  gap: 12px;
  padding: 8px 24px 16px 24px;
}

.exception-detail-grid div {
  min-height: 72px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
}

.exception-detail-grid span {
  display: block;
  margin-bottom: 6px;
  color: var(--el-text-color-secondary);
}

.exception-detail-grid strong {
  color: var(--el-text-color-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 1100px) {
  .section-title-row {
    align-items: stretch;
    flex-direction: column;
  }

  .exception-source-alert {
    max-width: none;
  }

  .exception-detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
