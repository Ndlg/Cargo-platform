<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Download, Refresh, Right, Warning } from '@element-plus/icons-vue'

import {
  downloadCaptureTaskDocument,
  getOrderRowDrafts,
  getRecords,
  saveBlob,
  type CaptureTaskRecord,
  type OrderRowDraftRecord,
  type OrderRowDraftsResponse,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

type RowStatusFilter = 'all' | 'draft' | 'special' | 'needs_review'

const router = useRouter()
const route = useRoute()
const session = useSessionStore()

const captureTasks = ref<CaptureTaskRecord[]>([])
const selectedTaskId = ref<number | null>(null)
const drafts = ref<OrderRowDraftsResponse | null>(null)
const keyword = ref('')
const sourceFilter = ref('')
const statusFilter = ref<RowStatusFilter>('all')
const currentPage = ref(1)
const pageSize = ref(50)
const loading = ref(false)
const downloadingKey = ref('')
const error = ref('')

const sortedTasks = computed(() => [...captureTasks.value].sort((a, b) => b.id - a.id))
const selectedTask = computed(() => sortedTasks.value.find((task) => task.id === selectedTaskId.value) ?? null)
const allRows = computed(() => drafts.value?.rows ?? [])
const reviewRows = computed(() => allRows.value.filter((row) => row.status === 'needs_review'))
const rulePackMissing = computed(() => drafts.value?.status === 'rule_pack_missing' || drafts.value?.rule_pack_required === true)
const activeRulePackName = computed(() => drafts.value?.recognition_rule_pack?.name ?? '')
const parentCount = computed(() => drafts.value?.summary.parent_waybill_count ?? waybillCountForTask(selectedTask.value))
const childCount = computed(() => drafts.value?.summary.child_waybill_count ?? 0)
const draftCount = computed(() => drafts.value?.summary.draft_count ?? 0)
const reviewCount = computed(() => drafts.value?.summary.needs_review_count ?? reviewRows.value.length)
const specialCount = computed(
  () => drafts.value?.summary.special_count ?? allRows.value.filter((row) => row.status === 'special').length,
)
const selectedTaskIndex = computed(() =>
  sortedTasks.value.findIndex((task) => task.id === selectedTaskId.value),
)
const selectedTaskTitle = computed(() => {
  if (!selectedTask.value) return '暂无采集'
  return taskLabel(selectedTask.value, selectedTaskIndex.value)
})
const sourceOptions = computed(() => {
  const values = new Set(allRows.value.map((row) => sourceLabel(row)).filter(Boolean))
  return [...values]
})
const sourceCounts = computed(() => {
  const rows = allRows.value
  return {
    total: sourceOptions.value.length,
    douyin: rows.filter((row) => row.source_component === 'cloud-print-client').length,
    cainiao: rows.filter((row) => row.source_component === 'cainiao-cnprint').length,
    other: rows.filter((row) => !['cloud-print-client', 'cainiao-cnprint'].includes(row.source_component)).length,
  }
})
const filteredRows = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return allRows.value.filter((row) => {
    if (statusFilter.value !== 'all' && row.status !== statusFilter.value) return false
    if (sourceFilter.value && sourceLabel(row) !== sourceFilter.value) return false
    if (!query) return true
    return [
      row.child_label,
      row.parent_label,
      row.product,
      row.sales_attr1,
      row.sales_attr2,
      row.quantity,
      row.remark,
      row.original_text,
      row.image_match_text,
      row.review_reason,
      sourceLabel(row),
    ]
      .join('\n')
      .toLowerCase()
      .includes(query)
  })
})
const pagedRows = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredRows.value.slice(start, start + pageSize.value)
})

function taskLabel(task: CaptureTaskRecord, index = 0): string {
  const round = index <= 0 ? '最近一轮' : `上一轮 ${index}`
  return `${round}：${formatTime(task.started_at)} ${statusLabel(task.status)}`
}

function statusLabel(status?: string | null): string {
  if (status === 'collecting') return '采集中'
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '失败'
  return status || '-'
}

function formatTime(value?: string | null): string {
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

function numericCount(value: unknown): number | null {
  const count = Number(value)
  return Number.isFinite(count) && count >= 0 ? count : null
}

function waybillCountForTask(task?: CaptureTaskRecord | null): number {
  if (!task) return 0
  return numericCount(task.waybill_count ?? task.parent_waybill_count) ?? 0
}

function queryPositiveInt(value: unknown): number | null {
  const rawValue = Array.isArray(value) ? value[0] : value
  if (rawValue === undefined || rawValue === null || rawValue === '') return null
  const parsed = Number(rawValue)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function sourceLabel(row: OrderRowDraftRecord): string {
  if (row.source_component === 'cloud-print-client') return '抖店 / CloudPrint'
  if (row.source_component === 'cainiao-cnprint') return '菜鸟组件'
  return row.source_component || '未知来源'
}

function rowKey(row: OrderRowDraftRecord): string {
  return `${row.raw_record_id}-${row.child_label}-${row.child_index}`
}

function rowStatusLabel(row: OrderRowDraftRecord): string {
  if (row.status === 'draft') return '已生成订单行'
  if (row.status === 'special') return '特殊单'
  if (row.review_reason === 'no_readable_waybill_text') return '没有读到面单文字'
  if (row.review_reason === 'no_product_text') return '未读到商品'
  if (row.review_reason.includes('quantity')) return '缺数量'
  if (row.review_reason.includes('product')) return '缺商品'
  return '需要复核'
}

function rowReviewText(row: OrderRowDraftRecord): string {
  if (row.status === 'draft') return '-'
  if (row.status === 'special') return '特殊单，不进入商品/SKU/图片匹配。'
  if (row.review_reason === 'no_readable_waybill_text') return '没有读到可用面单文字，请展开查看来源。'
  if (row.review_reason === 'no_product_text') return '有面单文字，但没有拆出商品；请展开查看原文。'
  if (row.review_reason.includes('quantity')) return '没有拆出数量；默认规则无法安全确认。'
  if (row.review_reason.includes('product')) return '没有拆出商品；需要补解析规则或人工复核。'
  return row.review_reason || '需要复核。'
}

function productDisplayText(row: OrderRowDraftRecord): string {
  return row.product || row.original_text || row.image_match_text || '未读到商品'
}

function diagnosticsSourceText(row: OrderRowDraftRecord): string {
  return row.source_index ? '可追溯到原始面单' : '缺少原始面单追溯'
}

function rowStatusType(row: OrderRowDraftRecord): 'success' | 'warning' | 'danger' | 'info' {
  if (row.status === 'draft') return 'success'
  if (row.status === 'special') return 'info'
  if (row.review_reason === 'no_product_text' || row.review_reason === 'no_readable_waybill_text') return 'danger'
  return 'warning'
}

function emptyText(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '空'
  return String(value)
}

async function loadTasks() {
  const records = await getRecords('/capture-tasks?limit=2000')
  captureTasks.value = records as CaptureTaskRecord[]
  const taskIds = new Set(captureTasks.value.map((task) => task.id))
  const routeTaskId = queryPositiveInt(route.query.task_id)
  if (!selectedTaskId.value && routeTaskId && taskIds.has(routeTaskId)) {
    selectedTaskId.value = routeTaskId
    return
  }
  if (!selectedTaskId.value || !taskIds.has(selectedTaskId.value)) {
    selectedTaskId.value = sortedTasks.value[0]?.id ?? null
  }
}

async function loadDrafts() {
  if (!selectedTaskId.value) {
    drafts.value = null
    return
  }
  drafts.value = await getOrderRowDrafts(selectedTaskId.value, { limit: 5000 })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    await loadTasks()
    await loadDrafts()
  } catch (err) {
    drafts.value = null
    error.value = err instanceof Error ? err.message : '面单解析结果加载失败'
  } finally {
    loading.value = false
  }
}

async function downloadSelectedTaskDocument(kind: 'raw' | 'standard') {
  if (!selectedTaskId.value) {
    error.value = '请先选择采集任务。'
    return
  }
  downloadingKey.value = `${selectedTaskId.value}-${kind}`
  error.value = ''
  try {
    const { blob, filename } = await downloadCaptureTaskDocument(selectedTaskId.value, kind)
    saveBlob(blob, filename)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '采集任务文档下载失败'
  } finally {
    downloadingKey.value = ''
  }
}

watch(
  () => session.currentWorkspaceId,
  () => {
    selectedTaskId.value = null
    void load()
  },
)

watch(selectedTaskId, () => {
  currentPage.value = 1
  void loadDrafts()
})

watch([keyword, sourceFilter, statusFilter, pageSize], () => {
  currentPage.value = 1
})

onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>面单解析</h1>
      <p>按采集任务查看面单原文和解析出的订单行；多商品面单应拆成多行，异常进入复核。</p>
    </div>
    <div class="action-row">
      <el-select
        v-model="selectedTaskId"
        class="task-select"
        filterable
        placeholder="选择采集任务"
        style="width: 360px"
      >
        <el-option
          v-for="(task, index) in sortedTasks"
          :key="task.id"
          :label="taskLabel(task, index)"
          :value="task.id"
        />
      </el-select>
      <el-button :icon="Refresh" :loading="loading" plain @click="load">刷新</el-button>
      <el-button
        :disabled="!selectedTaskId"
        :icon="Download"
        :loading="downloadingKey === `${selectedTaskId}-raw`"
        plain
        @click="downloadSelectedTaskDocument('raw')"
      >
        下载原文
      </el-button>
      <el-button
        :disabled="!selectedTaskId"
        :icon="Download"
        :loading="downloadingKey === `${selectedTaskId}-standard`"
        type="primary"
        plain
        @click="downloadSelectedTaskDocument('standard')"
      >
        下载整理文档
      </el-button>
      <el-button :icon="Right" type="primary" @click="router.push('/exports')">
        查看导出中心
      </el-button>
    </div>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />
  <el-alert
    v-else-if="rulePackMissing"
    :closable="false"
    title="当前工作空间没有启用识别规则包"
    type="warning"
    show-icon
  >
    <template #default>
      系统不会使用内置默认规则识别面单。请先导入并启用当前商品场景的规则包，再刷新面单解析。
    </template>
  </el-alert>
  <el-alert
    v-else-if="activeRulePackName"
    :closable="false"
    :title="`当前识别规则包：${activeRulePackName}`"
    type="success"
    show-icon
  />

  <section class="stat-grid">
    <div class="stat-tile">
      <span>本轮采集</span>
      <strong>{{ statusLabel(selectedTask?.status) }}</strong>
      <small>{{ selectedTaskTitle }}</small>
    </div>
    <div class="stat-tile">
      <span>面单数量</span>
      <strong>{{ parentCount }}</strong>
      <small>采集到并展开后的业务面单数量</small>
    </div>
    <div class="stat-tile">
      <span>订单行</span>
      <strong>{{ childCount }}</strong>
      <small>可用 {{ draftCount }} / 特殊 {{ specialCount }}</small>
    </div>
    <div class="stat-tile">
      <span>面单来源</span>
      <strong>{{ sourceCounts.total }}</strong>
      <small>抖店 {{ sourceCounts.douyin }} / 菜鸟 {{ sourceCounts.cainiao }} / 其他 {{ sourceCounts.other }}</small>
    </div>
    <div class="stat-tile">
      <span>需复核</span>
      <strong>{{ reviewCount }}</strong>
      <small>缺商品、缺数量或无法读取</small>
    </div>
  </section>

  <section class="work-surface">
    <div class="table-heading">
      <div>
        <h2>本轮订单行</h2>
        <p class="muted-line">这里显示的是订单行解析结果，后续商品匹配和导出也会消费这一份数据。</p>
      </div>
      <div class="table-toolbar">
        <el-segmented
          v-model="statusFilter"
          :options="[
            { label: '全部', value: 'all' },
            { label: '可用', value: 'draft' },
            { label: '特殊单', value: 'special' },
            { label: '需复核', value: 'needs_review' },
          ]"
        />
        <el-input
          v-model="keyword"
          clearable
          placeholder="搜索商品、属性、原文"
          style="width: 280px"
        />
        <el-select v-model="sourceFilter" clearable placeholder="全部来源" style="width: 180px">
          <el-option v-for="source in sourceOptions" :key="source" :label="source" :value="source" />
        </el-select>
      </div>
    </div>

    <el-table
      v-loading="loading"
      :data="pagedRows"
      :row-key="rowKey"
      height="560"
      stripe
    >
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="order-row-detail">
            <div>
              <span>面单原文</span>
              <pre>{{ row.original_text || '无' }}</pre>
            </div>
            <div>
              <span>图片匹配文本</span>
              <pre>{{ row.image_match_text || '无' }}</pre>
            </div>
            <div>
              <span>采集来源</span>
              <strong>{{ sourceLabel(row) }} / {{ diagnosticsSourceText(row) }}</strong>
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="订单行" min-width="170" fixed>
        <template #default="{ row }">
          <strong>{{ row.child_label }}</strong>
          <div v-if="row.child_count > 1" class="muted-line">多商品 {{ row.child_index }}/{{ row.child_count }}</div>
        </template>
      </el-table-column>
      <el-table-column label="商品" min-width="320" show-overflow-tooltip>
        <template #default="{ row }">
          <span :class="{ 'muted-value': !row.product }">{{ productDisplayText(row) }}</span>
          <div v-if="!row.product && (row.original_text || row.image_match_text)" class="muted-line">未拆出商品</div>
        </template>
      </el-table-column>
      <el-table-column label="销售属性1" min-width="170" show-overflow-tooltip>
        <template #default="{ row }">{{ emptyText(row.sales_attr1) }}</template>
      </el-table-column>
      <el-table-column label="销售属性2" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">{{ emptyText(row.sales_attr2) }}</template>
      </el-table-column>
      <el-table-column label="数量" width="90">
        <template #default="{ row }">{{ emptyText(row.quantity) }}</template>
      </el-table-column>
      <el-table-column label="备注" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">{{ emptyText(row.remark) }}</template>
      </el-table-column>
      <el-table-column label="来源" min-width="140">
        <template #default="{ row }">{{ sourceLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="异常原因" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">{{ rowReviewText(row) }}</template>
      </el-table-column>
      <el-table-column label="检查" width="150" fixed="right">
        <template #default="{ row }">
          <el-tag :type="rowStatusType(row)" size="small">
            {{ rowStatusLabel(row) }}
          </el-tag>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="当前采集任务还没有可展示的订单行" />
      </template>
    </el-table>

    <div class="pagination-row">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[20, 50, 100, 200]"
        :total="filteredRows.length"
        layout="total, sizes, prev, pager, next"
      />
    </div>
  </section>

  <section v-if="reviewRows.length" class="work-surface">
    <h2>需复核原文</h2>
    <el-alert
      :closable="false"
      title="这些行已经保存为原始采集记录，但还不能安全进入商品匹配。"
      type="warning"
      show-icon
    />
    <el-table :data="reviewRows" height="280" stripe>
      <el-table-column label="订单行" prop="child_label" width="170" />
      <el-table-column label="原因" width="180">
        <template #default="{ row }">{{ rowStatusLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="面单原文" min-width="420">
        <template #default="{ row }">
          <pre class="raw-payload compact">{{ row.original_text || '无' }}</pre>
        </template>
      </el-table-column>
      <el-table-column label="处理建议" min-width="220">
        <template #default="{ row }">
          {{ rowReviewText(row) }}
        </template>
      </el-table-column>
    </el-table>
    <el-alert
      :closable="false"
      class="surface-alert"
      type="warning"
      show-icon
    >
      <template #title>
        <el-icon><Warning /></el-icon>
        需复核行不会静默进入商品匹配或导出。
      </template>
    </el-alert>
  </section>
</template>

<style scoped>
.table-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.table-heading p {
  margin: 0;
}

.table-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
}

.order-row-detail {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(220px, 0.6fr);
  gap: 12px;
  padding: 10px 18px;
}

.order-row-detail span {
  display: block;
  margin-bottom: 6px;
  color: #667085;
  font-size: 12px;
}

.order-row-detail pre,
.raw-payload {
  min-height: 48px;
  max-height: 160px;
  margin: 0;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: 6px;
  background: #f6f8fb;
  padding: 10px;
  font-family: inherit;
  font-size: 13px;
}

.raw-payload.compact {
  min-height: 36px;
  max-height: 120px;
}

.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.surface-alert {
  margin-top: 14px;
}

@media (max-width: 1200px) {
  .table-heading,
  .action-row {
    flex-direction: column;
    align-items: stretch;
  }

  .table-toolbar {
    justify-content: flex-start;
  }

  .order-row-detail {
    grid-template-columns: 1fr;
  }
}
</style>
