import { workspaceStorage } from '../../../services/workspaceContext'
import {
  StudioShotsService, StudioShotDetailsService, StudioShotDialogLinesService,
  StudioShotFrameImagesService, StudioShotLinksService, StudioShotCharacterLinksService,
} from '../../../services/generated'
import type { ShotRead, ShotDetailRead, ProjectAssetLinkCreate, ShotGraphWrite, ShotGraphResult, ShotGraphRead } from '../../../services/generated'

export const STRUCTURAL_EDIT_HINT = '原子保存整个章节顺序；已有来源时间和采用记录保持原身份，历史计划可能需要重新编译。'
export const MERGE_HINT = '合并连续镜头为一个新的待确认镜头。原镜头行将删除，完整准备资料保存为收据；不继承生成任务、采用结果或原片时钟。被计划或原片来源引用的镜头受到保护。'
export const TRANSITION_EDIT_HINT = '前往剪辑页，先锁定基础时间线，再保存并实际渲染独立的交叉淡化版本；不会在这里标记假转场。'
export const PREVIEW_STEP_SECONDS = 1 / 30
export const LEGACY_GENERATION_AVAILABLE = false
export const LEGACY_GENERATION_REASON = '当前环境暂不支持此处的图片、提示词和视频生成；请使用镜头执行计划检查可用模型与输入。'

/** Preserve useful API errors instead of replacing every failed operation with generic success. */
export function studioActionError(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const value = error as { body?: { message?: unknown }; message?: unknown }
    if (typeof value.body?.message === 'string') return value.body.message
    if (typeof value.message === 'string') return value.message
  }
  return fallback
}

/** Read every bounded page before calculating indices or cloning a saved relation set. */
async function readPages<T>(fetchPage: (page: number) => Promise<{ data?: { items: T[]; pagination: { total: number } } | null }>): Promise<T[]> {
  const result: T[] = []
  for (let page = 1; page <= 50; page += 1) {
    const response = await fetchPage(page)
    const items = response.data?.items ?? []
    result.push(...items)
    const total = response.data?.pagination.total ?? result.length
    if (total > 5000) throw new Error('当前章节数据超过本次操作上限，请先拆分章节。')
    if (result.length >= total || items.length === 0) return result
  }
  throw new Error('未能完整读取当前章节，操作未开始。')
}

/** Read saved chapter ordering before writes; never derive a free slot from a truncated UI list. */
export function readChapterShots(chapterId: string): Promise<ShotRead[]> {
  return readPages(page => StudioShotsService.listShotsApiV1StudioShotsGet({ chapterId, page, pageSize: 100, order: 'index' }))
}

/** Scope persisted recovery keys to the actual project and chapter. */
function graphIntentKey(projectId: string, chapterId: string): string {
  return `jellyfish:shot-graph:${projectId}:${chapterId}:intent`
}

/** Recover the exact original intent after reload or a lost response, never make a new insert key. */
export function pendingGraphAction(projectId: string, chapterId: string): ShotGraphWrite | null {
  const value = workspaceStorage.getItem(graphIntentKey(projectId, chapterId))
  if (!value) return null
  const parsed = JSON.parse(value) as ShotGraphWrite
  if (parsed.project_id !== projectId || parsed.chapter_id !== chapterId) throw new Error('结构操作恢复标识归属不符，请清除该标识后刷新。')
  return parsed
}

/** An explicit discard forgets only local recovery metadata and never rolls back a committed edit. */
export function discardGraphAction(projectId: string, chapterId: string): void {
  workspaceStorage.removeItem(graphIntentKey(projectId, chapterId))
}

/** Submit/replay one already persisted command and retain ambiguous network/server outcomes. */
async function submitGraphIntent(body: ShotGraphWrite): Promise<ShotGraphResult> {
  try {
    const response = await StudioShotsService.mutateShotGraph({ requestBody: body })
    if (!response.data) throw new Error('未收到结构保存收据，请核对原请求。')
    try { discardGraphAction(body.project_id, body.chapter_id) } catch { /* The saved receipt still proves success; the same intent remains safely replayable. */ }
    return response.data
  } catch (error) {
    const status = error && typeof error === 'object' ? (error as { status?: number }).status : undefined
    if (status && status >= 400 && status < 500) {
      try { discardGraphAction(body.project_id, body.chapter_id) } catch { /* Recovery metadata may require an explicit browser action. */ }
      throw error
    }
    throw new Error(`${studioActionError(error, '未收到服务响应')} 原请求已保留，请使用“核对上次结构操作”，不要重复新建。`)
  }
}

/** Compare the order the user saw with the complete server state, then persist before the first POST. */
export async function saveGraphAction(projectId: string, chapterId: string, expectedOrder: string[], fields: Pick<ShotGraphWrite, 'action'> & Partial<Pick<ShotGraphWrite, 'ordered_shot_ids' | 'source_shot_ids' | 'after_shot_id' | 'title'>>): Promise<ShotGraphResult> {
  if (pendingGraphAction(projectId, chapterId)) throw new Error('上一次结构操作尚待核对，请先核对原请求。')
  const graph = (await StudioShotsService.getShotGraph({ projectId, chapterId })).data
  if (!graph) throw new Error('未能读取完整镜头结构，操作未提交。')
  if (JSON.stringify(graph.shots.map(shot => shot.id)) !== JSON.stringify(expectedOrder)) throw new Error('镜头顺序已经变化，请刷新后重新确认。')
  const body: ShotGraphWrite = { project_id: projectId, chapter_id: chapterId, request_key: crypto.randomUUID(), expected_revision: graph.revision, expected_fingerprint: graph.fingerprint, ...fields }
  try { workspaceStorage.setItem(graphIntentKey(projectId, chapterId), JSON.stringify(body)) }
  catch { throw new Error('浏览器无法保存结构操作恢复标识，操作未提交。') }
  return submitGraphIntent(body)
}

/** Replay exactly one saved operation; server receipts make this safe even after later edits. */
export async function recoverGraphAction(projectId: string, chapterId: string): Promise<ShotGraphResult> {
  const intent = pendingGraphAction(projectId, chapterId)
  if (!intent) throw new Error('此章节没有待核对的结构请求。')
  return submitGraphIntent(intent)
}

/** Read the current durable receipt list without creating structure rows. */
export async function readGraphState(projectId: string, chapterId: string): Promise<ShotGraphRead> {
  const result = (await StudioShotsService.getShotGraph({ projectId, chapterId })).data
  if (!result) throw new Error('未能读取镜头结构收据。')
  return result
}

/** Download actual original preparation data for auditing/manual reconstruction, not generated provenance. */
export async function downloadGraphReceipt(projectId: string, receiptId: string): Promise<void> {
  const result = (await StudioShotsService.readShotGraphReceipt({ projectId, receiptId })).data
  if (!result) throw new Error('未能读取原始镜头资料收据。')
  const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url; link.download = `shot-graph-${receiptId}.json`; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Clone saved preparation data and references, never adopting another shot's generated output. */
export async function copySavedShot(source: ShotRead, projectId: string): Promise<ShotRead> {
  const all = await readChapterShots(source.chapter_id)
  const latest = all.find(shot => shot.id === source.id)
  if (!latest) throw new Error('原分镜已不存在，请刷新后重试。')
  const [details, dialogs, frames, characters, ...links] = await Promise.all([
    readPages(page => StudioShotDetailsService.listShotDetailsApiV1StudioShotDetailsGet({ shotId: source.id, page, pageSize: 100 })),
    readPages(page => StudioShotDialogLinesService.listShotDialogLinesApiV1StudioShotDialogLinesGet({ shotDetailId: source.id, page, pageSize: 100 })),
    readPages(page => StudioShotFrameImagesService.listShotFrameImagesApiV1StudioShotFrameImagesGet({ shotDetailId: source.id, page, pageSize: 100 })),
    StudioShotCharacterLinksService.listShotCharacterLinksApiV1StudioShotCharacterLinksGet({ shotId: source.id }).then(result => result.data ?? []),
    ...(['actor', 'scene', 'prop', 'costume'] as const).map(entityType => readPages(page =>
      StudioShotLinksService.listProjectEntityLinksApiV1StudioShotLinksEntityTypeGet({ entityType, projectId, chapterId: source.chapter_id, shotId: source.id, page, pageSize: 100 }))),
  ])
  const identity = crypto.randomUUID()
  let created = false
  try {
    const result = await StudioShotsService.createShotApiV1StudioShotsPost({ requestBody: {
      id: identity, chapter_id: latest.chapter_id, index: Math.max(0, ...all.map(shot => shot.index)) + 1,
      title: `${latest.title}（副本）`, script_excerpt: latest.script_excerpt ?? '',
      status: 'pending', skip_extraction: false, thumbnail: '', generated_video_file_id: null,
    } })
    if (!result.data) throw new Error('服务没有返回已保存的副本。')
    created = true
    const detail: ShotDetailRead | undefined = details[0]
    if (detail) {
      const { id: _oldIdentity, ...preparation } = detail
      await StudioShotDetailsService.createShotDetailApiV1StudioShotDetailsPost({ requestBody: { ...preparation, id: identity } })
      for (const line of dialogs) {
        const { id: _oldId, shot_detail_id: _oldShot, ...text } = line
        await StudioShotDialogLinesService.createShotDialogLineApiV1StudioShotDialogLinesPost({ requestBody: { ...text, shot_detail_id: identity } })
      }
      for (const frame of frames) {
        const { id: _oldId, shot_detail_id: _oldShot, ...reference } = frame
        await StudioShotFrameImagesService.createShotFrameImageApiV1StudioShotFrameImagesPost({ requestBody: { ...reference, shot_detail_id: identity } })
      }
    }
    for (const character of characters) {
      await StudioShotCharacterLinksService.upsertShotCharacterLinkApiV1StudioShotCharacterLinksPost({ requestBody: {
        shot_id: identity, character_id: character.character_id, index: character.index, note: character.note,
      } })
    }
    const methods = [
      StudioShotLinksService.createProjectActorLinkApiV1StudioShotLinksActorPost,
      StudioShotLinksService.createProjectSceneLinkApiV1StudioShotLinksScenePost,
      StudioShotLinksService.createProjectPropLinkApiV1StudioShotLinksPropPost,
      StudioShotLinksService.createProjectCostumeLinkApiV1StudioShotLinksCostumePost,
    ]
    const fields = ['actor_id', 'scene_id', 'prop_id', 'costume_id']
    for (let group = 0; group < links.length; group += 1) {
      for (const saved of links[group]) {
        const assetId = (saved as Record<string, unknown>)[fields[group]]
        if (typeof assetId !== 'string') throw new Error('原分镜的资产关联不完整，副本未完成。')
        const body: ProjectAssetLinkCreate = { project_id: projectId, chapter_id: source.chapter_id, shot_id: identity, asset_id: assetId }
        await methods[group]({ requestBody: body })
      }
    }
    return result.data
  } catch (error) {
    if (created) {
      try {
        const remove = [
          StudioShotLinksService.deleteProjectActorLinkApiV1StudioShotLinksActorLinkIdDelete,
          StudioShotLinksService.deleteProjectSceneLinkApiV1StudioShotLinksSceneLinkIdDelete,
          StudioShotLinksService.deleteProjectPropLinkApiV1StudioShotLinksPropLinkIdDelete,
          StudioShotLinksService.deleteProjectCostumeLinkApiV1StudioShotLinksCostumeLinkIdDelete,
        ]
        for (const [group, entityType] of (['actor', 'scene', 'prop', 'costume'] as const).entries()) {
          const partial = await readPages(page => StudioShotLinksService.listProjectEntityLinksApiV1StudioShotLinksEntityTypeGet({
            entityType, projectId, chapterId: source.chapter_id, shotId: identity, page, pageSize: 100,
          }))
          for (const link of partial) await remove[group]({ linkId: Number((link as Record<string, unknown>).id) })
        }
        await StudioShotsService.deleteShotApiV1StudioShotsShotIdDelete({ shotId: identity })
      }
      catch { throw new Error(`副本未完成，且未能回滚 ${identity}，请刷新检查。${studioActionError(error, '')}`) }
    }
    throw error
  }
}

/** Step the real player by an explicitly labelled preview interval, without inventing a source FPS. */
export function stepPreview(video: HTMLVideoElement | null, direction: -1 | 1): number | null {
  if (!video || video.readyState < 2 || !Number.isFinite(video.duration) || video.duration <= 0) return null
  video.pause()
  const next = Math.min(Math.max(video.currentTime + direction * PREVIEW_STEP_SECONDS, 0), video.duration)
  video.currentTime = next
  return next
}

/** Capture actual decoded pixels; cross-origin or unavailable frames fail rather than returning stand-ins. */
export function capturePreviewPng(video: HTMLVideoElement): Promise<Blob> {
  if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) return Promise.reject(new Error('视频尚未完成解码，暂不能截帧。'))
  if (video.videoWidth * video.videoHeight > 1920 * 1920) return Promise.reject(new Error('当前视频分辨率超过截帧上限。'))
  const canvas = document.createElement('canvas')
  canvas.width = video.videoWidth
  canvas.height = video.videoHeight
  const context = canvas.getContext('2d')
  if (!context) return Promise.reject(new Error('浏览器未提供截帧画布。'))
  try { context.drawImage(video, 0, 0, canvas.width, canvas.height) }
  catch { return Promise.reject(new Error('浏览器无法读取当前视频像素，请检查本地媒体或跨域配置。')) }
  return new Promise((resolve, reject) => {
    try { canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('截帧编码失败。')), 'image/png') }
    catch { reject(new Error('浏览器拒绝读取跨域视频像素，未保存图片。')) }
  })
}
