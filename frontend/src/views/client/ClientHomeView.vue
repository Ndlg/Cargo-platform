<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { CircleClose, Document, Download, Upload, VideoPlay, Warning } from '@element-plus/icons-vue'

import { useSessionStore } from '../../stores/session'

const router = useRouter()
const session = useSessionStore()
const workspaceName = computed(() => session.currentWorkspace?.name ?? '未选择工作空间')

const statusTiles = [
  { label: '采集状态', value: '待开始', note: '由业务页面统一控制本轮采集' },
  { label: '采集记录', value: '0', note: '等待业务机采集器上传' },
  { label: '面单批次', value: '0', note: '等待上传或读取面单' },
  { label: '最近导出', value: '0', note: '导出记录会在生成后显示' },
]

const captureControls = [
  {
    label: '开始采集',
    description: '连接当前工作空间下的业务机采集器，准备接收本轮打印订单。',
    icon: VideoPlay,
    type: 'primary' as const,
  },
  {
    label: '结束采集',
    description: '关闭本轮采集批次，等待原始信息回传并进入平台处理。',
    icon: CircleClose,
    type: 'danger' as const,
  },
]

const nextActions = [
  {
    label: '采集记录',
    description: '查看采集器上传到当前工作空间的原始面单内容。',
    path: '/capture-records',
    icon: Document,
  },
  {
    label: '上传 / 读取面单',
    description: '创建面单批次，等待系统识别模式并生成标准化明细。',
    path: '/waybill-batches',
    icon: Upload,
  },
  {
    label: '处理异常',
    description: '处理面单无法解析、商品识别失败、SKU 缺失和导出配置缺失等异常。',
    path: '/exceptions',
    icon: Warning,
  },
  {
    label: '导出结果',
    description: '按管理员配置的字段、分组和文件拆分方式导出。',
    path: '/exports',
    icon: Download,
  },
]
</script>

<template>
  <section class="page-header">
    <div>
      <h1>业务页面</h1>
      <p>{{ workspaceName }}。一线人员从这里控制采集、打印后的处理、异常和导出。</p>
    </div>
    <el-button :icon="Document" type="primary" @click="router.push('/capture-records')">
      采集控制
    </el-button>
  </section>

  <section class="stat-grid">
    <article v-for="item in statusTiles" :key="item.label" class="stat-tile">
      <span>{{ item.label }}</span>
      <strong>{{ item.value }}</strong>
      <small>{{ item.note }}</small>
    </article>
  </section>

  <section class="work-surface">
    <h2>本轮采集</h2>
    <div class="process-grid">
      <article v-for="control in captureControls" :key="control.label" class="process-card">
        <el-icon><component :is="control.icon" /></el-icon>
        <div>
          <strong>{{ control.label }}</strong>
          <p>{{ control.description }}</p>
        </div>
        <el-button :type="control.type" plain @click="router.push('/capture-records')">
          {{ control.label }}
        </el-button>
      </article>
    </div>
  </section>

  <section class="work-surface">
    <h2>下一步操作</h2>
    <div class="process-grid">
      <article v-for="action in nextActions" :key="action.path" class="process-card">
        <el-icon><component :is="action.icon" /></el-icon>
        <div>
          <strong>{{ action.label }}</strong>
          <p>{{ action.description }}</p>
        </div>
        <el-button type="primary" plain @click="router.push(action.path)">进入</el-button>
      </article>
    </div>
  </section>
</template>
