<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Delete, Refresh, UploadFilled } from '@element-plus/icons-vue'
import type { UploadRequestOptions } from 'element-plus'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createRecord,
  deleteRecord,
  fetchImageAssetBlob,
  getRecords,
  updateRecord,
  uploadProductSkuImage,
  uploadProductSkuZip,
  type ApiRecord,
  type ProductSkuZipUploadResult,
} from '../../services/api'
import { useSessionStore } from '../../stores/session'

type ProductRecord = ApiRecord & {
  id: number
  name: string
  stall_id?: number | null
  remark?: string | null
}

type ProductSkuRecord = ApiRecord & {
  id: number
  product_id: number
  name: string
  stall_id?: number | null
  image_asset_id?: number | null
}

type StallRecord = ApiRecord & {
  id: number
  name: string
  contact_name?: string | null
  remark?: string | null
}

const session = useSessionStore()
const route = useRoute()
const router = useRouter()
const products = ref<ProductRecord[]>([])
const skus = ref<ProductSkuRecord[]>([])
const stalls = ref<StallRecord[]>([])
const selectedProductId = ref<number | null>(null)
const selectedProductRows = ref<ProductRecord[]>([])
const selectedSkuRows = ref<ProductSkuRecord[]>([])
const loading = ref(false)
const saving = ref(false)
const deletingProducts = ref(false)
const deletingSkus = ref(false)
const uploading = ref(false)
const manualUploading = ref(false)
const error = ref('')
const zipInputRef = ref<HTMLInputElement | null>(null)
const zipDirectoryInputRef = ref<HTMLInputElement | null>(null)
const zipUploadSummary = ref('')
const previewLoading = ref(false)
const previewUrl = ref('')
const previewTitle = ref('')
const previewSkuId = ref<number | null>(null)

const productForm = reactive({
  name: '',
  stall_id: null as number | null,
  remark: '',
})
const manualSkuForm = reactive({
  name: '',
  stall_id: null as number | null,
})

const selectedProduct = computed(
  () => products.value.find((product) => product.id === selectedProductId.value) ?? null,
)
const selectedSkus = computed(() =>
  selectedProductId.value ? skus.value.filter((sku) => sku.product_id === selectedProductId.value) : [],
)
const enabledStalls = computed(() => stalls.value.filter((stall) => stall.is_enabled !== false))

function routeProductId(): number | null {
  const rawValue = Array.isArray(route.query.product_id)
    ? route.query.product_id[0]
    : route.query.product_id
  const parsed = Number(rawValue)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function selectProductFromRoute(): boolean {
  const productId = routeProductId()
  if (!productId) return false
  const product = products.value.find((row) => row.id === productId)
  if (!product) return false
  selectedProductId.value = product.id
  return true
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [productRecords, skuRecords, stallRecords] = await Promise.all([
      getRecords('/products?limit=2000'),
      getRecords('/product-skus?limit=2000'),
      getRecords('/stalls?limit=2000'),
    ])
    products.value = productRecords as ProductRecord[]
    skus.value = skuRecords as ProductSkuRecord[]
    stalls.value = stallRecords as StallRecord[]
    if (!selectProductFromRoute() && !selectedProductId.value && products.value[0]) {
      selectedProductId.value = products.value[0].id
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '商品资料加载失败'
  } finally {
    loading.value = false
  }
}

async function saveProduct() {
  if (!productForm.name.trim()) {
    error.value = '商品名称不能为空。'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const product = (await createRecord('/products', {
      name: productForm.name.trim(),
      stall_id: productForm.stall_id || null,
      remark: productForm.remark.trim() || null,
      is_enabled: true,
    })) as ProductRecord
    productForm.name = ''
    productForm.stall_id = null
    productForm.remark = ''
    await load()
    selectedProductId.value = product.id
  } catch (err) {
    error.value = err instanceof Error ? err.message : '商品保存失败'
  } finally {
    saving.value = false
  }
}

function stallName(stallId?: number | null): string {
  if (!stallId) return '未设置档口'
  return stalls.value.find((stall) => stall.id === stallId)?.name ?? '未设置档口'
}

function effectiveSkuStallId(row: ProductSkuRecord): number | null {
  return row.stall_id ?? selectedProduct.value?.stall_id ?? null
}

async function updateProductStall(row: ProductRecord) {
  error.value = ''
  try {
    await updateRecord(`/products/${row.id}`, { stall_id: row.stall_id || null })
    skus.value = skus.value.map((sku) => (
      sku.product_id === row.id && sku.stall_id == null ? { ...sku } : sku
    ))
    ElMessage.success('商品默认档口已更新')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '商品档口更新失败'
    await load()
  }
}

async function updateSkuStall(row: ProductSkuRecord) {
  error.value = ''
  try {
    await updateRecord(`/product-skus/${row.id}`, { stall_id: row.stall_id || null })
    ElMessage.success('SKU 档口已更新')
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'SKU 档口更新失败'
    await load()
  }
}

function onProductSelectionChange(rows: ProductRecord[]) {
  selectedProductRows.value = rows
}

function onSkuSelectionChange(rows: ProductSkuRecord[]) {
  selectedSkuRows.value = rows
}

async function removeSelectedProducts() {
  if (!selectedProductRows.value.length) {
    error.value = '请先勾选要删除的商品。'
    return
  }
  const names = selectedProductRows.value.map((row) => row.name).join('、')
  try {
    await ElMessageBox.confirm(
      `确定删除已选 ${selectedProductRows.value.length} 个商品吗？${names}`,
      '删除商品',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  const deletingIds = new Set(selectedProductRows.value.map((row) => row.id))
  deletingProducts.value = true
  error.value = ''
  try {
    await Promise.all(selectedProductRows.value.map((row) => deleteRecord(`/products/${row.id}`)))
    ElMessage.success('商品已删除')
    if (selectedProductId.value && deletingIds.has(selectedProductId.value)) {
      selectedProductId.value = null
    }
    selectedProductRows.value = []
    await load()
    if (!selectedProductId.value && products.value[0]) {
      selectedProductId.value = products.value[0].id
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '商品删除失败'
  } finally {
    deletingProducts.value = false
  }
}

async function removeSelectedSkus() {
  if (!selectedSkuRows.value.length) {
    error.value = '请先勾选要删除的 SKU。'
    return
  }
  const names = selectedSkuRows.value.map((row) => row.name).join('、')
  try {
    await ElMessageBox.confirm(
      `确定删除已选 ${selectedSkuRows.value.length} 个 SKU 吗？${names}`,
      '删除 SKU',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  const deletingIds = new Set(selectedSkuRows.value.map((row) => row.id))
  deletingSkus.value = true
  error.value = ''
  try {
    await Promise.all(selectedSkuRows.value.map((row) => deleteRecord(`/product-skus/${row.id}`)))
    ElMessage.success('SKU 已删除')
    if (previewSkuId.value && deletingIds.has(previewSkuId.value)) {
      previewSkuId.value = null
      previewTitle.value = ''
      clearPreviewUrl()
    }
    selectedSkuRows.value = []
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'SKU 删除失败'
  } finally {
    deletingSkus.value = false
  }
}

function selectProduct(row?: ProductRecord) {
  if (row?.id) selectedProductId.value = row.id
}

function skuRowClassName({ row }: { row: ProductSkuRecord }) {
  return row.id === previewSkuId.value ? 'active-preview-row' : ''
}

function handleSkuRowClick(row: ProductSkuRecord, _column: unknown, event: MouseEvent) {
  const target = event.target as HTMLElement | null
  if (target?.closest('.el-checkbox')) return
  void previewSkuImage(row)
}

function clearPreviewUrl() {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
}

async function previewSkuImage(row: ProductSkuRecord) {
  if (!row.image_asset_id) return
  clearPreviewUrl()
  previewTitle.value = `${selectedProduct.value?.name ?? '商品'} / ${row.name}`
  previewSkuId.value = row.id
  previewLoading.value = true
  error.value = ''
  try {
    const blob = await fetchImageAssetBlob(row.image_asset_id)
    previewUrl.value = URL.createObjectURL(blob)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '图片预览失败'
    previewTitle.value = ''
    previewSkuId.value = null
  } finally {
    previewLoading.value = false
  }
}

function openSkuZipPicker() {
  if (!selectedProductId.value || uploading.value) return
  zipInputRef.value?.click()
}

function openSkuZipDirectoryPicker() {
  if (!selectedProductId.value || uploading.value) return
  zipDirectoryInputRef.value?.click()
}

function zipFileKey(file: File) {
  const relativePath = file.webkitRelativePath || file.name
  return `${relativePath.toLowerCase()}::${file.size}::${file.lastModified}`
}

function isZipFile(file: File) {
  return /\.zip$/i.test(file.name)
}

function sumZipResult(results: ProductSkuZipUploadResult[], field: 'imported' | 'updated' | 'duplicated' | 'skipped') {
  return results.reduce((total, result) => total + Number(result[field] ?? 0), 0)
}

async function uploadSkuZipFiles(files: File[]) {
  if (!selectedProductId.value) {
    error.value = '请先选择商品，再上传 SKU 图片 ZIP。'
    return
  }

  const seen = new Set<string>()
  const uploadFiles: File[] = []
  let invalidCount = 0
  let repeatedFileCount = 0
  for (const file of files) {
    if (!isZipFile(file)) {
      invalidCount += 1
      continue
    }
    const key = zipFileKey(file)
    if (seen.has(key)) {
      repeatedFileCount += 1
      continue
    }
    seen.add(key)
    uploadFiles.push(file)
  }

  if (!uploadFiles.length) {
    error.value = invalidCount ? '请选择 ZIP 图片包。' : '没有可上传的 ZIP 图片包。'
    return
  }

  uploading.value = true
  error.value = ''
  zipUploadSummary.value = ''
  const results: ProductSkuZipUploadResult[] = []
  const failures: string[] = []
  try {
    for (const file of uploadFiles) {
      try {
        results.push(await uploadProductSkuZip(selectedProductId.value, file))
      } catch (err) {
        failures.push(`${file.name}：${err instanceof Error ? err.message : '上传失败'}`)
      }
    }
    await load()
    const imported = sumZipResult(results, 'imported')
    const updated = sumZipResult(results, 'updated')
    const duplicated = sumZipResult(results, 'duplicated') + repeatedFileCount
    const skipped = sumZipResult(results, 'skipped') + invalidCount
    zipUploadSummary.value = `已处理 ${uploadFiles.length} 个 ZIP：新增 ${imported}，更新 ${updated}，重复 ${duplicated}，跳过 ${skipped}。`
    if (failures.length) {
      error.value = failures.slice(0, 3).join('；')
      ElMessage.warning(`部分图片包上传失败：${failures.length} 个`)
    } else {
      ElMessage.success(zipUploadSummary.value)
    }
  } finally {
    uploading.value = false
  }
}

function onSkuZipInputChange(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  void uploadSkuZipFiles(files)
}

function onSkuZipDirectoryInputChange(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  void uploadSkuZipFiles(files)
}

function onSkuZipDrop(event: DragEvent) {
  if (!selectedProductId.value || uploading.value) return
  const files = Array.from(event.dataTransfer?.files ?? [])
  void uploadSkuZipFiles(files)
}

async function uploadManualSkuImage(options: UploadRequestOptions) {
  if (!selectedProductId.value) {
    error.value = '请先选择商品，再上传 SKU 图片。'
    options.onError(new Error(error.value) as never)
    return
  }
  const skuName = manualSkuForm.name.trim()
  if (!skuName) {
    error.value = '请填写 SKU 名称。'
    options.onError(new Error(error.value) as never)
    return
  }

  manualUploading.value = true
  error.value = ''
  try {
    const result = await uploadProductSkuImage(selectedProductId.value, skuName, options.file)
    const uploadedSku = result.sku as ProductSkuRecord | undefined
    if (uploadedSku?.id && manualSkuForm.stall_id) {
      await updateRecord(`/product-skus/${uploadedSku.id}`, { stall_id: manualSkuForm.stall_id })
    }
    manualSkuForm.name = ''
    manualSkuForm.stall_id = null
    await load()
    options.onSuccess({})
    if (uploadedSku?.id) {
      const row = skus.value.find((sku) => sku.id === uploadedSku.id)
      if (row) await previewSkuImage(row)
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : 'SKU 图片上传失败'
    error.value = message
    options.onError(new Error(message) as never)
  } finally {
    manualUploading.value = false
  }
}

watch(() => session.currentWorkspaceId, () => {
  selectedProductId.value = null
  selectedProductRows.value = []
  selectedSkuRows.value = []
  previewSkuId.value = null
  previewTitle.value = ''
  clearPreviewUrl()
  void load()
})
watch(() => route.query.product_id, () => {
  selectProductFromRoute()
})
watch(selectedProductId, () => {
  selectedSkuRows.value = []
  previewSkuId.value = null
  previewTitle.value = ''
  zipUploadSummary.value = ''
  clearPreviewUrl()
})
onMounted(load)
onBeforeUnmount(clearPreviewUrl)
</script>

<template>
  <section class="page-header">
    <div>
      <h1>商品 / SKU</h1>
      <p>先定义商品名称，再把这个商品对应的 SKU 图片 ZIP 上传到商品下面。</p>
    </div>
    <el-button :icon="Refresh" :loading="loading" plain @click="load">刷新</el-button>
  </section>

  <el-alert v-if="error" :closable="false" :title="error" type="error" />

  <section class="workflow-grid product-catalog-grid">
    <div class="work-surface">
      <h2>新增商品</h2>
      <el-form label-position="top" class="inline-config-form">
        <el-form-item label="商品名称">
          <el-input v-model="productForm.name" />
        </el-form-item>
        <el-form-item label="默认档口">
          <div class="form-control-row">
            <el-select v-model="productForm.stall_id" clearable filterable placeholder="未设置档口">
              <el-option
                v-for="stall in enabledStalls"
                :key="stall.id"
                :label="stall.name"
                :value="stall.id"
              />
            </el-select>
            <el-button plain @click="router.push('/admin/stalls')">档口库</el-button>
          </div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="productForm.remark" />
        </el-form-item>
        <el-button type="primary" :loading="saving" @click="saveProduct">保存商品</el-button>
      </el-form>

      <div v-if="products.length" class="table-toolbar">
        <el-button
          :icon="Delete"
          :disabled="!selectedProductRows.length"
          :loading="deletingProducts"
          plain
          type="danger"
          @click="removeSelectedProducts"
        >
          删除已选
        </el-button>
        <span class="muted-text">已选 {{ selectedProductRows.length }} 个商品</span>
      </div>
      <el-table
        v-if="products.length"
        :data="products"
        height="340"
        row-key="id"
        stripe
        highlight-current-row
        :current-row-key="selectedProductId"
        @current-change="selectProduct"
        @selection-change="onProductSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column label="商品" prop="name" />
        <el-table-column label="默认档口" min-width="150">
          <template #default="{ row }">
            <el-select
              v-model="row.stall_id"
              clearable
              filterable
              placeholder="未设置档口"
              @change="updateProductStall(row)"
            >
              <el-option
                v-for="stall in enabledStalls"
                :key="stall.id"
                :label="stall.name"
                :value="stall.id"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="备注">
          <template #default="{ row }">{{ row.remark || '-' }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="还没有商品。请先新增一个商品名称。" />
    </div>

    <div class="work-surface">
      <h2>SKU 图片包</h2>
      <div class="sku-upload-panel">
        <strong>
          {{
            selectedProduct
              ? `当前商品：${selectedProduct.name} / 默认档口：${stallName(selectedProduct.stall_id)}`
              : '请先选择商品'
          }}
        </strong>
        <div
          class="zip-upload-drop"
          :class="{ 'zip-upload-drop--disabled': !selectedProductId || uploading }"
          role="button"
          tabindex="0"
          @click="openSkuZipPicker"
          @keydown.enter.prevent="openSkuZipPicker"
          @keydown.space.prevent="openSkuZipPicker"
          @dragover.prevent
          @drop.prevent="onSkuZipDrop"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="zip-upload-drop__title">{{ uploading ? '正在上传图片包' : '批量上传 SKU 图片 ZIP' }}</div>
          <div class="zip-upload-drop__meta">推荐选择 ZIP 所在文件夹；系统会自动跳过重复文件、重复 SKU 和已绑定相同图片。</div>
          <div class="zip-upload-actions">
            <el-button
              type="primary"
              plain
              :loading="uploading"
              :disabled="!selectedProductId || uploading"
              @click.stop="openSkuZipPicker"
            >
              选择多个 ZIP 文件
            </el-button>
            <el-button
              plain
              :disabled="!selectedProductId || uploading"
              @click.stop="openSkuZipDirectoryPicker"
            >
              选择 ZIP 所在文件夹
            </el-button>
          </div>
          <input
            ref="zipInputRef"
            class="zip-upload-input"
            type="file"
            accept=".zip"
            multiple
            @change="onSkuZipInputChange"
          >
          <input
            ref="zipDirectoryInputRef"
            class="zip-upload-input"
            type="file"
            accept=".zip"
            multiple
            webkitdirectory
            directory
            @change="onSkuZipDirectoryInputChange"
          >
        </div>
        <el-alert
          v-if="zipUploadSummary"
          :closable="false"
          :title="zipUploadSummary"
          type="success"
          show-icon
        />
        <div class="manual-sku-upload">
          <strong>手动补录 SKU</strong>
          <div class="manual-sku-upload__row">
            <span>SKU 名称</span>
            <el-input v-model="manualSkuForm.name" class="manual-sku-upload__input" aria-label="SKU 名称" />
            <el-select
              v-model="manualSkuForm.stall_id"
              clearable
              filterable
              placeholder="继承商品档口"
              class="manual-sku-upload__input"
            >
              <el-option
                v-for="stall in enabledStalls"
                :key="stall.id"
                :label="stall.name"
                :value="stall.id"
              />
            </el-select>
            <el-upload
              accept=".jpg,.jpeg,.png,.webp"
              :show-file-list="false"
              :http-request="uploadManualSkuImage"
              :disabled="!selectedProductId || manualUploading"
            >
              <el-button :loading="manualUploading" type="primary" plain>上传单张图片</el-button>
            </el-upload>
          </div>
        </div>
      </div>

      <div v-if="selectedSkus.length" class="sku-browser">
        <div>
          <div class="table-toolbar">
            <el-button
              :icon="Delete"
              :disabled="!selectedSkuRows.length"
              :loading="deletingSkus"
              plain
              type="danger"
              @click="removeSelectedSkus"
            >
              删除已选
            </el-button>
            <span class="muted-text">已选 {{ selectedSkuRows.length }} 个 SKU</span>
          </div>
          <el-table
            :data="selectedSkus"
            height="360"
            row-key="id"
            stripe
            :row-class-name="skuRowClassName"
            @row-click="handleSkuRowClick"
            @selection-change="onSkuSelectionChange"
          >
            <el-table-column type="selection" width="46" />
            <el-table-column label="SKU" prop="name" min-width="150" />
            <el-table-column label="档口" min-width="150">
              <template #default="{ row }">
                <el-select
                  v-model="row.stall_id"
                  clearable
                  filterable
                  :placeholder="stallName(selectedProduct?.stall_id)"
                  @change="updateSkuStall(row)"
                >
                  <el-option
                    v-for="stall in enabledStalls"
                    :key="stall.id"
                    :label="stall.name"
                    :value="stall.id"
                  />
                </el-select>
                <span v-if="!row.stall_id" class="muted-line">继承：{{ stallName(effectiveSkuStallId(row)) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="图片资料" width="120">
              <template #default="{ row }">
                <el-tag
                  :class="{ 'clickable-tag': row.image_asset_id }"
                  :type="row.image_asset_id ? 'success' : 'info'"
                  @click.stop="previewSkuImage(row)"
                >
                  {{ row.image_asset_id ? '已绑定' : '未绑定' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div v-loading="previewLoading" class="sku-inline-preview">
          <template v-if="previewUrl">
            <div class="sku-inline-preview__title">{{ previewTitle }}</div>
            <img :alt="previewTitle" :src="previewUrl" />
          </template>
          <el-empty v-else description="未选择 SKU" />
        </div>
      </div>
      <el-empty v-else description="选中商品后上传 ZIP，会在这里生成 SKU 列表。" />
    </div>
  </section>
</template>

<style scoped>
.zip-upload-drop {
  display: grid;
  justify-items: center;
  gap: 8px;
  min-height: 150px;
  padding: 26px 18px;
  border: 1px dashed #8ec5ff;
  border-radius: 6px;
  background: #f8fbff;
  color: #334155;
  cursor: pointer;
  text-align: center;
}

.zip-upload-drop:hover {
  border-color: #409eff;
  background: #f3f8ff;
}

.zip-upload-drop--disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.zip-upload-drop__title {
  font-weight: 700;
}

.zip-upload-drop__meta {
  max-width: 560px;
  color: #64748b;
  font-size: 13px;
  line-height: 1.6;
}

.zip-upload-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
}

.zip-upload-input {
  display: none;
}
</style>
