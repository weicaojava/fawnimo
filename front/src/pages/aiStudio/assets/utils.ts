import { OpenAPI } from '../../../services/generated'
import { workspaceMediaUrl } from '../../../services/workspaceContext'

function tryExtractFileIdFromUrl(value: string): string | null {
  try {
    const url = new URL(value)
    const m = url.pathname.match(/\/api\/v1\/studio\/files\/([^/]+)\/download\/?$/)
    if (m?.[1]) return decodeURIComponent(m[1])
  } catch {
    // ignore parse error
  }
  return null
}

/** Resolve private relative media against the configured API, preserving valid same-origin mode. */
export function resolveAssetUrl(value?: string | null): string | undefined {
  if (!value) return undefined
  const trimmed = value.trim()
  if (!trimmed) return undefined

  if (/^(?:[a-z][a-z\d+\-.]*:)?\/\//i.test(trimmed) || trimmed.startsWith('data:') || trimmed.startsWith('blob:')) {
    const fileId = tryExtractFileIdFromUrl(trimmed)
    if (fileId) return buildFileDownloadUrl(fileId)
    return workspaceMediaUrl(trimmed)
  }

  // 后端有些缩略图字段可能直接返回 file_id（不包含 / 或 :）。
  // 这种情况下需要拼接下载地址，否则 new URL 会生成错误路径。
  if (!trimmed.includes('/') && !trimmed.includes(':')) {
    return buildFileDownloadUrl(trimmed)
  }

  try {
    return workspaceMediaUrl(new URL(trimmed, OpenAPI.BASE || window.location.origin).toString())
  } catch {
    return trimmed
  }
}

export function buildFileDownloadUrl(fileId?: string | null): string | undefined {
  if (!fileId) return undefined
  return resolveAssetUrl(`/api/v1/studio/files/${encodeURIComponent(fileId)}/download`)
}
