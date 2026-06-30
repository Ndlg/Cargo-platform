<script setup lang="ts">
import AppShell from './AppShell.vue'

const clientAdminNavItems = [
  { label: '管理首页', path: '/admin' },
  { label: '采集连接', path: '/admin/collector-connections' },
  { label: '档口库', path: '/admin/stalls' },
  { label: '商品/SKU', path: '/admin/products' },
  { label: '商品匹配', path: '/admin/product-matching' },
  { label: '识别规则包', path: '/admin/recognition-rule-packs' },
  { label: '导出表头', path: '/admin/export-headers' },
  { label: '系统设置', path: '/admin/system-settings' },
]
</script>

<template>
  <AppShell
    :nav-items="clientAdminNavItems"
    section-title="管理页面"
    section-description="公司管理员维护本工作空间的采集连接、档口库、商品资料、导出表头和系统设置"
  />
</template>
