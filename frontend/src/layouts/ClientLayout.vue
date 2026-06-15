<script setup lang="ts">
import AppShell from './AppShell.vue'

const clientNavItems = [
  { label: '业务首页', path: '/' },
  { label: '采集记录', path: '/capture-records' },
  { label: '面单批次', path: '/waybill-batches' },
  { label: '异常处理', path: '/exceptions' },
  { label: '导出中心', path: '/exports' },
]
</script>

<template>
  <AppShell
    :nav-items="clientNavItems"
    section-title="业务页面"
    section-description="一线业务人员处理采集、上传、异常和导出"
  />
</template>
