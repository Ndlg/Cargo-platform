<script setup lang="ts">
import AppShell from './AppShell.vue'

const clientAdminNavItems = [
  { label: '管理首页', path: '/admin' },
  { label: '采集连接', path: '/admin/collector-connections' },
  { label: '档口库', path: '/admin/stalls' },
  { label: '商品/SKU', path: '/admin/products' },
  { label: '打印模板规则', path: '/admin/print-template-rules' },
  { label: '商品识别', path: '/admin/matching' },
  { label: '导出表头', path: '/admin/export-headers' },
]
</script>

<template>
  <AppShell
    :nav-items="clientAdminNavItems"
    section-title="管理页面"
    section-description="公司管理员维护本工作空间的采集连接、档口库、商品资料、打印模板规则、商品识别规则和导出表头"
  />
</template>
