<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Download, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  activateRecognitionRulePack,
  deactivateRecognitionRulePack,
  deleteRecognitionRulePack,
  exportRecognitionRulePack,
  importRecognitionRulePack,
  listRecognitionRulePacks,
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
const editingPayload = ref<Record<string, unknown> | null>(null)
const specialRules = ref<Array<{ keyword: string; reason: string; displayReason: string }>>([])
const quantityDefault = ref(1)
const labelPrefixesText = ref('')
const stripPurchaseHint = ref(true)
const allowEmptyProduct = ref(true)
const allowNonNumericSalesAttr2 = ref(true)

const hasImportPayload = computed(() => importText.value.trim().length > 0)

function readableDate(value?: string | null): string {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

function packDisplayName(pack: RecognitionRulePackSummary): string {
  return `${pack.name} (${pack.code} / ${pack.version})`
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
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
    const policy = recordValue(result.payload.parser_policy)
    const quantity = recordValue(policy.quantity)
    const labelCleanup = recordValue(policy.label_cleanup)
    const sizeNormalization = recordValue(policy.size_normalization)
    const manualLabelOnly = recordValue(policy.manual_label_only)
    const nonShoe = recordValue(policy.non_shoe)
    const rules = policy.special_text_keywords
    editingPack.value = pack
    editingPayload.value = result.payload
    const configuredQuantity = Number(quantity.default_if_missing)
    quantityDefault.value = Number.isInteger(configuredQuantity) && configuredQuantity > 0 ? configuredQuantity : 1
    labelPrefixesText.value = Array.isArray(labelCleanup.strip_prefixes)
      ? labelCleanup.strip_prefixes.map(String).join('、')
      : ''
    stripPurchaseHint.value = sizeNormalization.strip_purchase_hint !== false
    allowEmptyProduct.value = manualLabelOnly.allow_empty_product !== false
    allowNonNumericSalesAttr2.value = nonShoe.allow_non_numeric_sales_attr2 !== false
    specialRules.value = (Array.isArray(rules) ? rules : []).map((rule, index) => {
      const values = rule && typeof rule === 'object' && !Array.isArray(rule)
        ? (rule as Record<string, unknown>)
        : {}
      return {
        keyword: String(values.keyword ?? ''),
        reason: String(values.reason ?? `special_keyword_${index + 1}`),
        displayReason: String(values.display_reason ?? ''),
      }
    })
    ruleEditorVisible.value = true
  } catch (err) {
    error.value = err instanceof Error ? err.message : '规则包读取失败'
  } finally {
    editingId.value = null
  }
}

function addSpecialRule() {
  specialRules.value.push({
    keyword: '',
    reason: `special_keyword_${specialRules.value.length + 1}`,
    displayReason: '',
  })
}

async function savePackRules() {
  if (!editingPack.value || !editingPayload.value) return
  if (specialRules.value.some((rule) => !rule.keyword.trim())) {
    ElMessage.warning('特殊单关键词不能为空。')
    return
  }
  if (!Number.isInteger(quantityDefault.value) || quantityDefault.value <= 0) {
    ElMessage.warning('默认数量必须是大于 0 的整数。')
    return
  }

  const currentPolicy = recordValue(editingPayload.value.parser_policy)
  const packMeta = recordValue(editingPayload.value.pack)
  const currentQuantity = recordValue(currentPolicy.quantity)
  const currentLabelCleanup = recordValue(currentPolicy.label_cleanup)
  const currentSizeNormalization = recordValue(currentPolicy.size_normalization)
  const currentManualLabelOnly = recordValue(currentPolicy.manual_label_only)
  const currentNonShoe = recordValue(currentPolicy.non_shoe)
  const versionMatch = String(packMeta.version ?? '1.0.0').match(/^(\d+)\.(\d+)\.(\d+)$/)
  const nextVersion = versionMatch
    ? `${versionMatch[1]}.${versionMatch[2]}.${Number(versionMatch[3]) + 1}`
    : String(packMeta.version ?? '1.0.0')
  const prefixes = labelPrefixesText.value
    .split(/[、,，\n]+/)
    .map((value) => value.trim())
    .filter(Boolean)
  const nextPayload = {
    ...editingPayload.value,
    pack: {
      ...packMeta,
      version: nextVersion,
    },
    parser_policy: {
      ...currentPolicy,
      special_text_keywords: specialRules.value.map((rule, index) => ({
        keyword: rule.keyword.trim(),
        status: 'special',
        reason: rule.reason || `special_keyword_${index + 1}`,
        parse_fields: false,
        match_product: false,
        match_image: false,
        display_reason: rule.displayReason.trim() || `${rule.keyword.trim()}特殊单，保留原文，不参与商品和图片匹配。`,
      })),
      quantity: {
        ...currentQuantity,
        default_if_missing: quantityDefault.value,
      },
      label_cleanup: {
        ...currentLabelCleanup,
        strip_prefixes: prefixes,
        separator_chars: Array.isArray(currentLabelCleanup.separator_chars)
          ? currentLabelCleanup.separator_chars
          : [':', '：', ';', '；', ',', '，', '/', '|'],
      },
      size_normalization: {
        ...currentSizeNormalization,
        enabled: true,
        strip_purchase_hint: stripPurchaseHint.value,
      },
      manual_label_only: {
        ...currentManualLabelOnly,
        allow_empty_product: allowEmptyProduct.value,
        default_quantity_if_missing: quantityDefault.value,
      },
      non_shoe: {
        ...currentNonShoe,
        allow_non_numeric_sales_attr2: allowNonNumericSalesAttr2.value,
      },
    },
  }

  savingRules.value = true
  error.value = ''
  try {
    await importRecognitionRulePack({
      payload: nextPayload,
      activate: activePack.value?.id === editingPack.value.id,
      description: editingPack.value.description ?? null,
    })
    ruleEditorVisible.value = false
    await loadPacks()
    ElMessage.success('子规则已保存。')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '子规则保存失败'
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
          <p>不同商品场景可以保存为不同规则包。导出后可备份，也可导入到其他工作空间。</p>
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
              编辑子规则
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

  <el-dialog v-model="ruleEditorVisible" title="编辑识别子规则" width="760px">
    <el-form class="policy-form" label-position="top">
      <el-form-item label="面单没有写数量时">
        <el-input-number v-model="quantityDefault" :min="1" :max="999" />
        <span class="field-help">系统使用这个默认数量，不从商品或尺码数字里猜数量。</span>
      </el-form-item>
      <el-form-item label="需要从商品属性开头去掉的字段名">
        <el-input
          v-model="labelPrefixesText"
          type="textarea"
          :rows="2"
          placeholder="例如：颜色分类、颜色、鞋码、尺码、规格"
        />
        <span class="field-help">用顿号、逗号或换行分隔；只清理开头字段名，原始面单仍保留。</span>
      </el-form-item>
      <el-form-item label="尺码中的购买提示">
        <el-switch
          v-model="stripPurchaseHint"
          active-text="去掉提示，只保留尺码"
          inactive-text="保留提示原文"
        />
      </el-form-item>
      <el-form-item label="只有颜色/尺码、没有商品名的手工单">
        <el-switch
          v-model="allowEmptyProduct"
          active-text="允许进入商品匹配"
          inactive-text="列为解析异常"
        />
      </el-form-item>
      <el-form-item label="非数字规格（例如均码）">
        <el-switch
          v-model="allowNonNumericSalesAttr2"
          active-text="允许"
          inactive-text="列为解析异常"
        />
      </el-form-item>
    </el-form>
    <el-alert
      :closable="false"
      title="特殊单关键词"
      description="面单原文中任意出现一条关键词，就按特殊单保留，不进入商品和图片匹配。多商品拆行和原文追溯是固定业务约束，不能在这里关闭。"
      type="info"
      show-icon
    />
    <div class="special-rule-list">
      <div v-for="(rule, index) in specialRules" :key="index" class="special-rule-row">
        <el-input v-model="rule.keyword" placeholder="关键词，例如：微信" />
        <el-input v-model="rule.displayReason" placeholder="页面说明（可选）" />
        <el-button type="danger" plain @click="specialRules.splice(index, 1)">删除</el-button>
      </div>
      <el-button plain @click="addSpecialRule">添加关键词规则</el-button>
    </div>
    <template #footer>
      <el-button @click="ruleEditorVisible = false">取消</el-button>
      <el-button type="primary" :loading="savingRules" @click="savePackRules">保存子规则</el-button>
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

.special-rule-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
}

.policy-form {
  margin-bottom: 18px;
}

.field-help {
  margin-left: 10px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.special-rule-row {
  display: grid;
  grid-template-columns: minmax(160px, 0.7fr) minmax(260px, 1.3fr) auto;
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

@media (max-width: 1100px) {
  .rule-pack-grid {
    grid-template-columns: 1fr;
  }

  .special-rule-row {
    grid-template-columns: 1fr;
  }
}
</style>
