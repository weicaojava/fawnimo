import { useCallback, useEffect, useRef, useState } from 'react'
import { loadChapterFlowStats } from '../projectFlowStats'
import type { ChapterFlowStats, ProjectFlowStats } from '../projectFlowStats'
import { sumChapterFlow } from '../productionFlow'

type FlowState = { scope: string; byChapter: Record<string, ChapterFlowStats>; totals: ProjectFlowStats | null; loading: boolean; error: string }

/** Share one observation between project header, overview and chapter list; discard late results after navigation. */
export function useProductionFlow(projectId: string | undefined, chapters: { id: string; index?: number; title?: string }[], chaptersReady: boolean) {
  const scope = JSON.stringify([projectId, chaptersReady, chapters.map(chapter => [chapter.id, chapter.index, chapter.title])])
  const sequence = useRef(0)
  const mounted = useRef(true)
  const activeScope = useRef(scope)
  activeScope.current = scope
  const [state, setState] = useState<FlowState>({ scope: '', byChapter: {}, totals: null, loading: true, error: '' })
  const refresh = useCallback(async () => {
    if (!mounted.current || activeScope.current !== scope) return
    const request = ++sequence.current
    setState({ scope, byChapter: {}, totals: null, loading: chaptersReady, error: '' })
    if (!chaptersReady) return
    try {
      const rows = await loadChapterFlowStats(chapters, () => request === sequence.current && activeScope.current === scope)
      if (request === sequence.current && activeScope.current === scope) setState({ scope, byChapter: Object.fromEntries(rows.map(row => [row.chapterId, row])), totals: sumChapterFlow(rows), loading: false, error: '' })
    } catch {
      if (request === sequence.current && activeScope.current === scope) setState({ scope, byChapter: {}, totals: null, loading: false, error: '分镜制作进度读取失败，请重试。当前确认和任务状态尚未核对。' })
    }
  }, [scope, chapters, chaptersReady])
  useEffect(() => { mounted.current = true; void refresh(); return () => { mounted.current = false; sequence.current++ } }, [refresh])
  const current: FlowState = state.scope === scope ? state : { scope, byChapter: {}, totals: null, loading: chaptersReady, error: '' }
  return { ...current, refresh }
}

export type ProductionFlowState = ReturnType<typeof useProductionFlow>
