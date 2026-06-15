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
}

export interface CollectorControlStatus {
  collectors: CollectorRecord[]
  active_task: CaptureTaskRecord | null
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

export interface ExportFieldDefinitionPayload {
  name: string
  code: string
  export_order: number
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
  rows: RecognitionPreviewRow[]
  summary: Record<string, number>
}

export function getCurrentWorkspaceId(): number | null {
  const rawValue = localStorage.getItem('cargo-platform-workspace-id')
  if (!rawValue) return null
  const parsed = Number(rawValue)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function normalizeErrorMessage(status: number, body: string): string {
  let detail = body

  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    if (typeof parsed.detail === 'string') {
      detail = parsed.detail
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
  if (status === 422) return '提交内容校验未通过。'
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

export function upsertExportFieldDefinition(payload: ExportFieldDefinitionPayload): Promise<ApiRecord> {
  return request<ApiRecord>('/export-field-definitions/upsert', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getCaptureTaskRecognitionPreview(taskId: number): Promise<RecognitionPreviewResponse> {
  const workspaceId = getCurrentWorkspaceId()
  const path = `/collector-control/tasks/${taskId}/recognition-preview${workspaceId ? `?workspace_id=${workspaceId}` : ''}`
  return request<RecognitionPreviewResponse>(path)
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

  const fallback = `capture-task-${taskId}-${kind}.xlsx`
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
  const path = `/collector-control/tasks/${taskId}/recognition-report${query ? `?${query}` : ''}`
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

  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(
      response.headers.get('Content-Disposition'),
      `capture-task-${taskId}-recognition-report.xlsx`,
    ),
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
