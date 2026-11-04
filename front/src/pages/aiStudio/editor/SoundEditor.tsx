import { useEffect, useId, useRef, useState } from 'react'
import { Alert, Button, Card, Checkbox, Collapse, Input, InputNumber, List, Select, Space, Tag } from 'antd'
import { EditingService as E, OpenAPI, ProductionService as P, SoundV3Service as S } from '../../../services/generated'
import type { EditCatalog, EditDocument, LocalSpeechHistory, LocalSpeechRequest, ProductionMediaRead, SoundCandidateImport, SoundCandidateRead, SoundDocument_Input, SoundDraftRead, SoundExportCatalog, SoundPublicationRead, SoundTrack } from '../../../services/generated'
import { workspaceMediaUrl, workspaceStorage } from '../../../services/workspaceContext'
import { useImageActionScope } from '../assets/components/useImageActionScope'

type Scope = ReturnType<typeof useImageActionScope>
type Document = SoundDocument_Input & { title: string; tracks: SoundTrack[]; edges: NonNullable<SoundDocument_Input['edges']> }
type Pending = { version: 1; projectId: string; chapterId: string } & (
  { kind: 'import'; body: SoundCandidateImport } | { kind: 'speech'; body: LocalSpeechRequest }
  | { kind: 'export'; publicationId: string; publicationHash: string; body: { request_key: string } })

/** Keep genuine saved choices while making all controlled arrays present. */
function normalized(value: SoundDocument_Input): Document { return { ...value, title: value.title ?? '', tracks: value.tracks ?? [], edges: value.edges ?? [] } }
/** Copy only an explicitly selected whole picture document; later v1 edits never change this form automatically. */
function initial(picture: EditDocument): Document {
  const clips = picture.clips ?? []
  return { schema_version: 3, title: '章节声音版本', picture: structuredClone(picture), tracks: [], edges: clips.slice(0, -1).map((clip, index) => ({ left_shot_id: clip.shot_id, right_shot_id: clips[index + 1].shot_id, kind: 'hard_cut', duration_ms: 0 })) }
}
/** Show an actionable public message without rendering raw private response objects. */
function explain(error: unknown): string {
  const body = (error as { body?: { message?: unknown; detail?: unknown } })?.body
  return typeof body?.message === 'string' ? body.message : typeof body?.detail === 'string' ? body.detail : error instanceof Error ? error.message : '操作未完成，请核对原任务、修订和素材。'
}
/** Present the original named speaker while preserving stable IDs in every request and history record. */
function speakerLabel(item: EditCatalog['items'][number]): string {
  return item.speaker_name?.trim() || (item.speaker_id ? '原角色（未找到名称）' : '未标明说话人')
}

/** Native audio/download requests carry the visible account identity, never an API key. */
function mediaUrl(projectId: string, mediaId: string) { return workspaceMediaUrl(`${OpenAPI.BASE}/api/v1/studio/production/projects/${encodeURIComponent(projectId)}/media/${encodeURIComponent(mediaId)}/content`) }
/** Display actual execution states in the same language as the surrounding editing controls. */
function statusName(value: string) { const names: Record<string, string> = { queued: '排队中', running: '处理中', ready: '已完成', failed: '失败', cancelled: '已取消' }; return names[value] ?? value }

/** Draw peaks computed from actual decoded PCM; silence stays flat instead of receiving a decorative waveform. */
function Waveform({ audio }: { audio: SoundCandidateRead['audio'] }) {
  const peaks = Array.isArray(audio.envelope_pcm16) ? audio.envelope_pcm16 as number[] : []
  return <svg role="img" aria-label="实际声音波形" viewBox="0 0 512 44" className="w-full" style={{ maxWidth: 512, height: 44 }}>
    <line x1="0" x2="512" y1="22" y2="22" stroke="currentColor" opacity=".2" />
    {peaks.map((peak, index) => <line key={index} x1={(index + .5) * 512 / peaks.length} x2={(index + .5) * 512 / peaks.length} y1={22 - Math.min(32768, Math.max(0, peak)) / 32768 * 21} y2={22 + Math.min(32768, Math.max(0, peak)) / 32768 * 21} stroke="currentColor" strokeWidth="1.5" />)}
  </svg>
}

/** Remount private form state when identity changes, and fence every asynchronous continuation and owned dialog. */
export function SoundEditor(props: { projectId: string; chapterId: string; picture: EditDocument; onLocked: (value: boolean) => void }) {
  const scope = useImageActionScope(JSON.stringify(['chapter-sound', props.projectId, props.chapterId]))
  return <SoundForm key={scope.key} {...props} scope={scope} />
}

/** Own one project/chapter's explicit picture copy, audio adoptions and durable original request identities. */
function SoundForm({ projectId, chapterId, picture, onLocked, scope }: { projectId: string; chapterId: string; picture: EditDocument; onLocked: (value: boolean) => void; scope: Scope }) {
  const [saved, setSaved] = useState<SoundDraftRead | null>(null)
  const [document, setDocument] = useState<Document>(() => initial(picture))
  const [publication, setPublication] = useState<SoundPublicationRead | null>(null)
  const [catalog, setCatalog] = useState<EditCatalog | null>(null)
  const [candidates, setCandidates] = useState<SoundCandidateRead[]>([])
  const [media, setMedia] = useState<ProductionMediaRead[]>([])
  const [speech, setSpeech] = useState<LocalSpeechHistory | null>(null)
  const [exports, setExports] = useState<SoundExportCatalog | null>(null)
  const [busy, setBusy] = useState(true), [dirty, setDirty] = useState(false), [error, setError] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [mediaId, setMediaId] = useState<string>(), [kind, setKind] = useState<'voice' | 'effect' | 'music'>('voice'), [name, setName] = useState('')
  const [voiceId, setVoiceId] = useState<string>(), [dialogueId, setDialogueId] = useState<string>(), [rate, setRate] = useState(0)
  const [note, setNote] = useState(''), [acknowledged, setAcknowledged] = useState(false)
  const flight = useRef(false), polling = useRef(false), listsSequence = useRef(0), dirtyNow = useRef(false)
  const uploadId = useId()
  const storageKey = `fawnimo:sound-intent:${projectId}:${chapterId}`
  const stamp = scope.capture()
  const duration = (document.picture.clips ?? []).reduce((sum, clip) => sum + clip.source_out_ms - clip.source_in_ms, 0) - document.edges.reduce((sum, edge) => sum + (edge.duration_ms ?? 0), 0)
  const dialogues = (catalog?.items ?? []).filter(item => item.kind === 'dialogue')

  /** Run one action at a time and re-attest the actual account cookie before and after its work. */
  async function run(action: (observed: ReturnType<Scope['capture']>) => Promise<void>) {
    const observed = scope.capture()
    if (flight.current || !scope.isCurrent(observed)) return
    flight.current = true; onLocked(true); setBusy(true); setError('')
    try { await scope.attest(observed); await action(observed) }
    catch (failure) { if (scope.isCurrent(observed)) setError(explain(failure)) }
    finally { flight.current = false; if (scope.isCurrent(observed)) { setBusy(false); onLocked(dirtyNow.current) } }
  }
  /** Form changes invalidate any older confirmation and leave saved historical versions unchanged. */
  function change(value: Document) { if (busy || flight.current || !scope.isCurrent(stamp)) return; scope.invalidate(); setDocument(value); dirtyNow.current = true; setDirty(true); onLocked(true) }
  /** Settings changes also dismiss an older confirmation whose captured request no longer matches the visible choice. */
  function setting(action: () => void) { if (busy || flight.current || !scope.isCurrent(stamp)) return; scope.invalidate(); action() }

  /** Refresh histories independently of the editable form; late reads cannot replace later results or another account. */
  async function refreshLists(observed: ReturnType<Scope['capture']>) {
    const sequence = ++listsSequence.current
    const [candidateRows, mediaRows, speechRows, exportRows] = await Promise.all([
      S.listSoundCandidates({ projectId, chapterId }), P.listMediaApiV1StudioProductionProjectsProjectIdMediaGet({ projectId }),
      S.listLocalSpeech({ projectId, chapterId }), S.listSoundExports({ projectId, chapterId }),
    ])
    await scope.attest(observed)
    if (!scope.isCurrent(observed) || sequence !== listsSequence.current) return
    if ((candidateRows.data ?? []).some(row => row.project_id !== projectId || row.chapter_id !== chapterId)
      || (speechRows.data?.jobs ?? []).some(row => row.project_id !== projectId || row.chapter_id !== chapterId)
      || (exportRows.data?.exports ?? []).some(row => row.project_id !== projectId || row.chapter_id !== chapterId)) throw new Error('返回的历史记录不属于当前项目和章节，请刷新核对')
    setCandidates(candidateRows.data ?? []); setMedia((mediaRows.data ?? []).filter(row => row.project_id === projectId && row.mime === 'audio/wav'))
    setSpeech(speechRows.data ?? null); setExports(exportRows.data ?? null)
  }
  /** Load actual saved sound input once; routine job polling never resets unsaved edits. */
  async function load(observed: ReturnType<Scope['capture']>) {
    const result = await S.listSoundDrafts({ projectId, chapterId })
    await scope.attest(observed)
    const row = result.data?.[0] ?? null
    if (row && (row.project_id !== projectId || row.chapter_id !== chapterId)) throw new Error('声音草稿归属不符')
    setSaved(row); setDocument(row ? normalized(row.document) : initial(picture)); setDirty(false); dirtyNow.current = false
    setPending(workspaceStorage.getItem(storageKey))
    if (row?.publications.length) {
      const result = await S.getSoundPublication({ projectId, publicationId: row.publications[0].id })
      await scope.attest(observed)
      if (result.data?.project_id !== projectId || result.data.chapter_id !== chapterId) throw new Error('历史声音版本归属不符')
      setPublication(result.data)
    } else setPublication(null)
    await refreshLists(observed)
  }
  useEffect(() => {
    const observed = scope.capture()
    let active = true
    flight.current = true; setBusy(true)
    void scope.attest(observed).then(() => load(observed)).catch(failure => { if (active && scope.isCurrent(observed)) setError(explain(failure)) })
      .finally(() => { if (active && scope.isCurrent(observed)) { flight.current = false; setBusy(false) } })
    return () => { active = false; listsSequence.current++; flight.current = false }
  }, [])
  useEffect(() => { onLocked(busy || dirty) }, [busy, dirty, onLocked])
  useEffect(() => () => onLocked(false), [onLocked])
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirtyNow.current) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn)
  }, [])
  useEffect(() => {
    const observed = scope.capture()
    let active = true
    setCatalog(null)
    void E.getEditCatalogApiV1EditingProjectsProjectIdCatalogGet({ projectId, chapterId, planId: document.picture.plan_id }).then(async result => {
      await scope.attest(observed)
      if (active && scope.isCurrent(observed)) setCatalog(result.data ?? null)
    }).catch(failure => { if (active && scope.isCurrent(observed)) setError(explain(failure)) })
    return () => { active = false }
  }, [projectId, chapterId, document.picture.plan_id, scope.key])
  const activeJobs = (speech?.jobs ?? []).some(row => row.state === 'queued' || row.state === 'running') || (exports?.exports ?? []).some(row => row.state === 'queued' || row.state === 'running')
  useEffect(() => {
    if (!activeJobs) return
    const timer = window.setInterval(() => {
      if (flight.current || polling.current) return
      const observed = scope.capture()
      polling.current = true
      void refreshLists(observed).catch(failure => { if (scope.isCurrent(observed)) setError(explain(failure)) }).finally(() => { polling.current = false })
    }, 2000)
    return () => window.clearInterval(timer)
  }, [activeJobs, scope.key])

  /** Validate a recovery envelope's original scope before choosing any endpoint. Unresolved failures retain its exact bytes. */
  async function sendPending(raw: string, observed: ReturnType<Scope['capture']>) {
    const value = JSON.parse(raw) as Pending
    if (value.version !== 1 || value.projectId !== projectId || value.chapterId !== chapterId || !['import', 'speech', 'export'].includes(value.kind) || !value.body?.request_key) throw new Error('恢复记录格式或原项目不符，请核对历史；保留原记录')
    await scope.attest(observed)
    if (workspaceStorage.getItem(storageKey) !== raw) throw new Error('本地提交记录已被其他页面更新，请刷新核对')
    if (value.kind === 'import') {
      const result = (await S.importSoundCandidate({ projectId, requestBody: value.body })).data
      await scope.attest(observed)
      if (!result || result.project_id !== projectId || result.chapter_id !== chapterId || result.media_id !== value.body.media_id || result.sha256 !== value.body.expected_sha256 || result.kind !== value.body.kind || result.origin !== 'user_import') throw new Error('原声音候选返回不符，请核对历史；保留提交记录')
    } else if (value.kind === 'speech') {
      const result = (await S.createLocalSpeech({ projectId, requestBody: value.body })).data
      await scope.attest(observed)
      if (!result || result.project_id !== projectId || result.chapter_id !== chapterId || result.plan_id !== value.body.plan_id || result.dialogue_id !== value.body.dialogue_id || result.voice.id !== value.body.voice_id || result.rate !== (value.body.rate ?? 0) || result.profile !== value.body.profile || result.external_fee_fen !== 0) throw new Error('原配音任务返回不符，请核对历史；保留提交记录')
    } else {
      const result = (await S.createSoundExport({ projectId, publicationId: value.publicationId, requestBody: value.body })).data
      await scope.attest(observed)
      if (!result || result.project_id !== projectId || result.chapter_id !== chapterId || result.publication_id !== value.publicationId || result.publication_hash !== value.publicationHash) throw new Error('原导出任务返回不符，请核对历史；保留提交记录')
    }
    scope.ensure(observed)
    if (workspaceStorage.getItem(storageKey) === raw) { workspaceStorage.removeItem(storageKey); setPending(null) }
    else { setPending(workspaceStorage.getItem(storageKey)); throw new Error('原请求已处理，但另一条本地记录已出现；已保留，请核对') }
    await refreshLists(observed)
  }
  /** Persist an original request identity before sending; a different pending record is never overwritten. */
  function submit(value: Pending, title: string) {
    scope.confirm({ title, content: '结果将先进入当前项目的候选或任务历史。配音和导出均不调用云端模型，外部费用为0元。', onOk: () => run(async observed => {
      const existing = workspaceStorage.getItem(storageKey)
      if (existing !== null) { setPending(existing); throw new Error('已有待核对的原请求，请先恢复或核对历史后清除本地记录') }
      const raw = JSON.stringify(value); workspaceStorage.setItem(storageKey, raw); setPending(raw)
      await sendPending(raw, observed)
    }) })
  }
  /** Removing a recovery record is an explicit local-only action, after fresh identity and exact-record checks. */
  function discardPending() {
    const raw = pending
    if (raw === null) return
    scope.confirm({ title: '已核对原候选与任务历史？', content: '只清除此条浏览器提交记录，不取消任务、不删除候选、不改变已锁定版本。确认原请求结果后再清除。', onOk: () => run(async observed => {
      await scope.attest(observed)
      if (workspaceStorage.getItem(storageKey) !== raw) { setPending(workspaceStorage.getItem(storageKey)); throw new Error('原记录已变化，未清除；请重新核对') }
      workspaceStorage.removeItem(storageKey); setPending(null)
    }) })
  }
  /** Upload adds only original-project material; registration and track adoption are still explicit later actions. */
  async function upload(file?: File) {
    if (!file) return
    if (!/\.wav$/i.test(file.name) || !file.size || file.size > 32 * 1024 * 1024) { setError('请选择32MiB以内的PCM WAV文件'); return }
    await run(async observed => {
      const result = await P.postMediaApiV1StudioProductionProjectsProjectIdMediaPost({ projectId, formData: { file: file as unknown as string } })
      await scope.attest(observed)
      const row = result.data?.[0]
      if (!row || row.project_id !== projectId || row.mime !== 'audio/wav') throw new Error('上传结果归属或格式不符')
      await refreshLists(observed); scope.ensure(observed); setMediaId(row.id); setName(row.name)
    })
  }
  /** Persist an independent sound form without changing the original picture editor draft or adoptions. */
  async function save() { await run(async observed => {
    const result = saved ? await S.patchSoundDraft({ projectId, draftId: saved.id, requestBody: { expected_revision: saved.revision, document } })
      : await S.createSoundDraft({ projectId, requestBody: { chapter_id: chapterId, document } })
    await scope.attest(observed)
    if (!result.data || result.data.project_id !== projectId || result.data.chapter_id !== chapterId) throw new Error('声音草稿响应归属不符，请保留输入并刷新核对')
    setSaved(result.data); setDocument(normalized(result.data.document)); setDirty(false); dirtyNow.current = false
  }) }
  /** Freeze only a saved revision, preserving every original source and human acknowledgment. */
  function freeze() {
    if (!saved || dirty) return
    const revision = saved.revision, draftId = saved.id
    scope.confirm({ title: '锁定当前画面与声音版本？', content: '会重新完整校验实际素材和对白覆盖。人工确认不代表语音内容、音色或口型已经自动验收。', onOk: () => run(async observed => {
      const result = await S.freezeSoundDraft({ projectId, draftId, requestBody: { expected_revision: revision, acknowledge_changes: acknowledged, change_note: note } })
      await scope.attest(observed)
      if (!result.data || result.data.project_id !== projectId || result.data.chapter_id !== chapterId || result.data.draft_revision !== revision) throw new Error('锁定版本响应不符，请核对历史')
      setPublication(result.data)
      const refreshed = await S.listSoundDrafts({ projectId, chapterId }); await scope.attest(observed)
      const actual = refreshed.data?.find(row => row.id === draftId && row.project_id === projectId && row.chapter_id === chapterId)
      if (actual) setSaved(actual)
    }) })
  }
  /** Adopting a candidate adds a visible editable placement; it does not silently save or lock anything. */
  function adopt(candidate: SoundCandidateRead) {
    const availableMs = Math.floor(Number(candidate.audio.duration_us) / 10000) * 10
    const end = Math.min(availableMs, Math.floor(Math.max(0, duration) / 10) * 10)
    if (end <= 0 || document.tracks.length >= 64) { setError('请先保留有效画面时长；音轨最多64条'); return }
    change({ ...document, tracks: [...document.tracks, { id: crypto.randomUUID(), candidate_id: candidate.id, source_in_ms: 0, source_out_ms: end, timeline_start_ms: 0, gain_db: 0, muted: false, dialogue_id: null, content_confirmed: false }] })
  }
  /** Update one complete placement and require a fresh spoken-content declaration after any change. */
  function trackChange(index: number, patch: Partial<SoundTrack>) { change({ ...document, tracks: document.tracks.map((track, at) => at === index ? { ...track, ...patch, content_confirmed: patch.content_confirmed ?? false } : track) }) }
  /** Change only an explicitly selected music operation; clearing both retains the original sound behavior. */
  function musicChange(index: number, patch: Partial<NonNullable<SoundTrack['music']>>) {
    const options = { ...document.tracks[index].music, ...patch }
    trackChange(index, { music: options.loop_until_ms == null && options.voice_duck_db == null ? null : options })
  }
  /** A loop extends a chosen shorter source range to a visible output position, never the picture itself. */
  function musicLoop(index: number, enabled: boolean) {
    const track = document.tracks[index]
    if (enabled && duration <= track.timeline_start_ms + track.source_out_ms - track.source_in_ms) { setError('请先将音乐源区间裁短，循环终点须晚于原输出终点'); return }
    musicChange(index, { loop_until_ms: enabled ? duration : null })
  }
  /** Inspect a historical publication without replacing the current unsaved form. */
  async function inspect(publicationId: string) { await run(async observed => {
    const result = await S.getSoundPublication({ projectId, publicationId }); await scope.attest(observed)
    if (!result.data || result.data.project_id !== projectId || result.data.chapter_id !== chapterId) throw new Error('声音历史版本归属不符')
    setPublication(result.data)
  }) }
  const selectedMedia = media.find(row => row.id === mediaId)
  const selectedLine = dialogues.find(row => row.item_id === dialogueId)
  const voiceOptions = speech?.catalog.voices ?? []
  return <Card id="sound-timeline" title="声音时间线" className="production-sound-panel" data-testid="sound-editor">
    <Space direction="vertical" className="w-full" size="large">
      <Alert type="info" showIcon message="独立配音、音效与配乐" description="使用实际PCM WAV，可裁剪、安排位置和调整音量。离线配音使用当前环境已安装的声音，外部费用0元；生成结果须单独试听和采用。声音版本独立保存，字幕沿用原计划文字与说话人。" />
      {error && <Alert type="error" message={error} />}
      {dirty && <Alert type="warning" message="声音草稿有未保存修改，请保存后再锁定或离开。" />}
      {pending !== null && <Alert type="warning" message="有一条提交结果待核对，原记录已保留" description={<Space wrap><Button disabled={busy} onClick={() => void run(observed => sendPending(pending, observed))}>恢复原请求</Button><Button disabled={busy} onClick={discardPending}>核对历史后清除本地记录</Button></Space>} />}
      <Space wrap><Input aria-label="声音版本标题" disabled={busy} style={{ width: 280 }} value={document.title} onChange={event => change({ ...document, title: event.target.value })} /><Tag>最终画面时长 {duration} 毫秒</Tag><Button disabled={busy} onClick={() => dirty ? scope.confirm({ title: '重新载入已保存声音草稿？', content: '将替换当前未保存声音输入。已处理的原提交可在候选和任务历史中核对。', onOk: () => run(load) }) : void run(load)}>重新载入已保存声音资料</Button></Space>
      <Collapse items={[{ key: 'picture', label: '画面裁剪、原声和原内容标记', children: <Space direction="vertical" className="w-full">
        <p>这里保存独立的画面副本。标记使用原视频时钟；配音的位置使用最终输出时钟。修改裁剪后请重新核对对白与字幕。</p>
        {(document.picture.clips ?? []).map((clip, index) => <Space wrap key={clip.take_id}><Tag>镜头{index + 1}</Tag><span>源起点</span><InputNumber aria-label={`声音画面${index + 1}起点`} disabled={busy} min={0} max={300000} step={100} value={clip.source_in_ms} onChange={value => change({ ...document, picture: { ...document.picture, clips: document.picture.clips?.map((row, at) => at === index ? { ...row, source_in_ms: value ?? 0 } : row) } })} /><span>源终点</span><InputNumber aria-label={`声音画面${index + 1}终点`} disabled={busy} min={100} max={300000} step={100} value={clip.source_out_ms} onChange={value => change({ ...document, picture: { ...document.picture, clips: document.picture.clips?.map((row, at) => at === index ? { ...row, source_out_ms: value ?? 100 } : row) } })} /><Checkbox disabled={busy} checked={clip.source_audio} onChange={event => change({ ...document, picture: { ...document.picture, clips: document.picture.clips?.map((row, at) => at === index ? { ...row, source_audio: event.target.checked } : row) } })}>保留此镜原声</Checkbox></Space>)}
        {(document.picture.anchors ?? []).map((anchor, index) => <Space wrap key={`${anchor.kind}:${anchor.item_id}`}><span>{catalog?.items.find(row => row.kind === anchor.kind && row.item_id === anchor.item_id)?.text || anchor.item_id}</span><InputNumber aria-label={`内容标记${index + 1}起点`} disabled={busy} min={0} step={1} value={anchor.start_ms} onChange={value => change({ ...document, picture: { ...document.picture, anchors: document.picture.anchors?.map((row, at) => at === index ? { ...row, start_ms: value ?? 0, confirmed: false } : row) } })} /><InputNumber aria-label={`内容标记${index + 1}终点`} disabled={busy} min={1} step={1} value={anchor.end_ms} onChange={value => change({ ...document, picture: { ...document.picture, anchors: document.picture.anchors?.map((row, at) => at === index ? { ...row, end_ms: value ?? 1, confirmed: false } : row) } })} /><Checkbox disabled={busy} checked={anchor.confirmed} onChange={event => change({ ...document, picture: { ...document.picture, anchors: document.picture.anchors?.map((row, at) => at === index ? { ...row, confirmed: event.target.checked } : row) } })}>确认保留原内容</Checkbox></Space>)}
        {document.edges.map((edge, index) => <Space key={index}><span>边界{index + 1}</span><Select aria-label={`声音画面边界${index + 1}`} disabled={busy} value={edge.kind} options={[{ value: 'hard_cut', label: '硬切' }, { value: 'crossfade', label: '交叉淡化' }]} onChange={value => change({ ...document, edges: document.edges.map((row, at) => at === index ? { ...row, kind: value, duration_ms: value === 'hard_cut' ? 0 : 100 } : row) })} /><InputNumber aria-label={`声音画面重叠${index + 1}`} disabled={busy || edge.kind === 'hard_cut'} min={100} max={1000} step={100} value={edge.duration_ms} onChange={value => change({ ...document, edges: document.edges.map((row, at) => at === index ? { ...row, duration_ms: value ?? 100 } : row) })} /></Space>)}
      </Space> }]} />
      <Card size="small" title="1. 准备声音候选">
        <Space direction="vertical" className="w-full">
          <Space wrap><label htmlFor={uploadId}>上传原项目WAV</label><input id={uploadId} aria-label="上传声音素材" type="file" accept=".wav,audio/wav" disabled={busy} onChange={event => { void upload(event.target.files?.[0]); event.target.value = '' }} /></Space>
          <p>接受24/48kHz、16位PCM、单声道或双声道WAV，每个不超过32MiB、300秒。上传只添加素材。</p>
          <Space wrap><Select aria-label="原项目声音素材" disabled={busy} style={{ minWidth: 240 }} value={mediaId} options={media.map(row => ({ value: row.id, label: row.name }))} placeholder="选择实际WAV" onChange={value => setting(() => { setMediaId(value); setName(media.find(row => row.id === value)?.name ?? '') })} /><Select aria-label="声音候选类型" disabled={busy} value={kind} options={[{ value: 'voice', label: '配音' }, { value: 'effect', label: '音效' }, { value: 'music', label: '配乐' }]} onChange={value => setting(() => setKind(value))} /><Input aria-label="声音候选名称" disabled={busy} value={name} onChange={event => setting(() => setName(event.target.value))} /><Button disabled={busy || pending !== null || !selectedMedia || !name.trim()} onClick={() => { if (selectedMedia) submit({ version: 1, projectId, chapterId, kind: 'import', body: { request_key: crypto.randomUUID(), chapter_id: chapterId, media_id: selectedMedia.id, expected_sha256: selectedMedia.sha256, kind, name } }, '登记这份声音为候选？') }}>登记声音候选</Button></Space>
          <Collapse items={[{ key: 'speech', label: '用本机声音生成原对白配音（外部费用0元）', children: <Space direction="vertical" className="w-full">
            {speech && !speech.catalog.enabled && <Alert type="info" message={speech.catalog.reason} />}
            <Select aria-label="配音原对白" disabled={busy} className="w-full" placeholder="明确选择原计划对白" value={dialogueId} options={dialogues.map(row => ({ value: row.item_id, label: `${speakerLabel(row)}：${row.text}` }))} onChange={value => setting(() => setDialogueId(value))} />
            {selectedLine && <p>原对白：{selectedLine.text} · 说话人：{speakerLabel(selectedLine)}</p>}
            <Space wrap><Select aria-label="本地配音声音" disabled={busy || !speech?.catalog.enabled} style={{ minWidth: 260 }} placeholder="明确选择已安装声音" value={voiceId} options={voiceOptions.map(row => ({ value: row.id, label: `${row.name} / ${row.culture}` }))} onChange={value => setting(() => setVoiceId(value))} /><span>语速</span><InputNumber aria-label="本地配音语速" disabled={busy} min={-2} max={2} step={1} precision={0} value={rate} onChange={value => setting(() => setRate(value ?? 0))} /><Button disabled={busy || pending !== null || !speech?.catalog.enabled || !voiceOptions.some(row => row.id === voiceId) || !selectedLine || !document.picture.plan_id} onClick={() => { if (voiceId && selectedLine && document.picture.plan_id && speech?.catalog.profile) submit({ version: 1, projectId, chapterId, kind: 'speech', body: { request_key: crypto.randomUUID(), plan_id: document.picture.plan_id, dialogue_id: selectedLine.item_id, voice_id: voiceId, rate, profile: speech.catalog.profile } }, '使用所选本地声音生成这句原对白？') }}>生成配音候选</Button></Space>
            <List size="small" dataSource={speech?.jobs ?? []} renderItem={row => <List.Item><Space wrap><Tag>{statusName(row.state)}</Tag><span>{row.text} · {row.voice.name}</span><span>{row.reason}</span>{(row.state === 'queued' || row.state === 'running') && <Button disabled={busy} onClick={() => scope.confirm({ title: '取消此本地配音任务？', onOk: () => run(async observed => { await S.cancelLocalSpeech({ projectId, jobId: row.id, requestBody: {} }); await scope.attest(observed); await refreshLists(observed) }) })}>取消配音</Button>}</Space></List.Item>} />
          </Space> }]} />
          <List pagination={{ pageSize: 10, hideOnSinglePage: true }} dataSource={candidates} locale={{ emptyText: '尚无声音候选；先上传登记或生成本地配音' }} renderItem={row => <List.Item><Space direction="vertical" className="w-full"><Space wrap><Tag>{row.kind === 'voice' ? '配音' : row.kind === 'music' ? '配乐' : '音效'}</Tag><span>{row.name}</span><Tag>{row.origin === 'local_speech' ? '本地合成声音' : '用户导入'}</Tag><span>{Math.floor(Number(row.audio.duration_us) / 1000)}毫秒</span><Button disabled={busy} onClick={() => adopt(row)}>采用到声音时间线</Button></Space><Waveform audio={row.audio} /><audio aria-label={`试听${row.name}`} controls preload="none" src={mediaUrl(projectId, row.media_id)} /></Space></List.Item>} />
        </Space>
      </Card>
      <Card size="small" title="2. 安排声音与原对白">
        <Space direction="vertical" className="w-full">
          <p>位置和裁剪使用10毫秒步长。原对白配音须人工试听确认文字与说话人，并完整覆盖对应字幕；静音、过低音量或无有效信号不能作为对白覆盖。</p>
          {document.tracks.map((track, index) => {
            const candidate = candidates.find(row => row.id === track.candidate_id), cue = dialogues.find(row => row.item_id === track.dialogue_id)
            const end = track.music?.loop_until_ms ?? track.timeline_start_ms + track.source_out_ms - track.source_in_ms
            return <Card size="small" key={track.id} title={`${index + 1}. ${candidate?.name ?? '原声音候选不可用'}`} extra={<Button disabled={busy} onClick={() => change({ ...document, tracks: document.tracks.filter((_, at) => at !== index) })}>移除此音轨</Button>}>
              <Space direction="vertical" className="w-full"><div aria-label={`音轨${index + 1}位置`} style={{ height: 20, background: 'var(--ant-color-fill-secondary, #80808020)', position: 'relative', overflow: 'hidden' }}><div style={{ position: 'absolute', left: `${track.timeline_start_ms / Math.max(1, duration) * 100}%`, width: `${(end - track.timeline_start_ms) / Math.max(1, duration) * 100}%`, height: 20, background: end > duration ? '#c94848' : '#508d78', opacity: track.muted ? .25 : .8 }} /></div>
                <Space wrap><span>源起点</span><InputNumber aria-label={`音轨${index + 1}源起点`} disabled={busy} min={0} max={300000} step={10} value={track.source_in_ms} onChange={value => trackChange(index, { source_in_ms: value ?? 0 })} /><span>源终点</span><InputNumber aria-label={`音轨${index + 1}源终点`} disabled={busy} min={10} max={300000} step={10} value={track.source_out_ms} onChange={value => trackChange(index, { source_out_ms: value ?? 10 })} /><span>输出位置</span><InputNumber aria-label={`音轨${index + 1}输出位置`} disabled={busy} min={0} max={300000} step={10} value={track.timeline_start_ms} onChange={value => trackChange(index, { timeline_start_ms: value ?? 0 })} /><span>音量dB</span><InputNumber aria-label={`音轨${index + 1}音量`} disabled={busy} min={-60} max={6} step={1} precision={0} value={track.gain_db} onChange={value => trackChange(index, { gain_db: value ?? 0 })} /><Checkbox disabled={busy} checked={track.muted} onChange={event => trackChange(index, { muted: event.target.checked })}>静音</Checkbox></Space>
                {candidate?.kind === 'music' && <><Space wrap><Checkbox aria-label={`音轨${index + 1}循环音乐`} disabled={busy} checked={track.music?.loop_until_ms != null} onChange={event => musicLoop(index, event.target.checked)}>循环音乐</Checkbox>{track.music?.loop_until_ms != null && <><span>循环至输出位置</span><InputNumber aria-label={`音轨${index + 1}循环终点`} disabled={busy} min={10} max={Math.max(10, duration)} step={10} precision={0} value={track.music.loop_until_ms} onChange={value => musicChange(index, { loop_until_ms: value ?? duration })} /></>}<Checkbox aria-label={`音轨${index + 1}对白期间降低音乐`} disabled={busy} checked={track.music?.voice_duck_db != null} onChange={event => musicChange(index, { voice_duck_db: event.target.checked ? -12 : null })}>已确认对白期间降低音乐</Checkbox>{track.music?.voice_duck_db != null && <><span>降低dB</span><InputNumber aria-label={`音轨${index + 1}对白音乐降低量`} disabled={busy} min={-30} max={-3} step={1} precision={0} value={track.music.voice_duck_db} onChange={value => musicChange(index, { voice_duck_db: value ?? -12 })} /></>}</Space><span style={{ fontSize: 12, opacity: .75 }}>音乐只在所选源区间内循环。对白避让使用已试听确认的配音：对白前50毫秒渐低，结束后150毫秒恢复；不自动识别原片对白。</span></>}
                {candidate?.kind === 'voice' && <><Select aria-label={`音轨${index + 1}对应原对白`} allowClear disabled={busy} className="w-full" placeholder="如用于原对白，明确选择对应条目" value={track.dialogue_id ?? undefined} options={dialogues.map(row => ({ value: row.item_id, label: `${speakerLabel(row)}：${row.text}` }))} onChange={value => trackChange(index, { dialogue_id: value ?? null })} />{cue && <Checkbox disabled={busy} checked={track.content_confirmed} onChange={event => trackChange(index, { content_confirmed: event.target.checked })}>已试听并确认保留原对白“{cue.text}”及说话人“{speakerLabel(cue)}”</Checkbox>}</>}
              </Space>
            </Card>
          })}
        </Space>
      </Card>
      <Card size="small" title="3. 保存、锁定与导出">
        <Space direction="vertical" className="w-full"><Space wrap><Button type="primary" disabled={busy} onClick={() => void save()}>保存声音草稿</Button><Tag>声音修订 {saved?.revision ?? '未保存'}</Tag><Button disabled={busy || dirty || !saved?.can_freeze} onClick={freeze}>锁定声音版本</Button></Space>
          {saved && <Alert type={saved.can_freeze ? 'success' : 'warning'} message={saved.can_freeze ? '已保存声音输入可锁定；不代表语义或音色质量通过' : '已保存声音输入仍有缺项'} description={saved.issues.length ? <List size="small" dataSource={saved.issues} renderItem={row => <List.Item>{row.message}</List.Item>} /> : undefined} />}
          {!!saved?.changes.length && <Alert type="info" message={saved.changes.join('；')} />}
          <Checkbox disabled={busy} checked={acknowledged} onChange={event => setting(() => setAcknowledged(event.target.checked))}>确认本声音版本的裁剪、重叠或历史计划变化</Checkbox><Input aria-label="声音版本变化说明" disabled={busy} value={note} onChange={event => setting(() => setNote(event.target.value))} placeholder="有裁剪、时长或历史计划变化时，请填写原因" />
          <Space wrap><Select aria-label="已锁定声音版本" disabled={busy} style={{ minWidth: 260 }} value={publication?.id} placeholder="选择声音历史版本" options={saved?.publications.map(row => ({ value: row.id, label: `声音修订${row.draft_revision} · ${row.duration_ms}毫秒` }))} onChange={value => void inspect(value)} /><Button disabled={busy || !publication} onClick={() => { if (publication) scope.confirm({ title: '从此历史声音版本恢复新草稿？', content: '替换当前未保存声音表单，历史版本与原任务保持可查。', onOk: () => { change(normalized(publication.payload.document as SoundDocument_Input)) } }) }}>恢复历史声音为草稿</Button><Button disabled={busy || dirty || pending !== null || !publication || !exports?.enabled} onClick={() => { if (publication) submit({ version: 1, projectId, chapterId, kind: 'export', publicationId: publication.id, publicationHash: publication.content_hash, body: { request_key: crypto.randomUUID() } }, '导出此已锁定声音版本？') }}>导出声音版本</Button></Space>
          {exports && !exports.enabled && <Alert type="info" message={exports.reason} />}
          <p>输出360×640/30帧视频、48kHz双声道WAV混音、独立SRT/VTT字幕和完整下载包。音量按明确设置叠加，超过样本范围时限幅。明确启用的音乐可循环并在已确认对白期间渐低，音频保持原速。</p>
          <List size="small" dataSource={exports?.exports ?? []} locale={{ emptyText: '尚无声音导出任务' }} renderItem={row => <List.Item><Space direction="vertical" className="w-full"><Space wrap><Tag>{statusName(row.state)}</Tag><span>{row.reason}</span>{(row.state === 'queued' || row.state === 'running') && <Button disabled={busy} onClick={() => scope.confirm({ title: '取消此声音导出？', onOk: () => run(async observed => { await S.cancelSoundExport({ projectId, exportId: row.id, requestBody: {} }); await scope.attest(observed); await refreshLists(observed) }) })}>取消声音导出</Button>}{row.files.map(file => <a key={file.kind} download href={workspaceMediaUrl(OpenAPI.BASE + file.content_url)}>{file.kind === 'mixdown' ? '独立混音WAV' : file.name}</a>)}</Space>{row.state === 'ready' && row.files.find(file => file.kind === 'video') && <video aria-label="声音合成结果" controls preload="none" style={{ maxHeight: 380 }} src={workspaceMediaUrl(OpenAPI.BASE + row.files.find(file => file.kind === 'video')!.content_url)} />}</Space></List.Item>} />
        </Space>
      </Card>
    </Space>
  </Card>
}
