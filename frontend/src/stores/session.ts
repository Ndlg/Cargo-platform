import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { getMe, type CurrentUser, type WorkspaceOption } from '../services/api'

const TOKEN_KEY = 'cargo-platform-token'
const WORKSPACE_KEY = 'cargo-platform-workspace-id'

export const useSessionStore = defineStore('session', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) ?? '')
  const user = ref<CurrentUser | null>(null)
  const workspaces = ref<WorkspaceOption[]>([])
  const currentWorkspaceId = ref<number | null>(readStoredWorkspaceId())
  const loading = ref(false)
  const initialized = ref(false)

  const isAuthenticated = computed(() => Boolean(token.value))
  const currentWorkspace = computed(
    () => workspaces.value.find((workspace) => workspace.id === currentWorkspaceId.value) ?? null,
  )
  const workspaceOptions = computed(() => workspaces.value)

  function readStoredWorkspaceId(): number | null {
    const rawValue = localStorage.getItem(WORKSPACE_KEY)
    if (!rawValue) return null
    const parsed = Number(rawValue)
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null
  }

  function setToken(nextToken: string) {
    token.value = nextToken
    localStorage.setItem(TOKEN_KEY, nextToken)
  }

  function setCurrentWorkspace(workspaceId: number) {
    if (!workspaces.value.some((workspace) => workspace.id === workspaceId)) {
      return
    }
    currentWorkspaceId.value = workspaceId
    localStorage.setItem(WORKSPACE_KEY, String(workspaceId))
  }

  function syncCurrentWorkspace(nextWorkspaces: WorkspaceOption[]) {
    const storedWorkspaceId = readStoredWorkspaceId()
    const allowedIds = new Set(nextWorkspaces.map((workspace) => workspace.id))
    const nextWorkspaceId =
      storedWorkspaceId && allowedIds.has(storedWorkspaceId)
        ? storedWorkspaceId
        : nextWorkspaces[0]?.id ?? null

    currentWorkspaceId.value = nextWorkspaceId
    if (nextWorkspaceId) {
      localStorage.setItem(WORKSPACE_KEY, String(nextWorkspaceId))
    } else {
      localStorage.removeItem(WORKSPACE_KEY)
    }
  }

  async function loadCurrentUser() {
    if (!token.value) {
      initialized.value = true
      return
    }

    loading.value = true
    try {
      const response = await getMe()
      user.value = response
      workspaces.value = response.workspaces
      syncCurrentWorkspace(response.workspaces)
      initialized.value = true
    } catch (error) {
      clearSession()
      throw error
    } finally {
      loading.value = false
    }
  }

  async function ensureSession() {
    if (!token.value) {
      initialized.value = true
      return false
    }
    if (!initialized.value) {
      await loadCurrentUser()
    }
    return Boolean(user.value)
  }

  function clearSession() {
    token.value = ''
    user.value = null
    workspaces.value = []
    currentWorkspaceId.value = null
    initialized.value = false
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(WORKSPACE_KEY)
  }

  return {
    token,
    user,
    workspaces,
    currentWorkspaceId,
    currentWorkspace,
    workspaceOptions,
    loading,
    initialized,
    isAuthenticated,
    setToken,
    setCurrentWorkspace,
    loadCurrentUser,
    ensureSession,
    clearSession,
  }
})
