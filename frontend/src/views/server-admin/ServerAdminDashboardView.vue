<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'

import { getRecords, type ApiRecord } from '../../services/api'

const supportedWaybillModeCodes = new Set([
  'douyin_cloud_print',
  'cainiao_direct_shop',
  'cainiao_woda_printxml',
])

const tenants = ref<ApiRecord[]>([])
const workspaces = ref<ApiRecord[]>([])
const users = ref<ApiRecord[]>([])
const modes = ref<ApiRecord[]>([])
const templates = ref<ApiRecord[]>([])
const templateFields = ref<ApiRecord[]>([])
const loading = ref(false)
const error = ref('')

const supportedModes = computed(() =>
  modes.value.filter((mode) =>
    supportedWaybillModeCodes.has(String(mode.code ?? ''))
    && mode.is_enabled !== false,
  ),
)

const enabledUsers = computed(() =>
  users.value.filter((user) => user.is_enabled !== false).length,
)

const enabledTemplateRows = computed(() =>
  templates.value.filter((template) => {
    if (template.is_enabled === false) return false
    const id = Number(template.waybill_mode_id)
    const mode = modes.value.find((item) => Number(item.id) === id)
    return supportedWaybillModeCodes.has(String(mode?.code ?? '')) && mode?.is_enabled !== false
  }),
)

const stats = computed(() => [
  { label: '客户', value: tenants.value.length, note: '平台账户主体' },
  { label: '工作空间', value: workspaces.value.length, note: '业务数据隔离单位' },
  { label: '启用账号', value: enabledUsers.value, note: `总账号 ${users.value.length}` },
  { label: '解析能力', value: supportedModes.value.length, note: `启用模板 ${enabledTemplateRows.value.length}` },
])

function textValue(value: unknown, fallback = '-'): string {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function fieldCountForTemplate(templateId: unknown): number {
  const id = Number(templateId)
  if (!Number.isInteger(id)) return 0
  return templateFields.value.filter((field) => Number(field.waybill_template_id) === id).length
}

function modeName(modeId: unknown): string {
  const id = Number(modeId)
  const mode = modes.value.find((item) => Number(item.id) === id)
  return textValue(mode?.name)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [
      tenantRecords,
      workspaceRecords,
      userRecords,
      modeRecords,
      templateRecords,
      fieldRecords,
    ] = await Promise.all([
      getRecords('/tenants?limit=2000'),
      getRecords('/workspaces?limit=2000'),
      getRecords('/users?limit=2000'),
      getRecords('/waybill-modes?limit=2000'),
      getRecords('/waybill-templates?limit=2000'),
      getRecords('/waybill-template-fields?limit=2000'),
    ])
    tenants.value = tenantRecords
    workspaces.value = workspaceRecords
    users.value = userRecords
    modes.value = modeRecords
    templates.value = templateRecords
    templateFields.value = fieldRecords
  } catch (err) {
    error.value = err instanceof Error ? err.message : '平台概览加载失败。'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>平台概览</h1>
      <p>这里只保留部署后真正需要看的平台状态：客户、工作空间、账号和当前内置解析能力。</p>
    </div>
    <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="stat-grid">
    <article v-for="stat in stats" :key="stat.label" class="stat-tile">
      <span>{{ stat.label }}</span>
      <strong>{{ stat.value }}</strong>
      <small>{{ stat.note }}</small>
    </article>
  </section>

  <section class="work-surface">
    <div class="section-title-row">
      <h2>当前支持的面单来源</h2>
      <span class="muted-line">客户业务配置放在 5173 管理页面，这里只看平台内置解析是否存在。</span>
    </div>
    <el-table :data="supportedModes" stripe>
      <el-table-column label="来源" min-width="190">
        <template #default="{ row }">{{ textValue(row.name) }}</template>
      </el-table-column>
      <el-table-column label="编码" min-width="190">
        <template #default="{ row }">{{ textValue(row.code) }}</template>
      </el-table-column>
      <el-table-column label="输入" width="150">
        <template #default="{ row }">
          {{ row.input_format === 'print_component_json' ? '打印组件 JSON' : textValue(row.input_format) }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default>
          <el-tag type="success">启用</el-tag>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无启用的正式解析模式" />
      </template>
    </el-table>
  </section>

  <section class="work-surface">
    <div class="section-title-row">
      <h2>解析模板概况</h2>
      <span class="muted-line">细节不再单独开页面，避免现场用户误以为这里要配置业务规则。</span>
    </div>
    <el-table :data="enabledTemplateRows" height="360" stripe>
      <el-table-column label="模板" min-width="240">
        <template #default="{ row }">{{ textValue(row.name) }}</template>
      </el-table-column>
      <el-table-column label="所属来源" min-width="180">
        <template #default="{ row }">{{ modeName(row.waybill_mode_id) }}</template>
      </el-table-column>
      <el-table-column label="版本" width="90">
        <template #default="{ row }">{{ textValue(row.version) }}</template>
      </el-table-column>
      <el-table-column label="字段数" width="100">
        <template #default="{ row }">{{ fieldCountForTemplate(row.id) }}</template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无解析模板" />
      </template>
    </el-table>
  </section>
</template>
