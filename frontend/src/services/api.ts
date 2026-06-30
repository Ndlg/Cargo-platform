const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

export type ApiRecord = Record<string, unknown>

export interface WorkspaceOption {
  id: number
  tenant_id?: number | null
  name: string
  code: string
}

export interface CurrentUser {
  id: number
  username: string
  display_name: string
  tenant_ids: number[]
  roles: string[]
  workspaces: WorkspaceOption[]
}

export interface PlatformCustomerTenant {
  id: number
  name: string
  code: string
  status: string
  remark?: string | null
}

export interface PlatformCustomerAdminUser {
  id: number
  username: string
  display_name: string
  role_name: string
  is_enabled: boolean
}

export interface PlatformCustomerWorkspace {
  id: number
  tenant_id?: number | null
  tenant_name: string
  tenant_code: string
  name: string
  code: string
  remark?: string | null
  admin_users: PlatformCustomerAdminUser[]
}

export interface PlatformCustomerMembership {
  tenant_id?: number | null
  workspace_id: number
  workspace_name: string
  workspace_code: string
  role_name: string
}

export interface PlatformCustomerUser {
  id: number
  username: string
  display_name: string
  is_enabled: boolean
  memberships: PlatformCustomerMembership[]
}

export interface PlatformCustomerAccountsResponse {
  tenants: PlatformCustomerTenant[]
  workspaces: PlatformCustomerWorkspace[]
  users: PlatformCustomerUser[]
}

export interface PlatformCustomerAccountCreatePayload {
  tenant_name: string
  tenant_code: string
  workspace_name: string
  workspace_code: string
  username: string
  display_name: string
  password: string
}

export interface CollectorRecord extends ApiRecord {
  id: number
  tenant_id?: number | null
  workspace_id: number
  collector_id: string
  collector_name: string
  source_machine?: string | null
  client_version?: string | null
  is_enabled: boolean
  online_status: string
  last_heartbeat_at?: string | null
  status_payload?: {
    runtime_status?: string | null
    adapter_status?: Record<string, Record<string, unknown>>
    queue_size?: number | null
    last_error?: string | null
    received_at?: string | null
    stale_reason?: string | null
    heartbeat_timeout_seconds?: number | null
  } | null
}

export interface CaptureTaskRecord extends ApiRecord {
  id: number
  tenant_id?: number | null
  workspace_id: number
  name: string
  collector_id?: number | null
  status: string
  started_at?: string | null
  ended_at?: string | null
  archived_at?: string | null
  archived_by?: number | null
  raw_record_count?: number
  waybill_count?: number
  parent_waybill_count?: number
}

export interface CollectorControlStatus {
  collectors: CollectorRecord[]
  active_task: CaptureTaskRecord | null
  collector_client?: CollectorClientPackageStatus
}

export interface CollectorClientPackageStatus {
  package_version: string
  release_available: boolean
  status: string
  archive_name: string
  release_exe: string
  message: string
}

export interface CollectorRegistrationResponse {
  collector: CollectorRecord
  collector_token: string
}

export interface ProductSkuZipUploadResult extends ApiRecord {
  imported: number
  updated: number
  duplicated: number
  skipped: number
  skus: ApiRecord[]
}

export interface ExportHeaderPayload {
  name: string
  code: string
  export_order: number
}

export type StandardWaybillFieldCode = 'product' | 'sales_attr1' | 'sales_attr2' | 'quantity' | 'remark'

export type StandardWaybillFields = Record<StandardWaybillFieldCode, string>

export type ProductMatchingScopeType = 'global' | 'current_batch' | 'selected_records'

export interface ProductMatchingScope {
  scope_type: ProductMatchingScopeType
  task_id?: number | null
  standard_detail_ids?: number[]
  selected_record_ids?: number[]
  confirmed_by_user?: boolean
  preview_impact_count?: number | null
}

export interface ProductMatchingRulePayload {
  name?: string | null
  scope?: ProductMatchingScope
  product_id: number
  product_match_fields: StandardWaybillFieldCode[]
  product_keyword: string
  product_match_type?: 'contains' | 'exact'
  sku_match_fields?: StandardWaybillFieldCode[]
  sku_id?: number | null
  image_asset_id?: number | null
  source_samples?: Partial<StandardWaybillFields>[]
  field_sources?: Partial<Record<StandardWaybillFieldCode, string>>
  preview_summary?: Record<string, unknown>
  revision_note?: string | null
  priority?: number
  is_enabled?: boolean
}

export interface ProductMatchingRuleRecord extends ApiRecord {
  id: number
  name?: string | null
  scope_type: ProductMatchingScopeType
  scope_payload?: Record<string, unknown> | null
  product_id: number
  product_match_fields: StandardWaybillFieldCode[]
  product_keyword: string
  product_match_type: 'contains' | 'exact'
  sku_match_fields?: StandardWaybillFieldCode[]
  sku_id?: number | null
  image_asset_id?: number | null
  source_samples?: Partial<StandardWaybillFields>[]
  field_sources?: Partial<Record<StandardWaybillFieldCode, string>>
  preview_summary?: Record<string, number>
  revision: number
  revision_note?: string | null
  priority: number
  is_enabled: boolean
}

export interface ProductMatchingRulesResponse extends ApiRecord {
  rules: ProductMatchingRuleRecord[]
}

export interface ProductMatchingPreviewRow extends ApiRecord {
  row_index: number
  input: StandardWaybillFields
  product?: { id: number; name: string } | null
  sku?: { id: number; name: string } | null
  image?: { id: number; name: string; file_path?: string } | null
  match_status: string
  exception_reason: string
  match_source?: string
  matched_linking_rule?: ProductMatchingRuleRecord | ApiRecord | null
  conflict_kind?: string
  conflict_linking_rules?: Array<ProductMatchingRuleRecord | ApiRecord>
  sku_candidates?: Array<ApiRecord & { id?: number; name?: string; product_id?: number }>
}

export interface ProductMatchingPreviewResponse extends ApiRecord {
  summary: Record<string, number>
  samples: Record<string, ProductMatchingPreviewRow[]>
  rows: ProductMatchingPreviewRow[]
  linking_rules: ApiRecord[]
  coverage?: {
    scope_type?: string
    task_id?: number
    standard_row_count?: number
    order_row_waybill_count?: number
    missing_order_row_count?: number
    total_waybill_count?: number
    total_raw_record_count?: number
    standard_detail_count?: number
    source_type_counts?: Record<string, number>
  }
}

export interface ProductMatchingApplyResponse extends ApiRecord {
  applied_detail_count: number
  applied_standard_detail_count?: number
  applied_item_count?: number
  summary: Record<string, number>
  samples: Record<string, ProductMatchingPreviewRow[]>
}

export interface WaybillTextBlock {
  block_id: string
  text: string
  source?: string | null
  block_kind?: string | null
  line_index?: number | null
  order?: number | null
  raw_record_id?: number | null
  source_path?: string | null
  document_id?: string | null
  document_sequence?: number | null
  parent_block_id?: string | null
  parent_text?: string | null
  split_reason?: string | null
  trace?: Record<string, unknown> | null
}

export interface WaybillReadingSample extends ApiRecord {
  sample_id: string
  raw_record_id: number
  task_id?: number | null
  document_id?: string | null
  document_sequence?: number | null
  record_order?: number | null
  sample_order?: number | null
  source_component?: string | null
  source_index?: string | null
  payload_format?: string | null
  sample_text?: string | null
  text_blocks: WaybillTextBlock[]
}

export interface WaybillReadingBatchSummary extends ApiRecord {
  bulk_supported?: boolean
  record_count?: number
  loaded_record_count?: number
  total_record_count?: number
  sample_count?: number
  loaded_sample_count?: number
  total_sample_count?: number
  total_sample_count_exact?: boolean
  total_sample_count_note?: string | null
  limit?: number
  offset?: number
  has_more_records?: boolean
  scope?: string
}

export interface WaybillReadingSamplesResponse extends ApiRecord {
  contract_version: string
  batch?: WaybillReadingBatchSummary
  samples: WaybillReadingSample[]
}

export interface OrderRowDraftRecord extends ApiRecord {
  raw_record_id: number
  task_id?: number | null
  parent_label: string
  child_label: string
  child_index: number
  child_count: number
  source_component: string
  source_index: string
  product: string
  sales_attr1: string
  sales_attr2: string
  quantity?: number | null
  remark: string
  image_match_text: string
  original_text: string
  status: 'draft' | 'needs_review' | string
  review_reason: string
}

export interface ParentWaybillDraftRecord extends ApiRecord {
  raw_record_id: number
  task_id?: number | null
  parent_label: string
  source_component: string
  source_index: string
  child_count: number
  rows: OrderRowDraftRecord[]
}

export interface OrderRowDraftsResponse extends ApiRecord {
  contract_version: 'order_row_drafts_v1' | string
  task_id: number
  status?: string
  rule_pack_required?: boolean
  message?: string
  recognition_rule_pack?: RecognitionRulePackSummary | null
  summary: {
    parent_waybill_count: number
    child_waybill_count: number
    draft_count: number
    needs_review_count: number
    special_count?: number
    pending_rule_pack_count?: number
  }
  parents: ParentWaybillDraftRecord[]
  rows: OrderRowDraftRecord[]
}

export interface RecognitionRulePackSummary extends ApiRecord {
  id: number
  name: string
  code: string
  version: string
  status: string
  is_enabled: boolean
  activated_at?: string | null
  description?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface RecognitionRulePacksResponse extends ApiRecord {
  contract_version: 'recognition_rule_pack_v1' | string
  active_pack?: RecognitionRulePackSummary | null
  packs: RecognitionRulePackSummary[]
}

export interface RecognitionRulePackImportPayload {
  payload: Record<string, unknown>
  activate?: boolean
  description?: string | null
}

export interface RecognitionRulePackImportResponse extends ApiRecord {
  contract_version: 'recognition_rule_pack_v1' | string
  pack: RecognitionRulePackSummary & { payload?: Record<string, unknown> }
}

export interface RecognitionRulePackExportResponse extends ApiRecord {
  contract_version: 'recognition_rule_pack_v1' | string
  pack: RecognitionRulePackSummary
  payload: Record<string, unknown>
}

export interface RecognitionPreviewRow extends ApiRecord {
  detail_id: number
  candidate_key: string
  source_label: string
  waybill_mode?: string | null
  item_index?: number | null
  item_count: number
  product_text: string
  sales_attr1_text: string
  sales_attr2_text: string
  quantity_text: string
  remark_text: string
  image_match_text: string
  product_id?: number | null
  product_name: string
  sku_id?: number | null
  sku_name: string
  sku_image_asset_id?: number | null
  stall_id?: number | null
  stall_name?: string | null
  rule_id?: number | null
  match_type: string
  match_field: string
  match_keyword: string
  status: string
  reason: string
}

export interface RecognitionPreviewResponse extends ApiRecord {
  task_id: number
  task_name: string
  detail_count: number
  waybill_count?: number
  order_row_count?: number
  rows: RecognitionPreviewRow[]
  summary: Record<string, number>
}

export interface DataMaintenanceBucket {
  capture_tasks: number
  archive_ready_tasks?: number
  raw_records: number
  standard_details: number
}

export interface DataMaintenanceSummary extends ApiRecord {
  active: DataMaintenanceBucket
  archived: DataMaintenanceBucket
  collecting_tasks: number
}

export interface DataMaintenanceResult extends ApiRecord {
  archived_capture_tasks?: number
  archived_raw_records?: number
  archived_standard_details?: number
  deleted_capture_tasks?: number
  deleted_raw_records?: number
  deleted_standard_details?: number
  deleted_standard_detail_batches?: number
  summary: DataMaintenanceSummary
}

export function getCurrentWorkspaceId(): number | null {
  const rawValue = localStorage.getItem('cargo-platform-workspace-id')
  if (!rawValue) return null
  const parsed = Number(rawValue)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function readableValidationDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return ''
        const record = item as Record<string, unknown>
        const msg = typeof record.msg === 'string' ? record.msg : ''
        const loc = Array.isArray(record.loc) ? record.loc.join('.') : ''
        return [loc, msg].filter(Boolean).join('：')
      })
      .filter(Boolean)
    return messages.slice(0, 3).join('；')
  }
  return ''
}

function normalizeErrorMessage(status: number, body: string): string {
  let detail = body

  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    if (typeof parsed.detail === 'string') {
      detail = parsed.detail
    } else {
      detail = readableValidationDetail(parsed.detail) || body
    }
  } catch {
    detail = body
  }

  if (/Invalid username or password/i.test(detail)) {
    return '用户名或密码错误。'
  }

  if (/already collecting/i.test(detail)) {
    return '当前工作空间已有采集任务正在进行。'
  }

  if (status === 401 || /Missing bearer token|Not authenticated|Invalid token/i.test(detail)) {
    return '登录状态已失效，请重新登录。'
  }

  if (status === 403) return '没有权限执行该操作。'
  if (status === 404) return '接口不存在或资源未找到。'
  if (status === 409) return detail || '当前状态冲突，请刷新后重试。'
  if (status === 422) return detail && detail !== body ? `提交内容校验未通过：${detail}` : '提交内容校验未通过。'
  if (status >= 500) return '服务器处理失败，请稍后重试。'

  return /[\u4e00-\u9fff]/.test(detail) ? detail : `请求失败，状态码 ${status}。`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = localStorage.getItem('cargo-platform-token')
  const workspaceId = getCurrentWorkspaceId()
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(workspaceId ? { 'X-Workspace-Id': String(workspaceId) } : {}),
      ...(init?.headers ?? {}),
    },
    ...init,
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(normalizeErrorMessage(response.status, message))
  }

  return (await response.json()) as T
}

function withCurrentWorkspace(path: string): string {
  if (path.startsWith('/workspaces')) return path

  const workspaceId = getCurrentWorkspaceId()
  if (!workspaceId) return path

  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}workspace_id=${workspaceId}`
}

export function getRecords(path: string): Promise<ApiRecord[]> {
  return request<ApiRecord[]>(withCurrentWorkspace(path))
}

export function getRecord(path: string): Promise<ApiRecord> {
  return request<ApiRecord>(withCurrentWorkspace(path))
}

export function createRecord(path: string, payload: ApiRecord): Promise<ApiRecord> {
  return request<ApiRecord>(path, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateRecord(path: string, payload: ApiRecord): Promise<ApiRecord> {
  return request<ApiRecord>(path, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function getPlatformCustomerAccounts(): Promise<PlatformCustomerAccountsResponse> {
  return request<PlatformCustomerAccountsResponse>('/platform/customer-accounts')
}

export function createPlatformCustomerAccount(payload: PlatformCustomerAccountCreatePayload): Promise<ApiRecord> {
  return request<ApiRecord>('/platform/customer-accounts', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function resetPlatformCustomerPassword(userId: number, password: string): Promise<ApiRecord> {
  return request<ApiRecord>(`/platform/customer-accounts/users/${userId}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ password }),
  })
}

export function upsertExportHeader(payload: ExportHeaderPayload): Promise<ApiRecord> {
  return request<ApiRecord>('/export-headers/upsert', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getWaybillReadingSamples(query: {
  raw_record_id?: number | null
  task_id?: number | null
  standard_detail_id?: number | null
  limit?: number
  offset?: number
}): Promise<WaybillReadingSamplesResponse> {
  const params = new URLSearchParams()
  if (query.raw_record_id) params.set('raw_record_id', String(query.raw_record_id))
  if (query.task_id) params.set('task_id', String(query.task_id))
  if (query.standard_detail_id) params.set('standard_detail_id', String(query.standard_detail_id))
  if (query.limit) params.set('limit', String(query.limit))
  if (query.offset !== undefined && query.offset !== null) params.set('offset', String(query.offset))
  return request<WaybillReadingSamplesResponse>(`/waybill-reading/samples?${params.toString()}`)
}

export function getOrderRowDrafts(taskId: number, query: { limit?: number; offset?: number } = {}): Promise<OrderRowDraftsResponse> {
  const params = new URLSearchParams()
  if (query.limit) params.set('limit', String(query.limit))
  if (query.offset !== undefined && query.offset !== null) params.set('offset', String(query.offset))
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return request<OrderRowDraftsResponse>(withCurrentWorkspace(`/order-row-drafts/tasks/${taskId}${suffix}`))
}

export function listRecognitionRulePacks(): Promise<RecognitionRulePacksResponse> {
  return request<RecognitionRulePacksResponse>(withCurrentWorkspace('/recognition-rule-packs'))
}

export function importRecognitionRulePack(
  payload: RecognitionRulePackImportPayload,
): Promise<RecognitionRulePackImportResponse> {
  return request<RecognitionRulePackImportResponse>(withCurrentWorkspace('/recognition-rule-packs/import'), {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function activateRecognitionRulePack(packId: number): Promise<RecognitionRulePackImportResponse> {
  return request<RecognitionRulePackImportResponse>(withCurrentWorkspace(`/recognition-rule-packs/${packId}/activate`), {
    method: 'POST',
  })
}

export function deactivateRecognitionRulePack(packId: number): Promise<RecognitionRulePackImportResponse> {
  return request<RecognitionRulePackImportResponse>(
    withCurrentWorkspace(`/recognition-rule-packs/${packId}/deactivate`),
    {
      method: 'POST',
    },
  )
}

export function deleteRecognitionRulePack(packId: number): Promise<ApiRecord> {
  return request<ApiRecord>(withCurrentWorkspace(`/recognition-rule-packs/${packId}`), {
    method: 'DELETE',
  })
}

export function exportRecognitionRulePack(packId: number): Promise<RecognitionRulePackExportResponse> {
  return request<RecognitionRulePackExportResponse>(withCurrentWorkspace(`/recognition-rule-packs/${packId}/export`))
}

export function getProductMatchingRules(includeDisabled = true): Promise<ProductMatchingRulesResponse> {
  return request<ProductMatchingRulesResponse>(
    `/product-sku-linking/rules?include_disabled=${includeDisabled ? 'true' : 'false'}`,
  )
}

export function previewProductMatching(payload: {
  rows?: Partial<StandardWaybillFields>[]
  scope?: ProductMatchingScope
  rule?: ProductMatchingRulePayload
  rule_ids?: number[]
  include_saved_rules?: boolean
}): Promise<ProductMatchingPreviewResponse> {
  return request<ProductMatchingPreviewResponse>('/product-sku-linking/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function saveProductMatchingRule(payload: ProductMatchingRulePayload): Promise<{ rule: ProductMatchingRuleRecord }> {
  return request<{ rule: ProductMatchingRuleRecord }>('/product-sku-linking/rules', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateProductMatchingRule(
  ruleId: number,
  payload: Partial<ProductMatchingRulePayload> & { is_enabled?: boolean },
): Promise<{ rule: ProductMatchingRuleRecord }> {
  return request<{ rule: ProductMatchingRuleRecord }>(`/product-sku-linking/rules/${ruleId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteProductMatchingRule(ruleId: number): Promise<void> {
  return deleteRecord(`/product-sku-linking/rules/${ruleId}`)
}

export function applyProductMatching(payload: {
  scope: ProductMatchingScope
  rule_ids?: number[]
  include_enabled_rules?: boolean
}): Promise<ProductMatchingApplyResponse> {
  return request<ProductMatchingApplyResponse>('/product-sku-linking/apply', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getCaptureTaskRecognitionPreview(taskId: number): Promise<RecognitionPreviewResponse> {
  const workspaceId = getCurrentWorkspaceId()
  const path = `/collector-control/tasks/${taskId}/report-preview${workspaceId ? `?workspace_id=${workspaceId}` : ''}`
  return request<RecognitionPreviewResponse>(path)
}

export function getDataMaintenanceSummary(): Promise<DataMaintenanceSummary> {
  return request<DataMaintenanceSummary>('/system-settings/data-maintenance')
}

export function archiveCaptureData(payload: { days_before?: number | null } = {}): Promise<DataMaintenanceResult> {
  return request<DataMaintenanceResult>('/system-settings/data-maintenance/archive-capture-data', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function deleteArchivedCaptureData(payload: {
  confirm_text: string
  days_before?: number | null
}): Promise<DataMaintenanceResult> {
  return request<DataMaintenanceResult>('/system-settings/data-maintenance/delete-archived-capture-data', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function downloadCollectorClientZip(): Promise<void> {
  const token = localStorage.getItem('cargo-platform-token')
  const workspaceId = getCurrentWorkspaceId()
  const response = await fetch(`${API_BASE_URL}/collector-client/download`, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(workspaceId ? { 'X-Workspace-Id': String(workspaceId) } : {}),
    },
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(normalizeErrorMessage(response.status, message))
  }

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filenameFromDisposition(
    response.headers.get('Content-Disposition'),
    '订单整理系统采集器.zip',
  )
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export async function deleteRecord(path: string): Promise<void> {
  const token = localStorage.getItem('cargo-platform-token')
  const workspaceId = getCurrentWorkspaceId()
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(workspaceId ? { 'X-Workspace-Id': String(workspaceId) } : {}),
    },
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(normalizeErrorMessage(response.status, message))
  }
}

export async function uploadProductSkuZip(productId: number, file: File): Promise<ProductSkuZipUploadResult> {
  const token = localStorage.getItem('cargo-platform-token')
  const workspaceId = getCurrentWorkspaceId()
  const formData = new FormData()
  formData.append('file', file)
  const path = `/products/${productId}/sku-zip${workspaceId ? `?workspace_id=${workspaceId}` : ''}`
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(workspaceId ? { 'X-Workspace-Id': String(workspaceId) } : {}),
    },
    body: formData,
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(normalizeErrorMessage(response.status, message))
  }

  return (await response.json()) as ProductSkuZipUploadResult
}

export async function uploadProductSkuImage(productId: number, skuName: string, file: File): Promise<ApiRecord> {
  const token = localStorage.getItem('cargo-platform-token')
  const workspaceId = getCurrentWorkspaceId()
  const formData = new FormData()
  formData.append('sku_name', skuName)
  formData.append('file', file)
  const path = `/products/${productId}/sku-image${workspaceId ? `?workspace_id=${workspaceId}` : ''}`
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(workspaceId ? { 'X-Workspace-Id': String(workspaceId) } : {}),
    },
    body: formData,
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(normalizeErrorMessage(response.status, message))
  }

  return (await response.json()) as ApiRecord
}

export async function fetchImageAssetBlob(imageAssetId: number): Promise<Blob> {
  const token = localStorage.getItem('cargo-platform-token')
  const workspaceId = getCurrentWorkspaceId()
  const path = `/image-assets/${imageAssetId}/content${workspaceId ? `?workspace_id=${workspaceId}` : ''}`
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(workspaceId ? { 'X-Workspace-Id': String(workspaceId) } : {}),
    },
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(normalizeErrorMessage(response.status, message))
  }

  return response.blob()
}

function filenameFromDisposition(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1])
  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i)
  return asciiMatch?.[1] ?? fallback
}

function businessDownloadTimestamp(date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join('') + '_' + [
    pad(date.getHours()),
    pad(date.getMinutes()),
    pad(date.getSeconds()),
  ].join('')
}

function businessDownloadFilename(prefix: string, extension: string): string {
  return `${prefix}_${businessDownloadTimestamp()}.${extension.replace(/^\./, '')}`
}

export async function downloadCaptureTaskDocument(
  taskId: number,
  kind: 'raw' | 'standard',
): Promise<{ blob: Blob; filename: string }> {
  const token = localStorage.getItem('cargo-platform-token')
  const workspaceId = getCurrentWorkspaceId()
  const documentPath = kind === 'raw' ? 'raw-document' : 'standard-document'
  const path = `/collector-control/tasks/${taskId}/${documentPath}${workspaceId ? `?workspace_id=${workspaceId}` : ''}`
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(workspaceId ? { 'X-Workspace-Id': String(workspaceId) } : {}),
    },
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(normalizeErrorMessage(response.status, message))
  }

  const fallback = businessDownloadFilename(kind === 'raw' ? '采集原文' : '整理文档', 'xlsx')
  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get('Content-Disposition'), fallback),
  }
}

export async function downloadCaptureTaskRecognitionReport(
  taskId: number,
  layout?: ApiRecord,
): Promise<{ blob: Blob; filename: string }> {
  const token = localStorage.getItem('cargo-platform-token')
  const workspaceId = getCurrentWorkspaceId()
  const params = new URLSearchParams()
  if (workspaceId) params.set('workspace_id', String(workspaceId))
  if (layout) params.set('layout', JSON.stringify(layout))
  const query = params.toString()
  const path = `/collector-control/tasks/${taskId}/report-workbook${query ? `?${query}` : ''}`
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(workspaceId ? { 'X-Workspace-Id': String(workspaceId) } : {}),
    },
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(normalizeErrorMessage(response.status, message))
  }

  const outputMode = String(layout?.output_mode ?? layout?.outputMode ?? '')
  const fallback = businessDownloadFilename(
    outputMode === 'stall_workbooks' ? '订单整理文档_分档口' : '订单整理文档',
    outputMode === 'stall_workbooks' ? 'zip' : 'xlsx',
  )
  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get('Content-Disposition'), fallback),
  }
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function login(username: string, password: string): Promise<{ access_token: string }> {
  return request<{ access_token: string }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export function getMe(): Promise<CurrentUser> {
  return request<CurrentUser>('/auth/me')
}

export function getCollectorControlStatus(): Promise<CollectorControlStatus> {
  return request<CollectorControlStatus>('/collector-control/status')
}

export function registerCollector(payload: {
  collector_id?: string
  collector_name: string
  source_machine?: string
  client_version?: string
}): Promise<CollectorRegistrationResponse> {
  return request<CollectorRegistrationResponse>('/collector-control/register', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function startCapture(payload: { name?: string; collector_id?: number | null } = {}) {
  return request<CaptureTaskRecord>('/collector-control/start', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function stopCapture(taskId?: number | null) {
  return request<CaptureTaskRecord>('/collector-control/stop', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId ?? null }),
  })
}
