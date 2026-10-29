import { message } from 'antd'
import { create } from 'zustand'
import { FilmService } from '../../../services/generated'
import type { TaskListItemRead, TaskStatus } from '../../../services/generated'
import { resolveTaskSourceLabel, resolveTaskTitle } from './taskCopy'

export type TaskUiItem = {
  taskId: string
  title?: string | null
  sourceLabel?: string | null
  status: TaskStatus
  progress: number
  progressKnown?: boolean
  cancelInTaskCenter?: boolean
  currentPage?: boolean
  cancelRequested: boolean
  createdAtTs?: number | null
  startedAtTs?: number | null
  finishedAtTs?: number | null
  elapsedMs?: number | null
  relationType?: string | null
  relationEntityId?: string | null
  resourceType?: string | null
  navigateRelationType?: string | null
  navigateRelationEntityId?: string | null
  onCancel?: (() => void) | null
  onNavigate?: (() => void) | null
}

export type TaskPageContext = {
  relationType: string
  relationEntityId: string
}

type TaskUiState = {
  serverItems: Record<string, TaskListItemRead>
  optimisticItems: Record<string, TaskUiItem>
  contextScopes: Record<string, TaskPageContext[]>
  open: boolean
  serverReadError: string | null
  serverRefreshing: boolean
  serverRefreshVersion: number
  setServerReadState: (error: string | null, loading: boolean) => void
  requestServerRefresh: () => void
  setServerTasks: (tasks: TaskListItemRead[]) => void
  upsertTask: (task: TaskUiItem) => void
  removeTask: (taskId: string) => void
  registerPageContext: (scopeId: string, contexts: TaskPageContext[]) => void
  unregisterPageContext: (scopeId: string) => void
  cancelTask: (taskId: string) => Promise<void>
  setOpen: (open: boolean) => void
  toggleOpen: () => void
}

export function mergeTaskUiItems(
  serverItems: Record<string, TaskListItemRead>,
  optimisticItems: Record<string, TaskUiItem>,
): TaskUiItem[] {
  const taskIds = new Set([...Object.keys(serverItems), ...Object.keys(optimisticItems)])
  return Array.from(taskIds).map((taskId) => {
    const server = serverItems[taskId]
    const optimistic = optimisticItems[taskId]
    return {
      taskId,
      title: optimistic?.title ?? resolveTaskTitle(server?.task_kind),
      sourceLabel:
        optimistic?.sourceLabel ??
        resolveTaskSourceLabel(server?.relation_type, server?.relation_entity_id),
      status: server?.status ?? optimistic?.status ?? 'pending',
      progress: server?.progress ?? optimistic?.progress ?? 0,
      progressKnown: optimistic?.progressKnown,
      cancelInTaskCenter: optimistic?.cancelInTaskCenter,
      currentPage: optimistic?.currentPage,
      cancelRequested: !!(server?.cancel_requested ?? optimistic?.cancelRequested),
      createdAtTs: server?.created_at_ts ?? optimistic?.createdAtTs,
      startedAtTs: server?.started_at_ts ?? optimistic?.startedAtTs,
      finishedAtTs: server?.finished_at_ts ?? optimistic?.finishedAtTs,
      elapsedMs: server?.elapsed_ms ?? optimistic?.elapsedMs,
      relationType: server?.relation_type ?? optimistic?.relationType,
      relationEntityId: server?.relation_entity_id ?? optimistic?.relationEntityId,
      resourceType: server?.resource_type ?? optimistic?.resourceType,
      navigateRelationType: server?.navigate_relation_type ?? optimistic?.navigateRelationType,
      navigateRelationEntityId:
        server?.navigate_relation_entity_id ?? optimistic?.navigateRelationEntityId,
      onCancel: optimistic?.onCancel ?? null,
      onNavigate: optimistic?.onNavigate ?? null,
    }
  })
}

export function flattenPageContexts(contextScopes: Record<string, TaskPageContext[]>): TaskPageContext[] {
  return Object.values(contextScopes).flat()
}

export function isTaskHighlighted(task: TaskUiItem, contexts: TaskPageContext[]): boolean {
  if (task.currentPage) return true
  const matchRelationType = task.navigateRelationType ?? task.relationType
  const matchRelationEntityId =
    task.navigateRelationEntityId ?? task.relationEntityId
  if (!matchRelationType || !matchRelationEntityId) return false
  return contexts.some(
    (context) =>
      context.relationType === matchRelationType &&
      context.relationEntityId === matchRelationEntityId,
  )
}

export const useTaskUiStore = create<TaskUiState>((set, get) => ({
  serverItems: {},
  optimisticItems: {},
  contextScopes: {},
  open: false,
  serverReadError: null,
  serverRefreshing: false,
  serverRefreshVersion: 0,
  setServerReadState: (error, loading) => set(() => ({ serverReadError: error, serverRefreshing: loading })),
  requestServerRefresh: () => set(state => ({ serverRefreshVersion: state.serverRefreshVersion + 1 })),
  setServerTasks: (tasks) =>
    set(() => ({
      serverItems: Object.fromEntries(tasks.map((task) => [task.task_id, task])),
    })),
  upsertTask: (task) =>
    set((state) => ({
      optimisticItems: {
        ...state.optimisticItems,
        [task.taskId]: task,
      },
    })),
  removeTask: (taskId) =>
    set((state) => {
      const nextOptimisticItems = { ...state.optimisticItems }
      delete nextOptimisticItems[taskId]
      return {
        optimisticItems: nextOptimisticItems,
      }
    }),
  registerPageContext: (scopeId, contexts) =>
    set((state) => ({
      contextScopes: {
        ...state.contextScopes,
        [scopeId]: contexts,
      },
    })),
  unregisterPageContext: (scopeId) =>
    set((state) => {
      const next = { ...state.contextScopes }
      delete next[scopeId]
      return { contextScopes: next }
    }),
  cancelTask: async (taskId) => {
    // Independent durable jobs have different cancellation contracts; never send their namespaced IDs to Film.
    if (taskId.startsWith('workspace:')) {
      message.info('请回任务所属页面查看状态并取消，任务中心不会代发旧任务取消请求。')
      return
    }
    const optimisticTask = get().optimisticItems[taskId]
    if (optimisticTask?.onCancel) {
      optimisticTask.onCancel()
      return
    }
    try {
      const res = await FilmService.cancelTaskApiV1FilmTasksTaskIdCancelPost({
        taskId,
        requestBody: { reason: '用户在任务中心取消任务' },
      })
      const data = res.data
      set((state) => {
        const nextServerItems = { ...state.serverItems }
        const currentServer = nextServerItems[taskId]
        if (currentServer) {
          nextServerItems[taskId] = {
            ...currentServer,
            status: data?.status ?? currentServer.status,
            cancel_requested: data?.cancel_requested ?? true,
          }
        }
        const nextOptimisticItems = { ...state.optimisticItems }
        const currentOptimistic = nextOptimisticItems[taskId]
        if (currentOptimistic) {
          nextOptimisticItems[taskId] = {
            ...currentOptimistic,
            status: data?.status ?? currentOptimistic.status,
            cancelRequested: data?.cancel_requested ?? true,
          }
        }
        return {
          serverItems: nextServerItems,
          optimisticItems: nextOptimisticItems,
        }
      })
      message.success(data?.effective_immediately ? '任务已取消' : '已发送取消请求')
    } catch {
      message.error('取消任务失败')
    }
  },
  setOpen: (open) => set(() => ({ open })),
  toggleOpen: () => set((state) => ({ open: !state.open })),
}))
