<script setup lang="ts">
import AppShell from './AppShell.vue'
import { serverAdminNavigationItems } from '../router/serverAdminRoutes'

const serverAdminNavItems = serverAdminNavigationItems.map((item) => ({
  label: item.label,
  path: item.path,
}))

const serverAdminPortalItems = [
  { label: '平台后台', value: 'admin', path: '/admin' },
]
</script>

<template>
  <AppShell
    :nav-items="serverAdminNavItems"
    :portal-items="serverAdminPortalItems"
    :show-workspace-selector="false"
    :show-api-status="false"
    section-title="平台后台"
    section-description="平台后台只保留部署、账号和解析能力概览"
  />
</template>
