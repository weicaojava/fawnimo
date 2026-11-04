import { StudioChaptersService, StudioShotsService } from '../../../../services/generated'
import { collectWorkflowPages, countShotFlow, sumChapterFlow } from './productionFlow'
import type { FlowCounts } from './productionFlow'

type ChapterLike = { id: string; index?: number; title?: string }
export type ProjectFlowStats = FlowCounts
export type ChapterFlowStats = FlowCounts & {
  chapterId: string
  chapterIndex?: number
  chapterTitle?: string
  firstPendingShotId?: string
}

/** Load all chapters with the generated client and reject a response from a different project. */
export async function loadCompleteProjectChapters(projectId: string, isCurrent: () => boolean = () => true) {
  const chapters = await collectWorkflowPages((page, pageSize) => StudioChaptersService.listChaptersApiV1StudioChaptersGet({ projectId, page, pageSize, order: 'index', isDesc: false }), isCurrent)
  if (chapters.some(chapter => chapter.project_id !== projectId)) throw new Error('章节不属于当前工程，请重新读取。')
  return chapters
}

/** Preserve every selected chapter shot, including those after the first page. */
export async function loadCompleteChapterShots(chapterId: string, isCurrent: () => boolean = () => true) {
  const shots = await collectWorkflowPages((page, pageSize) => StudioShotsService.listShotsApiV1StudioShotsGet({ chapterId, page, pageSize, order: 'index', isDesc: false }), isCurrent)
  if (shots.some(shot => shot.chapter_id !== chapterId)) throw new Error('分镜不属于当前章节，请重新读取。')
  return shots
}

/** Observe information confirmation and video-task facts without trusting a legacy chapter status. */
async function loadFlowStatsForChapter(chapter: ChapterLike, isCurrent: () => boolean): Promise<ChapterFlowStats> {
  const [shots, runtime] = await Promise.all([loadCompleteChapterShots(chapter.id, isCurrent),
    StudioShotsService.listShotRuntimeSummaryApiV1StudioShotsRuntimeSummaryGet({ chapterId: chapter.id })])
  if (!isCurrent()) throw new Error('本次制作资料读取已停止。')
  if (!Array.isArray(runtime.data)) throw new Error('分镜任务读取不完整，请重试。')
  return { chapterId: chapter.id, chapterIndex: chapter.index, chapterTitle: chapter.title, ...countShotFlow(shots, runtime.data) }
}

/** Bound concurrent reads while collecting the complete chapter list. */
export async function loadChapterFlowStats(chapters: ChapterLike[], isCurrent: () => boolean = () => true): Promise<ChapterFlowStats[]> {
  const results: ChapterFlowStats[] = []
  for (let offset = 0; offset < chapters.length; offset += 4) {
    if (!isCurrent()) throw new Error('本次制作资料读取已停止。')
    results.push(...await Promise.all(chapters.slice(offset, offset + 4).map(chapter => loadFlowStatsForChapter(chapter, isCurrent))))
  }
  return results
}

/** Retain the aggregate helper for callers that do not also need per-chapter rows. */
export async function loadProjectFlowStatsForChapters(chapters: ChapterLike[]): Promise<ProjectFlowStats> {
  return sumChapterFlow(await loadChapterFlowStats(chapters))
}
