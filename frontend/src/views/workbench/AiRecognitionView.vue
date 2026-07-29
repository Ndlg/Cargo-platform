<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  getOrderRowDrafts,
  getRecords,
  getWaybillReadingSamples,
  startManualAiRecognition,
  type CaptureTaskRecord,
  type OrderRowDraftsResponse,
  type WaybillReadingSample,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

type QueueItem = WaybillReadingSample & {
  parentSequence: number
  reason: string
}

const session = useSessionStore()
const tasks = ref<CaptureTaskRecord[]>([])
const selectedTaskId = ref<number | null>(null)
const samples = ref<WaybillReadingSample[]>([])
const drafts = ref<OrderRowDraftsResponse | null>(null)
const loading = ref(false)
const startingSampleId = ref('')
const error = ref('')
const consoleUrl = ref('')
const currentSessionLabel = ref('')
const sessionError = ref('')

const sessionStatusText = computed(() => {
  if (startingSampleId.value) return '正在创建会话'
  if (sessionError.value) return '启动失败'
  if (consoleUrl.value) return '会话已连接'
  return '等待选择面单'
})

function savedSessionKey(): string {
  if (!selectedTaskId.value) return ''
  return `cargo-platform-ai-session:${session.currentWorkspaceId ?? 'default'}:${selectedTaskId.value}`
}

function restoreSavedSession() {
  consoleUrl.value = ''
  currentSessionLabel.value = ''
  sessionError.value = ''
  const key = savedSessionKey()
  if (!key) return
  try {
    const saved = JSON.parse(localStorage.getItem(key) || '{}') as { consoleUrl?: string; label?: string }
    consoleUrl.value = saved.consoleUrl || ''
    currentSessionLabel.value = saved.label || ''
  } catch {
    localStorage.removeItem(key)
  }
}

function saveCurrentSession() {
  const key = savedSessionKey()
  if (!key || !consoleUrl.value) return
  localStorage.setItem(
    key,
    JSON.stringify({ consoleUrl: consoleUrl.value, label: currentSessionLabel.value }),
  )
}

const taskOptions = computed(() =>
  [...tasks.value]
    .sort((a, b) => b.id - a.id)
    .map((task, index) => ({
      value: task.id,
      label: `${index === 0 ? '最近一轮' : `上一轮 ${index}`}：${formatTime(task.started_at)} ${taskStatus(task.status)}`,
    })),
)

const queue = computed<QueueItem[]>(() => {
  const diagnostics = drafts.value?.diagnostics ?? []
  return samples.value
    .map((sample, index) => ({
      ...sample,
      parentSequence: (sample.sample_order ?? index) + 1,
      reason:
        drafts.value?.status === 'rule_pack_missing'
          ? 'rule_pack_missing'
          : diagnostics[index]?.reason ?? '',
    }))
    .filter((item) => item.reason)
})

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

function taskStatus(value?: string | null): string {
  if (value === 'completed') return '已完成'
  if (value === 'collecting') return '采集中'
  return value || ''
}

function reasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    rule_pack_missing: '尚无识别规则',
    format_profile_missing: '陌生面单格式',
    format_profile_incomplete: '规则未完整生成订单行',
    missing_product: '未读到商品',
    missing_quantity: '未读到数量',
  }
  return labels[reason] ?? reason
}

async function loadTasks() {
  const records = (await getRecords('/capture-tasks?limit=200')) as CaptureTaskRecord[]
  tasks.value = records
  selectedTaskId.value = [...records].sort((a, b) => b.id - a.id)[0]?.id ?? null
}

async function loadQueue() {
  if (!selectedTaskId.value) {
    samples.value = []
    drafts.value = null
    consoleUrl.value = ''
    currentSessionLabel.value = ''
    return
  }
  restoreSavedSession()
  loading.value = true
  error.value = ''
  try {
    const [reading, parsed] = await Promise.all([
      getWaybillReadingSamples({ task_id: selectedTaskId.value, limit: 200 }),
      getOrderRowDrafts(selectedTaskId.value, { limit: 5000 }),
    ])
    samples.value = reading.samples
    drafts.value = parsed
  } catch (err) {
    error.value = err instanceof Error ? err.message : '待学习面单读取失败'
  } finally {
    loading.value = false
  }
}

async function start(item: QueueItem) {
  if (!selectedTaskId.value || startingSampleId.value) return
  startingSampleId.value = item.sample_id
  currentSessionLabel.value = `面单 ${item.parentSequence}`
  consoleUrl.value = ''
  sessionError.value = ''
  localStorage.removeItem(savedSessionKey())
  error.value = ''
  try {
    const result = await startManualAiRecognition(selectedTaskId.value, {
      raw_record_id: item.raw_record_id,
      document_sequence: item.document_sequence ?? 1,
      parent_sequence: item.parentSequence,
    })
    const recognitionSession = result.ai_sessions?.[0]
    if (recognitionSession?.console_url) {
      consoleUrl.value = recognitionSession.console_url
      saveCurrentSession()
      ElMessage.success('已开始解析这一张面单')
    } else if (result.status === 'parsed') {
      ElMessage.success('当前规则已经可以识别这张面单')
      await loadQueue()
    } else {
      sessionError.value =
        result.status === 'ai_unavailable'
          ? 'AI 识别服务暂时不可用，未创建解析会话。'
          : 'AI 会话未创建，请检查识别服务。'
    }
  } catch (err) {
    sessionError.value = err instanceof Error ? err.message : 'AI 面单解析启动失败'
  } finally {
    startingSampleId.value = ''
  }
}

function handleConsoleMessage(event: MessageEvent) {
  if (!consoleUrl.value || event.origin !== new URL(consoleUrl.value).origin) return
  const payload = event.data as { type?: string }
  if (payload?.type !== 'cargo-ai-rule-approved') return
  ElMessage.success('新规则已同步，正在重新检查本轮面单')
  void loadQueue()
}

watch(
  () => session.currentWorkspaceId,
  async () => {
    await loadTasks()
    await loadQueue()
  },
)

onMounted(async () => {
  window.addEventListener('message', handleConsoleMessage)
  await loadTasks()
  await loadQueue()
})
onBeforeUnmount(() => window.removeEventListener('message', handleConsoleMessage))
</script>

<template>
  <section class="page-header">
    <div>
      <h1>AI 面单解析</h1>
      <p>这里只处理规则尚不认识的面单。每次由管理员手动选择一张，确认后自动生成并启用新规则。</p>
    </div>
    <el-button :loading="loading" @click="loadQueue">刷新待学习清单</el-button>
  </section>

  <el-alert
    v-if="error"
    :closable="false"
    :title="error"
    show-icon
    type="error"
  />

  <section class="work-surface ai-session-window" aria-live="polite">
    <div class="console-header">
      <div>
        <h2>AI 解析会话</h2>
        <p>
          {{ currentSessionLabel || '尚未选择面单' }} ·
          模型候选必须由管理员确认后才会同步为识别规则。
        </p>
      </div>
      <div class="console-actions">
        <el-tag :type="sessionError ? 'danger' : consoleUrl ? 'success' : 'info'">
          {{ sessionStatusText }}
        </el-tag>
        <el-button v-if="consoleUrl" tag="a" :href="consoleUrl" target="_blank">
          在独立窗口打开
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="sessionError"
      :closable="false"
      :title="sessionError"
      show-icon
      type="error"
    />
    <div v-else-if="startingSampleId" class="session-placeholder">
      <el-skeleton :rows="4" animated />
      <p>正在创建会话并提交给本地模型，请稍候……</p>
    </div>
    <iframe
      v-else-if="consoleUrl"
      :src="consoleUrl"
      title="本地 AI 面单识别会话"
    />
    <el-empty
      v-else
      :image-size="72"
      description="从下方待学习清单选择一张面单，解析过程会显示在这里"
    />
  </section>

  <section class="work-surface">
    <div class="toolbar">
      <span>采集轮次</span>
      <el-select
        v-model="selectedTaskId"
        :disabled="loading"
        placeholder="选择采集轮次"
        @change="loadQueue"
      >
        <el-option
          v-for="option in taskOptions"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>
      <el-tag type="warning">{{ queue.length }} 张待学习</el-tag>
    </div>

    <el-empty v-if="!loading && !queue.length" description="这一轮没有需要 AI 学习的面单" />

    <div v-loading="loading" class="queue-list">
      <article v-for="item in queue" :key="item.sample_id" class="queue-card">
        <div class="queue-card__body">
          <div class="queue-card__title">
            <strong>面单 {{ item.parentSequence }}</strong>
            <el-tag type="warning">{{ reasonLabel(item.reason) }}</el-tag>
          </div>
          <p>{{ item.sample_text || '这张面单没有可展示的商品文本' }}</p>
          <small>{{ item.source_component || '未知打印平台' }}</small>
        </div>
        <el-button
          :loading="startingSampleId === item.sample_id"
          :disabled="Boolean(startingSampleId)"
          type="primary"
          @click="start(item)"
        >
          手动开始解析这一张
        </el-button>
      </article>
    </div>
  </section>

</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.toolbar .el-select {
  width: min(520px, 70vw);
}

.queue-list {
  min-height: 120px;
}

.queue-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 16px 0;
  border-top: 1px solid #ebeef5;
}

.queue-card:first-child {
  border-top: 0;
}

.queue-card__body {
  min-width: 0;
}

.queue-card__title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.queue-card p {
  margin: 8px 0;
  color: #303133;
  white-space: pre-wrap;
}

.queue-card small {
  color: #909399;
}

.console-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.console-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.console-header h2,
.console-header p {
  margin: 0 0 6px;
}

.ai-session-window iframe {
  width: 100%;
  min-height: 620px;
  margin-top: 12px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  background: white;
}

.session-placeholder {
  min-height: 180px;
  padding-top: 20px;
  color: #606266;
}

@media (max-width: 760px) {
  .toolbar,
  .queue-card,
  .console-header,
  .console-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .toolbar .el-select {
    width: 100%;
  }
}
</style>
