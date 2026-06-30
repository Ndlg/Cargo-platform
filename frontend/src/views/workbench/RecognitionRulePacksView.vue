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
const activatingId = ref<number | null>(null)
const deactivatingId = ref<number | null>(null)
const deletingId = ref<number | null>(null)
const error = ref('')
const activePack = ref<RecognitionRulePackSummary | null>(null)
const packs = ref<RecognitionRulePackSummary[]>([])
const importText = ref('')
const importDescription = ref('')

const hasImportPayload = computed(() => importText.value.trim().length > 0)

function readableDate(value?: string | null): string {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

function packDisplayName(pack: RecognitionRulePackSummary): string {
  return `${pack.name} (${pack.code} / ${pack.version})`
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
        <el-table-column label="操作" width="280" fixed="right">
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

@media (max-width: 1100px) {
  .rule-pack-grid {
    grid-template-columns: 1fr;
  }
}
</style>
