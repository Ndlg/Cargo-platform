<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'

import {
  createPlatformCustomerAccount,
  getPlatformCustomerAccounts,
  resetPlatformCustomerPassword,
  type PlatformCustomerAccountCreatePayload,
  type PlatformCustomerAccountsResponse,
  type PlatformCustomerUser,
} from '../../services/api'

const loading = ref(false)
const creating = ref(false)
const resetting = ref(false)
const error = ref('')
const accounts = ref<PlatformCustomerAccountsResponse>({
  tenants: [],
  workspaces: [],
  users: [],
})

const createForm = reactive<PlatformCustomerAccountCreatePayload>({
  tenant_name: '',
  tenant_code: '',
  workspace_name: '',
  workspace_code: '',
  username: '',
  display_name: '',
  password: '',
})

const resetDialogVisible = ref(false)
const resetUser = ref<PlatformCustomerUser | null>(null)
const resetPassword = ref('')

const stats = computed(() => [
  { label: '客户', value: accounts.value.tenants.length },
  { label: '工作空间', value: accounts.value.workspaces.length },
  { label: '账号', value: accounts.value.users.length },
  {
    label: '启用账号',
    value: accounts.value.users.filter((user) => user.is_enabled).length,
  },
])

function textValue(value: unknown, fallback = '-'): string {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function adminUsersText(users: Array<{ username: string; display_name: string; role_name: string }>): string {
  if (!users.length) return '-'
  return users
    .map((user) => `${user.display_name || user.username} / ${user.username} / ${user.role_name}`)
    .join('，')
}

function membershipText(user: PlatformCustomerUser): string {
  if (!user.memberships.length) return '-'
  return user.memberships
    .map((membership) => `${membership.workspace_name} / ${membership.role_name}`)
    .join('，')
}

function resetCreateForm() {
  createForm.tenant_name = ''
  createForm.tenant_code = ''
  createForm.workspace_name = ''
  createForm.workspace_code = ''
  createForm.username = ''
  createForm.display_name = ''
  createForm.password = ''
}

function validateCreateForm(): boolean {
  const requiredFields: Array<[keyof PlatformCustomerAccountCreatePayload, string]> = [
    ['tenant_name', '客户名称'],
    ['tenant_code', '客户编码'],
    ['workspace_name', '工作空间名称'],
    ['workspace_code', '工作空间编码'],
    ['username', '登录账号'],
    ['display_name', '显示名称'],
    ['password', '初始密码'],
  ]
  const missing = requiredFields.find(([key]) => !createForm[key].trim())
  if (missing) {
    ElMessage.warning(`请填写${missing[1]}`)
    return false
  }
  if (createForm.password.length < 6) {
    ElMessage.warning('初始密码至少 6 位')
    return false
  }
  return true
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    accounts.value = await getPlatformCustomerAccounts()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '客户账号加载失败。'
  } finally {
    loading.value = false
  }
}

async function submitCreate() {
  if (!validateCreateForm()) return
  creating.value = true
  try {
    await createPlatformCustomerAccount({
      tenant_name: createForm.tenant_name.trim(),
      tenant_code: createForm.tenant_code.trim(),
      workspace_name: createForm.workspace_name.trim(),
      workspace_code: createForm.workspace_code.trim(),
      username: createForm.username.trim(),
      display_name: createForm.display_name.trim(),
      password: createForm.password,
    })
    ElMessage.success('客户账号已创建')
    resetCreateForm()
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '客户账号创建失败')
  } finally {
    creating.value = false
  }
}

function openResetPassword(user: PlatformCustomerUser) {
  resetUser.value = user
  resetPassword.value = ''
  resetDialogVisible.value = true
}

async function submitResetPassword() {
  if (!resetUser.value) return
  if (resetPassword.value.length < 6) {
    ElMessage.warning('新密码至少 6 位')
    return
  }
  resetting.value = true
  try {
    await resetPlatformCustomerPassword(resetUser.value.id, resetPassword.value)
    ElMessage.success('密码已重置')
    resetDialogVisible.value = false
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '密码重置失败')
  } finally {
    resetting.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>客户账号</h1>
      <p>这里维护客户登录入口：新增客户时会同时创建客户、工作空间、业务管理员账号和绑定关系。</p>
    </div>
    <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="stat-grid">
    <article v-for="stat in stats" :key="stat.label" class="stat-tile">
      <span>{{ stat.label }}</span>
      <strong>{{ stat.value }}</strong>
      <small>平台账号维护</small>
    </article>
  </section>

  <section class="work-surface">
    <div class="section-title-row">
      <h2>新增客户账号</h2>
      <span class="muted-line">业务商品、模板和导出规则仍然到客户工作台维护。</span>
    </div>

    <el-form class="customer-account-form" label-position="top">
      <el-form-item label="客户名称">
        <el-input v-model="createForm.tenant_name" placeholder="例如：示例客户" />
      </el-form-item>
      <el-form-item label="客户编码">
        <el-input v-model="createForm.tenant_code" placeholder="例如：demo" />
      </el-form-item>
      <el-form-item label="工作空间名称">
        <el-input v-model="createForm.workspace_name" placeholder="例如：示例业务空间" />
      </el-form-item>
      <el-form-item label="工作空间编码">
        <el-input v-model="createForm.workspace_code" placeholder="例如：demo-main" />
      </el-form-item>
      <el-form-item label="登录账号">
        <el-input v-model="createForm.username" placeholder="客户登录用户名" />
      </el-form-item>
      <el-form-item label="显示名称">
        <el-input v-model="createForm.display_name" placeholder="例如：业务管理员" />
      </el-form-item>
      <el-form-item label="初始密码">
        <el-input v-model="createForm.password" placeholder="至少 6 位" show-password type="password" />
      </el-form-item>
    </el-form>

    <div class="form-actions">
      <el-button :icon="Plus" :loading="creating" type="primary" @click="submitCreate">
        创建客户账号
      </el-button>
    </div>
  </section>

  <section class="work-surface">
    <div class="section-title-row">
      <h2>客户工作空间</h2>
      <span class="muted-line">一个客户可以后续扩展多个工作空间；当前先保持一客户一业务空间。</span>
    </div>
    <el-table :data="accounts.workspaces" stripe>
      <el-table-column label="客户" min-width="180">
        <template #default="{ row }">{{ textValue(row.tenant_name) }}</template>
      </el-table-column>
      <el-table-column label="客户编码" min-width="140">
        <template #default="{ row }">{{ textValue(row.tenant_code) }}</template>
      </el-table-column>
      <el-table-column label="工作空间" min-width="200">
        <template #default="{ row }">{{ textValue(row.name) }}</template>
      </el-table-column>
      <el-table-column label="空间编码" min-width="150">
        <template #default="{ row }">{{ textValue(row.code) }}</template>
      </el-table-column>
      <el-table-column label="管理员账号" min-width="320">
        <template #default="{ row }">{{ adminUsersText(row.admin_users) }}</template>
      </el-table-column>
      <template #empty>
        <el-empty description="还没有客户工作空间" />
      </template>
    </el-table>
  </section>

  <section class="work-surface">
    <div class="section-title-row">
      <h2>登录账号</h2>
      <span class="muted-line">现场忘记密码时，在这里重置后让客户重新登录。</span>
    </div>
    <el-table :data="accounts.users" stripe>
      <el-table-column label="登录账号" min-width="180">
        <template #default="{ row }">{{ textValue(row.username) }}</template>
      </el-table-column>
      <el-table-column label="显示名称" min-width="180">
        <template #default="{ row }">{{ textValue(row.display_name) }}</template>
      </el-table-column>
      <el-table-column label="所属工作空间" min-width="300">
        <template #default="{ row }">{{ membershipText(row) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="row.is_enabled ? 'success' : 'info'">
            {{ row.is_enabled ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button link type="primary" @click="openResetPassword(row)">重置密码</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="还没有登录账号" />
      </template>
    </el-table>
  </section>

  <el-dialog v-model="resetDialogVisible" title="重置密码" width="420px">
    <el-form label-position="top">
      <el-form-item label="账号">
        <el-input :model-value="resetUser?.username ?? '-'" disabled />
      </el-form-item>
      <el-form-item label="新密码">
        <el-input v-model="resetPassword" placeholder="至少 6 位" show-password type="password" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="resetDialogVisible = false">取消</el-button>
      <el-button :loading="resetting" type="primary" @click="submitResetPassword">保存新密码</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.customer-account-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
  gap: 4px 18px;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}

@media (max-width: 860px) {
  .customer-account-form {
    grid-template-columns: 1fr;
  }
}
</style>
