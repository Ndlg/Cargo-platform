<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'

import { login } from '../services/api'
import { useSessionStore } from '../stores/session'

const router = useRouter()
const route = useRoute()
const session = useSessionStore()
const username = ref('admin')
const password = ref('admin123')
const loading = ref(false)
const error = ref('')

async function submit() {
  loading.value = true
  error.value = ''
  try {
    const response = await login(username.value, password.value)
    session.setToken(response.access_token)
    await session.loadCurrentUser()
    const queryRedirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
    const defaultRedirect = typeof route.meta.defaultRedirect === 'string' ? route.meta.defaultRedirect : '/'
    await router.push(queryRedirect || defaultRedirect)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-screen">
    <section class="login-panel">
      <h1>面单整理系统</h1>
      <p>字段驱动的面单读取与报表平台</p>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="username" :prefix-icon="User" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" :prefix-icon="Lock" show-password type="password" />
        </el-form-item>
        <el-alert v-if="error" :closable="false" :title="error" type="error" />
        <el-button class="login-button" :loading="loading" type="primary" @click="submit">
          登录
        </el-button>
      </el-form>
    </section>
  </main>
</template>
