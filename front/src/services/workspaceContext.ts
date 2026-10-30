import { OpenAPI } from './generated'

let namespace = ''
let activeScope: { userId: string; workspaceId: string } | null = null

/** Bind requests and recovery identities before any private page can mount. */
export function bindWorkspaceContext(userId: string, workspaceId: string) {
  namespace = `studio:${userId}:${workspaceId}:`
  activeScope = { userId, workspaceId }
  OpenAPI.HEADERS = { 'X-Workspace-Id': workspaceId, 'X-Identity-Id': userId }
}

/** Bind native image/video/download requests to their original visible identity without exposing a secret. */
export function workspaceMediaUrl(value: string): string {
  if (!activeScope) return value
  try {
    const target = new URL(value, OpenAPI.BASE || window.location.origin)
    const api = new URL(OpenAPI.BASE || window.location.origin)
    if (!['http:', 'https:', 'data:', 'blob:'].includes(target.protocol)) return ''
    if (target.origin !== api.origin || !target.pathname.startsWith('/api/v1/')) return value
    target.searchParams.set('__studio_user', activeScope.userId)
    target.searchParams.set('__studio_workspace', activeScope.workspaceId)
    return target.toString()
  } catch { return '' }
}

/** Preserve all legacy request keys verbatim; new identities cannot replay another space's intent. */
export const workspaceStorage = {
  getItem(key: string) { return localStorage.getItem(namespace + key) },
  setItem(key: string, value: string) { localStorage.setItem(namespace + key, value) },
  removeItem(key: string) { localStorage.removeItem(namespace + key) },
}

/** Fence pending page actions to the visible identity even before a route has unmounted. */
export function workspaceRequestScope(): string { return namespace }
