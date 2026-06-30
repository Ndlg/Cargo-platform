<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DataLine, Grid, Tickets } from '@element-plus/icons-vue'

import { useSessionStore } from '../stores/session'

interface ShellNavItem {
  label: string
  path: string
}

interface PortalItem {
  label: string
  value: string
  path: string
}

const props = defineProps<{
  navItems: ShellNavItem[]
  sectionTitle: string
  sectionDescription: string
  portalItems?: PortalItem[]
  showWorkspaceSelector?: boolean
  showApiStatus?: boolean
}>()

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const defaultPortalItems: PortalItem[] = [
  { label: '业务页面', value: 'business', path: '/' },
  { label: '管理页面', value: 'admin', path: '/admin' },
]

const portalOptions = computed(() => props.portalItems ?? defaultPortalItems)
const sortedPortalOptions = computed(() =>
  [...portalOptions.value].sort((left, right) => right.path.length - left.path.length),
)

const entryMode = computed({
  get: () => {
    return (
      sortedPortalOptions.value.find((item) => route.path.startsWith(item.path))?.value ??
      portalOptions.value[0]?.value ??
      'business'
    )
  },
  set: async (value: string) => {
    const selected = portalOptions.value.find((item) => item.value === value)
    await router.push(selected?.path ?? portalOptions.value[0]?.path ?? '/')
  },
})

const selectedWorkspaceId = computed({
  get: () => session.currentWorkspaceId,
  set: (workspaceId: number | null) => {
    if (workspaceId) {
      session.setCurrentWorkspace(workspaceId)
    }
  },
})

const userLabel = computed(() => session.user?.display_name || session.user?.username || '未加载用户')

function logout() {
  session.clearSession()
  router.push('/login')
}
</script>

<template>
  <el-container class="app-shell">
    <el-aside class="sidebar" width="260px">
      <div class="brand">
        <el-icon><Tickets /></el-icon>
        <span>面单整理系统</span>
      </div>
      <div v-if="portalOptions.length > 1" class="entry-switch">
        <el-radio-group v-model="entryMode" size="small">
          <el-radio-button
            v-for="portal in portalOptions"
            :key="portal.value"
            :value="portal.value"
          >
            {{ portal.label }}
          </el-radio-button>
        </el-radio-group>
      </div>
      <div v-else class="entry-lock">
        {{ portalOptions[0]?.label ?? sectionTitle }}
      </div>
      <el-scrollbar class="nav-scroll">
        <el-menu :default-active="route.path" router>
          <el-menu-item v-for="item in navItems" :key="item.path" :index="item.path">
            <el-icon><Grid /></el-icon>
            <span>{{ item.label }}</span>
          </el-menu-item>
        </el-menu>
      </el-scrollbar>
    </el-aside>

    <el-container>
      <el-header class="topbar">
        <div>
          <div class="eyebrow">{{ sectionTitle }}</div>
          <strong>{{ userLabel }}</strong>
          <span class="topbar-description">{{ sectionDescription }}</span>
        </div>
        <div class="topbar-actions">
          <el-select
            v-if="props.showWorkspaceSelector !== false && session.workspaceOptions.length"
            v-model="selectedWorkspaceId"
            class="workspace-select"
            placeholder="选择工作空间"
          >
            <el-option
              v-for="workspace in session.workspaceOptions"
              :key="workspace.id"
              :label="workspace.name"
              :value="workspace.id"
            />
          </el-select>
          <el-tag
            v-else-if="props.showWorkspaceSelector !== false"
            type="danger"
          >
            无可用工作空间
          </el-tag>
          <el-tag v-if="props.showApiStatus !== false" type="success" effect="plain">
            <el-icon><DataLine /></el-icon>
            接口就绪
          </el-tag>
          <el-button plain @click="logout">退出</el-button>
        </div>
      </el-header>

      <el-main class="main-panel">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>
