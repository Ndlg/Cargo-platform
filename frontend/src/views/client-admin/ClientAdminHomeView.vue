<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Box, Connection, Document, Operation, Search } from '@element-plus/icons-vue'

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
    label: '打印模板规则',
    description: '专门维护我打面单模板，按采集样本定义商品、销售属性和数量拆分。',
    path: '/admin/print-template-rules',
    icon: Operation,
  },
  {
    label: '商品识别',
    description: '查看平台模板字段，选择参与识别的字段和关键词，把面单文字关联到自己的商品。',
    path: '/admin/matching',
    icon: Search,
  },
  {
    label: '导出表头',
    description: '查看抖店面单读取到的字段含义，并定义整理文档的 Excel 表头。',
    path: '/admin/export-headers',
    icon: Document,
  },
]
</script>

<template>
  <section class="page-header">
    <div>
      <h1>管理页面</h1>
      <p>{{ workspaceName }}。公司管理员维护采集连接、档口库、商品/SKU、打印模板规则、商品识别和导出表头。</p>
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
