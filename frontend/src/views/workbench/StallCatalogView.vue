<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Delete, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createRecord,
  deleteRecord,
  getRecords,
  updateRecord,
  type ApiRecord,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

type StallRecord = ApiRecord & {
  id: number
  name: string
  contact_name?: string | null
  remark?: string | null
  is_enabled?: boolean
}

const session = useSessionStore()
const stalls = ref<StallRecord[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref('')

const form = reactive({
  name: '',
  contact_name: '',
  remark: '',
})

const activeCount = computed(() => stalls.value.filter((stall) => stall.is_enabled !== false).length)

async function load() {
  loading.value = true
  error.value = ''
  try {
    stalls.value = (await getRecords('/stalls?limit=2000')) as StallRecord[]
  } catch (err) {
    error.value = err instanceof Error ? err.message : '档口库加载失败'
  } finally {
    loading.value = false
  }
}

async function saveStall() {
  const name = form.name.trim()
  if (!name) {
    error.value = '档口名称不能为空。'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await createRecord('/stalls', {
      name,
      contact_name: form.contact_name.trim() || null,
      remark: form.remark.trim() || null,
      is_enabled: true,
    })
    form.name = ''
    form.contact_name = ''
    form.remark = ''
    await load()
    ElMessage.success('档口已保存')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '档口保存失败'
  } finally {
    saving.value = false
  }
}

async function updateStall(row: StallRecord, payload: Partial<StallRecord>) {
  error.value = ''
  try {
    await updateRecord(`/stalls/${row.id}`, payload)
    await load()
    ElMessage.success('档口已更新')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '档口更新失败'
    await load()
  }
}

async function saveRow(row: StallRecord) {
  const name = String(row.name || '').trim()
  if (!name) {
    error.value = '档口名称不能为空。'
    await load()
    return
  }
  await updateStall(row, {
    name,
    contact_name: row.contact_name ? String(row.contact_name).trim() : null,
    remark: row.remark ? String(row.remark).trim() : null,
    is_enabled: row.is_enabled !== false,
  })
}

async function removeStall(row: StallRecord) {
  try {
    await ElMessageBox.confirm(
      `确定删除档口“${row.name}”吗？`,
      '删除档口',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  error.value = ''
  try {
    await deleteRecord(`/stalls/${row.id}`)
    await load()
    ElMessage.success('档口已删除')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '档口删除失败'
  }
}

watch(() => session.currentWorkspaceId, load)
onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>档口库</h1>
      <p>当前启用 {{ activeCount }} 个档口。</p>
    </div>
    <el-button :icon="Refresh" :loading="loading" plain @click="load">刷新</el-button>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="work-surface">
    <h2>新增档口</h2>
    <el-form label-position="top" class="inline-config-form stall-form-grid">
      <el-form-item label="档口名称">
        <el-input v-model="form.name" />
      </el-form-item>
      <el-form-item label="联系人">
        <el-input v-model="form.contact_name" />
      </el-form-item>
      <el-form-item label="备注">
        <el-input v-model="form.remark" />
      </el-form-item>
      <div class="form-action-row">
        <el-button type="primary" :loading="saving" @click="saveStall">保存档口</el-button>
      </div>
    </el-form>
  </section>

  <section class="work-surface">
    <div class="section-title-row">
      <h2>已有档口</h2>
      <span class="muted-line">{{ stalls.length }} 条</span>
    </div>
    <el-table v-if="stalls.length" v-loading="loading" :data="stalls" row-key="id" class="roomy-table" stripe>
      <el-table-column label="档口名称" min-width="220">
        <template #default="{ row }">
          <el-input v-model="row.name" @change="saveRow(row)" />
        </template>
      </el-table-column>
      <el-table-column label="联系人" min-width="180">
        <template #default="{ row }">
          <el-input v-model="row.contact_name" @change="saveRow(row)" />
        </template>
      </el-table-column>
      <el-table-column label="备注" min-width="260">
        <template #default="{ row }">
          <el-input v-model="row.remark" @change="saveRow(row)" />
        </template>
      </el-table-column>
      <el-table-column label="状态" width="150" align="center">
        <template #default="{ row }">
          <el-switch
            v-model="row.is_enabled"
            active-text="启用"
            inactive-text="停用"
            inline-prompt
            @change="updateStall(row, { is_enabled: row.is_enabled !== false })"
          />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" align="center">
        <template #default="{ row }">
          <el-button :icon="Delete" link type="danger" @click="removeStall(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-else v-loading="loading" description="还没有档口。" />
  </section>
</template>
