<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { CircleClose, Document, Download, Upload, VideoPlay, Warning } from '@element-plus/icons-vue'

import {
  getOrderRowDrafts,
  getRecords,
  type CaptureTaskRecord,
  type OrderRowDraftsResponse,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

const router = useRouter()
const session = useSessionStore()
const workspaceName = computed(() => session.currentWorkspace?.name ?? '未选择工作空间')
const captureTasks = ref<CaptureTaskRecord[]>([])
const latestDrafts = ref<OrderRowDraftsResponse | null>(null)
const loading = ref(false)
const error = ref('')

const sortedTasks = computed(() => [...captureTasks.value].sort((a, b) => b.id - a.id))
const latestTask = computed(() => sortedTasks.value[0] ?? null)
const summary = computed(() => latestDrafts.value?.summary ?? null)
const summaryLoadingValue = computed(() => (latestTask.value && !summary.value ? '读取中' : '0'))
const latestTaskWaybillCount = computed(() => waybillCountForTask(latestTask.value))
const statusTiles = computed(() => [
  {
    label: '采集状态',
    value: latestTask.value ? taskStatusLabel(latestTask.value.status) : '待开始',
    note: latestTask.value ? `最近一轮：${formatTaskTime(latestTask.value.started_at)}` : '等待业务机采集器上传',
  },
  {
    label: '面单数量',
    value: latestTask.value ? String(latestTaskWaybillCount.value) : summaryLoadingValue.value,
    note: latestTask.value ? `采集任务 #${latestTask.value.id}，本轮采集到的业务面单` : '还没有可读取面单',
  },
  {
    label: '订单行',
    value: summary.value ? String(summary.value.child_waybill_count) : summaryLoadingValue.value,
    note: summary.value ? `可用 ${summary.value.draft_count} / 特殊 ${summary.value.special_count ?? 0}` : '读取订单行中',
  },
  {
    label: '需复核',
    value: summary.value ? String(summary.value.needs_review_count) : summaryLoadingValue.value,
    note: '缺商品、缺数量或规则包无法安全解析',
  },
])

const captureControls = [
  {
    label: '开始采集',
    description: '连接当前工作空间下的业务机采集器，准备接收本轮面单订单。',
    icon: VideoPlay,
    type: 'primary' as const,
  },
  {
    label: '结束采集',
    description: '关闭本轮采集批次，等待面单原文回传并进入平台处理。',
    icon: CircleClose,
    type: 'danger' as const,
  },
]

const nextActions = [
  {
    label: '采集记录',
    description: '查看采集器上传到当前工作空间的面单原文内容。',
    path: '/capture-records',
    icon: Document,
  },
  {
    label: '面单解析',
    description: '查看采集到的面单如何被解析成可编辑订单行。',
    path: '/waybill-batches',
    icon: Upload,
  },
  {
    label: '处理异常',
    description: '处理面单无法解析、商品识别失败、SKU 缺失和导出配置缺失等异常。',
    path: '/exceptions',
    icon: Warning,
  },
  {
    label: '导出结果',
    description: '按管理员配置的字段、分组和文件拆分方式导出。',
    path: '/exports',
    icon: Download,
  },
]

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
  return status || '待开始'
}

function numericCount(value: unknown): number | null {
  const count = Number(value)
  return Number.isFinite(count) && count >= 0 ? count : null
}

function waybillCountForTask(task?: CaptureTaskRecord | null): number {
  if (!task) return 0
  return numericCount(task.waybill_count ?? task.parent_waybill_count) ?? 0
}

async function loadHomeSummary() {
  loading.value = true
  error.value = ''
  try {
    const tasks = await getRecords('/capture-tasks?limit=2000')
    captureTasks.value = tasks as CaptureTaskRecord[]
    const task = latestTask.value
    latestDrafts.value = task ? await getOrderRowDrafts(task.id, { limit: 5000 }) : null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '首页数据加载失败'
    latestDrafts.value = null
  } finally {
    loading.value = false
  }
}

watch(
  () => session.currentWorkspaceId,
  () => {
    latestDrafts.value = null
    void loadHomeSummary()
  },
)

onMounted(loadHomeSummary)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>业务页面</h1>
      <p>{{ workspaceName }}。一线人员从这里控制采集、采集后的处理、异常和导出。</p>
    </div>
    <el-button :icon="Document" type="primary" @click="router.push('/capture-records')">
      采集控制
    </el-button>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section v-loading="loading" class="stat-grid">
    <article v-for="item in statusTiles" :key="item.label" class="stat-tile">
      <span>{{ item.label }}</span>
      <strong>{{ item.value }}</strong>
      <small>{{ item.note }}</small>
    </article>
  </section>

  <section class="work-surface">
    <h2>本轮采集</h2>
    <div class="process-grid">
      <article v-for="control in captureControls" :key="control.label" class="process-card">
        <el-icon><component :is="control.icon" /></el-icon>
        <div>
          <strong>{{ control.label }}</strong>
          <p>{{ control.description }}</p>
        </div>
        <el-button :type="control.type" plain @click="router.push('/capture-records')">
          {{ control.label }}
        </el-button>
      </article>
    </div>
  </section>

  <section class="work-surface">
    <h2>下一步操作</h2>
    <div class="process-grid">
      <article v-for="action in nextActions" :key="action.path" class="process-card">
        <el-icon><component :is="action.icon" /></el-icon>
        <div>
          <strong>{{ action.label }}</strong>
          <p>{{ action.description }}</p>
        </div>
        <el-button type="primary" plain @click="router.push(action.path)">进入</el-button>
      </article>
    </div>
  </section>
</template>
