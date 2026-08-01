<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'

import {
  getAuthSetupStatus,
  login,
  setupSystemAdmin,
  type AuthSetupStatus,
} from '../services/api'
import { useSessionStore } from '../stores/session'

const router = useRouter()
const route = useRoute()
const session = useSessionStore()
const username = ref('')
const password = ref('')
const setupStatus = ref<AuthSetupStatus | null>(null)
const setupToken = ref('')
const displayName = ref('Administrator')
const newPassword = ref('')
const confirmPassword = ref('')
const statusLoading = ref(true)
const loading = ref(false)
const error = ref('')

async function finishLogin(accessToken: string) {
  session.setToken(accessToken)
  await session.loadCurrentUser()
  const queryRedirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
  const defaultRedirect = typeof route.meta.defaultRedirect === 'string' ? route.meta.defaultRedirect : '/'
  await router.push(queryRedirect || defaultRedirect)
}

async function loadSetupStatus() {
  statusLoading.value = true
  error.value = ''
  try {
    setupStatus.value = await getAuthSetupStatus()
  } catch (err) {
    setupStatus.value = null
    error.value = err instanceof Error ? err.message : '无法读取系统初始化状态'
  } finally {
    statusLoading.value = false
  }
}

async function submitLogin() {
  loading.value = true
  error.value = ''
  try {
    const response = await login(username.value, password.value)
    await finishLogin(response.access_token)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '登录失败'
  } finally {
    loading.value = false
  }
}

async function submitSetup() {
  error.value = ''
  if (newPassword.value.length < 12) {
    error.value = '新密码至少需要 12 个字符。'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    error.value = '两次输入的密码不一致。'
    return
  }

  loading.value = true
  try {
    const response = await setupSystemAdmin({
      setup_token: setupToken.value,
      display_name: displayName.value,
      password: newPassword.value,
    })
    setupToken.value = ''
    await finishLogin(response.access_token)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '系统初始化失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadSetupStatus)
</script>

<template>
  <main class="login-screen">
    <section class="login-panel">
      <h1>面单整理系统</h1>
      <p>字段驱动的面单读取与报表平台</p>
      <div v-if="statusLoading" class="login-loading">正在检查系统状态…</div>
      <template v-else-if="setupStatus?.required">
        <el-alert
          v-if="!setupStatus.available"
          :closable="false"
          title="系统尚未初始化，服务器未配置初始化令牌。"
          type="error"
        />
        <el-form v-else label-position="top" @submit.prevent="submitSetup">
          <el-alert
            :closable="false"
            title="首次使用：请设置系统管理员密码。"
            type="warning"
          />
          <el-form-item label="初始化令牌">
            <el-input v-model="setupToken" autocomplete="one-time-code" show-password type="password" />
          </el-form-item>
          <el-form-item label="管理员名称">
            <el-input v-model="displayName" :prefix-icon="User" autocomplete="name" />
          </el-form-item>
          <el-form-item label="新密码">
            <el-input
              v-model="newPassword"
              :prefix-icon="Lock"
              autocomplete="new-password"
              show-password
              type="password"
            />
          </el-form-item>
          <el-form-item label="确认新密码">
            <el-input
              v-model="confirmPassword"
              :prefix-icon="Lock"
              autocomplete="new-password"
              show-password
              type="password"
            />
          </el-form-item>
          <el-alert v-if="error" :closable="false" :title="error" type="error" />
          <el-button class="login-button" :loading="loading" type="primary" @click="submitSetup">
            完成初始化并登录
          </el-button>
        </el-form>
      </template>
      <el-form v-else-if="setupStatus" label-position="top" @submit.prevent="submitLogin">
        <el-form-item label="用户名">
          <el-input v-model="username" :prefix-icon="User" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="password"
            :prefix-icon="Lock"
            autocomplete="current-password"
            show-password
            type="password"
          />
        </el-form-item>
        <el-alert v-if="error" :closable="false" :title="error" type="error" />
        <el-button class="login-button" :loading="loading" type="primary" @click="submitLogin">
          登录
        </el-button>
      </el-form>
      <div v-else>
        <el-alert v-if="error" :closable="false" :title="error" type="error" />
        <el-button class="login-button" @click="loadSetupStatus">重新检查</el-button>
      </div>
    </section>
  </main>
</template>
