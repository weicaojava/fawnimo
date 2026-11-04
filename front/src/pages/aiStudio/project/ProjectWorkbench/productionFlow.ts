import type { Pagination, ShotRead, ShotRuntimeSummaryRead } from '../../../../services/generated'

export type FlowCounts = { totalShots: number; pendingConfirmShots: number; readyShots: number; generatingShots: number }
type Page<T> = { data?: { items: T[]; pagination: Pagination } | null }

/** Read every declared page, rejecting partial or changed collections instead of presenting them as a complete workflow. */
export async function collectWorkflowPages<T extends { id: string }>(read: (page: number, pageSize: number) => Promise<Page<T>>, isCurrent: () => boolean = () => true): Promise<T[]> {
  const pageSize = 100
  const result: T[] = []
  const seen = new Set<string>()
  let total: number | undefined
  for (let page = 1; page <= 100; page++) {
    if (!isCurrent()) throw new Error('本次制作资料读取已停止。')
    const data = (await read(page, pageSize)).data
    if (!isCurrent()) throw new Error('本次制作资料读取已停止。')
    if (!data || !Array.isArray(data.items)) throw new Error('制作资料读取不完整，请刷新后重试。')
    const meta = data.pagination
    if (!meta || meta.page !== page || meta.page_size !== pageSize || !Number.isSafeInteger(meta.total)
      || meta.total < 0 || meta.total > 10000 || (total !== undefined && meta.total !== total)) {
      throw new Error('制作资料数量已变化或超出读取范围，请刷新后重试。')
    }
    total = meta.total
    if (data.items.length !== Math.min(pageSize, total - result.length)) throw new Error('制作资料分页不完整，请刷新后重试。')
    for (const item of data.items) {
      if (!item.id || seen.has(item.id)) throw new Error('制作资料分页发生重复，请刷新后重试。')
      seen.add(item.id)
      result.push(item)
    }
    if (result.length === total) return result
  }
  throw new Error('制作资料超过读取范围，请分章查看。')
}

/** Count confirmation independently of unfinished video-task links, which can include waiting or paused work. */
export function countShotFlow(shots: ShotRead[], runtime: ShotRuntimeSummaryRead[]): FlowCounts & { firstPendingShotId?: string } {
  const shotIds = new Set(shots.map(shot => shot.id))
  const runtimeIds = new Set<string>()
  if (shotIds.size !== shots.length) throw new Error('分镜范围发生重复，请刷新后重试。')
  for (const row of runtime) {
    if (!shotIds.has(row.shot_id) || runtimeIds.has(row.shot_id)) throw new Error('分镜任务范围已变化，请刷新后重试。')
    runtimeIds.add(row.shot_id)
  }
  if (runtimeIds.size !== shotIds.size) throw new Error('分镜任务读取不完整，请刷新后重试。')
  const pending = shots.filter(shot => shot.status !== 'ready')
  return { totalShots: shots.length, pendingConfirmShots: pending.length, readyShots: shots.length - pending.length,
    generatingShots: runtime.filter(row => row.has_active_video_tasks).length, firstPendingShotId: pending[0]?.id }
}

/** Aggregate one already collected chapter observation instead of requesting a second, inconsistent set of counts. */
export function sumChapterFlow(rows: FlowCounts[]): FlowCounts {
  return rows.reduce((sum, row) => ({ totalShots: sum.totalShots + row.totalShots,
    pendingConfirmShots: sum.pendingConfirmShots + row.pendingConfirmShots, readyShots: sum.readyShots + row.readyShots,
    generatingShots: sum.generatingShots + row.generatingShots }),
  { totalShots: 0, pendingConfirmShots: 0, readyShots: 0, generatingShots: 0 })
}

/** Choose the next preparation action; legacy chapter status never proves shot confirmation or video generation readiness. */
export function chapterFlowStep(chapter: { rawText?: string; storyboardCount?: number }, flow?: FlowCounts) {
  const hasShots = (flow?.totalShots ?? chapter.storyboardCount ?? 0) > 0
  if (!hasShots && !chapter.rawText?.trim()) return { key: 'edit_raw' as const, text: '待录入原文', color: 'default',
    hint: '先补章节原文，再提取或手工建立分镜', primaryAction: '编辑原文' }
  if (!hasShots) return { key: 'extract_shots' as const, text: '待建立分镜', color: 'gold',
    hint: '已有章节原文，可提取候选或手工建立分镜', primaryAction: '准备分镜' }
  if (flow && flow.pendingConfirmShots === 0) return { key: 'shoot' as const, text: '可进入生成准备', color: 'green',
    hint: '分镜信息已确认；进入工作室检查视频生成条件', primaryAction: '进入生成工作室' }
  return { key: 'prepare_shots' as const, text: flow ? '待确认分镜' : '分镜状态待核对', color: 'blue',
    hint: flow ? `还有 ${flow.pendingConfirmShots} 条分镜信息待确认，先进入分镜编辑页` : '先查看实际分镜并完成信息确认',
    primaryAction: flow ? '继续分镜确认' : '查看分镜准备' }
}
