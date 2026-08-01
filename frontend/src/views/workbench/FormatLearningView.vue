<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import {
  getFormatLearningQueue,
  getRecords,
  learnFormat,
  prepareFormatLearning,
  type CaptureTaskRecord,
  type FormatLearningPrepareResponse,
  type FormatLearningQueueItem,
  type FormatLearningQueueResponse,
  type FormatLearningResultResponse,
  type FormatLearningSelectedField,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'
import { selectCaptureRoundId } from './captureRoundSelection'
import {
  prepareLearningRows,
  type EditableLearningRow,
} from './formatLearning'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const tasks = ref<CaptureTaskRecord[]>([])
const selectedTaskId = ref<number | null>(null)
const includeAll = ref(false)
const queue = ref<FormatLearningQueueResponse | null>(null)
const prepared = ref<FormatLearningPrepareResponse | null>(null)
const editableRows = ref<EditableLearningRow[]>([])
const lastResult = ref<FormatLearningResultResponse | null>(null)
const loading = ref(false)
const preparingKey = ref('')
const saving = ref(false)
const error = ref('')

const items = computed(() => queue.value?.items ?? [])
const taskOptions = computed(() =>
  [...tasks.value]
    .sort((a, b) => b.id - a.id)
    .map((task, index) => ({
      value: task.id,
      label: `${index === 0 ? '最近一轮' : `上一轮 ${index}`}：${formatTime(task.started_at)} ${taskStatus(task.status)}`,
    })),
)

function emptyRow(): EditableLearningRow {
  return { product: '', sales_attr1: '', sales_attr2: '', quantity: 1, remark: '' }
}

function itemKey(item: FormatLearningQueueItem): string {
  return `${item.raw_record_id}:${item.document_sequence}:${item.parent_sequence}`
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

function taskStatus(value?: string | null): string {
  if (value === 'completed') return '已完成'
  if (value === 'collecting') return '采集中'
  return value || ''
}

function reasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    rule_pack_missing: '尚无识别规则',
    format_profile_missing: '陌生面单格式',
    format_profile_incomplete: '现有规则未完整生成商品行',
    profile_ambiguous: '多个规则产生冲突',
    missing_product: '未读到商品',
    missing_quantity: '未读到数量',
  }
  return labels[reason] ?? (reason || '已识别，可重新学习')
}

function fieldValues(field: FormatLearningSelectedField): string {
  return field.values
    .map((value) => {
      if (typeof value === 'string') return value
      if (value === null || value === undefined) return ''
      try {
        return JSON.stringify(value)
      } catch {
        return String(value)
      }
    })
    .filter(Boolean)
    .join('\n')
}

async function loadTasks() {
  const records = (await getRecords('/capture-tasks?limit=200')) as CaptureTaskRecord[]
  tasks.value = records
  selectedTaskId.value = selectCaptureRoundId(records, route.query.task_id)
}

async function loadQueue() {
  prepared.value = null
  editableRows.value = []
  lastResult.value = null
  if (!selectedTaskId.value) {
    queue.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    queue.value = await getFormatLearningQueue(selectedTaskId.value, includeAll.value)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '面单格式学习清单加载失败'
  } finally {
    loading.value = false
  }
}

async function selectItem(item: FormatLearningQueueItem) {
  if (!selectedTaskId.value || preparingKey.value) return
  preparingKey.value = itemKey(item)
  error.value = ''
  lastResult.value = null
  try {
    prepared.value = await prepareFormatLearning(selectedTaskId.value, {
      raw_record_id: item.raw_record_id,
      document_sequence: item.document_sequence,
      parent_sequence: item.parent_sequence,
    })
    const initialRows = item.rows.length ? item.rows : prepared.value.rows
    editableRows.value = initialRows.length
      ? initialRows.map((row) => ({ ...row }))
      : [emptyRow()]
  } catch (err) {
    prepared.value = null
    editableRows.value = []
    error.value = err instanceof Error ? err.message : '面单学习字段读取失败'
  } finally {
    preparingKey.value = ''
  }
}

function addRow() {
  editableRows.value.push(emptyRow())
}

function removeRow(index: number) {
  if (editableRows.value.length <= 1) return
  editableRows.value.splice(index, 1)
}

async function saveLearning() {
  if (!selectedTaskId.value || !prepared.value || saving.value) return
  const result = prepareLearningRows(editableRows.value)
  if (!result.ok) {
    ElMessage.warning(result.message)
    return
  }

  saving.value = true
  error.value = ''
  try {
    const learned = await learnFormat(selectedTaskId.value, {
      raw_record_id: prepared.value.raw_record_id,
      document_sequence: prepared.value.document_sequence,
      parent_sequence: prepared.value.parent_sequence,
      expected_evidence_sha256: prepared.value.evidence_sha256,
      rows: result.rows,
    })
    ElMessage.success(learned.message || '规则已保存并完成回放校验')
    prepared.value = null
    editableRows.value = []
    await loadQueue()
    lastResult.value = learned
  } catch (err) {
    error.value = err instanceof Error ? err.message : '识别规则保存失败'
  } finally {
    saving.value = false
  }
}

watch(
  () => session.currentWorkspaceId,
  async () => {
    await loadTasks()
    await loadQueue()
  },
)

onMounted(async () => {
  await loadTasks()
  await loadQueue()
})
</script>

<template>
  <section class="page-header">
    <div>
      <h1>面单格式学习</h1>
      <p>管理员只核对五个业务字段；系统负责生成规则、回放校验并重新解析受影响的采集轮次。</p>
    </div>
    <el-button :loading="loading" @click="loadQueue">刷新清单</el-button>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" show-icon type="error" />
  <el-alert
    v-else-if="lastResult"
    :closable="false"
    :title="lastResult.message"
    :description="lastResult.replay_summary ? `回放通过 ${lastResult.replay_summary.passed}/${lastResult.replay_summary.total}` : ''"
    show-icon
    type="success"
  />

  <section class="work-surface toolbar">
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
    <el-switch
      v-model="includeAll"
      active-text="显示全部面单，可修正已识别规则"
      @change="loadQueue"
    />
    <el-tag type="warning">{{ queue?.summary.learning_required_count ?? 0 }} 张待学习</el-tag>
  </section>

  <section class="learning-layout">
    <article class="work-surface queue-surface">
      <div class="section-heading">
        <div>
          <h2>面单清单</h2>
          <p>{{ includeAll ? '显示本轮全部面单' : '只显示当前规则无法稳定识别的面单' }}</p>
        </div>
        <el-tag effect="plain">{{ items.length }} 张</el-tag>
      </div>

      <el-empty v-if="!loading && !items.length" description="这一轮没有需要学习的面单" />
      <div v-loading="loading" class="queue-list">
        <button
          v-for="item in items"
          :key="itemKey(item)"
          class="queue-card"
          :class="{ active: prepared && itemKey(item) === `${prepared.raw_record_id}:${prepared.document_sequence}:${prepared.parent_sequence}` }"
          :aria-pressed="Boolean(prepared && itemKey(item) === `${prepared.raw_record_id}:${prepared.document_sequence}:${prepared.parent_sequence}`)"
          type="button"
          @click="selectItem(item)"
        >
          <span>
            <strong>{{ item.parent_label || `面单 ${item.parent_sequence}` }}</strong>
            <small>{{ item.source_component || '未知打印平台' }}</small>
          </span>
          <span>
            <el-tag :type="item.reason ? 'warning' : 'success'">
              {{ reasonLabel(item.reason) }}
            </el-tag>
            <small>{{ preparingKey === itemKey(item) ? '正在读取…' : '点击学习' }}</small>
          </span>
        </button>
      </div>
    </article>

    <article class="work-surface editor-surface">
      <el-empty
        v-if="!prepared"
        :image-size="72"
        description="从左侧选择一张面单开始学习"
      />
      <template v-else>
        <div class="section-heading">
          <div>
            <h2>{{ prepared.parent_label || `面单 ${prepared.parent_sequence}` }}</h2>
            <p>{{ prepared.source_component }} · {{ reasonLabel(prepared.reason) }}</p>
          </div>
          <el-tag effect="plain">
            {{ prepared.fingerprint.code }} · {{ prepared.fingerprint.name }}
          </el-tag>
        </div>

        <h3>用于生成规则的脱敏字段</h3>
        <el-alert
          v-if="!prepared.selected_fields.length"
          :closable="false"
          title="当前指纹尚未选择学习字段"
          type="warning"
          show-icon
        >
          <template #default>
            <el-link type="primary" @click="router.push('/admin/fingerprint-settings')">前往面单指纹配置</el-link>
          </template>
        </el-alert>
        <div v-else class="field-list">
          <div v-for="field in prepared.selected_fields" :key="field.key" class="field-card">
            <span>{{ field.label }} <code>{{ field.path }}</code></span>
            <pre>{{ fieldValues(field) || '空' }}</pre>
          </div>
        </div>

        <div class="row-heading">
          <div>
            <h3>管理员确认的商品行</h3>
            <p>备注可以为空；一张面单有多个商品时增加商品行。</p>
          </div>
          <el-button @click="addRow">添加商品行</el-button>
        </div>

        <el-table :data="editableRows" border>
          <el-table-column label="商品" min-width="220">
            <template #default="{ row }"><el-input v-model="row.product" aria-label="商品" /></template>
          </el-table-column>
          <el-table-column label="销售属性1" min-width="150">
            <template #default="{ row }"><el-input v-model="row.sales_attr1" aria-label="销售属性1" /></template>
          </el-table-column>
          <el-table-column label="销售属性2" min-width="130">
            <template #default="{ row }"><el-input v-model="row.sales_attr2" aria-label="销售属性2" /></template>
          </el-table-column>
          <el-table-column label="数量" width="130">
            <template #default="{ row }"><el-input-number v-model="row.quantity" :min="1" :step="1" aria-label="数量" /></template>
          </el-table-column>
          <el-table-column label="备注" min-width="160">
            <template #default="{ row }"><el-input v-model="row.remark" aria-label="备注" /></template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ $index }">
              <el-button
                :disabled="editableRows.length <= 1"
                link
                type="danger"
                @click="removeRow($index)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="editor-actions">
          <el-button @click="prepared = null">取消</el-button>
          <el-button
            type="primary"
            :disabled="!prepared.selected_fields.length"
            :loading="saving"
            @click="saveLearning"
          >
            保存并验证规则
          </el-button>
        </div>
      </template>
    </article>
  </section>
</template>

<style scoped>
.toolbar,
.section-heading,
.row-heading,
.editor-actions,
.queue-card {
  display: flex;
  align-items: center;
  gap: 12px;
}

.toolbar {
  margin-bottom: 16px;
}

.toolbar .el-select {
  width: min(480px, 60vw);
}

.learning-layout {
  display: grid;
  grid-template-columns: minmax(320px, 0.72fr) minmax(640px, 1.28fr);
  gap: 16px;
  align-items: start;
}

.section-heading,
.row-heading,
.queue-card {
  justify-content: space-between;
}

.section-heading h2,
.section-heading p,
.row-heading h3,
.row-heading p {
  margin: 0 0 6px;
}

.queue-list {
  min-height: 140px;
}

.queue-card {
  width: 100%;
  padding: 14px 4px;
  border: 0;
  border-top: 1px solid var(--el-border-color-lighter);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.queue-card:first-child {
  border-top: 0;
}

.queue-card.active {
  padding-inline: 12px;
  border-radius: 8px;
  background: var(--el-color-primary-light-9);
}

.queue-card > span {
  display: grid;
  gap: 6px;
}

.queue-card > span:last-child {
  justify-items: end;
}

.queue-card small,
.section-heading p,
.row-heading p {
  color: var(--el-text-color-secondary);
}

.field-list {
  display: grid;
  gap: 10px;
  margin-bottom: 20px;
}

.field-card {
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-light);
}

.field-card span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.field-card code {
  margin-left: 6px;
}

.field-card pre {
  margin: 8px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.row-heading {
  margin-top: 18px;
}

.editor-actions {
  justify-content: flex-end;
  margin-top: 18px;
}

@media (max-width: 1100px) {
  .learning-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .toolbar,
  .section-heading,
  .row-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .toolbar .el-select {
    width: 100%;
  }
}
</style>
