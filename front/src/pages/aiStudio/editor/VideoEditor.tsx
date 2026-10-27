import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Checkbox, Collapse, Empty, Input, InputNumber, List, Modal, Select, Space, Tag, message } from 'antd'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { EditingService as E, EditingExportService as X, OpenAPI, StudioChaptersService } from '../../../services/generated'
import { useTaskUiStore } from '../components/taskUiStore'
import type { EditCatalog, EditDocument, EditAnchor, EditClip, EditDraftRead, EditPublicationRead, EditPreflightRead, SelectionWrite, ExportRead, ExportCatalog } from '../../../services/generated'

const origin = (value: string) => value === 'execution_candidate_unqualified' ? '执行候选（未获质量资格）' : value === 'user_import' ? '用户导入' : '历史素材（未绑定计划）'
type Document = EditDocument & { clips: EditClip[]; anchors: EditAnchor[] }
const normalize = (value: EditDocument): Document => ({ ...value, clips: value.clips ?? [], anchors: value.anchors ?? [] })
const blank = (planId: string | null = null): Document => ({ schema_version: 1, plan_id: planId, title: '', clips: [], anchors: [] })
/** Keep errors actionable without exposing internal responses or private paths. */
function explain(error: unknown) { const body = (error as { body?: { message?: unknown; detail?: unknown } }).body; return typeof body?.message === 'string' ? body.message : typeof body?.detail === 'string' ? body.detail : '操作失败，请核对当前修订、素材和工具状态。' }
/** Media routes always resolve an owned logical ID, never a supplied remote URL. */
function mediaUrl(projectId: string, mediaId: string) { return `${OpenAPI.BASE}/api/v1/studio/production/projects/${encodeURIComponent(projectId)}/media/${encodeURIComponent(mediaId)}/content` }

/** Select an actual chapter before mounting a scope-isolated editor. */
export default function VideoEditor() {
  const { projectId = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const query = new URLSearchParams(location.search)
  const chapterId = query.get('chapter') ?? ''
  const [chapters, setChapters] = useState<{ value: string; label: string }[]>([])
  const [error, setError] = useState('')
  const [scopeLocked, setScopeLocked] = useState(false)
  useEffect(() => {
    let cancelled = false
    setChapters([]); setError('')
    void StudioChaptersService.listChaptersApiV1StudioChaptersGet({ projectId, pageSize: 100 }).then(result => {
      if (cancelled) return
      if ((result.data?.pagination.total ?? 0) > 100) { setError('工程超过100章，请从章节工作室进入具体章节的剪辑页。'); return }
      setChapters((result.data?.items ?? []).map(item => ({ value: item.id, label: item.title })))
    }).catch(e => { if (!cancelled) setError(explain(e)) })
    return () => { cancelled = true }
  }, [projectId])
  return <div className="space-y-4"><Link to={`/projects/${projectId}/chapters`} onClick={event => { if (scopeLocked) { event.preventDefault(); message.warning("请先保存修改或等待当前操作完成") } }}>返回章节列表</Link><Card title="章节剪辑与导出">
    <Alert className="mb-3" type="info" showIcon message="本地剪辑软件流程，不代表AI成片质量验收" description="固定整章原镜头顺序、单视频轨与硬切；裁剪步长100毫秒，原声音轨可保留，字幕另存SRT/VTT。输出360×640/30fps。暂不支持重排、转场、配乐、烧录字幕。人工内容标记不证明语义、口型或配音正确。" />
    {error && <Alert type="error" message={error} />}
    <Select aria-label="剪辑章节" disabled={scopeLocked} style={{ minWidth: 260 }} value={chapterId || undefined} placeholder="选择实际章节" options={chapters} onChange={value => navigate(`/projects/${projectId}/editor?chapter=${encodeURIComponent(value)}`)} />
  </Card>{chapterId ? <ChapterEditor key={`${projectId}:${chapterId}`} projectId={projectId} chapterId={chapterId} requestedPlanId={query.get('plan')} requestedJobId={query.get('job')} onScopeLocked={setScopeLocked} /> : <Empty description="选择章节后，明确采用候选并建立时间线" />}</div>
}

/** Controlled chapter document; immutable publications are inspected separately. */
function ChapterEditor({ projectId, chapterId, requestedPlanId, requestedJobId, onScopeLocked }: { projectId: string; chapterId: string; requestedPlanId: string | null; requestedJobId: string | null; onScopeLocked: (value: boolean) => void }) {
  const [catalog, setCatalog] = useState<EditCatalog | null>(null)
  const [draft, setDraft] = useState<EditDraftRead | null>(null)
  const [document, setDocument] = useState<Document>(blank)
  const [publication, setPublication] = useState<EditPublicationRead | null>(null)
  const [check, setCheck] = useState<EditPreflightRead | null>(null)
  const [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [selectionSources, setSelectionSources] = useState<Record<string, string>>({})
  const [recovery, setRecovery] = useState<SelectionWrite | null>(null)
  const alive = useRef(true)
  const sequence = useRef(0)
  const formVersion = useRef(0)
  const locked = useRef(false)
  const dirtyNow = useRef(false)
  const modal = useRef<ReturnType<typeof Modal.confirm> | null>(null)
  const storageKey = `jellyfish:editing-selection:${projectId}:${chapterId}`
  const current = () => alive.current
  /** The synchronous guard also blocks two clicks before React renders busy. */
  function start() { if (!current() || locked.current) return false; locked.current = true; setBusy(true); setError(''); return true }
  function finish() { if (current()) { locked.current = false; setBusy(false) } }
  function edit(value: EditDocument) { formVersion.current++; dirtyNow.current = true; setDocument(normalize(value)); setDirty(true) }
  /** Each confirmation belongs to one form generation and is destroyed on navigation. */
  function confirm(title: string, content: string, run: () => Promise<void>) { const version = formVersion.current; modal.current?.destroy(); modal.current = Modal.confirm({ title, content, onOk: async () => { if (current() && version === formVersion.current) await run() } }) }

  /** Refresh current adoption without replacing unsaved timeline input. */
  async function refreshCatalog(planId: string | null) {
    const request = ++sequence.current
    const result = await E.getEditCatalogApiV1EditingProjectsProjectIdCatalogGet({ projectId, chapterId, planId })
    if (current() && request === sequence.current) setCatalog(result.data ?? null)
  }
  /** History inspection never changes the current timeline draft. */
  async function loadPublication(publicationId: string) {
    const request = ++sequence.current
    const [saved, preflight] = await Promise.all([E.getEditPublicationApiV1EditingProjectsProjectIdPublicationsPublicationIdGet({ projectId, publicationId }), E.preflightEditPublicationApiV1EditingProjectsProjectIdPublicationsPublicationIdPreflightGet({ projectId, publicationId })])
    if (!current() || request !== sequence.current) return
    setPublication(saved.data ?? null); setCheck(preflight.data ?? null)
  }
  /** Initial load resolves actual saved input; URL hints never cause adoption. */
  async function load() {
    if (!start()) return
    try {
      const result = await E.listEditDraftsApiV1EditingProjectsProjectIdDraftsGet({ projectId, chapterId })
      if (!current()) return
      const saved = result.data?.[0] ?? null
      const planId = requestedPlanId ?? saved?.document.plan_id ?? null
      setDraft(saved); setDocument(normalize(saved?.document ?? blank(planId))); formVersion.current++; dirtyNow.current = false; setDirty(false)
      await refreshCatalog(planId)
      if (saved?.publications.length) await loadPublication(saved.publications[0].id)
      try { const raw = localStorage.getItem(storageKey); if (raw && current()) setRecovery(JSON.parse(raw) as SelectionWrite) } catch { if (current()) setError('无法读取采用恢复记录；请核对当前采用后再操作。') }
    } catch (e) { if (current()) setError(explain(e)) } finally { finish() }
  }
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; sequence.current++; modal.current?.destroy() } }, [])
  useEffect(() => { onScopeLocked(busy || dirty) }, [busy, dirty, onScopeLocked])
  useEffect(() => () => onScopeLocked(false), [onScopeLocked])
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (dirtyNow.current) { event.preventDefault(); event.returnValue = '' } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn) }, [])

  /** Keep the exact request body with its key until a server response confirms it. */
  async function sendSelection(body: SelectionWrite) {
    if (!start()) return
    try {
      localStorage.setItem(storageKey, JSON.stringify(body)); setRecovery(body)
      const result = await E.writeEditSelectionApiV1EditingProjectsProjectIdSelectionsPost({ projectId, requestBody: body })
      if (!current()) return
      try { localStorage.removeItem(storageKey); setRecovery(null) } catch { setError('原采用请求已处理，但浏览器无法清除恢复标识；可继续核对原请求。') }
      await refreshCatalog(catalog?.plan_id ?? null)
      message.success(result.data?.replayed ? '已取回原采用事件，当前采用以刷新结果为准' : '采用已更新；已有时间线不会自动改变')
    } catch (e) { if (current()) setError(e instanceof DOMException ? '浏览器无法保存恢复记录，未发送新的采用请求。' : explain(e)) } finally { finish() }
  }
  /** Select a candidate, historical take or acknowledged manual source explicitly. */
  function adopt(shotId: string) {
    if (busy || dirty || recovery) return
    const source = selectionSources[shotId]
    if (!source) return
    const [kind, id] = source.split(':')
    const cursor = catalog?.selections.find(item => item.shot_id === shotId)
    const body: SelectionWrite = { request_key: crypto.randomUUID(), shot_id: shotId, expected_revision: cursor?.revision ?? 0, job_id: kind === 'job' ? id : null, take_id: kind === 'take' ? id : null, media_id: kind === 'media' ? id : null, plan_id: kind === 'take' ? null : catalog?.plan_id ?? null, accept_historical: acknowledged, change_note: note }
    confirm('明确采用此片段？', '将切换此镜头的当前采用。历史保留，已有时间线与导出不会被替换；执行候选仍未获质量资格。', () => sendSelection(body))
  }
  /** Build every required clip in fixed plan order using actual current selections. */
  function buildTimeline() {
    if (!catalog?.plan_id) return
    const clips: EditClip[] = []
    for (const shot of catalog.clips) {
      const selected = catalog.selections.find(item => item.shot_id === shot.shot_id)?.take_id
      const take = shot.takes.find(item => item.id === selected && item.eligible_for_plan)
      if (!take) { setError(`镜头“${shot.title}”尚未采用可用于本计划的片段。`); return }
      clips.push({ shot_id: shot.shot_id, take_id: take.id, source_in_ms: 0, source_out_ms: Math.floor(Math.min(take.duration_ms ?? shot.duration_ms, shot.duration_ms) / 100) * 100, source_audio: true })
    }
    confirm('按当前采用重新建立时间线？', '将替换本地未保存的时间线和人工标记；已锁历史保留。请逐项重新确认内容标记。', async () => { edit({ schema_version: 1, plan_id: catalog.plan_id, title: document.title, clips, anchors: [] }) })
  }
  /** Save the entire controlled document with its original server CAS revision. */
  async function save(next = document) {
    if (!start()) return
    try {
      const result = draft ? await E.patchEditDraftApiV1EditingProjectsProjectIdDraftsDraftIdPatch({ projectId, draftId: draft.id, requestBody: { expected_revision: draft.revision, document: next } }) : await E.createEditDraftApiV1EditingProjectsProjectIdDraftsPost({ projectId, requestBody: { chapter_id: chapterId, document: next } })
      if (!current()) return
      setDraft(result.data ?? null); setDocument(normalize(result.data?.document ?? next)); formVersion.current++; dirtyNow.current = false; setDirty(false); message.success('已保存剪辑草稿')
    } catch (e) { if (current()) setError(explain(e)) } finally { finish() }
  }
  /** Freeze only saved input and retain any older publication. */
  function freeze() {
    if (!draft || dirty || busy) return
    confirm('锁定此时间线版本？', '只锁定软件输入和人工声明，不证明实际剧情、发声或口型质量。裁剪或上游变化需要勾选确认并说明。', async () => {
      if (!start()) return
      try {
        const result = await E.freezeEditDraftApiV1EditingProjectsProjectIdDraftsDraftIdFreezePost({ projectId, draftId: draft.id, requestBody: { expected_revision: draft.revision, acknowledge_changes: acknowledged, change_note: note } })
        if (!current() || !result.data) return
        const refreshed = await E.getEditDraftApiV1EditingProjectsProjectIdDraftsDraftIdGet({ projectId, draftId: draft.id })
        if (!current()) return
        setDraft(refreshed.data ?? null); await loadPublication(result.data.id); message.success('已锁定时间线，导出仍需单独提交')
      } catch (e) { if (current()) setError(explain(e)) } finally { finish() }
    })
  }
  /** One original story item has one complete-source declaration, even across mappings. */
  function changeAnchor(kind: 'beat' | 'dialogue', itemId: string, patch: Partial<EditAnchor> | null) {
    const existing = document.anchors.find(item => item.kind === kind && item.item_id === itemId)
    const next = document.anchors.filter(item => !(item.kind === kind && item.item_id === itemId))
    if (patch) next.push({ kind, item_id: itemId, take_id: '', start_ms: 0, end_ms: 1, confirmed: false, ...existing, ...patch })
    edit({ ...document, anchors: next })
  }
  const activePlanId = catalog?.plan_id ?? null
  return <div data-testid="editing-workspace" className="space-y-4">
    {error && <Alert type="error" showIcon message={error} />}
    {catalog?.plan_id && document.plan_id && catalog.plan_id !== document.plan_id && <Alert type="warning" message="当前查看采用的计划与草稿计划不同；草稿保持原输入。按当前采用建立时间线后才会替换本地草稿。" />}
    {dirty && <Alert type="warning" message="有未保存修改。请保存后再采用其他片段、刷新或离开页面。" />}
    <Card title="1. 选择计划与明确采用" extra={<Button disabled={busy || dirty} onClick={() => void load()}>刷新已保存资料</Button>}>
      <Space direction="vertical" className="w-full">
        <Select aria-label="剪辑镜头计划" className="w-full" placeholder="选择整章计划" value={activePlanId ?? undefined} disabled={busy || dirty} options={catalog?.plans.map(item => ({ value: item.id, label: `${item.title} · ${item.content_hash.slice(0, 10)}` }))} onChange={value => { if (!start()) return; void refreshCatalog(value).then(() => { if (current()) edit(blank(value)) }).catch(e => { if (current()) setError(explain(e)) }).finally(finish) }} />
        {requestedJobId && <Alert type="info" message={`工作室候选：${requestedJobId}；请在对应镜头中选择并明确采用，页面不会自动采用。`} />}
        <Checkbox checked={acknowledged} disabled={busy} onChange={event => { formVersion.current++; setAcknowledged(event.target.checked) }}>明确确认采用历史计划、裁剪或时长变化（不等于质量合格）</Checkbox>
        <Input.TextArea aria-label="剪辑变更说明" placeholder="历史采用、人工导入或裁剪变更说明" value={note} maxLength={1000} disabled={busy} onChange={event => { formVersion.current++; setNote(event.target.value) }} />
        {recovery && <Alert type="warning" message="存在未确认结果的原采用请求" description="恢复将发送原正文和原标识，先取回原事件，不保证重新切回其片段。" />}
        {recovery && <Button disabled={busy || dirty} onClick={() => confirm('核对原采用请求？', '使用原标识与原正文，不创建新采用意图。', () => sendSelection(recovery))}>核对原采用请求</Button>}
        {recovery && error && <Button disabled={busy || dirty} onClick={() => confirm('放弃本地采用恢复标识？', '这不会撤销已经完成的采用。请先核对当前采用与历史；之后如重新操作会产生新的意图。', async () => { try { localStorage.removeItem(storageKey); setRecovery(null); await refreshCatalog(catalog?.plan_id ?? null) } catch { if (current()) setError('无法清除恢复标识或刷新当前采用') } })}>放弃本地恢复标识</Button>}
        <List dataSource={catalog?.clips ?? []} renderItem={shot => { const currentTake = catalog?.selections.find(item => item.shot_id === shot.shot_id)?.take_id; return <List.Item><Space direction="vertical" className="w-full"><strong>{shot.index}. {shot.title}</strong><div>当前采用：{currentTake ? shot.takes.find(item => item.id === currentTake)?.name ?? currentTake : '尚未采用'}</div>
          <Select aria-label={`镜头${shot.index}采用来源`} className="w-full" disabled={busy || dirty || !!recovery} value={selectionSources[shot.shot_id]} placeholder="选择候选、历史片段或人工素材" onChange={value => { formVersion.current++; setSelectionSources(old => ({ ...old, [shot.shot_id]: value })) }} options={[...shot.takes.map(take => ({ value: `take:${take.id}`, label: `${take.id === currentTake ? '当前 · ' : '历史 · '}${take.name} · ${origin(take.origin_kind)}${take.eligible_for_plan ? '' : ' · 仅可切回，不可用于本计划'}` })), ...(catalog?.candidates ?? []).filter(item => item.shot_id === shot.shot_id && item.state === 'ready' && item.media_id).map(item => ({ value: `job:${item.job_id}`, label: `执行候选（未获质量资格） · ${item.job_id}${item.job_id === requestedJobId ? ' · 来自工作室' : ''}` })), ...(catalog?.media ?? []).map(item => ({ value: `media:${item.id}`, label: `人工导入 · ${item.name}` }))]} />
          <Button disabled={busy || dirty || !!recovery || !selectionSources[shot.shot_id]} onClick={() => adopt(shot.shot_id)}>确认采用或切回</Button>
          {shot.takes.map(take => <div key={take.id}><a href={mediaUrl(projectId, take.media_id)} target="_blank" rel="noreferrer">{take.name} · 预览片段</a> <Tag>{origin(take.origin_kind)}</Tag>{take.issues.map(issue => <span key={issue.code}>{issue.message} </span>)}</div>)}
        </Space></List.Item> }} />
      </Space>
    </Card>
    <Card title="2. 固定顺序时间线与源裁剪" extra={<Button disabled={busy || !activePlanId} onClick={buildTimeline}>按当前采用建立时间线</Button>}>
      <Input aria-label="剪辑标题" className="mb-3" value={document.title} maxLength={120} disabled={busy} placeholder="剪辑标题" onChange={event => edit({ ...document, title: event.target.value })} />
      <Collapse items={document.clips.map((clip, index) => { const shot = catalog?.clips.find(item => item.shot_id === clip.shot_id); const take = shot?.takes.find(item => item.id === clip.take_id); const change = (patch: Partial<typeof clip>) => edit({ ...document, clips: document.clips.map((item, position) => position === index ? { ...item, ...patch } : item) }); return { key: clip.shot_id, label: `${index + 1}. ${shot?.title ?? clip.shot_id} · ${clip.source_in_ms}–${clip.source_out_ms}ms`, children: <Space direction="vertical" className="w-full"><div>固定片段：{take?.name ?? clip.take_id} · {origin(take?.origin_kind ?? '')}</div><Space wrap><label>源入点（毫秒）<InputNumber aria-label={`镜头${index + 1}源入点`} min={0} max={300000} step={100} precision={0} disabled={busy} value={clip.source_in_ms} onChange={value => change({ source_in_ms: value ?? 0 })} /></label><label>源出点（毫秒）<InputNumber aria-label={`镜头${index + 1}源出点`} min={100} max={300000} step={100} precision={0} disabled={busy} value={clip.source_out_ms} onChange={value => change({ source_out_ms: value ?? 100 })} /></label><Checkbox checked={clip.source_audio} disabled={busy} onChange={event => change({ source_audio: event.target.checked })}>保留源声音（对白镜头必须保留真实音轨）</Checkbox></Space>{take && <video controls preload="metadata" style={{ maxHeight: 240, maxWidth: '100%' }} src={mediaUrl(projectId, take.media_id)} />}</Space> } })} />
      {!document.clips.length && <Empty description="先采用全部镜头，再建立整章时间线" />}
    </Card>
    <Card title="3. 逐条人工内容保留标记">
      <Alert className="mb-3" type="info" message="时间使用源视频零点毫秒，允许1ms精度；每条必需剧情/对白恰一标记。一句对白须在一个镜头内完整保留，不能跨硬切。" />
      <Collapse items={(catalog?.items ?? []).map(item => { const anchor = document.anchors.find(value => value.kind === item.kind && value.item_id === item.item_id); const options = document.clips.filter(clip => item.shot_ids.includes(clip.shot_id)); return { key: `${item.kind}:${item.item_id}`, label: `${item.required ? '必需' : '可选'} · ${item.kind === 'dialogue' ? '对白' : '剧情'} · ${item.text}`, children: <Space direction="vertical" className="w-full"><div>{item.speaker_id && `说话角色：${item.speaker_id} · `}{item.text}</div><Select aria-label={`内容${item.item_id}承载镜头`} className="w-full" disabled={busy} allowClear placeholder="选择实际完整承载此条内容的镜头" value={anchor?.take_id || undefined} options={options.map(clip => ({ value: clip.take_id, label: catalog?.clips.find(shot => shot.shot_id === clip.shot_id)?.title ?? clip.shot_id }))} onChange={value => { const clip = options.find(entry => entry.take_id === value); changeAnchor(item.kind, item.item_id, clip ? { take_id: clip.take_id, start_ms: clip.source_in_ms, end_ms: clip.source_out_ms, confirmed: false } : null) }} /><Space wrap><InputNumber aria-label={`内容${item.item_id}开始`} disabled={busy || !anchor} min={0} max={300000} precision={0} value={anchor?.start_ms} onChange={value => changeAnchor(item.kind, item.item_id, { start_ms: value ?? 0, confirmed: false })} /><span>至</span><InputNumber aria-label={`内容${item.item_id}结束`} disabled={busy || !anchor} min={1} max={300000} precision={0} value={anchor?.end_ms} onChange={value => changeAnchor(item.kind, item.item_id, { end_ms: value ?? 1, confirmed: false })} /><Checkbox disabled={busy || !anchor} checked={anchor?.confirmed ?? false} onChange={event => changeAnchor(item.kind, item.item_id, { confirmed: event.target.checked })}>我确认此段完整保留原内容（人工声明）</Checkbox></Space></Space> } })} />
    </Card>
    <Card title="4. 保存与锁定版本"><Space wrap><Button type="primary" disabled={busy} onClick={() => void save()}>保存剪辑草稿</Button><Tag>草稿修订 {draft?.revision ?? '未保存'}</Tag><Button disabled={busy || dirty || !draft?.readiness.can_freeze} onClick={freeze}>锁定时间线版本</Button></Space>{draft && <Alert className="mt-3" type={draft.readiness.can_freeze ? 'success' : 'warning'} message={draft.readiness.can_freeze ? '已保存输入可锁定；不代表内容质量通过' : '已保存输入尚有缺项'} description={<List size="small" dataSource={draft.readiness.issues} renderItem={issue => <List.Item>{issue.message}</List.Item>} />} />}</Card>
    <Card title="5. 历史版本与本地导出"><Space wrap><Select aria-label="已锁定时间线" style={{ minWidth: 280 }} disabled={busy} value={publication?.id} placeholder="选择已锁定版本" options={draft?.publications.map(item => ({ value: item.id, label: `修订${item.draft_revision} · ${item.content_hash.slice(0, 10)}` }))} onChange={value => { if (!start()) return; void loadPublication(value).catch(e => { if (current()) setError(explain(e)) }).finally(finish) }} /><Button disabled={busy || dirty || !publication} onClick={() => { if (publication) confirm('基于此历史版本恢复新草稿？', '历史保持不变，恢复内容需重新保存和锁定。', async () => { const restored = publication.payload.document as EditDocument; edit(restored); await refreshCatalog(restored.plan_id ?? null) }) }}>恢复为新草稿</Button></Space>{check && <Alert className="mt-3" type={check.usable ? 'info' : 'warning'} message={check.usable ? '历史输入可用；当前采用变化不会替换该版本' : '历史源当前不可用'} description={[...check.issues.map(item => item.message), ...check.current_selection_differences.map(item => `镜头${item.shot_id}当前采用已改变，历史仍固定${item.publication_take_id}`)].join('；')} />}{publication && <ExportPanel key={publication.id} projectId={projectId} chapterId={chapterId} publication={publication} usable={!!check?.usable} />}</Card>
  </div>
}

/** Export is its own durable local task; keys persist before the first HTTP write. */
function ExportPanel({ projectId, chapterId, publication, usable }: { projectId: string; chapterId: string; publication: EditPublicationRead; usable: boolean }) {
  const [catalog, setCatalog] = useState<ExportCatalog | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [requestKey, setRequestKey] = useState('')
  const alive = useRef(true)
  const inFlight = useRef(false)
  const submitting = useRef(false)
  const sequence = useRef(0)
  const ownModal = useRef<ReturnType<typeof Modal.confirm> | null>(null)
  const navigate = useNavigate()
  const upsert = useTaskUiStore(state => state.upsertTask)
  const openTasks = useTaskUiStore(state => state.setOpen)
  const storage = `jellyfish:editing-export:${projectId}:${publication.id}`
  /** Resolve all server jobs, retaining separate immutable publication identities. */
  async function refresh() {
    const version = ++sequence.current
    const result = await X.listEditingExports({ projectId, chapterId })
    if (alive.current && version === sequence.current) setCatalog(result.data ?? null)
  }
  useEffect(() => {
    alive.current = true
    try { setRequestKey(localStorage.getItem(storage) ?? '') } catch { setError('无法读取导出恢复标识。') }
    void refresh().catch(e => { if (alive.current) setError(explain(e)) })
    const timer = window.setInterval(() => { if (inFlight.current) return; inFlight.current = true; void refresh().catch(() => {}).finally(() => { inFlight.current = false }) }, 3000)
    return () => { alive.current = false; sequence.current++; window.clearInterval(timer); ownModal.current?.destroy() }
  }, [projectId, chapterId, publication.id])
  /** Confirm recovery separately from creating a genuinely new local render. */
  function submit() {
    if (busy) return
    const existing = requestKey
    ownModal.current?.destroy()
    ownModal.current = Modal.confirm({ title: existing ? '核对原导出请求？' : '开始本地导出？', content: '固定使用此锁定版本。本地处理不调用模型、不证明剧情、口型或配音质量；字幕单独下载。', onOk: async () => {
      if (!alive.current || submitting.current) return
      submitting.current = true; setBusy(true); setError('')
      try {
        const key = existing || crypto.randomUUID()
        localStorage.setItem(storage, key); setRequestKey(key)
        const result = await X.createEditingExport({ projectId, publicationId: publication.id, requestBody: { request_key: key } })
        if (!alive.current) return
        const job = result.data
        if (job) { upsert({ taskId: job.task_id, title: '本地剪辑导出', status: job.state === 'ready' ? 'succeeded' : job.state === 'failed' || job.state === 'cancelled' ? job.state : job.state === 'queued' ? 'pending' : 'running', progress: job.state === 'ready' ? 100 : 0, cancelRequested: false, resourceType: 'local_export', relationType: 'chapter', relationEntityId: chapterId, onNavigate: () => navigate(`/projects/${projectId}/editor?chapter=${encodeURIComponent(chapterId)}`), onCancel: () => { void cancel(job) } }); openTasks(true) }
        await refresh()
      } catch (e) { if (alive.current) setError(e instanceof DOMException ? '无法保存恢复标识，未提交新的导出请求。' : explain(e)) } finally { submitting.current = false; if (alive.current) setBusy(false) }
    } })
  }
  /** Record cancellation on the owned local job, never a model-budget operation. */
  async function cancel(job: ExportRead) {
    if (busy) return
    setBusy(true)
    try { await X.cancelEditingExport({ projectId, exportId: job.id, requestBody: {} }); if (alive.current) { await refresh(); message.success('已处理本地导出取消请求') } } catch (e) { if (alive.current) setError(explain(e)) } finally { if (alive.current) setBusy(false) }
  }
  const ownJobs = catalog?.exports.filter(job => job.publication_id === publication.id) ?? []
  const names: Record<string, string> = { queued: '等待导出', running: '本地处理中', ready: '导出完成（软件产物）', failed: '导出失败', cancelled: '已取消' }
  const fileNames: Record<string, string> = { video: 'MP4视频', subtitles_srt: 'SRT字幕', subtitles_vtt: 'VTT字幕', package: '来源ZIP包' }
  return <div className="mt-3" data-testid="editing-export-panel">
    {error && <Alert type="error" message={error} />}
    <Alert className="mb-3" type={catalog?.enabled ? 'info' : 'warning'} message={catalog?.enabled ? '本地工具可用；不需要模型账号' : catalog?.reason || '本地导出尚不可用'} />
    <Button type="primary" disabled={busy || (!requestKey && (!usable || !catalog?.enabled))} onClick={submit}>{requestKey ? '核对原导出请求' : '导出此锁定版本'}</Button>
    {requestKey && ownJobs.length > 0 && ownJobs.every(job => ['ready', 'failed', 'cancelled'].includes(job.state)) && <Button className="ml-2" disabled={busy || !usable || !catalog?.enabled} onClick={() => { ownModal.current?.destroy(); ownModal.current = Modal.confirm({ title: '准备另一次本地导出？', content: '原任务和产物保留。清除本地恢复标识后，下一次提交将产生新的导出意图。', onOk: () => { if (!alive.current) return; try { localStorage.removeItem(storage); setRequestKey('') } catch { setError('无法更新本地恢复标识') } } }) }}>准备重新导出</Button>}
    <List dataSource={ownJobs} locale={{ emptyText: '此版本尚无导出任务' }} renderItem={job => <List.Item><Space direction="vertical"><div><Tag>{names[job.state] ?? job.state}</Tag>{job.id} · 尝试{job.attempts}次</div>{job.reason && <div>{job.reason}</div>}{['queued', 'running'].includes(job.state) && <Button disabled={busy} onClick={() => void cancel(job)}>取消本地导出</Button>}{job.state === 'ready' && <><Space wrap>{job.files.map(file => <a key={file.kind} href={`${OpenAPI.BASE}${file.content_url}`} target="_blank" rel="noreferrer">下载{fileNames[file.kind] ?? file.kind} · {file.size}字节</a>)}</Space>{job.files.find(file => file.kind === 'video') && <video crossOrigin="anonymous" controls preload="metadata" style={{ maxWidth: '100%', maxHeight: 360 }} src={`${OpenAPI.BASE}${job.files.find(file => file.kind === 'video')!.content_url}`}>{job.files.find(file => file.kind === "subtitles_vtt") && <track kind="subtitles" srcLang="zh" label="中文字幕（人工时间标记）" default src={`${OpenAPI.BASE}${job.files.find(file => file.kind === "subtitles_vtt")!.content_url}`} />}</video>}</>}</Space></List.Item>} />
  </div>
}
