<script setup lang="ts">
import { computed, ref } from 'vue'

import type {
  RecognitionBusinessField,
  RecognitionFormatProfile,
  RecognitionLearningRecord,
  RecognitionTextStep,
} from '../../services/api'

const props = defineProps<{
  modelValue: RecognitionFormatProfile
  learningRecord?: RecognitionLearningRecord
}>()

const emit = defineEmits<{
  'update:modelValue': [value: RecognitionFormatProfile]
  delete: []
}>()

const openSections = ref<string[]>([])

const businessFields: Array<{ key: RecognitionBusinessField; label: string }> = [
  { key: 'product', label: '商品' },
  { key: 'sales_attr1', label: '销售属性1' },
  { key: 'sales_attr2', label: '销售属性2' },
  { key: 'quantity', label: '数量' },
  { key: 'remark', label: '备注' },
]

const stateFields = ['text', 'product', 'sales_attr1', 'sales_attr2', 'quantity', 'remark']

const strategyLabel = computed(() => {
  if (props.modelValue.strategy === 'structured_items_v1') return '结构化字段规则'
  if (props.modelValue.strategy === 'source_projection_v1') return '自动来源绑定规则'
  return '文本拆分规则'
})

const validationState = computed(() => {
  const result = props.learningRecord?.compiler_result
  if (!result) return { type: 'info' as const, title: '导入规则：没有管理员学习校验记录' }
  const replay = result.replay_report ?? []
  if (result.status === 'compiled' && replay.length && replay.every((item) => item.passed === true)) {
    return { type: 'success' as const, title: `规则编译和 ${replay.length} 个样本回放已通过` }
  }
  return { type: 'warning' as const, title: '规则校验未通过，请重新学习这种面单格式' }
})

function patchProfile(values: Partial<RecognitionFormatProfile>) {
  emit('update:modelValue', { ...props.modelValue, ...values })
}

function patchProfileMap(
  key: 'fields' | 'defaults',
  field: RecognitionBusinessField,
  value: string,
) {
  patchProfile({
    [key]: {
      ...(props.modelValue[key] ?? {}),
      [field]: key === 'defaults' && field === 'quantity' && /^\d+$/.test(value)
        ? Number(value)
        : value,
    },
  })
}

function profileField(field: RecognitionBusinessField): string {
  return props.modelValue.fields?.[field] ?? ''
}

function profileDefault(field: RecognitionBusinessField): string {
  return String(props.modelValue.defaults?.[field] ?? '')
}

function stepDefaults(op: string): RecognitionTextStep {
  if (op === 'extract_between') {
    return { op, source: 'text', start: '', end: '', target: 'product', consume: false }
  }
  if (op === 'trim') return { op, target: 'text', chars: '' }
  if (op === 'strip_prefix' || op === 'strip_suffix') return { op, target: 'text', literal: '' }
  if (op === 'to_positive_int') return { op, target: 'quantity' }
  return { op, source: 'text', delimiter: '', targets: ['product', 'sales_attr1'] }
}

function patchStep(index: number, values: Partial<RecognitionTextStep>) {
  const steps = [...(props.modelValue.steps ?? [])]
  steps[index] = { ...steps[index], ...values }
  patchProfile({ steps })
}

function changeStepOp(index: number, op: string) {
  const steps = [...(props.modelValue.steps ?? [])]
  steps[index] = stepDefaults(op)
  patchProfile({ steps })
}

function addStep() {
  patchProfile({
    steps: [...(props.modelValue.steps ?? []), stepDefaults('split')],
  })
}

function removeStep(index: number) {
  patchProfile({
    steps: (props.modelValue.steps ?? []).filter((_step, stepIndex) => stepIndex !== index),
  })
}

function moveStep(index: number, offset: number) {
  const steps = [...(props.modelValue.steps ?? [])]
  const targetIndex = index + offset
  if (targetIndex < 0 || targetIndex >= steps.length) return
  ;[steps[index], steps[targetIndex]] = [steps[targetIndex], steps[index]]
  patchProfile({ steps })
}

function targetsText(step: RecognitionTextStep): string {
  return (step.targets ?? []).join('、')
}

function updateTargets(index: number, value: string) {
  patchStep(index, {
    targets: value.split(/[、,，\s]+/).map((target) => target.trim()).filter(Boolean),
  })
}

function sampleText(): string {
  return JSON.stringify(props.learningRecord?.sample_payload ?? {}, null, 2)
}
</script>

<template>
  <section class="profile-editor">
    <div class="profile-heading">
      <div>
        <el-tag type="primary">{{ strategyLabel }}</el-tag>
      </div>
      <el-button type="danger" plain @click="emit('delete')">删除这条规则</el-button>
    </div>

    <el-alert
      :closable="false"
      :title="`${modelValue.name || strategyLabel} · 已确认 ${learningRecord?.confirmed_rows?.length ?? 0} 条商品行`"
      :description="modelValue.description || '管理员确认后生成，后续同格式面单会自动复用。'"
      type="info"
      show-icon
    />
    <el-alert
      class="validation-alert"
      :closable="false"
      :title="validationState.title"
      :type="validationState.type"
      show-icon
    />

    <div class="learning-grid">
      <section>
        <h4>确认时的五字段结果</h4>
        <el-table :data="learningRecord?.confirmed_rows ?? []" border empty-text="这条导入规则没有学习记录">
          <el-table-column prop="product" label="商品" min-width="180" />
          <el-table-column prop="sales_attr1" label="销售属性1" min-width="130" />
          <el-table-column prop="sales_attr2" label="销售属性2" min-width="110" />
          <el-table-column prop="quantity" label="数量" width="80" />
          <el-table-column prop="remark" label="备注" min-width="140" />
        </el-table>
      </section>
    </div>

    <el-collapse v-model="openSections" class="technical-collapse">
      <el-collapse-item name="technical" title="高级：查看技术路径和处理步骤">
        <p class="technical-meta">
          格式指纹：{{ modelValue.fingerprint }}<br />
          语法签名：{{ modelValue.grammar_signature || '未绑定' }}<br />
          技术来源：{{ learningRecord?.source_component || '导入规则' }}
        </p>
        <section v-if="learningRecord?.sample_payload" class="technical-sample">
          <h4>确认时的脱敏字段样本</h4>
          <pre>{{ sampleText() }}</pre>
        </section>
        <el-form label-position="top">
      <div class="two-column">
        <el-form-item label="规则名称">
          <el-input
            :model-value="modelValue.name ?? ''"
            placeholder="例如：抖音结构化商品"
            @update:model-value="patchProfile({ name: String($event) })"
          />
        </el-form-item>
        <el-form-item label="规则说明">
          <el-input
            :model-value="modelValue.description ?? ''"
            placeholder="说明这种格式来自哪里"
            @update:model-value="patchProfile({ description: String($event) })"
          />
        </el-form-item>
      </div>

      <template v-if="modelValue.strategy === 'structured_items_v1'">
        <el-form-item label="每个商品所在的集合路径">
          <el-input
            :model-value="modelValue.items_path ?? ''"
            placeholder="例如：task.documents[].contents[].data.items[]"
            @update:model-value="patchProfile({ items_path: String($event) })"
          />
        </el-form-item>

        <el-table :data="businessFields" border>
          <el-table-column prop="label" label="订单行字段" width="130" />
          <el-table-column label="从商品对象的哪个字段读取" min-width="260">
            <template #default="{ row }">
              <el-input
                :model-value="profileField(row.key)"
                placeholder="不读取则留空"
                @update:model-value="patchProfileMap('fields', row.key, String($event))"
              />
            </template>
          </el-table-column>
          <el-table-column label="读不到时的默认值" min-width="200">
            <template #default="{ row }">
              <el-input
                :model-value="profileDefault(row.key)"
                placeholder="没有默认值则留空"
                @update:model-value="patchProfileMap('defaults', row.key, String($event))"
              />
            </template>
          </el-table-column>
        </el-table>
      </template>

      <template v-else-if="modelValue.strategy === 'text_pipeline_v1'">
        <div class="two-column">
          <el-form-item label="商品文本路径">
            <el-input
              :model-value="modelValue.text_path ?? ''"
              placeholder="例如：task.documents[].contents[].data.productInfo"
              @update:model-value="patchProfile({ text_path: String($event) })"
            />
          </el-form-item>
          <el-form-item label="一张面单有多个商品时的分隔符">
            <el-input
              :model-value="modelValue.item_split ?? ''"
              placeholder="单商品规则可留空"
              @update:model-value="patchProfile({ item_split: String($event) })"
            />
          </el-form-item>
        </div>

        <div class="steps-heading">
          <strong>文本处理步骤</strong>
          <el-button plain @click="addStep">添加步骤</el-button>
        </div>
        <article v-for="(step, index) in modelValue.steps ?? []" :key="index" class="step-card">
          <div class="step-toolbar">
            <span>第 {{ index + 1 }} 步</span>
            <div>
              <el-button text :disabled="index === 0" @click="moveStep(index, -1)">上移</el-button>
              <el-button text :disabled="index === (modelValue.steps?.length ?? 0) - 1" @click="moveStep(index, 1)">
                下移
              </el-button>
              <el-button text type="danger" @click="removeStep(index)">删除</el-button>
            </div>
          </div>
          <el-select :model-value="step.op" @update:model-value="changeStepOp(index, String($event))">
            <el-option label="从左拆分" value="split" />
            <el-option label="从右拆分" value="rsplit" />
            <el-option label="截取两个标记之间" value="extract_between" />
            <el-option label="清理首尾字符" value="trim" />
            <el-option label="去掉固定开头" value="strip_prefix" />
            <el-option label="去掉固定结尾" value="strip_suffix" />
            <el-option label="数量转正整数" value="to_positive_int" />
          </el-select>

          <div v-if="step.op === 'split' || step.op === 'rsplit'" class="step-fields">
            <el-select :model-value="step.source" @update:model-value="patchStep(index, { source: String($event) })">
              <el-option v-for="field in stateFields" :key="field" :label="field" :value="field" />
            </el-select>
            <el-input
              :model-value="step.delimiter ?? ''"
              placeholder="分隔符"
              @update:model-value="patchStep(index, { delimiter: String($event) })"
            />
            <el-input
              :model-value="targetsText(step)"
              placeholder="依次写入：product、sales_attr1"
              @update:model-value="updateTargets(index, String($event))"
            />
          </div>

          <div v-else-if="step.op === 'extract_between'" class="step-fields">
            <el-select :model-value="step.source" @update:model-value="patchStep(index, { source: String($event) })">
              <el-option v-for="field in stateFields" :key="field" :label="field" :value="field" />
            </el-select>
            <el-input :model-value="step.start ?? ''" placeholder="开始标记" @update:model-value="patchStep(index, { start: String($event) })" />
            <el-input :model-value="step.end ?? ''" placeholder="结束标记" @update:model-value="patchStep(index, { end: String($event) })" />
            <el-select :model-value="step.target" @update:model-value="patchStep(index, { target: String($event) })">
              <el-option v-for="field in stateFields" :key="field" :label="field" :value="field" />
            </el-select>
            <el-switch
              :model-value="step.consume ?? false"
              active-text="从原文本移除"
              @update:model-value="patchStep(index, { consume: Boolean($event) })"
            />
          </div>

          <div v-else-if="step.op === 'trim'" class="step-fields">
            <el-select :model-value="step.target" @update:model-value="patchStep(index, { target: String($event) })">
              <el-option v-for="field in stateFields" :key="field" :label="field" :value="field" />
            </el-select>
            <el-input :model-value="step.chars ?? ''" placeholder="留空表示清理空白" @update:model-value="patchStep(index, { chars: String($event) })" />
          </div>

          <div v-else-if="step.op === 'strip_prefix' || step.op === 'strip_suffix'" class="step-fields">
            <el-select :model-value="step.target" @update:model-value="patchStep(index, { target: String($event) })">
              <el-option v-for="field in stateFields" :key="field" :label="field" :value="field" />
            </el-select>
            <el-input :model-value="step.literal ?? ''" placeholder="需要去掉的固定文字" @update:model-value="patchStep(index, { literal: String($event) })" />
          </div>
        </article>
      </template>

      <el-alert
        v-else
        type="info"
        :closable="false"
        show-icon
        title="这条规则由已确认样本自动生成"
        :description="`已绑定 ${modelValue.rows?.length ?? 0} 个商品行模板并通过当前样本回放；有历史样本时会同时校验。识别有误时请回到 AI 面单解析重新学习。`"
      />
        </el-form>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<style scoped>
.profile-editor {
  min-width: 0;
}

.profile-heading,
.step-toolbar,
.steps-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.profile-heading {
  margin-bottom: 18px;
}

.profile-heading small {
  display: block;
  margin-top: 7px;
  color: var(--el-text-color-secondary);
}

.two-column,
.learning-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.steps-heading {
  margin: 18px 0 10px;
}

.step-card {
  padding: 12px;
  margin-bottom: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-light);
}

.step-toolbar {
  margin-bottom: 10px;
}

.step-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-top: 10px;
}

.learning-grid {
  margin-top: 20px;
}

.validation-alert {
  margin-top: 10px;
}

.technical-collapse {
  margin-top: 18px;
}

.technical-meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  word-break: break-all;
}

.technical-sample {
  margin-bottom: 18px;
}

.learning-grid h4 {
  margin: 0 0 10px;
}

.learning-grid > section:only-child {
  grid-column: 1 / -1;
}

pre {
  max-height: 300px;
  padding: 12px;
  margin: 0;
  overflow: auto;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 900px) {
  .two-column,
  .learning-grid {
    grid-template-columns: 1fr;
  }
}
</style>
