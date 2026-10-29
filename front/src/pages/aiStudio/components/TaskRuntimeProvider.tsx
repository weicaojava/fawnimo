import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { FilmService } from '../../../services/generated'
import { useTaskUiStore } from './taskUiStore'

const TASK_POLL_INTERVAL_MS = 4000
const TASK_RECENT_SECONDS = 15
const TASK_PAGE_SIZE = 50

type TaskRuntimeProviderProps = {
  children: ReactNode
}

export function TaskRuntimeProvider({ children }: TaskRuntimeProviderProps) {
  const setServerTasks = useTaskUiStore((state) => state.setServerTasks)
  const setServerReadState = useTaskUiStore((state) => state.setServerReadState)
  const refreshVersion = useTaskUiStore((state) => state.serverRefreshVersion)

  useEffect(() => {
    let cancelled = false
    let timer: number | null = null
    let request: ReturnType<typeof FilmService.listTasksApiV1FilmTasksGet> | null = null

    /** Preserve the last real task list on read failures and expose a recoverable status in TaskCenter. */
    const load = async () => {
      setServerReadState(useTaskUiStore.getState().serverReadError, true)
      try {
        request = FilmService.listTasksApiV1FilmTasksGet({
          recentSeconds: TASK_RECENT_SECONDS,
          page: 1,
          pageSize: TASK_PAGE_SIZE,
        })
        const res = await request
        if (cancelled) return
        if (!res.data || !Array.isArray(res.data.items)) throw new Error('Missing legacy task list')
        setServerTasks(res.data.items)
        setServerReadState(null, false)
      } catch {
        if (!cancelled) {
          setServerReadState('暂时无法更新原任务列表，已保留上次记录；请重试或回所属页面核对。', false)
        }
      } finally {
        if (!cancelled) {
          timer = window.setTimeout(() => {
            void load()
          }, TASK_POLL_INTERVAL_MS)
        }
      }
    }

    void load()
    return () => {
      cancelled = true
      request?.cancel()
      if (timer) window.clearTimeout(timer)
    }
  }, [setServerTasks, setServerReadState, refreshVersion])

  return <>{children}</>
}
