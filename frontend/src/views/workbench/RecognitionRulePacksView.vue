<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Download, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import RecognitionProfileEditor from '../../components/recognition/RecognitionProfileEditor.vue'
import {
  activateRecognitionRulePack,
  deactivateRecognitionRulePack,
  deleteRecognitionRulePack,
  exportRecognitionRulePack,
  importRecognitionRulePack,
  listRecognitionRulePacks,
  type RecognitionFormatProfile,
  type RecognitionLearningRecord,
  type RecognitionRulePackPayload,
  type RecognitionRulePackSummary,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

const session = useSessionStore()
const loading = ref(false)
const importing = ref(false)
const exportingId = ref<number | null>(null)
const editingId = ref<number | null>(null)
const savingRules = ref(false)
const activatingId = ref<number | null>(null)
const deactivatingId = ref<number | null>(null)
const deletingId = ref<number | null>(null)
const error = ref('')
const activePack = ref<RecognitionRulePackSummary | null>(null)
const packs = ref<RecognitionRulePackSummary[]>([])
const importText = ref('')
const importDescription = ref('')
const ruleEditorVisible = ref(false)
const editingPack = ref<RecognitionRulePackSummary | null>(null)
const editingPayload = ref<RecognitionRulePackPayload | null>(null)
const formatProfiles = ref<RecognitionFormatProfile[]>([])
const learningRecords = ref<RecognitionLearningRecord[]>([])
const selectedProfileKey = ref('')
const editorError = ref('')

const hasImportPayload = computed(() => importText.value.trim().length > 0)
const selectedProfile = computed(() => (
  formatProfiles.value.find((profile) => profileKey(profile) === selectedProfileKey.value) ?? null
))
const selectedLearningRecord = computed(() => (
  selectedProfile.value ? learningRecordFor(selectedProfile.value) : undefined
))
const capabilityCounts = computed(() => [
  { label: '结构化字段读取', count: formatProfiles.value.filter((item) => item.strategy === 'structured_items_v1').length },
  { label: '文本拆分', count: formatProfiles.value.filter((item) => item.strategy === 'text_pipeline_v1').length },
  { label: '来源字段绑定', count: formatProfiles.value.filter((item) => item.strategy === 'source_projection_v1').length },
].filter((item) => item.count > 0))

function readableDate(value?: string | null): string {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

function packDisplayName(pack: RecognitionRulePackSummary): string {
  return `${pack.name} (${pack.code} / ${pack.version})`
}

function profileStrategyLabel(profile: RecognitionFormatProfile): string {
  if (profile.strategy === 'structured_items_v1') return '结构化字段'
  if (profile.strategy === 'source_projection_v1') return '自动来源绑定'
  return '文本拆分'
}

function profileValidationLabel(profile: RecognitionFormatProfile): string {
  const result = learningRecordFor(profile)?.compiler_result
  if (!result) return '尚无学习校验'
  const replay = result.replay_report ?? []
  if (result.status === 'compiled' && replay.length && replay.every((item) => item.passed === true)) {
    return '规则与样本回放已通过'
  }
  return '规则需要重新校验'
}

function profileKey(value: {
  fingerprint: string
  grammar_signature?: string
  strategy?: string
  selected_fields?: string[]
}): string {
  return JSON.stringify([
    value.fingerprint,
    value.strategy ?? '',
    value.selected_fields ?? [],
    value.grammar_signature ?? '',
  ])
}

function profileDisplayName(profile: RecognitionFormatProfile): string {
  if (profile.name) return profile.name
  const code = profile.fingerprint.match(/^v2:([^:]+):/)?.[1] ?? ''
  const names: Record<string, string> = {
    'CN-ITEM-INFO': '菜鸟商品文本型',
    'CN-PRINT-XML': '菜鸟打印 XML 型',
    'CN-CUSTOM-CONTENT': '菜鸟自定义内容型',
    'CN-PACKAGE-ITEMS': '菜鸟包裹明细型',
    'CLOUD-PRODUCT-INFO': '云打印商品信息型',
  }
  return `${names[code] ?? '已学习面单格式'} · ${profileStrategyLabel(profile)}`
}

function learningRecordFor(profile: RecognitionFormatProfile): RecognitionLearningRecord | undefined {
  const sessionId = profile.provenance?.learning_session_id
  return [...learningRecords.value].reverse().find((record) => (
    (sessionId && record.session_id === sessionId)
    || (
      record.fingerprint === profile.fingerprint
      && (record.grammar_signature ?? '') === (profile.grammar_signature ?? '')
    )
  ))
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function jsonCopy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

async function loadPacks() {
  loading.value = true
  error.value = ''
  try {
    const result = await listRecognitionRulePacks()
    activePack.value = result.active_pack ?? null
    packs.value = result.packs ?? []
  } catch (err) {
    error.value = err instanceof Error ? err.message : '识别规则包加载失败'
  } finally {
    loading.value = false
  }
}

function parseImportPayload(): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(importText.value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      ElMessage.warning('规则包 JSON 必须是一个对象。')
      return null
    }
    return parsed as Record<string, unknown>
  } catch {
    ElMessage.error('规则包 JSON 解析失败，请检查文件内容。')
    return null
  }
}

async function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importText.value = await file.text()
  ElMessage.success(`已读取 ${file.name}`)
  input.value = ''
}

async function importPack(activate: boolean) {
  if (!hasImportPayload.value) {
    ElMessage.warning('请先选择或粘贴规则包 JSON。')
    return
  }
  const payload = parseImportPayload()
  if (!payload) return

  importing.value = true
  error.value = ''
  try {
    const result = await importRecognitionRulePack({
      payload,
      activate,
      description: importDescription.value.trim() || null,
    })
    importText.value = ''
    importDescription.value = ''
    await loadPacks()
    ElMessage.success(activate ? `已导入并启用：${result.pack.name}` : `已导入：${result.pack.name}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '规则包导入失败'
  } finally {
    importing.value = false
  }
}

async function activatePack(pack: RecognitionRulePackSummary) {
  try {
    await ElMessageBox.confirm(
      `启用后，面单解析会使用「${pack.name}」解析面单。`,
      '启用识别规则包',
      {
        confirmButtonText: '启用',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  activatingId.value = pack.id
  error.value = ''
  try {
    await activateRecognitionRulePack(pack.id)
    await loadPacks()
    ElMessage.success(`已启用：${pack.name}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '规则包启用失败'
  } finally {
    activatingId.value = null
  }
}

async function deactivatePack(pack: RecognitionRulePackSummary) {
  try {
    await ElMessageBox.confirm(
      `停用后，面单解析不会继续使用「${pack.name}」。没有其他启用规则包时，系统会提示先导入或启用规则包。`,
      '停用识别规则包',
      {
        confirmButtonText: '停用',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  deactivatingId.value = pack.id
  error.value = ''
  try {
    await deactivateRecognitionRulePack(pack.id)
    await loadPacks()
    ElMessage.success(`已停用：${pack.name}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '规则包停用失败'
  } finally {
    deactivatingId.value = null
  }
}

async function deletePack(pack: RecognitionRulePackSummary) {
  try {
    await ElMessageBox.confirm(
      `删除后，「${pack.name}」不会再出现在已保存规则包列表。已采集面单、商品、SKU、图片和导出数据不会被删除。`,
      '删除识别规则包',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  deletingId.value = pack.id
  error.value = ''
  try {
    await deleteRecognitionRulePack(pack.id)
    await loadPacks()
    ElMessage.success(`已删除：${pack.name}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '规则包删除失败'
  } finally {
    deletingId.value = null
  }
}

async function exportPack(pack: RecognitionRulePackSummary) {
  exportingId.value = pack.id
  error.value = ''
  try {
    const result = await exportRecognitionRulePack(pack.id)
    const blob = new Blob([JSON.stringify(result.payload, null, 2)], {
      type: 'application/json;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${pack.code || 'recognition-rule-pack'}.${pack.version || 'v1'}.json`
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '规则包导出失败'
  } finally {
    exportingId.value = null
  }
}

async function editPackRules(pack: RecognitionRulePackSummary) {
  editingId.value = pack.id
  error.value = ''
  try {
    const result = await exportRecognitionRulePack(pack.id)
    const payload = result.payload as RecognitionRulePackPayload
    const policy = recordValue(payload.parser_policy)
    const profiles = Array.isArray(policy.format_profiles)
      ? policy.format_profiles.filter((profile): profile is RecognitionFormatProfile => (
          Boolean(profile)
          && typeof profile === 'object'
          && typeof (profile as RecognitionFormatProfile).fingerprint === 'string'
        ))
      : []
    editingPack.value = pack
    editingPayload.value = jsonCopy(payload)
    formatProfiles.value = jsonCopy(profiles)
    learningRecords.value = Array.isArray(payload.ai_learning_records)
      ? jsonCopy(payload.ai_learning_records)
      : []
    selectedProfileKey.value = formatProfiles.value[0] ? profileKey(formatProfiles.value[0]) : ''
    editorError.value = ''
    ruleEditorVisible.value = true
  } catch (err) {
    error.value = err instanceof Error ? err.message : '规则包读取失败'
  } finally {
    editingId.value = null
  }
}

function updateSelectedProfile(profile: RecognitionFormatProfile) {
  const index = formatProfiles.value.findIndex((item) => profileKey(item) === selectedProfileKey.value)
  if (index < 0) return
  formatProfiles.value[index] = profile
  selectedProfileKey.value = profileKey(profile)
}

async function deleteSelectedProfile() {
  if (!selectedProfile.value) return
  if (formatProfiles.value.length === 1) {
    ElMessage.warning('这是最后一条子规则。如需清空，请关闭编辑窗口后删除整个规则包。')
    return
  }
  try {
    await ElMessageBox.confirm(
      `删除后，这种面单格式将不再被识别：${selectedProfile.value.name || '未命名规则'}`,
      '删除子规则',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  const key = profileKey(selectedProfile.value)
  formatProfiles.value = formatProfiles.value.filter((profile) => profileKey(profile) !== key)
  learningRecords.value = learningRecords.value.filter((record) => profileKey(record) !== key)
  selectedProfileKey.value = formatProfiles.value[0] ? profileKey(formatProfiles.value[0]) : ''
}

async function savePackRules() {
  if (!editingPack.value || !editingPayload.value) return
  if (!formatProfiles.value.length) {
    ElMessage.warning('规则包至少需要保留一条子规则。')
    return
  }

  const currentPolicy = recordValue(editingPayload.value.parser_policy)
  const nextPayload: RecognitionRulePackPayload = {
    ...editingPayload.value,
    parser_policy: {
      ...currentPolicy,
      order_row_parser: 'declarative_v1',
      format_profiles: jsonCopy(formatProfiles.value),
    },
    ai_learning_records: jsonCopy(learningRecords.value),
  }

  savingRules.value = true
  error.value = ''
  editorError.value = ''
  try {
    await importRecognitionRulePack({
      payload: nextPayload,
      activate: activePack.value?.id === editingPack.value.id,
      description: editingPack.value.description ?? null,
    })
    ruleEditorVisible.value = false
    await loadPacks()
    ElMessage.success(`已保存 ${formatProfiles.value.length} 条子规则。`)
  } catch (err) {
    const message = err instanceof Error ? err.message : '子规则保存失败'
    error.value = message
    editorError.value = message
    ElMessage.error(message)
  } finally {
    savingRules.value = false
  }
}

watch(() => session.currentWorkspaceId, loadPacks)
onMounted(loadPacks)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>识别规则包</h1>
      <p>规则包决定系统如何把采集到的面单拆成订单行。没有启用规则包时，系统不会偷偷识别。</p>
    </div>
    <el-button :icon="Refresh" :loading="loading" plain @click="loadPacks">刷新</el-button>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />
  <el-alert
    v-else-if="activePack"
    :closable="false"
    :title="`当前启用：${packDisplayName(activePack)}`"
    description="面单解析会使用这个规则包。切换商品场景前，请先导入并启用对应场景的规则包。"
    type="success"
    show-icon
  />
  <el-alert
    v-else
    :closable="false"
    title="当前没有启用识别规则包"
    description="面单解析不会进行面单识别。请先导入并启用适合当前商品场景的规则包。"
    type="warning"
    show-icon
  />

  <section class="rule-pack-grid">
    <article class="work-surface import-panel">
      <div class="panel-heading">
        <div>
          <h2><el-icon><UploadFilled /></el-icon> 导入规则包</h2>
          <p>上传从本系统导出的 JSON 规则包，或粘贴规则包内容。导入后可以立即启用。</p>
        </div>
      </div>

      <div class="import-actions">
        <label class="file-picker">
          选择 JSON 文件
          <input type="file" accept=".json,application/json" @change="handleFileChange" />
        </label>
        <el-input v-model="importDescription" clearable placeholder="导入说明（可选）" />
      </div>

      <el-input
        v-model="importText"
        type="textarea"
        :rows="10"
        placeholder="也可以把规则包 JSON 粘贴到这里"
      />

      <div class="panel-actions">
        <el-button :loading="importing" :disabled="!hasImportPayload" @click="importPack(false)">
          仅导入
        </el-button>
        <el-button type="primary" :loading="importing" :disabled="!hasImportPayload" @click="importPack(true)">
          导入并启用
        </el-button>
      </div>
    </article>

    <article class="work-surface">
      <div class="panel-heading">
        <div>
          <h2><el-icon><Download /></el-icon> 已保存规则包</h2>
          <p>AI 每确认一种新格式，就会追加到同一个“AI识别规则包”中；导出可用于备份。</p>
        </div>
      </div>

      <el-table v-loading="loading" :data="packs" empty-text="暂无规则包，请先导入。">
        <el-table-column label="规则包" min-width="240">
          <template #default="{ row }">
            <strong>{{ row.name }}</strong>
            <small>{{ row.code }} / {{ row.version }}</small>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag v-if="activePack?.id === row.id" type="success">已启用</el-tag>
            <el-tag v-else type="info">未启用</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="说明" prop="description" min-width="260" show-overflow-tooltip />
        <el-table-column label="更新时间" width="190">
          <template #default="{ row }">{{ readableDate(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="360" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="activePack?.id !== row.id"
              size="small"
              :loading="activatingId === row.id"
              :disabled="deletingId === row.id"
              @click="activatePack(row)"
            >
              启用
            </el-button>
            <el-button
              v-else
              size="small"
              type="warning"
              plain
              :loading="deactivatingId === row.id"
              :disabled="deletingId === row.id"
              @click="deactivatePack(row)"
            >
              停用
            </el-button>
            <el-button size="small" plain :loading="exportingId === row.id" @click="exportPack(row)">
              导出
            </el-button>
            <el-button size="small" plain :loading="editingId === row.id" @click="editPackRules(row)">
              查看识别能力
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :loading="deletingId === row.id"
              @click="deletePack(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </article>
  </section>

  <el-dialog v-model="ruleEditorVisible" title="AI识别规则包能力" width="min(1280px, 96vw)" top="4vh">
    <el-alert
      v-if="!formatProfiles.length"
      :closable="false"
      title="这个规则包还没有学习结果"
      description="请先到“AI 面单解析”页面手动确认一种新格式。"
      type="warning"
      show-icon
    />
    <template v-else>
      <div class="capability-overview">
        <div>
          <strong>已学习 {{ formatProfiles.length }} 种可复用格式能力</strong>
          <small>业务解析会自动选择并重放；冲突时失败关闭，不会静默猜测。</small>
        </div>
        <el-tag v-for="item in capabilityCounts" :key="item.label" effect="plain">
          {{ item.label }} {{ item.count }}
        </el-tag>
      </div>
      <el-collapse>
        <el-collapse-item title="高级：查看或修正技术规则">
          <div class="rule-editor-layout">
            <aside class="profile-list">
              <div class="profile-list__heading">
                <strong>技术规则</strong>
                <small>通常无需逐条维护</small>
              </div>
              <button
                v-for="profile in formatProfiles"
                :key="profileKey(profile)"
                type="button"
                class="profile-list__item"
                :class="{ active: selectedProfileKey === profileKey(profile) }"
                @click="selectedProfileKey = profileKey(profile)"
              >
                <strong>{{ profileDisplayName(profile) }}</strong>
                <span>{{ profileStrategyLabel(profile) }}</span>
                <small>{{ profileValidationLabel(profile) }}</small>
              </button>
            </aside>
            <RecognitionProfileEditor
              v-if="selectedProfile"
              :model-value="selectedProfile"
              :learning-record="selectedLearningRecord"
              @update:model-value="updateSelectedProfile"
              @delete="deleteSelectedProfile"
            />
          </div>
        </el-collapse-item>
      </el-collapse>
    </template>
    <template #footer>
      <div class="rule-editor-footer">
        <el-alert v-if="editorError" :closable="false" :title="editorError" type="error" show-icon />
        <div class="rule-editor-footer__actions">
          <el-button @click="ruleEditorVisible = false">取消</el-button>
          <el-button
            type="primary"
            :loading="savingRules"
            :disabled="!formatProfiles.length"
            @click="savePackRules"
          >
            保存技术规则
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.rule-pack-grid {
  display: grid;
  grid-template-columns: minmax(360px, 0.78fr) minmax(520px, 1.22fr);
  gap: 16px;
  margin-top: 16px;
  align-items: start;
}

.import-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.import-actions {
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
}

.file-picker {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  color: var(--el-color-primary);
  background: var(--el-fill-color-blank);
  cursor: pointer;
  font-size: 14px;
}

.file-picker input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.panel-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.el-table strong {
  display: block;
  color: var(--el-text-color-primary);
}

.el-table small {
  display: block;
  margin-top: 4px;
  color: var(--el-text-color-secondary);
}

.capability-overview {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px;
  margin-bottom: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-light);
}

.capability-overview > div {
  flex: 1;
}

.capability-overview small {
  display: block;
  margin-top: 4px;
  color: var(--el-text-color-secondary);
}

.rule-editor-layout {
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  gap: 20px;
  min-height: 560px;
}

.profile-list {
  padding-right: 14px;
  overflow: auto;
  border-right: 1px solid var(--el-border-color-lighter);
}

.profile-list__heading {
  padding: 4px 8px 12px;
}

.profile-list__heading small,
.profile-list__item span,
.profile-list__item small {
  display: block;
  margin-top: 5px;
  color: var(--el-text-color-secondary);
}

.profile-list__item {
  display: block;
  width: 100%;
  padding: 12px;
  margin-bottom: 8px;
  text-align: left;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
  cursor: pointer;
}

.profile-list__item.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.rule-editor-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 14px;
}

.rule-editor-footer .el-alert {
  flex: 1;
  text-align: left;
}

.rule-editor-footer__actions {
  display: flex;
  flex: none;
  gap: 10px;
}

@media (max-width: 1100px) {
  .rule-pack-grid {
    grid-template-columns: 1fr;
  }

  .rule-editor-layout {
    grid-template-columns: 1fr;
  }

  .profile-list {
    display: flex;
    gap: 8px;
    padding: 0 0 12px;
    border-right: 0;
    border-bottom: 1px solid var(--el-border-color-lighter);
  }

  .profile-list__heading {
    min-width: 160px;
  }

  .profile-list__item {
    min-width: 190px;
  }

  .rule-editor-footer {
    align-items: stretch;
    flex-direction: column;
  }

  .rule-editor-footer__actions {
    justify-content: flex-end;
  }
}
</style>
