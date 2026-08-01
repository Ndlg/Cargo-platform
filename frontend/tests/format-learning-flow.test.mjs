import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import {
  parserIssueFor,
  parserIssueRoute,
} from '../src/views/workbench/parserIssues.ts'

const learning = await import('../src/views/workbench/formatLearning.ts').catch(() => ({}))

assert.equal(
  parserIssueFor('format_profile_missing')?.action,
  'format-learning',
  'an unknown supported format must route to administrator learning',
)
assert.equal(parserIssueRoute('format-learning'), '/admin/format-learning')
assert.equal(
  parserIssueFor('fingerprint_adapter_required')?.action,
  'refresh',
  'an unsupported fingerprint remains a developer capability gap',
)

assert.equal(
  typeof learning.prepareLearningRows,
  'function',
  'the learning form must validate and normalize its five business fields before submission',
)

const valid = learning.prepareLearningRows([
  {
    product: '  商品甲  ',
    sales_attr1: ' 红色 ',
    sales_attr2: ' 42 ',
    quantity: '2',
    remark: '  加急 ',
  },
  {
    product: '商品乙',
    sales_attr1: '',
    sales_attr2: '',
    quantity: 1,
    remark: '',
  },
])
assert.deepEqual(valid, {
  ok: true,
  rows: [
    {
      product: '商品甲',
      sales_attr1: '红色',
      sales_attr2: '42',
      quantity: 2,
      remark: '加急',
    },
    {
      product: '商品乙',
      sales_attr1: '',
      sales_attr2: '',
      quantity: 1,
      remark: '',
    },
  ],
})

assert.deepEqual(learning.prepareLearningRows([]), {
  ok: false,
  message: '至少保留一条商品行',
})
assert.deepEqual(learning.prepareLearningRows([
  { product: ' ', sales_attr1: '', sales_attr2: '', quantity: 1, remark: '' },
]), {
  ok: false,
  message: '第 1 行商品不能为空',
})
assert.deepEqual(learning.prepareLearningRows([
  { product: '商品甲', sales_attr1: '', sales_attr2: '', quantity: 1.5, remark: '' },
]), {
  ok: false,
  message: '第 1 行数量必须是大于 0 的整数',
})

assert.deepEqual(
  learning.learningResultWarnings({
    warnings: [
      '规则已保存，但有一项提醒',
      '相关采集轮次重算失败：解析服务超时',
    ],
    reruns: [
      { task_id: 61, status: 'completed' },
      { task_id: 62, status: 'failed', error: '解析服务超时' },
    ],
  }),
  [
    '规则已保存，但有一项提醒',
    '相关采集轮次重算失败：解析服务超时',
  ],
  'warnings and failed reruns must remain visible without exposing internal task ids',
)
assert.equal(
  learning.isFingerprintFieldsMissingError(
    new Error('提交内容校验未通过：当前租户尚未配置该面单指纹的学习字段。'),
  ),
  true,
)
assert.equal(learning.isFingerprintFieldsMissingError(new Error('服务器处理失败')), false)

const apiSource = await readFile(new URL('../src/services/api.ts', import.meta.url), 'utf8')
const viewSource = await readFile(
  new URL('../src/views/workbench/FormatLearningView.vue', import.meta.url),
  'utf8',
)
assert.match(
  apiSource,
  /evidence_sha256:\s*string/,
  'prepare response must expose the server evidence digest',
)
assert.match(
  apiSource,
  /expected_evidence_sha256:\s*string/,
  'learn request must require the prepared evidence digest',
)
assert.match(
  viewSource,
  /expected_evidence_sha256:\s*prepared\.value\.evidence_sha256/,
  'saving must return the exact digest received during prepare',
)
assert.match(
  viewSource,
  /v-if="fingerprintConfigRequired"[\s\S]*?前往面单指纹配置/,
  'a prepare failure caused by missing fingerprint fields must expose the configuration action outside the editor',
)
assert.ok(
  [...viewSource.matchAll(/:disabled="loading \|\| Boolean\(preparingKey\) \|\| saving"/g)].length >= 2,
  'capture round and include-all controls must both be disabled while preparing or saving',
)
assert.match(
  viewSource,
  /queue\.value = null[\s\S]*?const requestId = \+\+queueRequestId[\s\S]*?requestId !== queueRequestId/,
  'queue loading must clear stale data and discard superseded responses',
)
assert.match(
  viewSource,
  /const requestId = \+\+prepareRequestId[\s\S]*?requestId !== prepareRequestId/,
  'prepare loading must discard superseded responses',
)

console.log('format learning flow contracts passed')
