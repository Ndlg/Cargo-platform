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
const loading = ref(false)
const actionLoading = ref(false)
const downloadingKey = ref('')
const error = ref('')

const captureStatus = computed(() => (activeTask.value ? '采集中' : '待开始'))
const enrichedActiveTask = computed(() => {
  if (!activeTask.value) return null
  return captureTasks.value.find((task) => task.id === activeTask.value?.id) ?? activeTask.value
})
const activeTaskWaybillCount = computed(() => {
  if (!enrichedActiveTask.value) return 0
  return waybillCountForTask(enrichedActiveTask.value)
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

function rawRecordsForTask(taskId: number): ApiRecord[] {
  return rawRecords.value
    .filter((record) => Number(record.task_id) === taskId)
    .sort((a, b) => Number(a.id ?? 0) - Number(b.id ?? 0))
}

function parsedRawPayload(record: ApiRecord): Record<string, unknown> | null {
  const raw = record.raw_payload
  if (typeof raw !== 'string') return raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : null
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null
  } catch {
    return null
  }
}

function waybillCountForRawRecord(record: ApiRecord): number {
  const payload = parsedRawPayload(record)
  const task = payload?.task
  const documents = task && typeof task === 'object' ? (task as Record<string, unknown>).documents : null
  if (Array.isArray(documents) && documents.length > 0) return documents.length
  return record.raw_payload || record.source_columns ? 1 : 0
}

function numericCount(value: unknown): number | null {
  const count = Number(value)
  return Number.isFinite(count) && count >= 0 ? count : null
}

function waybillCountForTask(task: CaptureTaskRecord): number {
  const backendCount = numericCount(task.waybill_count ?? task.parent_waybill_count)
  if (backendCount !== null) return backendCount
  return rawRecordsForTask(task.id).reduce((total, record) => total + waybillCountForRawRecord(record), 0)
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

function formatJsonValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
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
    const [status, tasks] = await Promise.all([
      getCollectorControlStatus(),
      getRecords('/capture-tasks?limit=2000'),
    ])
    collectors.value = status.collectors
    activeTask.value = status.active_task
    captureTasks.value = tasks as CaptureTaskRecord[]
    const taskIds = [...captureTasks.value]
      .sort((a, b) => b.id - a.id)
      .slice(0, 6)
      .map((task) => Number(task.id))
      .filter((taskId) => Number.isFinite(taskId) && taskId > 0)
    const recordGroups = await Promise.all(
      taskIds.map((taskId) => getRecords(`/raw-capture-records?task_id=${taskId}&limit=500`)),
    )
    rawRecords.value = recordGroups.flat()
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

async function downloadTaskDocument(task: CaptureTaskRecord, kind: 'raw') {
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
      <p>业务页面统一控制当前工作空间下的采集器，本轮面单采集结束后进入解析和整理。</p>
    </div>
    <el-button :icon="Right" type="primary" @click="router.push('/waybill-batches')">
      进入面单解析
    </el-button>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="stat-grid">
    <div class="stat-tile">
      <span>本轮状态</span>
      <strong>{{ captureStatus }}</strong>
      <small>{{ activeTask ? '当前正在接收面单' : '当前没有进行中的采集任务' }}</small>
    </div>
    <div class="stat-tile">
      <span>在线采集器</span>
      <strong>{{ onlineCount }}</strong>
      <small>当前工作区在线业务机</small>
    </div>
    <div class="stat-tile">
      <span>监听中采集器</span>
      <strong>{{ listeningCount }}</strong>
      <small>启动监听后才会采集面单任务</small>
    </div>
    <div class="stat-tile">
      <span>本轮面单数量</span>
      <strong>{{ activeTaskWaybillCount }}</strong>
      <small>已读取到的面单张数</small>
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
      <span>面单数量：{{ activeTaskWaybillCount }} 张</span>
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
        <el-table-column label="采集批次" prop="name" />
        <el-table-column label="状态" prop="status" width="120">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="面单数量" width="100">
          <template #default="{ row }">
            {{ waybillCountForTask(row) }}
          </template>
        </el-table-column>
        <el-table-column label="文档" width="120">
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
          </template>
        </el-table-column>
      </el-table>
    </div>
  </section>

  <section class="work-surface">
    <h2><el-icon><Document /></el-icon> 最近采集原文</h2>
    <p class="section-hint">这里只看最近采集批次的面单原文，用于排查采集器是否正常；平时主要看面单数量。</p>
    <el-table v-if="latestRawRecordGroups.length" :data="latestRawRecordGroups" height="430" stripe>
      <el-table-column type="expand">
        <template #default="{ row: group }">
          <div class="raw-detail">
            <div v-for="record in group.records" :key="record.id" class="raw-record-block">
              <div class="detail-line">
                <span>采集来源</span>
                <strong>{{ sourceLabel(record) }}</strong>
              </div>
              <div class="audit-grid">
                <div class="detail-line">
                  <span>文档ID</span>
                  <strong>{{ textValue(record.document_id) }}</strong>
                </div>
                <div class="detail-line">
                  <span>定位状态</span>
                  <strong>{{ record.source_index ? '已记录' : '无' }}</strong>
                </div>
                <div class="detail-line">
                  <span>采集时间</span>
                  <strong>{{ formatDateTime(record.captured_at) }}</strong>
                </div>
                <div class="detail-line">
                  <span>格式</span>
                  <strong>{{ textValue(record.payload_format) }}</strong>
                </div>
                <div class="detail-line audit-wide">
                  <span>去重键</span>
                  <strong>{{ textValue(record.dedupe_key) }}</strong>
                </div>
              </div>
              <span class="muted-line">采集器附带信息</span>
              <pre class="raw-payload compact">{{ formatJsonValue(record.source_columns) }}</pre>
              <span class="muted-line">采集原文</span>
              <pre class="raw-payload">{{ formatRawPayload(record) }}</pre>
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="采集批次" min-width="220">
        <template #default="{ row: group }">
          <strong>{{ group.task.name }}</strong>
          <small class="muted-line">面单 {{ waybillCountForTask(group.task) }} 张</small>
        </template>
      </el-table-column>
      <el-table-column label="来源组件" min-width="160">
        <template #default="{ row: group }">{{ groupComponents(group) }}</template>
      </el-table-column>
      <el-table-column label="采集器" min-width="180">
        <template #default="{ row: group }">{{ groupCollectors(group) }}</template>
      </el-table-column>
      <el-table-column label="面单数量" width="100">
        <template #default="{ row: group }">{{ waybillCountForTask(group.task) }}</template>
      </el-table-column>
      <el-table-column label="格式" width="90">
        <template #default="{ row: group }">{{ groupFormats(group) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row: group }">{{ groupParseStatus(group) }}</template>
      </el-table-column>
    </el-table>
    <el-empty v-else description="采集器保存面单原文后会在这里显示" />
  </section>
</template>
