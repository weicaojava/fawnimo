/** Read the API's safe error text while retaining a useful fallback for local failures. */
export function agentError(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'body' in error) {
    const body = error.body as { message?: string; detail?: string }
    if (typeof body?.message === 'string') return body.message
    if (typeof body?.detail === 'string') return body.detail
  }
  return fallback
}

/** Download a real validated response locally; the filename never becomes an executable path. */
export function downloadAgentConfig(value: unknown, name: string): void {
  const link = document.createElement('a')
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  link.href = url
  link.download = `${name.replace(/[\\/:*?"<>|]/g, '_').slice(0, 160) || 'agent'}.json`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
