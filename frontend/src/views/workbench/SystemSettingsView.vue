<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Box, Delete, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  archiveCaptureData,
  deleteArchivedCaptureData,
  getDataMaintenanceSummary,
  type DataMaintenanceSummary,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

const session = useSessionStore()
const summary = ref<DataMaintenanceSummary | null>(null)
const loading = ref(false)
const archiveLoading = ref(false)
const deleteLoading = ref(false)
const error = ref('')
const archiveDays = ref<number | null>(null)
const deleteDays = ref<number | null>(null)
const deleteConfirmText = ref('')

const active = computed(() => summary.value?.active ?? {
  capture_tasks: 0,
  archive_ready_tasks: 0,
  raw_records: 0,
  standard_details: 0,
})
const archived = computed(() => summary.value?.archived ?? {
  capture_tasks: 0,
  raw_records: 0,
  standard_details: 0,
})
const archiveReadyTasks = computed(() => active.value.archive_ready_tasks ?? 0)
const canDeleteArchived = computed(() => archived.value.capture_tasks > 0 && deleteConfirmText.value.trim() === '删除归档数据')

function maintenancePayload(days: number | null) {
  return { days_before: Number.isInteger(days) && Number(days) >= 0 ? Number(days) : null }
}

async function loadSummary() {
  loading.value = true
  error.value = ''
  try {
    summary.value = await getDataMaintenanceSummary()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '系统设置加载失败'
  } finally {
    loading.value = false
  }
}

async function archiveData() {
  if (archiveReadyTasks.value <= 0) {
    ElMessage.info('当前没有可归档的已结束采集任务。')
    return
  }
  await ElMessageBox.confirm(
    '归档后，这些采集回传数据会从面单解析、商品匹配和导出候选中隐藏，但仍可在归档统计中保留。',
    '归档采集数据',
    {
      confirmButtonText: '归档',
      cancelButtonText: '取消',
      type: 'warning',
    },
  )
  archiveLoading.value = true
  error.value = ''
  try {
    const result = await archiveCaptureData(maintenancePayload(archiveDays.value))
    summary.value = result.summary
    ElMessage.success(`已归档 ${result.archived_capture_tasks ?? 0} 个采集任务。`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '归档失败'
  } finally {
    archiveLoading.value = false
  }
}

async function deleteData() {
  if (!canDeleteArchived.value) {
    ElMessage.warning('请输入确认文字后再删除归档数据。')
    return
  }
  await ElMessageBox.confirm(
    '删除后，归档采集任务、原始回传内容和对应面单解析结果会从系统中移除。商品、SKU、档口和规则不会被删除。',
    '删除归档数据',
    {
      confirmButtonText: '确认删除',
      cancelButtonText: '取消',
      type: 'error',
    },
  )
  deleteLoading.value = true
  error.value = ''
  try {
    const result = await deleteArchivedCaptureData({
      ...maintenancePayload(deleteDays.value),
      confirm_text: deleteConfirmText.value,
    })
    summary.value = result.summary
    deleteConfirmText.value = ''
    ElMessage.success(`已删除 ${result.deleted_capture_tasks ?? 0} 个归档采集任务。`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '删除归档数据失败'
  } finally {
    deleteLoading.value = false
  }
}

watch(() => session.currentWorkspaceId, loadSummary)
onMounted(loadSummary)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>系统设置</h1>
      <p>维护当前工作空间的系统级选项。这里先集中处理采集器回传数据的归档和清理。</p>
    </div>
    <el-button :icon="Refresh" :loading="loading" plain @click="loadSummary">刷新</el-button>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="stat-grid">
    <div class="stat-tile">
      <span>活跃采集任务</span>
      <strong>{{ active.capture_tasks }}</strong>
      <small>{{ archiveReadyTasks }} 个已结束任务可归档</small>
    </div>
    <div class="stat-tile">
      <span>活跃采集诊断</span>
      <strong>{{ active.raw_records }}</strong>
      <small>采集器回传的原文诊断数据</small>
    </div>
    <div class="stat-tile">
      <span>活跃面单解析结果</span>
      <strong>{{ active.standard_details }}</strong>
      <small>参与商品匹配和导出的订单行基础数据</small>
    </div>
    <div class="stat-tile">
      <span>已归档任务</span>
      <strong>{{ archived.capture_tasks }}</strong>
      <small>{{ archived.raw_records }} 条采集明细已归档</small>
    </div>
  </section>

  <section class="settings-grid">
    <article class="work-surface">
      <div class="panel-heading">
        <div>
          <h2><el-icon><Box /></el-icon> 归档采集数据</h2>
          <p>把已结束的采集批次移出日常维护范围，减少面单解析和商品匹配页面的数据量。</p>
        </div>
      </div>
      <div class="setting-form">
        <label>
          只归档多少天前的数据
          <el-input-number v-model="archiveDays" :min="0" :max="3650" controls-position="right" placeholder="留空为全部" />
        </label>
        <small>留空表示归档所有已结束采集任务；正在采集中的任务不会被归档。</small>
        <el-button
          :disabled="archiveReadyTasks <= 0"
          :icon="Box"
          :loading="archiveLoading"
          type="primary"
          @click="archiveData"
        >
          归档已结束采集数据
        </el-button>
      </div>
    </article>

    <article class="work-surface danger-surface">
      <div class="panel-heading">
        <div>
          <h2><el-icon><Delete /></el-icon> 删除归档数据</h2>
          <p>只删除已经归档的采集回传数据。商品、SKU、档口和识别规则包不会被删除。</p>
        </div>
      </div>
      <div class="setting-form">
        <label>
          只删除多少天前归档的数据
          <el-input-number v-model="deleteDays" :min="0" :max="3650" controls-position="right" placeholder="留空为全部" />
        </label>
        <label>
          确认文字
          <el-input v-model="deleteConfirmText" placeholder="输入：删除归档数据" />
        </label>
        <small>删除会移除归档采集任务、原始回传内容和对应面单解析结果，保留业务配置。</small>
        <el-button
          :disabled="!canDeleteArchived"
          :icon="Delete"
          :loading="deleteLoading"
          type="danger"
          @click="deleteData"
        >
          删除归档数据
        </el-button>
      </div>
    </article>
  </section>
</template>

<style scoped>
.settings-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}

.panel-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.panel-heading h2 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 8px;
}

.panel-heading p {
  margin: 0;
  color: #64748b;
}

.setting-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 20px;
}

.setting-form label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: #334155;
  font-weight: 600;
}

.setting-form small {
  color: #64748b;
}

.danger-surface {
  border-color: #fecaca;
}

@media (max-width: 980px) {
  .settings-grid {
    grid-template-columns: 1fr;
  }
}
</style>
