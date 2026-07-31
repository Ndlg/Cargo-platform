<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, Right } from '@element-plus/icons-vue'

import {
  getCaptureTaskRecognitionPreview,
  getOrderRowDrafts,
  getRecords,
  type CaptureTaskRecord,
  type RecognitionPreviewResponse,
  type RecognitionPreviewRow,
  type OrderRowDraftsResponse,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'
import {
  parserIssueFor,
  parserIssueRoute,
  type ParserIssueDefinition,
} from './parserIssues'

type ExceptionStatus =
  | 'product_unmatched'
  | 'sku_unmatched'
  | 'sku_ambiguous'
  | 'image_unmatched'
  | 'conflict'
  | 'pending'
  | 'unmatched'
  | 'special'

type RepairTarget = 'product-matching' | 'product-assets' | 'ai-recognition' | null

type ExceptionDefinition = {
  label: string
  advice: string
  actionLabel: string
  target: RepairTarget
}

const exceptionDefinitions: Record<ExceptionStatus, ExceptionDefinition> = {
  product_unmatched: {
    label: '商品未命中',
    advice: '补商品关键词或商品匹配规则',
    actionLabel: '补商品规则',
    target: 'product-matching',
  },
  sku_unmatched: {
    label: 'SKU未命中',
    advice: '维护当前商品的 SKU 关键词、绑定和规格字段',
    actionLabel: '维护 SKU',
    target: 'product-assets',
  },
  sku_ambiguous: {
    label: 'SKU多候选',
    advice: '为当前商品行指定可复用的 SKU 匹配规则',
    actionLabel: '指定 SKU 匹配',
    target: 'product-matching',
  },
  image_unmatched: {
    label: '图片未命中',
    advice: '为当前商品或 SKU 补图片',
    actionLabel: '补 SKU 图片',
    target: 'product-assets',
  },
  conflict: {
    label: '冲突',
    advice: '检查并修订同时命中的匹配规则',
    actionLabel: '检查冲突规则',
    target: 'product-matching',
  },
  pending: {
    label: '解析待处理',
    advice: '在 AI 面单解析中确认或修复当前格式',
    actionLabel: '查看 AI 解析',
    target: 'ai-recognition',
  },
  unmatched: {
    label: '未匹配',
    advice: '补当前商品行的商品匹配规则',
    actionLabel: '补商品规则',
    target: 'product-matching',
  },
  special: {
    label: '特殊单',
    advice: '特殊单无需处理',
    actionLabel: '',
    target: null,
  },
}

const exceptionFilterStatuses: ExceptionStatus[] = [
  'product_unmatched',
  'sku_unmatched',
  'sku_ambiguous',
  'image_unmatched',
  'conflict',
  'pending',
  'unmatched',
]

const router = useRouter()
const route = useRoute()
const session = useSessionStore()

const SELECTED_TASK_STORAGE_KEY = 'cargo-platform-exceptions-task-id'

const captureTasks = ref<CaptureTaskRecord[]>([])
const selectedTaskId = ref<number | null>(null)
const recognitionPreview = ref<RecognitionPreviewResponse | null>(null)
const orderDrafts = ref<OrderRowDraftsResponse | null>(null)
const loading = ref(false)
const previewLoading = ref(false)
const error = ref('')
const statusFilter = ref<'all' | ExceptionStatus>('all')
const parserStatus = computed(() => orderDrafts.value?.status ?? '')
const aiSessionUrl = computed(() => {
  const sessionId = orderDrafts.value?.ai_sessions?.find((item) => item.session_id)?.session_id ?? ''
  return /^[A-Za-z0-9_-]{1,128}$/.test(sessionId)
    ? `/ai-recognition-console.html?session=${encodeURIComponent(sessionId)}`
    : ''
})

const sortedTasks = computed(() => [...captureTasks.value].sort((a, b) => b.id - a.id))
const selectedTask = computed(
  () => sortedTasks.value.find((task) => task.id === selectedTaskId.value) ?? null,
)

const recognitionRows = computed<RecognitionPreviewRow[]>(() => recognitionPreview.value?.rows ?? [])
const recognitionWaybillCount = computed(
  () => Math.max(
    recognitionPreview.value?.waybill_count ?? recognitionPreview.value?.detail_count ?? 0,
    orderDrafts.value?.summary.parent_waybill_count ?? 0,
  ),
)
const parseExceptionCount = computed(() => {
  if (!orderDrafts.value) return 0
  const resolvedParents = orderDrafts.value.parents.filter((parent) => parent.rows.length).length
  const unresolvedParents = Math.max(0, orderDrafts.value.summary.parent_waybill_count - resolvedParents)
  const representedParents = recognitionRows.value.filter((row) => row.status === 'pending').length
  return Math.max(0, unresolvedParents - representedParents)
})
const parseIssue = computed<ParserIssueDefinition | null>(() => {
  return parserIssueFor(
    parserStatus.value,
    orderDrafts.value?.message,
    parseExceptionCount.value > 0,
  )
})
const exceptionRows = computed(() =>
  recognitionRows.value.filter((row) => row.status !== 'matched' && row.status !== 'special'),
)
const specialRows = computed(() => recognitionRows.value.filter((row) => row.status === 'special'))
const totalExceptionCount = computed(() => exceptionRows.value.length + parseExceptionCount.value)
const showParseException = computed(
  () => parseExceptionCount.value > 0 && (statusFilter.value === 'all' || statusFilter.value === 'pending'),
)
const visibleExceptionRows = computed(() => {
  if (statusFilter.value === 'all') return exceptionRows.value
  return exceptionRows.value.filter((row) => row.status === statusFilter.value)
})
const exceptionTypes = computed(() => exceptionFilterStatuses.map((status) => ({
  key: status,
  label: exceptionDefinitions[status].label,
  count: exceptionCountByStatus(status),
  action: exceptionDefinitions[status].advice,
})))

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
  return `${round}：${formatTaskTime(task.started_at)} ${taskStatusLabel(task.status)}`
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
  const count = recognitionRows.value.filter((row) => row.status === status).length
  return status === 'pending' ? count + parseExceptionCount.value : count
}

function statusLabel(status: string): string {
  if (status === 'matched') return '已匹配'
  return (exceptionDefinition(status)?.label ?? status) || '-'
}

function statusTag(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'matched') return 'success'
  if (status === 'conflict') return 'danger'
  if (status === 'special') return 'info'
  if (
    status === 'product_unmatched'
    || status === 'sku_unmatched'
    || status === 'sku_ambiguous'
    || status === 'image_unmatched'
  ) return 'warning'
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
  return exceptionDefinition(row.status)?.advice ?? '暂无处理入口'
}

function repairTarget(row: RecognitionPreviewRow): string {
  return exceptionDefinition(row.status)?.actionLabel ?? ''
}

function exceptionDefinition(status: string): ExceptionDefinition | null {
  return exceptionDefinitions[status as ExceptionStatus] ?? null
}

function repairQuery(row: RecognitionPreviewRow): Record<string, string> {
  const query: Record<string, string> = {
    from: 'exceptions',
    status: row.status,
    source_label: row.source_label,
  }

  if (row.detail_id) query.detail_id = String(row.detail_id)
  if (selectedTaskId.value) query.task_id = String(selectedTaskId.value)
  if (row.product_id) query.product_id = String(row.product_id)
  if (row.product_text) query.product_text = row.product_text
  if (row.sales_attr1_text) query.sales_attr1 = row.sales_attr1_text
  if (row.sales_attr2_text) query.sales_attr2 = row.sales_attr2_text
  if (row.quantity_text) query.quantity = row.quantity_text
  if (row.remark_text) query.remark = row.remark_text
  if (row.image_match_text) query.image_match_text = row.image_match_text
  if (row.reason) query.reason = row.reason
  if (row.rule_id) query.rule_id = String(row.rule_id)
  if (row.status === 'sku_ambiguous') query.focus = 'sku'
  return query
}

function repairRoute(row: RecognitionPreviewRow) {
  const definition = exceptionDefinition(row.status)
  if (!definition?.target) return null

  if (definition.target === 'ai-recognition') {
    return {
      path: '/admin/ai-recognition',
      query: selectedTaskId.value ? { task_id: String(selectedTaskId.value) } : {},
    }
  }

  if (definition.target === 'product-assets') {
    if (!row.product_id) return null
    return {
      path: '/admin/products',
      query: { product_id: String(row.product_id) },
    }
  }

  if (definition.target === 'product-matching') {
    return {
      path: '/admin/product-matching',
      query: repairQuery(row),
    }
  }

  return null
}

function goToRepair(row: RecognitionPreviewRow) {
  const target = repairRoute(row)
  if (target) void router.push(target)
}

function handleParseIssueAction() {
  const issue = parseIssue.value
  if (!issue) return
  if (issue.action === 'refresh') {
    void loadRecognitionPreview()
    return
  }
  const path = parserIssueRoute(issue.action)
  if (!path) return
  void router.push({
    path,
    query: selectedTaskId.value ? { task_id: String(selectedTaskId.value) } : {},
  })
}

async function loadRecognitionPreview() {
  if (!selectedTaskId.value) {
    recognitionPreview.value = null
    return
  }
  previewLoading.value = true
  error.value = ''
  try {
    const [preview, drafts] = await Promise.all([
      getCaptureTaskRecognitionPreview(selectedTaskId.value),
      getOrderRowDrafts(selectedTaskId.value, { limit: 5000 }),
    ])
    recognitionPreview.value = preview
    orderDrafts.value = drafts
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
    const tasks = await getRecords('/capture-tasks?limit=2000&include_waybill_counts=false')
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
    orderDrafts.value = null
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
      <strong>采集轮次</strong>
      <el-select
        v-model="selectedTaskId"
        class="task-select"
        filterable
        placeholder="选择采集轮次"
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
  <el-alert
    v-else-if="parseIssue"
    :closable="false"
    :title="parseIssue.label"
    :type="parseIssue.type"
    show-icon
  >
    <template #default>
      当前面单保留为明确异常，不会进入正常导出。
      <el-link
        v-if="aiSessionUrl && parseIssue.action === 'ai-recognition'"
        :href="aiSessionUrl"
        target="_blank"
        rel="noopener noreferrer"
        type="primary"
      >
        查看 AI 识别会话
      </el-link>
      <el-link v-else type="primary" @click="handleParseIssueAction">
        {{ parseIssue.actionLabel }}
      </el-link>
    </template>
  </el-alert>

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
      <strong>{{ totalExceptionCount }}</strong>
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
        title="规则包未解析、商品未命中、SKU/图片未命中和冲突都会进入异常处理；特殊单属于正常跳过。"
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
        <span>{{ totalExceptionCount }}</span>
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

    <el-alert
      v-if="showParseException"
      :closable="false"
      :title="`${parseExceptionCount} 张面单：${parseIssue?.label || '解析未生成商品行'}`"
      :type="parseIssue?.type || 'warning'"
      show-icon
    >
      <template #default>
        解析未生成订单行，已保留为异常，不会进入正常导出。
        <el-link
          v-if="aiSessionUrl && parseIssue?.action === 'ai-recognition'"
          :href="aiSessionUrl"
          target="_blank"
          rel="noopener noreferrer"
          type="primary"
        >
          查看 AI 识别会话
        </el-link>
        <el-link v-else-if="parseIssue" type="primary" @click="handleParseIssueAction">
          {{ parseIssue.actionLabel }}
        </el-link>
      </template>
    </el-alert>

    <el-table
      v-if="visibleExceptionRows.length"
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
          <strong>{{ itemLabel(row) }}</strong>
          <div class="muted-line">本轮采集面单</div>
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
          <el-button v-if="repairRoute(row)" size="small" type="primary" plain @click="goToRepair(row)">
            {{ repairTarget(row) }}
          </el-button>
          <span v-else class="muted-line">暂无处理入口</span>
        </template>
      </el-table-column>
    </el-table>

    <el-empty
      v-else-if="!showParseException"
      v-loading="previewLoading"
      description="当前采集轮次没有会进入异常面单的待处理行"
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
