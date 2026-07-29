<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  listTenantFingerprintConfigs,
  updateTenantFingerprintConfig,
  type TenantFingerprintConfig,
} from '../../services/api'

const loading = ref(false)
const savingCode = ref('')
const error = ref('')
const fingerprints = ref<TenantFingerprintConfig[]>([])

async function loadConfigs() {
  loading.value = true
  error.value = ''
  try {
    const result = await listTenantFingerprintConfigs()
    fingerprints.value = result.fingerprints
  } catch (err) {
    error.value = err instanceof Error ? err.message : '面单指纹配置加载失败'
  } finally {
    loading.value = false
  }
}

async function saveConfig(item: TenantFingerprintConfig) {
  if (!item.selected_fields.length) {
    ElMessage.warning('至少保留一个提供给 AI 的字段。')
    return
  }
  savingCode.value = item.code
  error.value = ''
  try {
    const result = await updateTenantFingerprintConfig(item.code, item.selected_fields)
    const index = fingerprints.value.findIndex((fingerprint) => fingerprint.code === item.code)
    if (index >= 0) fingerprints.value[index] = result.fingerprint
    ElMessage.success(`${item.name}已保存`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '面单指纹配置保存失败'
  } finally {
    savingCode.value = ''
  }
}

onMounted(loadConfigs)
</script>

<template>
  <section class="page-shell" v-loading="loading">
    <header class="page-header">
      <div>
        <h1>面单指纹配置</h1>
        <p>选择每种已授权面单格式中允许展示并提供给 AI 解析的字段。</p>
      </div>
      <el-button @click="loadConfigs">刷新</el-button>
    </header>

    <el-alert
      title="这里仅调整 AI 输入字段，不会修改已经保存的识别规则。"
      type="info"
      :closable="false"
      show-icon
    />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />

    <el-empty
      v-if="!loading && !error && fingerprints.length === 0"
      description="当前租户尚未获授权使用面单指纹"
    />

    <div class="fingerprint-list">
      <article v-for="item in fingerprints" :key="item.code" class="fingerprint-card">
        <div class="fingerprint-heading">
          <div>
            <div class="fingerprint-title">
              <strong>{{ item.name }}</strong>
              <el-tag effect="plain">{{ item.code }}</el-tag>
            </div>
            <p>{{ item.description }}</p>
          </div>
          <el-button
            type="primary"
            :loading="savingCode === item.code"
            @click="saveConfig(item)"
          >
            保存字段
          </el-button>
        </div>

        <el-checkbox-group v-model="item.selected_fields" class="field-grid">
          <el-checkbox
            v-for="field in item.candidate_fields"
            :key="field.key"
            :value="field.key"
            border
          >
            {{ field.label }}
          </el-checkbox>
        </el-checkbox-group>
      </article>
    </div>
  </section>
</template>

<style scoped>
.page-shell {
  display: grid;
  gap: 16px;
  padding: 24px;
}

.page-header,
.fingerprint-heading,
.fingerprint-title {
  display: flex;
  align-items: center;
}

.page-header,
.fingerprint-heading {
  justify-content: space-between;
  gap: 20px;
}

h1,
p {
  margin: 0;
}

.page-header p,
.fingerprint-heading p {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
}

.fingerprint-list {
  display: grid;
  gap: 14px;
}

.fingerprint-card {
  padding: 20px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-bg-color);
}

.fingerprint-title {
  gap: 10px;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
  margin-top: 18px;
}

.field-grid :deep(.el-checkbox) {
  width: 100%;
  margin: 0;
}

@media (max-width: 720px) {
  .page-header,
  .fingerprint-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
