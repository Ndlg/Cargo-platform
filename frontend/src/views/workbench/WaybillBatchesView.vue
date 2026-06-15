<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Download, Refresh, Right } from '@element-plus/icons-vue'

import {
  downloadCaptureTaskDocument,
  getRecords,
  saveBlob,
  type ApiRecord,
  type CaptureTaskRecord,
  type CollectorRecord,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

type StandardDetailRecord = ApiRecord & {
  id: number
  waybill_mode?: string | null
  field_values?: Record<string, unknown>
  image_match_status?: string
  stall_match_status?: string
}

const router = useRouter()
const session = useSessionStore()
const details = ref<StandardDetailRecord[]>([])
const captureTasks = ref<CaptureTaskRecord[]>([])
const collectors = ref<CollectorRecord[]>([])
const rawRecords = ref<ApiRecord[]>([])
const selectedTaskId = ref<number | null>(null)
const keyword = ref('')
const sourceFilter = ref('')
const currentPage = ref(1)
const pageSize = ref(50)
const loading = ref(false)
const downloadingKey = ref('')
const error = ref('')

const sortedTasks = computed(() => [...captureTasks.value].sort((a, b) => b.id - a.id))
const selectedTask = computed(
  () => sortedTasks.value.find((task) => task.id === selectedTaskId.value) ?? null,
)
const taskDetails = computed(() => {
  if (!selectedTaskId.value) return []
  return details.value.filter((detail) => Number(detail.field_values?.capture_task_id) === selectedTaskId.value)
})
const goodsDetails = computed(() => taskDetails.value)
const sourceOptions = computed(() => {
  const values = new Set(goodsDetails.value.map((detail) => sourceLabel(detail)).filter(Boolean))
  return [...values]
})
const filteredGoodsDetails = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return goodsDetails.value.filter((detail) => {
    const matchesSource = !sourceFilter.value || sourceLabel(detail) === sourceFilter.value
    if (!matchesSource) return false
    if (!query) return true
    const haystack = [
      goodsText(detail),
      sourceLabel(detail),
      field(detail, 'raw_document_id'),
      field(detail, 'logistics_no'),
      field(detail, 'order_no'),
      field(detail, 'shop_name'),
    ]
      .join('\n')
      .toLowerCase()
    return haystack.includes(query)
  })
})
const pagedGoodsDetails = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredGoodsDetails.value.slice(start, start + pageSize.value)
})
const limitedRawRecords = computed(() => {
  if (!selectedTaskId.value) return []
  return rawRecords.value.filter((record) => {
    const status = String(record.status ?? '')
    return Number(record.task_id) === selectedTaskId.value && status === 'unsupported'
  })
})
const limitedCount = computed(() => limitedRawRecords.value.length)
const selectedTaskIndex = computed(() =>
  sortedTasks.value.findIndex((task) => task.id === selectedTaskId.value),
)
const selectedTaskTitle = computed(() => {
  if (!selectedTask.value) return '暂无采集'
  return taskLabel(selectedTask.value, selectedTaskIndex.value)
})
const douyinCount = computed(
  () => goodsDetails.value.filter((detail) => detail.waybill_mode === 'douyin_cloud_print').length,
)
const cainiaoDirectCount = computed(
  () => goodsDetails.value.filter((detail) => detail.waybill_mode === 'cainiao_direct_shop').length,
)
const wodaCount = computed(
  () => goodsDetails.value.filter((detail) => detail.waybill_mode === 'cainiao_woda_printxml').length,
)

const fieldLabels: Record<string, string> = {
  source_platform: '来源平台',
  logistics_no: '物流单号',
  order_no: '平台订单号',
  shop_name: '店铺名',
  product_short_text: '商品简称',
  product_full_text: '商品信息',
  spec_text: '规格文本',
  quantity: '数量',
  buyer_remark: '买家备注',
  seller_remark: '卖家备注',
  buyer_nick: '买家昵称',
  print_time: '打印时间',
  pay_order_time: '付款时间',
  create_order_time: '下单时间',
  item_total_price: '商品金额',
  item_total_count: '原始数量/金额字段',
  encrypted_waybill: '标准面单已加密',
  custom_area_kind: '自定义区类型',
  custom_area_raw_text: '客户自定义区原文',
  custom_area_lines: '自定义区行文字',
  print_template_key: '打印模板识别码',
  print_template_source: '模板识别来源',
  sender_masked: '发件方脱敏信息',
  recipient_masked: '收件方脱敏信息',
  template_urls: '模板地址',
}

function field(row: StandardDetailRecord, key: string, fallback = '-'): string {
  const value = row.field_values?.[key]
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function rawRecordForDetail(row: StandardDetailRecord): ApiRecord | null {
  const rawRecordId = Number(row.field_values?.raw_record_id)
  if (!rawRecordId) return null
  return rawRecords.value.find((record) => Number(record.id) === rawRecordId) ?? null
}

function collectorNameForRecord(record: ApiRecord | null): string {
  const collectorId = Number(record?.collector_id)
  if (!collectorId) return ''
  return collectors.value.find((collector) => collector.id === collectorId)?.collector_name ?? ''
}

function sourceOwnerLabel(record: ApiRecord | null): string {
  if (!record) return ''
  return collectorNameForRecord(record) || String(record.source_machine ?? '')
}

function platformLabel(row: StandardDetailRecord): string {
  const platform = field(row, 'source_platform')
  if (platform === 'douyin') return '抖店'
  if (platform === 'woda') return '菜鸟 / 我打'
  if (platform === 'cainiao_direct_shop') return '菜鸟直打'
  return platform
}

function sourceLabel(row: StandardDetailRecord): string {
  const record = rawRecordForDetail(row)
  const owner = sourceOwnerLabel(record)
  return owner ? `${owner} / ${platformLabel(row)}` : platformLabel(row)
}

function goodsText(row: StandardDetailRecord): string {
  if (field(row, 'source_platform') === 'woda') {
    return field(
      row,
      'custom_product_text',
      field(row, 'product_short_text', field(row, 'product_full_text', field(row, 'custom_area_raw_text'))),
    )
  }
  return field(row, 'product_short_text', field(row, 'product_full_text', field(row, 'custom_area_raw_text')))
}

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function numberField(row: StandardDetailRecord, key: string): number | null {
  const value = Number(row.field_values?.[key])
  return Number.isInteger(value) && value > 0 ? value : null
}

function goodsRowLabel(row: StandardDetailRecord, pageIndex: number): string {
  const sequence = numberField(row, 'document_sequence')
  const itemIndex = numberField(row, 'custom_item_index')
  const itemCount = numberField(row, 'custom_item_count') ?? 1
  if (sequence) {
    return itemIndex && itemCount > 1 ? `面单 ${sequence}-${itemIndex}` : `面单 ${sequence}`
  }
  return `第 ${(currentPage.value - 1) * pageSize.value + pageIndex + 1} 行`
}

function statusLabel(status?: string | null): string {
  if (status === 'collecting') return '采集中'
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '失败'
  return status || '-'
}

function formatTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function detailFields(row: StandardDetailRecord) {
  const values = row.field_values ?? {}
  return [
    {
      key: 'internal_standard_detail_id',
      label: '内部记录 ID',
      value: row.id,
    },
    ...Object.entries(values)
      .filter(
        ([key]) =>
          ![
            'raw_record_id',
            'capture_task_id',
            'parser_template_code',
            'raw_document_id',
            'document_sequence',
            'source_index',
            'source_component',
            'source_platform',
            'daily_unique_order',
          ].includes(key),
      )
      .map(([key, value]) => ({
        key,
        label: fieldLabels[key] ?? key,
        value,
      })),
  ]
}

function taskLabel(task: CaptureTaskRecord, index = 0): string {
  const round = index <= 0 ? '最近一轮' : `上一轮 ${index}`
  return `${round}：${formatTime(task.started_at)} ${statusLabel(task.status)}`
}

function rawRecordReason(record: ApiRecord): string {
  const parsedPayload = record.parsed_payload
  if (!parsedPayload || typeof parsedPayload !== 'object') {
    return record.status === 'unsupported' ? '当前平台解析模板暂不支持该原始内容。' : '-'
  }
  const documents = (parsedPayload as Record<string, unknown>).documents
  if (Array.isArray(documents)) {
    const reason = documents
      .map((item) => (item && typeof item === 'object' ? (item as Record<string, unknown>).limited_reason : null))
      .find((item) => typeof item === 'string' && item)
    if (typeof reason === 'string') return reason
  }
  return record.status === 'unsupported' ? '当前平台解析模板暂不支持该原始内容。' : '-'
}

function rawRecordSourceLabel(record: ApiRecord): string {
  const component = String(record.source_component ?? '')
  const owner = sourceOwnerLabel(record)
  const prefix = owner ? `${owner} / ` : ''
  const parsedPayload = record.parsed_payload
  if (parsedPayload && typeof parsedPayload === 'object') {
    const documents = (parsedPayload as Record<string, unknown>).documents
    if (Array.isArray(documents)) {
      const platform = documents
        .map((item) => {
          if (!item || typeof item !== 'object') return ''
          const values = (item as Record<string, unknown>).field_values
          if (!values || typeof values !== 'object') return ''
          return String((values as Record<string, unknown>).source_platform ?? '')
        })
        .find(Boolean)
      if (platform === 'woda') return `${prefix}菜鸟 / 我打中转`
      if (platform) return `${prefix}${platform}`
    }
  }
  if (component === 'cainiao-cnprint') return `${prefix}菜鸟组件`
  if (component === 'cloud-print-client') return `${prefix}抖店 / CloudPrint`
  return owner || component || '-'
}

function rawRecordVisibleText(record: ApiRecord): string {
  const parsedPayload = record.parsed_payload
  if (parsedPayload && typeof parsedPayload === 'object') {
    const documents = (parsedPayload as Record<string, unknown>).documents
    if (Array.isArray(documents)) {
      const texts = documents
        .map((item) => {
          if (!item || typeof item !== 'object') return ''
          const values = (item as Record<string, unknown>).field_values
          if (!values || typeof values !== 'object') return ''
          return String((values as Record<string, unknown>).custom_area_raw_text ?? '').trim()
        })
        .filter(Boolean)
      if (texts.length) return texts.join('\n---\n')
    }
  }
  return '-'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [tasks, collectorRecords, standardDetails, records] = await Promise.all([
      getRecords('/capture-tasks?limit=2000'),
      getRecords('/collectors?limit=2000'),
      getRecords('/standard-details?limit=2000'),
      getRecords('/raw-capture-records?limit=2000'),
    ])
    captureTasks.value = tasks as CaptureTaskRecord[]
    collectors.value = collectorRecords as CollectorRecord[]
    details.value = standardDetails as StandardDetailRecord[]
    rawRecords.value = records
    const taskIds = new Set(captureTasks.value.map((task) => task.id))
    if (!selectedTaskId.value || !taskIds.has(selectedTaskId.value)) {
      selectedTaskId.value = sortedTasks.value[0]?.id ?? null
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '面单整理结果加载失败'
  } finally {
    loading.value = false
  }
}

async function downloadSelectedTaskDocument(kind: 'raw' | 'standard') {
  if (!selectedTaskId.value) {
    error.value = '请先选择采集任务。'
    return
  }
  downloadingKey.value = `${selectedTaskId.value}-${kind}`
  error.value = ''
  try {
    const { blob, filename } = await downloadCaptureTaskDocument(selectedTaskId.value, kind)
    saveBlob(blob, filename)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '采集任务文档下载失败'
  } finally {
    downloadingKey.value = ''
  }
}

watch(
  () => session.currentWorkspaceId,
  () => {
    selectedTaskId.value = null
    void load()
  },
)
watch([selectedTaskId, keyword, sourceFilter, pageSize], () => {
  currentPage.value = 1
})
onMounted(load)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>面单批次</h1>
      <p>按采集任务查看本轮读取到的商品信息，后续基于这些文字匹配图片和档口。</p>
    </div>
    <div class="action-row">
      <el-select
        v-model="selectedTaskId"
        class="task-select"
        filterable
        placeholder="选择采集任务"
        style="width: 360px"
      >
        <el-option
          v-for="(task, index) in sortedTasks"
          :key="task.id"
          :label="taskLabel(task, index)"
          :value="task.id"
        />
      </el-select>
      <el-button :icon="Refresh" :loading="loading" plain @click="load">刷新</el-button>
      <el-button
        :disabled="!selectedTaskId"
        :icon="Download"
        :loading="downloadingKey === `${selectedTaskId}-raw`"
        plain
        @click="downloadSelectedTaskDocument('raw')"
      >
        下载原文
      </el-button>
      <el-button
        :disabled="!selectedTaskId"
        :icon="Download"
        :loading="downloadingKey === `${selectedTaskId}-standard`"
        type="primary"
        plain
        @click="downloadSelectedTaskDocument('standard')"
      >
        下载整理文档
      </el-button>
      <el-button :icon="Right" type="primary" @click="router.push('/exports')">
        查看导出中心
      </el-button>
    </div>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="stat-grid">
    <div class="stat-tile">
      <span>本轮采集</span>
      <strong>{{ statusLabel(selectedTask?.status) }}</strong>
      <small>{{ selectedTaskTitle }}</small>
    </div>
    <div class="stat-tile">
      <span>商品信息</span>
      <strong>{{ filteredGoodsDetails.length }}</strong>
      <small>本轮 {{ goodsDetails.length }} 条</small>
    </div>
    <div class="stat-tile">
      <span>读取来源</span>
      <strong>{{ douyinCount + cainiaoDirectCount + wodaCount }}</strong>
      <small>抖店 {{ douyinCount }} / 菜鸟直打 {{ cainiaoDirectCount }} / 我打 {{ wodaCount }}</small>
    </div>
    <div class="stat-tile">
      <span>未识别</span>
      <strong>{{ limitedCount }}</strong>
      <small>没有读到可用商品文字</small>
    </div>
  </section>

  <section class="work-surface">
    <h2>本轮商品信息</h2>
    <div class="table-toolbar">
      <el-input
        v-model="keyword"
        clearable
        placeholder="搜索商品文字、单号、店铺"
        style="max-width: 360px"
      />
      <el-select v-model="sourceFilter" clearable placeholder="全部来源" style="width: 180px">
        <el-option v-for="source in sourceOptions" :key="source" :label="source" :value="source" />
      </el-select>
    </div>
    <el-table v-if="filteredGoodsDetails.length" :data="pagedGoodsDetails" height="520" stripe>
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="raw-detail">
            <div v-for="item in detailFields(row)" :key="item.key" class="detail-line">
              <span>{{ item.label }}</span>
              <strong>
                <pre v-if="typeof item.value === 'object'" class="raw-payload">{{ valueText(item.value) }}</pre>
                <template v-else>{{ valueText(item.value) }}</template>
              </strong>
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="面单" width="120">
        <template #default="{ row, $index }">{{ goodsRowLabel(row, $index) }}</template>
      </el-table-column>
      <el-table-column label="商品信息" min-width="560">
        <template #default="{ row }">
          <pre class="goods-text">{{ goodsText(row) }}</pre>
        </template>
      </el-table-column>
      <el-table-column label="来源" min-width="130">
        <template #default="{ row }">{{ sourceLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="数量" width="90">
        <template #default="{ row }">{{ field(row, 'quantity') }}</template>
      </el-table-column>
      <el-table-column label="物流/订单号" min-width="180">
        <template #default="{ row }">{{ field(row, 'logistics_no', field(row, 'order_no')) }}</template>
      </el-table-column>
      <el-table-column label="图片" prop="image_match_status" width="100" />
      <el-table-column label="档口" prop="stall_match_status" width="100" />
    </el-table>
    <div v-if="filteredGoodsDetails.length" class="pagination-row">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[20, 50, 100, 200]"
        :total="filteredGoodsDetails.length"
        layout="total, sizes, prev, pager, next"
      />
    </div>
    <el-empty v-else description="当前筛选下没有商品信息。" />
  </section>

  <section v-if="limitedCount" class="work-surface">
    <h2>未识别内容</h2>
    <el-alert
      :closable="false"
      title="以下内容已经保存为原始采集记录，但当前没有读取到可用于匹配的商品文字。"
      type="info"
      show-icon
    />
    <el-table v-if="limitedRawRecords.length" :data="limitedRawRecords" height="260" stripe>
      <el-table-column label="序号" type="index" width="90" />
      <el-table-column label="来源" min-width="150">
        <template #default="{ row }">{{ rawRecordSourceLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="原因" min-width="360">
        <template #default="{ row }">{{ rawRecordReason(row) }}</template>
      </el-table-column>
      <el-table-column label="可见原文" min-width="260">
        <template #default="{ row }">
          <pre class="raw-payload compact">{{ rawRecordVisibleText(row) }}</pre>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>
