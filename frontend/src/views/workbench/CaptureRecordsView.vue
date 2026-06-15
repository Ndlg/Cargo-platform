<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { CircleClose, Connection, Document, Download, Refresh, Right, VideoPlay } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import {
  downloadCaptureTaskDocument,
  getCollectorControlStatus,
  getRecords,
  saveBlob,
  startCapture,
  stopCapture,
  type ApiRecord,
  type CaptureTaskRecord,
  type CollectorRecord,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

type StandardDetailRecord = ApiRecord & {
  id: number
  field_values?: Record<string, unknown>
}

type RawRecordGroup = {
  task: CaptureTaskRecord
  records: ApiRecord[]
}

const router = useRouter()
const session = useSessionStore()
const collectors = ref<CollectorRecord[]>([])
const activeTask = ref<CaptureTaskRecord | null>(null)
const captureTasks = ref<CaptureTaskRecord[]>([])
const rawRecords = ref<ApiRecord[]>([])
const standardDetails = ref<StandardDetailRecord[]>([])
const loading = ref(false)
const actionLoading = ref(false)
const downloadingKey = ref('')
const error = ref('')

const captureStatus = computed(() => (activeTask.value ? '采集中' : '待开始'))
const activeTaskWaybillCount = computed(() => {
  if (!activeTask.value) return 0
  return waybillCountForTask(activeTask.value.id)
})
const onlineCount = computed(() => collectors.value.filter((collector) => collector.online_status === 'online').length)
const listeningCount = computed(
  () => collectors.value.filter((collector) => collector.status_payload?.runtime_status === 'listening').length,
)
const latestTasks = computed(() => [...captureTasks.value].sort((a, b) => b.id - a.id).slice(0, 6))
const latestRawRecordGroups = computed<RawRecordGroup[]>(() =>
  [...captureTasks.value]
    .sort((a, b) => b.id - a.id)
    .map((task) => ({ task, records: rawRecordsForTask(task.id) }))
    .filter((group) => group.records.length > 0)
    .slice(0, 80),
)

function textValue(value: unknown, fallback = '-'): string {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function formatDateTime(value: unknown, fallback = '-'): string {
  const text = textValue(value, '')
  if (!text) return fallback
  const date = new Date(text)
  if (Number.isNaN(date.getTime())) return text
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function detailsForTask(taskId: number): StandardDetailRecord[] {
  return standardDetails.value.filter((detail) => Number(detail.field_values?.capture_task_id) === taskId)
}

function rawRecordsForTask(taskId: number): ApiRecord[] {
  return rawRecords.value
    .filter((record) => Number(record.task_id) === taskId)
    .sort((a, b) => Number(a.id ?? 0) - Number(b.id ?? 0))
}

function detailWaybillKey(detail: StandardDetailRecord): string {
  const values = detail.field_values ?? {}
  const candidates = [values.raw_document_id, values.logistics_no, values.order_no]
  const key = candidates.find((value) => value !== null && value !== undefined && value !== '')
  return key ? String(key) : `detail-${detail.id}`
}

function waybillCountForTask(taskId: number): number {
  const keys = new Set(detailsForTask(taskId).map(detailWaybillKey))
  return keys.size
}

function statusType(status: string) {
  if (status === 'collecting') return 'success'
  if (status === 'completed') return 'info'
  if (status === 'failed') return 'danger'
  return 'warning'
}

function runtimeStatusLabel(status: unknown): string {
  if (status === 'listening') return '监听中'
  if (status === 'checking') return '仅检查'
  if (status === 'stopped') return '已停止'
  if (status === 'stale') return '心跳超时'
  return textValue(status, '未知')
}

function formatRawPayload(record: ApiRecord): string {
  const raw = record.raw_payload
  if (typeof raw !== 'string') return textValue(raw)
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function collectorNameFor(record: ApiRecord): string {
  const collectorId = Number(record.collector_id)
  if (!collectorId) return ''
  return collectors.value.find((collector) => collector.id === collectorId)?.collector_name ?? ''
}

function machineNameFor(record: ApiRecord): string {
  return textValue(record.source_machine, '')
}

function collectorDisplayName(record: ApiRecord): string {
  return collectorNameFor(record) || machineNameFor(record) || '未知采集器'
}

function componentLabel(component: unknown): string {
  if (component === 'cloud-print-client') return '抖店打印组件'
  if (component === 'cainiao-cnprint') return '菜鸟打印组件'
  return textValue(component, '未知组件')
}

function sourceLabel(record: ApiRecord): string {
  return `${collectorDisplayName(record)} / ${componentLabel(record.source_component)}`
}

function uniqueText(values: string[]): string {
  const unique = [...new Set(values.filter(Boolean))]
  return unique.length ? unique.join('、') : '-'
}

function groupCollectors(group: RawRecordGroup): string {
  return uniqueText(group.records.map((record) => collectorDisplayName(record)))
}

function groupComponents(group: RawRecordGroup): string {
  return uniqueText(group.records.map((record) => componentLabel(record.source_component)))
}

function groupParseStatus(group: RawRecordGroup): string {
  const statuses = [...new Set(group.records.map((record) => textValue(record.status)))]
  if (statuses.length === 1) return statuses[0]
  return statuses.join('、')
}

function groupFormats(group: RawRecordGroup): string {
  return uniqueText(group.records.map((record) => textValue(record.payload_format)))
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [status, tasks, records, details] = await Promise.all([
      getCollectorControlStatus(),
      getRecords('/capture-tasks?limit=2000'),
      getRecords('/raw-capture-records?limit=2000'),
      getRecords('/standard-details?limit=2000'),
    ])
    collectors.value = status.collectors
    activeTask.value = status.active_task
    captureTasks.value = tasks as CaptureTaskRecord[]
    rawRecords.value = records
    standardDetails.value = details as StandardDetailRecord[]
  } catch (err) {
    error.value = err instanceof Error ? err.message : '采集信息加载失败'
  } finally {
    loading.value = false
  }
}

async function startCurrentCapture() {
  actionLoading.value = true
  error.value = ''
  try {
    activeTask.value = await startCapture({ name: `业务采集 ${new Date().toLocaleString()}` })
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '开始采集失败'
  } finally {
    actionLoading.value = false
  }
}

async function stopCurrentCapture() {
  actionLoading.value = true
  error.value = ''
  try {
    activeTask.value = await stopCapture(activeTask.value?.id)
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '结束采集失败'
  } finally {
    actionLoading.value = false
  }
}

async function downloadTaskDocument(task: CaptureTaskRecord, kind: 'raw' | 'standard') {
  downloadingKey.value = `${task.id}-${kind}`
  error.value = ''
  try {
    const { blob, filename } = await downloadCaptureTaskDocument(task.id, kind)
    saveBlob(blob, filename)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '采集任务文档下载失败'
  } finally {
    downloadingKey.value = ''
  }
}

watch(() => session.currentWorkspaceId, load)
onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>采集记录</h1>
      <p>业务页面统一控制当前工作空间下的采集器，本轮打印结束后原始内容进入系统处理。</p>
    </div>
    <el-button :icon="Right" type="primary" @click="router.push('/waybill-batches')">
      进入面单批次
    </el-button>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="stat-grid">
    <div class="stat-tile">
      <span>本轮状态</span>
      <strong>{{ captureStatus }}</strong>
      <small>{{ activeTask ? `任务 ${activeTask.id}` : '当前没有进行中的采集任务' }}</small>
    </div>
    <div class="stat-tile">
      <span>在线采集器</span>
      <strong>{{ onlineCount }}</strong>
      <small>当前工作区在线业务机</small>
    </div>
    <div class="stat-tile">
      <span>监听中采集器</span>
      <strong>{{ listeningCount }}</strong>
      <small>启动监听后才会采集打印任务</small>
    </div>
    <div class="stat-tile">
      <span>本轮面单</span>
      <strong>{{ activeTaskWaybillCount }}</strong>
      <small>已整理成可处理面单的数量</small>
    </div>
    <div class="stat-tile">
      <span>累计监听批次</span>
      <strong>{{ captureTasks.length }}</strong>
      <small>一次开始到结束为一个采集批次</small>
    </div>
  </section>

  <section class="work-surface">
    <h2>本轮采集控制</h2>
    <div class="capture-control-bar">
      <div class="capture-status-cell">
        <el-tag class="capture-status-tag" :type="activeTask ? 'success' : 'info'">{{ captureStatus }}</el-tag>
      </div>
      <div class="capture-button-row">
        <el-button
          class="capture-action-button"
          :disabled="Boolean(activeTask)"
          :icon="VideoPlay"
          :loading="actionLoading"
          type="primary"
          @click="startCurrentCapture"
        >
          开始采集
        </el-button>
        <el-button
          class="capture-action-button"
          :disabled="!activeTask"
          :icon="CircleClose"
          :loading="actionLoading"
          type="danger"
          plain
          @click="stopCurrentCapture"
        >
          结束采集
        </el-button>
        <el-button class="capture-action-button" :icon="Refresh" :loading="loading" plain @click="load">
          刷新状态
        </el-button>
      </div>
    </div>
    <div v-if="activeTask" class="capture-summary">
      <span>任务名：{{ activeTask.name }}</span>
      <span>开始：{{ formatDateTime(activeTask.started_at) }}</span>
      <span>面单：{{ activeTaskWaybillCount }} 单</span>
    </div>
  </section>

  <section class="workflow-grid">
    <div class="work-surface">
      <h2><el-icon><Connection /></el-icon> 业务机采集器</h2>
      <el-table :data="collectors" stripe>
        <el-table-column label="采集器" prop="collector_name" />
        <el-table-column label="来源机器" prop="source_machine" />
        <el-table-column label="状态" prop="online_status" width="120">
          <template #default="{ row }">
            <el-tag :type="row.online_status === 'online' ? 'success' : 'info'">
              {{ row.online_status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="运行" width="120">
          <template #default="{ row }">
            <el-tag :type="row.status_payload?.runtime_status === 'listening' ? 'success' : 'warning'">
              {{ runtimeStatusLabel(row.status_payload?.runtime_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后心跳" width="190">
          <template #default="{ row }">
            {{ formatDateTime(row.last_heartbeat_at) }}
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="work-surface">
      <h2>最近采集任务</h2>
      <el-table :data="latestTasks" height="260" stripe>
        <el-table-column label="ID" prop="id" width="80" />
        <el-table-column label="任务名" prop="name" />
        <el-table-column label="状态" prop="status" width="120">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="面单" width="90">
          <template #default="{ row }">
            {{ waybillCountForTask(row.id) }}
          </template>
        </el-table-column>
        <el-table-column label="文档" width="210">
          <template #default="{ row }">
            <el-button
              :icon="Download"
              :loading="downloadingKey === `${row.id}-raw`"
              link
              type="primary"
              @click="downloadTaskDocument(row, 'raw')"
            >
              原文
            </el-button>
            <el-button
              :icon="Download"
              :loading="downloadingKey === `${row.id}-standard`"
              link
              type="primary"
              @click="downloadTaskDocument(row, 'standard')"
            >
              整理
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </section>

  <section class="work-surface">
    <h2><el-icon><Document /></el-icon> 原始采集内容</h2>
    <el-table v-if="latestRawRecordGroups.length" :data="latestRawRecordGroups" height="430" stripe>
      <el-table-column type="expand">
        <template #default="{ row: group }">
          <div class="raw-detail">
            <div v-for="record in group.records" :key="record.id" class="raw-record-block">
              <div class="detail-line">
                <span>原始记录 {{ record.id }}</span>
                <strong>{{ sourceLabel(record) }}</strong>
              </div>
              <pre class="raw-payload">{{ formatRawPayload(record) }}</pre>
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="批次ID" width="90">
        <template #default="{ row: group }">{{ group.task.id }}</template>
      </el-table-column>
      <el-table-column label="采集任务" min-width="220">
        <template #default="{ row: group }">
          <strong>{{ group.task.name }}</strong>
          <small class="muted-line">原始打印任务 {{ group.records.length }} 条</small>
        </template>
      </el-table-column>
      <el-table-column label="来源组件" min-width="160">
        <template #default="{ row: group }">{{ groupComponents(group) }}</template>
      </el-table-column>
      <el-table-column label="采集器" min-width="180">
        <template #default="{ row: group }">{{ groupCollectors(group) }}</template>
      </el-table-column>
      <el-table-column label="面单" width="90">
        <template #default="{ row: group }">{{ waybillCountForTask(group.task.id) }}</template>
      </el-table-column>
      <el-table-column label="格式" width="90">
        <template #default="{ row: group }">{{ groupFormats(group) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row: group }">{{ groupParseStatus(group) }}</template>
      </el-table-column>
    </el-table>
    <el-empty v-else description="采集器保存原始记录后会在这里显示" />
  </section>
</template>
