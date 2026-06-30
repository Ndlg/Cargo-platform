<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Box, Connection, Document, Guide, Setting } from '@element-plus/icons-vue'

import { useSessionStore } from '../../stores/session'

const router = useRouter()
const session = useSessionStore()
const workspaceName = computed(() => session.currentWorkspace?.name ?? '未选择工作空间')

const configCards = [
  {
    label: '采集连接',
    description: '绑定公司业务机上的采集器，上传打印组件读取到的原始数据。',
    path: '/admin/collector-connections',
    icon: Connection,
  },
  {
    label: '档口库',
    description: '维护供商品和 SKU 选择的档口，导出时可按档口分 Sheet 或分文档。',
    path: '/admin/stalls',
    icon: Box,
  },
  {
    label: '商品/SKU',
    description: '维护商品默认档口和 SKU 图片，特殊 SKU 可单独覆盖档口。',
    path: '/admin/products',
    icon: Box,
  },
  {
    label: '识别规则包',
    description: '导入、启用或切换当前商品场景的面单识别规则包；没有启用规则包时系统不做隐藏识别。',
    path: '/admin/recognition-rule-packs',
    icon: Guide,
  },
  {
    label: '商品匹配',
    description: '基于面单解析后的订单行，维护商品、SKU 和图片的匹配学习记录。',
    path: '/admin/product-matching',
    icon: Box,
  },
  {
    label: '导出表头',
    description: '查看抖店面单读取到的字段含义，并定义整理文档的 Excel 表头。',
    path: '/admin/export-headers',
    icon: Document,
  },
  {
    label: '系统设置',
    description: '归档和清理采集器回传数据，控制历史数据是否继续参与日常维护。',
    path: '/admin/system-settings',
    icon: Setting,
  },
]
</script>

<template>
  <section class="page-header">
    <div>
      <h1>管理页面</h1>
      <p>{{ workspaceName }}。公司管理员维护采集连接、档口库、商品/SKU、导出表头和系统设置。</p>
    </div>
  </section>

  <section class="work-surface">
    <h2>配置入口</h2>
    <div class="process-grid">
      <article v-for="card in configCards" :key="card.path" class="process-card">
        <el-icon><component :is="card.icon" /></el-icon>
        <div>
          <strong>{{ card.label }}</strong>
          <p>{{ card.description }}</p>
        </div>
        <el-button type="primary" plain @click="router.push(card.path)">进入</el-button>
      </article>
    </div>
  </section>
</template>
