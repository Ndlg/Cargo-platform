<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Connection, Delete, Download, Key, Monitor, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  deleteRecord,
  downloadCollectorClientZip,
  getCollectorControlStatus,
  registerCollector,
  type CollectorRecord,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

type AdapterRow = {
  key: string
  displayName: string
  status: string
  dbPath: string
  taskCount: string
  maxRowid: string
  error: string
}

const session = useSessionStore()
const collectors = ref<CollectorRecord[]>([])
const loading = ref(false)
const downloadingClient = ref(false)
const registeringCollector = ref(false)
const deletingCollectorId = ref<number | null>(null)
const tokenDialogVisible = ref(false)
const generatedToken = ref('')
const generatedCollector = ref<CollectorRecord | null>(null)
const error = ref('')

const onlineCount = computed(() => collectors.value.filter((item) => item.online_status === 'online').length)
const listeningCount = computed(
  () => collectors.value.filter((item) => item.status_payload?.runtime_status === 'listening').length,
)
const readyAdapterCount = computed(() =>
  collectors.value.reduce((total, collector) => {
    return total + adapterRows(collector).filter((adapter) => adapter.status === 'ready').length
  }, 0),
)
const collectorLaunchBaseUrl = computed(() => {
  const origin = window.location.origin
  if (!origin || origin.includes('localhost') || origin.includes('127.0.0.1')) return 'http://服务器IP:5173'
  return origin
})
const collectorLaunchCommand = computed(() => {
  if (!generatedToken.value) return ''
  return `Cargo Platform 采集器.exe --base-url "${collectorLaunchBaseUrl.value}" --token "${generatedToken.value}" --loop`
})

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

function adapterRows(collector: CollectorRecord): AdapterRow[] {
  const statusMap = collector.status_payload?.adapter_status ?? {}
  return Object.entries(statusMap).map(([key, value]) => ({
    key,
    displayName: textValue(value.display_name, key),
    status: textValue(value.status, 'unknown'),
    dbPath: textValue(value.db_path),
    taskCount: textValue(value.task_count),
    maxRowid: textValue(value.max_rowid),
    error: textValue(value.error, ''),
  }))
}

function tagType(status: string) {
  if (status === 'ready') return 'success'
  if (status === 'missing') return 'info'
  if (status === 'error' || status === 'unsupported') return 'danger'
  return 'warning'
}

function collectorStatusType(status: string) {
  return status === 'online' ? 'success' : 'info'
}

function runtimeStatusLabel(status: unknown): string {
  if (status === 'listening') return '监听中'
  if (status === 'checking') return '仅检查'
  if (status === 'stopped') return '已停止'
  if (status === 'stale') return '心跳超时'
  return textValue(status, '未知')
}

function runtimeStatusType(status: unknown) {
  if (status === 'stale') return 'info'
  return status === 'listening' ? 'success' : 'warning'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const status = await getCollectorControlStatus()
    collectors.value = status.collectors
  } catch (err) {
    error.value = err instanceof Error ? err.message : '采集器连接加载失败'
  } finally {
    loading.value = false
  }
}

async function removeCollector(row: CollectorRecord) {
  try {
    await ElMessageBox.confirm(
      `确定移除采集器“${row.collector_name}”吗？移除后需要在后台重新生成 token 才能连接。`,
      '移除采集器',
      { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  deletingCollectorId.value = row.id
  error.value = ''
  try {
    await deleteRecord(`/collectors/${row.id}`)
    ElMessage.success('采集器已移除')
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '采集器移除失败'
  } finally {
    deletingCollectorId.value = null
  }
}

async function downloadCollectorClient() {
  downloadingClient.value = true
  error.value = ''
  try {
    await downloadCollectorClientZip()
    ElMessage.success('采集器下载已开始')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '采集器下载失败'
  } finally {
    downloadingClient.value = false
  }
}

async function generateCollectorToken() {
  registeringCollector.value = true
  error.value = ''
  try {
    const result = await registerCollector({
      collector_name: 'Cargo Platform 采集器',
      client_version: 'single-exe-token-collector-20260614',
    })
    generatedToken.value = result.collector_token
    generatedCollector.value = result.collector
    tokenDialogVisible.value = true
    ElMessage.success('采集器 token 已生成')
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '采集器 token 生成失败'
  } finally {
    registeringCollector.value = false
  }
}

async function copyLaunchCommand() {
  if (!collectorLaunchCommand.value) return
  await navigator.clipboard.writeText(collectorLaunchCommand.value)
  ElMessage.success('启动命令已复制')
}

watch(() => session.currentWorkspaceId, load)
onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>采集连接</h1>
      <p>业务机使用后台生成的采集器 token 连接，不在业务机保存系统账号密码。</p>
    </div>
    <div class="header-actions">
      <el-button :icon="Key" :loading="registeringCollector" type="primary" @click="generateCollectorToken">
        生成 token
      </el-button>
      <el-button :icon="Download" :loading="downloadingClient" type="success" @click="downloadCollectorClient">
        下载采集器
      </el-button>
      <el-button :icon="Refresh" :loading="loading" type="primary" plain @click="load">
        刷新状态
      </el-button>
    </div>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="stat-grid">
    <div class="stat-tile">
      <span>已连接业务机</span>
      <strong>{{ collectors.length }}</strong>
      <small>当前工作区绑定的采集器数量</small>
    </div>
    <div class="stat-tile">
      <span>在线采集器</span>
      <strong>{{ onlineCount }}</strong>
      <small>最近有心跳的业务机</small>
    </div>
    <div class="stat-tile">
      <span>监听中采集器</span>
      <strong>{{ listeningCount }}</strong>
      <small>真正会拉取采集任务的业务机</small>
    </div>
    <div class="stat-tile">
      <span>可用打印组件</span>
      <strong>{{ readyAdapterCount }}</strong>
      <small>状态为 ready 的本机组件</small>
    </div>
    <div class="stat-tile">
      <span>绑定方式</span>
      <strong>Token</strong>
      <small>后台生成后分配给业务机</small>
    </div>
  </section>

  <section class="workflow-grid">
    <div class="work-surface">
      <h2><el-icon><Connection /></el-icon> 已连接采集器</h2>
      <el-table v-if="collectors.length" :data="collectors" stripe>
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="collector-detail">
              <div class="detail-line">
                <span>最后状态上报</span>
                <strong>{{ formatDateTime(row.status_payload?.received_at) }}</strong>
              </div>
              <div class="detail-line">
                <span>运行状态</span>
                <strong>{{ runtimeStatusLabel(row.status_payload?.runtime_status) }}</strong>
              </div>
              <div class="detail-line">
                <span>本地队列</span>
                <strong>{{ textValue(row.status_payload?.queue_size, '0') }}</strong>
              </div>
              <div class="detail-line">
                <span>最近错误</span>
                <strong>{{ textValue(row.status_payload?.last_error, '无') }}</strong>
              </div>

              <div class="adapter-list">
                <div v-for="adapter in adapterRows(row)" :key="adapter.key" class="adapter-row">
                  <div>
                    <strong>{{ adapter.displayName }}</strong>
                    <p>{{ adapter.dbPath }}</p>
                    <p v-if="adapter.error" class="error-text">{{ adapter.error }}</p>
                  </div>
                  <div class="adapter-metrics">
                    <el-tag :type="tagType(adapter.status)">{{ adapter.status }}</el-tag>
                    <span>task {{ adapter.taskCount }}</span>
                    <span>rowid {{ adapter.maxRowid }}</span>
                  </div>
                </div>
                <el-empty v-if="!adapterRows(row).length" description="采集器尚未上报本机打印组件状态" />
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="采集器" prop="collector_name" />
        <el-table-column label="设备标识" prop="collector_id" />
        <el-table-column label="来源机器" prop="source_machine" />
        <el-table-column label="版本" prop="client_version" width="180" />
        <el-table-column label="状态" prop="online_status" width="120">
          <template #default="{ row }">
            <el-tag :type="collectorStatusType(row.online_status)">
              {{ row.online_status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="运行" width="120">
          <template #default="{ row }">
            <el-tag :type="runtimeStatusType(row.status_payload?.runtime_status)">
              {{ runtimeStatusLabel(row.status_payload?.runtime_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后心跳" width="190">
          <template #default="{ row }">
            {{ formatDateTime(row.last_heartbeat_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button
              :icon="Delete"
              :loading="deletingCollectorId === row.id"
              link
              type="danger"
              @click="removeCollector(row)"
            >
              移除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="还没有业务机采集器连接" />
    </div>

    <div class="work-surface">
      <h2><el-icon><Monitor /></el-icon> 业务机配置方式</h2>
      <div class="tag-list">
        <el-tag type="info">单 exe</el-tag>
        <el-tag type="info">token 参数</el-tag>
        <el-tag type="info">无黑框后台运行</el-tag>
      </div>
      <p class="muted-text">
        先生成 token，再下载采集器；下载包只包含 Cargo Platform 采集器.exe 和 参数说明.txt。
      </p>
      <p class="muted-text">
        业务机按参数启动后会自动进入当前工作区，设备标识自动使用 Windows 机器名。
      </p>
      <p class="muted-text">
        服务器临时断开时采集器会等待重连；token 被移除后需要重新生成。
      </p>
    </div>
  </section>

  <el-dialog v-model="tokenDialogVisible" title="采集器 token" width="720px">
    <div class="token-dialog-body">
      <div class="detail-line">
        <span>后台名称</span>
        <strong>{{ generatedCollector?.collector_name || 'Cargo Platform 采集器' }}</strong>
      </div>
      <div class="detail-line">
        <span>设备标识</span>
        <strong>启动后自动使用业务机机器名</strong>
      </div>
      <el-input :model-value="generatedToken" readonly type="textarea" :rows="3" />
      <el-input :model-value="collectorLaunchCommand" readonly type="textarea" :rows="3" />
    </div>
    <template #footer>
      <el-button @click="tokenDialogVisible = false">关闭</el-button>
      <el-button type="primary" @click="copyLaunchCommand">复制启动命令</el-button>
    </template>
  </el-dialog>
</template>
