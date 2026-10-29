import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { WorkspaceTasksService } from '../services/generated'
import type { WorkspaceTaskSummary } from '../services/generated'
import type { TaskUiItem } from '../pages/aiStudio/components/taskUiStore'

/** Limit return links to local business pages even if a malformed server response reaches the browser. */
export function safeWorkspaceTaskTarget(value: string): string | null {
  if (!value.startsWith('/') || value.startsWith('//') || /[\u0000-\u001f\\]/.test(value)) return null
  try {
    const target = new URL(value, window.location.origin)
    return target.origin === window.location.origin && /^\/(agents|models|projects|assets)(?:\/|$)/.test(target.pathname) ? target.pathname + target.search + target.hash : null
  } catch { return null }
}

/** Keep failed-source records instead of converting a network or migration error into an empty task center. */
export function useWorkspaceTasks() {
  const navigate = useNavigate()
  const location = useLocation()
  const [records, setRecords] = useState<Record<string, WorkspaceTaskSummary>>({})
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const active = useRef(false)
  const pending = useRef<ReturnType<typeof WorkspaceTasksService.listWorkspaceTasks>>()

  /** Read lightweight metadata only; this endpoint never dispatches or reconciles provider requests. */
  const refresh = useCallback(() => {
    pending.current?.cancel()
    const request = WorkspaceTasksService.listWorkspaceTasks({ limit: 100 })
    pending.current = request
    setLoading(true)
    void request.then(response => {
      if (!active.current || pending.current !== request) return
      if (!response.data) throw new Error('Missing task metadata')
      const feed = response.data
      const failed = new Set(feed.sources.filter(source => !source.available).map(source => source.kind))
      setRecords(previous => {
        const next = Object.fromEntries(Object.entries(previous).filter(([, item]) => failed.has(item.kind)))
        for (const item of feed.items) next[item.id] = item
        // Source errors may preserve older items, but never grow the browser cache without a bound.
        return Object.fromEntries(Object.values(next).sort((a, b) => (b.started_at_ts ?? b.created_at_ts ?? 0) - (a.started_at_ts ?? a.created_at_ts ?? 0)).slice(0, 250).map(item => [item.id, item]))
      })
      setError(feed.complete ? '' : '部分持久任务暂时无法更新，已保留上次记录。' + feed.sources.filter(source => !source.available).map(source => source.message).join(' '))
    }).catch(() => {
      if (active.current && pending.current === request) setError('暂时无法读取补充任务状态，已保留上次记录；请重试，或回所属页面核对。')
    }).finally(() => { if (active.current && pending.current === request) setLoading(false) })
  }, [])

  useEffect(() => {
    active.current = true
    refresh()
    const timer = window.setInterval(refresh, 3000)
    window.addEventListener('focus', refresh)
    return () => { active.current = false; window.clearInterval(timer); pending.current?.cancel(); window.removeEventListener('focus', refresh) }
  }, [refresh])

  const tasks = useMemo<TaskUiItem[]>(() => Object.values(records).map(item => {
    const target = safeWorkspaceTaskTarget(item.return_path)
    const parsed = target ? new URL(target, window.location.origin) : null
    const currentSearch = new URLSearchParams(location.search)
    const currentPage = !!parsed && parsed.pathname === location.pathname && (!parsed.searchParams.get('tab') || parsed.searchParams.get('tab') === currentSearch.get('tab'))
    return { taskId: item.id, title: item.title, sourceLabel: item.return_note ?? '持久执行记录 · 回原页面查看详情与操作',
      status: item.status, progress: item.progress ?? 0, progressKnown: item.progress != null,
      cancelRequested: false, cancelInTaskCenter: false, currentPage,
      createdAtTs: item.created_at_ts, startedAtTs: item.started_at_ts, finishedAtTs: item.finished_at_ts,
      elapsedMs: item.started_at_ts != null && item.finished_at_ts != null ? Math.max(0, Math.round((item.finished_at_ts - item.started_at_ts) * 1000)) : null,
      onNavigate: target ? () => navigate(target) : null }
  }), [records, navigate, location.pathname, location.search])
  return { tasks, error, loading, refresh }
}
