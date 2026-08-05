<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Connection, Delete, Download, Monitor, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  deleteRecord,
  downloadCollectorClientZip,
  getCollectorControlStatus,
  repairCollectorConnection,
  registerCollector,
  type CollectorClientPackageStatus,
  type CollectorRecord,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

type AdapterRow = {
  key: string
  displayName: string
  status: string
  dbPath: string
  localProgress: string
  error: string
}

const session = useSessionStore()
const collectors = ref<CollectorRecord[]>([])
const loading = ref(false)
const downloadingClient = ref(false)
const registeringCollector = ref(false)
const repairingCollectorId = ref<number | null>(null)
const deletingCollectorId = ref<number | null>(null)
const installDialogVisible = ref(false)
const connectionCode = ref('')
const error = ref('')
const collectorClient = ref<CollectorClientPackageStatus | null>(null)

const onlineCount = computed(() => collectors.value.filter((item) => item.online_status === 'online').length)
const listeningCount = computed(
  () => collectors.value.filter((item) => item.status_payload?.runtime_status === 'listening').length,
)
const readyAdapterCount = computed(() =>
  collectors.value.reduce((total, collector) => {
    return total + adapterRows(collector).filter((adapter) => adapter.status === 'ready').length
  }, 0),
)
const collectorClientReady = computed(() => collectorClient.value?.release_available === true)

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
    localProgress: value.max_rowid ? '已记录' : '无',
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

function reconnectReasonLabel(value: unknown): string {
  const labels: Record<string, string> = {
    network: '网络中断',
    http: '服务端暂时不可用',
    auth: '采集器凭证失效',
    sqlite: '打印数据库暂时不可读',
    state_save: '本地采集状态保存失败',
    unexpected: '采集轮询出现未分类异常，正在重试',
  }
  return labels[String(value ?? '')] ?? '无'
}

function runtimeStatusType(status: unknown) {
  if (status === 'stale') return 'info'
  return status === 'listening' ? 'success' : 'warning'
}

function collectorNeedsRepair(collector: CollectorRecord): boolean {
  const status = collector.status_payload
  return status?.last_reconnect_reason === 'auth'
    || status?.runtime_status === 'cleaned'
    || status?.stale_reason === 'heartbeat_cleanup'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const status = await getCollectorControlStatus()
    collectors.value = status.collectors
    collectorClient.value = status.collector_client ?? null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '采集器连接加载失败'
  } finally {
    loading.value = false
  }
}

async function removeCollector(row: CollectorRecord) {
  try {
    await ElMessageBox.confirm(
      `确定移除采集器“${row.collector_name}”吗？移除后需要重新添加业务机才能连接。`,
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
  if (!collectorClientReady.value) {
    error.value = collectorClient.value?.message || '采集器发布包尚未就绪'
    return
  }
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

async function addCollector() {
  registeringCollector.value = true
  error.value = ''
  try {
    const result = await registerCollector({
      collector_name: '',
      client_version: 'single-exe-token-collector-20260614',
      public_base_url: window.location.origin,
    })
    connectionCode.value = result.connection_code
    installDialogVisible.value = true
    ElMessage.success('业务机连接码已生成')
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '业务机连接码生成失败'
  } finally {
    registeringCollector.value = false
  }
}

async function repairCollector(row: CollectorRecord) {
  try {
    await ElMessageBox.confirm(
      `为“${row.collector_name}”生成新的连接码会使旧凭证失效。仅在凭证失效或采集器已被清理时修复，网络暂时离线不需要修复。`,
      '修复连接',
      { type: 'warning', confirmButtonText: '生成新连接码', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  repairingCollectorId.value = row.id
  error.value = ''
  try {
    const result = await repairCollectorConnection(row.id, { public_base_url: window.location.origin })
    connectionCode.value = result.connection_code
    installDialogVisible.value = true
    ElMessage.success('新的连接码已生成')
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '修复连接失败'
  } finally {
    repairingCollectorId.value = null
  }
}

async function copyConnectionCode() {
  if (!connectionCode.value) return
  await navigator.clipboard.writeText(connectionCode.value)
  ElMessage.success('连接码已复制')
}

watch(() => session.currentWorkspaceId, load)
onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>采集连接</h1>
      <p>下载并安装采集器后，只需粘贴一次连接码，不在业务机保存系统账号密码。</p>
    </div>
    <div class="header-actions">
      <el-button :loading="registeringCollector" type="primary" @click="addCollector">
        添加业务机
      </el-button>
      <el-button
        :disabled="!collectorClientReady"
        :icon="Download"
        :loading="downloadingClient"
        type="success"
        @click="downloadCollectorClient"
      >
        下载安装器
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
      <span>采集器发布包</span>
      <strong>{{ collectorClientReady ? '就绪' : '缺失' }}</strong>
      <small>{{ collectorClient?.package_version || '等待状态加载' }}</small>
    </div>
  </section>

  <el-alert
    v-if="collectorClient && !collectorClientReady"
    :closable="false"
    :title="collectorClient.message"
    type="warning"
  />

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
                <strong>{{ textValue(row.status_payload?.queue_size, '未知') }}</strong>
              </div>
              <div class="detail-line">
                <span>最后成功上传</span>
                <strong>{{ formatDateTime(row.status_payload?.last_upload_at) }}</strong>
              </div>
              <div class="detail-line">
                <span>最近重连原因</span>
                <strong>{{ reconnectReasonLabel(row.status_payload?.last_reconnect_reason) }}</strong>
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
                    <span>本地进度 {{ adapter.localProgress }}</span>
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
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="collectorNeedsRepair(row)"
              :loading="repairingCollectorId === row.id"
              link
              type="warning"
              @click="repairCollector(row)"
            >
              修复连接
            </el-button>
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
        <el-tag type="info">连接码登记</el-tag>
        <el-tag type="info">无黑框后台运行</el-tag>
        <el-tag :type="collectorClientReady ? 'success' : 'warning'">
          {{ collectorClientReady ? '发布包就绪' : '发布包缺失' }}
        </el-tag>
      </div>
      <p class="muted-text">
        添加业务机后下载并运行安装器，再粘贴连接码即可完成安装。
      </p>
      <p class="muted-text">
        采集器会自动进入当前工作区，设备标识自动使用 Windows 机器名。
      </p>
      <p class="muted-text">
        服务器临时断开时采集器会等待重连；只有凭证失效或采集器被清理时才需要修复连接。
      </p>
    </div>
  </section>

  <el-dialog v-model="installDialogVisible" title="添加业务机" width="560px">
    <div class="token-dialog-body">
      <p class="muted-text">1. 下载采集器安装器。</p>
      <el-button :disabled="!collectorClientReady" :loading="downloadingClient" type="success" @click="downloadCollectorClient">
        下载安装器
      </el-button>
      <p class="muted-text">2. 双击运行安装器。</p>
      <p class="muted-text">3. 在安装器中粘贴连接码完成登记。</p>
      <el-input :model-value="connectionCode" readonly />
    </div>
    <template #footer>
      <el-button @click="installDialogVisible = false">关闭</el-button>
      <el-button type="primary" @click="copyConnectionCode">复制连接码</el-button>
    </template>
  </el-dialog>
</template>
