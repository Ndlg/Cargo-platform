<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'

import { getRecords, type ApiRecord } from '../../services/api'

const tenants = ref<ApiRecord[]>([])
const workspaces = ref<ApiRecord[]>([])
const users = ref<ApiRecord[]>([])
const loading = ref(false)
const error = ref('')

const enabledUsers = computed(() =>
  users.value.filter((user) => user.is_enabled !== false).length,
)

const stats = computed(() => [
  { label: '客户', value: tenants.value.length, note: '平台账户主体' },
  { label: '工作空间', value: workspaces.value.length, note: '业务数据隔离单位' },
  { label: '启用账号', value: enabledUsers.value, note: `总账号 ${users.value.length}` },
  { label: '识别规则包', value: '按工作区', note: '在业务管理页导入、启用和导出' },
])

function textValue(value: unknown, fallback = '-'): string {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [
      tenantRecords,
      workspaceRecords,
      userRecords,
    ] = await Promise.all([
      getRecords('/tenants?limit=2000'),
      getRecords('/workspaces?limit=2000'),
      getRecords('/users?limit=2000'),
    ])
    tenants.value = tenantRecords
    workspaces.value = workspaceRecords
    users.value = userRecords
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
      <p>这里只保留部署后真正需要看的平台状态：客户、工作空间、账号和规则包入口。</p>
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
      <h2>当前识别路线</h2>
      <span class="muted-line">识别行为必须来自工作区启用的规则包，不再展示旧面单模板配置。</span>
    </div>
    <el-descriptions :column="1" border>
      <el-descriptions-item label="规则包">
        每个工作区需要在 5173 管理页面导入并启用识别规则包；没有启用规则包时，解析接口应提示先导入规则包。
      </el-descriptions-item>
      <el-descriptions-item label="面单解析">
        采集数据进入面单解析页后，应输出可审核的订单行；多商品面单拆成多行，特殊单保留可读状态。
      </el-descriptions-item>
      <el-descriptions-item label="商品匹配">
        商品匹配只在管理页面维护学习记录，业务页面不再暴露商品匹配工作台。
      </el-descriptions-item>
      <el-descriptions-item label="导出">
        导出中心只消费解析和匹配后的订单行，正常行与异常行数量必须可核对。
      </el-descriptions-item>
    </el-descriptions>
  </section>

  <section class="work-surface">
    <div class="section-title-row">
      <h2>工作区入口</h2>
      <span class="muted-line">客户现场配置统一在业务管理页完成，服务端管理页只看平台级状态。</span>
    </div>
    <el-table :data="workspaces" height="360" stripe>
      <el-table-column label="工作空间" min-width="220">
        <template #default="{ row }">{{ textValue(row.name) }}</template>
      </el-table-column>
      <el-table-column label="编码" min-width="180">
        <template #default="{ row }">{{ textValue(row.code) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.is_enabled === false ? 'info' : 'success'">
            {{ row.is_enabled === false ? '停用' : '启用' }}
          </el-tag>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无工作空间" />
      </template>
    </el-table>
  </section>
</template>
