import type { LocationQueryRaw, RouteLocationRaw } from 'vue-router'

export type CaptureRound = {
  id: number
}

export function queryPositiveInt(value: unknown): number | null {
  const rawValue = Array.isArray(value) ? value[0] : value
  if (rawValue === undefined || rawValue === null || rawValue === '') return null
  const parsed = Number(rawValue)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

export function selectCaptureRoundId(
  rounds: readonly CaptureRound[],
  queryValue: unknown,
): number | null {
  const requestedId = queryPositiveInt(queryValue)
  if (requestedId && rounds.some((round) => round.id === requestedId)) {
    return requestedId
  }
  return rounds.reduce<number | null>(
    (latestId, round) => latestId === null || round.id > latestId ? round.id : latestId,
    null,
  )
}

export function captureRoundRoute(
  path: string,
  taskId: number | null,
  currentQuery: LocationQueryRaw = {},
): RouteLocationRaw {
  const query = { ...currentQuery }
  if (taskId) {
    query.task_id = String(taskId)
  } else {
    delete query.task_id
  }
  return { path, query }
}
