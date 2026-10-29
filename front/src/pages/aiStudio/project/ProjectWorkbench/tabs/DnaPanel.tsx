import { workspaceStorage } from '../../../../../services/workspaceContext'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Collapse, Empty, Input, InputNumber, List, Modal, Select, Space, Tag, message } from 'antd'
import { ProjectSection } from '../../ProjectSection'
import { DnaProductionControls } from './DnaProductionControls'
import { VideoDnaService as V } from '../../../../../services/generated'
import type { DnaAsset_Input as DnaAsset, DnaCatalog, DnaClaim, DnaCreate, DnaDocument_Input as DnaDocument, DnaDraft, DnaImport, DnaPreflight, DnaShot_Input as DnaShot, DnaSnapshot, DnaPreparationRead, DnaCompareRead } from '../../../../../services/generated'

type Document = DnaDocument & { assets: DnaAsset[]; shots: DnaShot[] }
type Claim = DnaClaim & { evidence: { start_frame: number; end_frame: number }[] }
type Intent = { kind: 'create'; body: DnaCreate } | { kind: 'import'; body: DnaImport }
const fields = { character: ['face', 'hair', 'body', 'costume', 'makeup', 'accessories', 'voice'], scene: ['layout', 'architecture', 'props', 'lighting', 'background', 'time_and_space'], prop: ['shape', 'material', 'state'] } as const
const labels: Record<string, string> = { character: '角色', scene: '场景', prop: '道具', face: '面部', hair: '发型', body: '体型', costume: '服装', makeup: '妆容', accessories: '配饰', voice: '声线', layout: '空间布局', architecture: '建筑', props: '场内道具', lighting: '光影', background: '背景', time_and_space: '时间与空间', shape: '形状', material: '材质', state: '状态', composition: '构图', camera: '相机', action: '动作', continuity: '连续性', audio: '声音', image_prompt: '图像准备描述', video_prompt: '视频准备描述', global_style: '全局风格', negative_constraints: '负向约束', audio_editing: '声音与剪辑构想' }
const shotKeys = ['composition', 'camera', 'lighting', 'action', 'continuity', 'audio', 'image_prompt', 'video_prompt'] as const
/** An empty field explicitly means unknown; no understanding or translation is invented. */
function unknown(reason = ''): Claim { return { status: 'unknown', zh: null, en: null, reason, confidence: null, evidence: [] } }
/** Keep optional collections present in the controlled editor. */
function normalize(value: DnaDocument): Document { return { ...value, assets: value.assets ?? [], shots: value.shots ?? [] } }
/** Show safe server feedback without exposing raw implementation details. */
function explain(error: unknown) { const body = (error as { body?: { message?: unknown; detail?: unknown } }).body; return typeof body?.message === 'string' ? body.message : typeof body?.detail === 'string' ? body.detail : '操作失败，请检查源修订、字段、文件范围及引用。' }
/** Allocate a stable local schema ID without using an executable identity. */
function localId(prefix: string) { return `${prefix}_${crypto.randomUUID().replace(/-/g, '')}` }
/** Preserve strict integer JSON semantics before JS could round or coerce 1.0 tokens. */
function parsePortableJson(text: string): Record<string, unknown> {
  const tokens = /"(?:\\[\s\S]|[^"\\])*"|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g
  for (const match of text.matchAll(tokens)) if (match[1] && (/[.eE]/.test(match[1]) || !Number.isSafeInteger(Number(match[1])))) throw new Error('Only safe integer JSON tokens are supported')
  const value = JSON.parse(text)
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('A JSON object is required')
  return value
}
/** Browser downloads contain inert JSON and cannot carry execution eligibility. */
function download(value: unknown, name: string) { const url = URL.createObjectURL(new Blob([JSON.stringify(value)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) }

/** Shared bilingual declaration and original-frame evidence controls. */
function ClaimEditor({ value, onChange, name, busy, start, end, onInspect }: { value?: DnaClaim; onChange: (value: DnaClaim) => void; name: string; busy: boolean; start: number; end: number; onInspect: (frame: number) => void }) {
  const claim: Claim = { ...unknown(), ...value, evidence: value?.evidence ?? [] }
  /** Merge one claim field while retaining its other declarations and evidence. */
  const change = (patch: Partial<Claim>) => onChange({ ...claim, ...patch })
  return <Space direction="vertical" className="w-full" data-testid="dna-claim">
    <Space wrap><Select aria-label={`${name}状态`} disabled={busy} value={claim.status} style={{ width: 180 }} options={[{ value: 'unknown', label: '未知（未作判断）' }, { value: 'observed', label: '人工观察声明' }, { value: 'inferred', label: '复现构想/推断' }]} onChange={status => change(status === 'unknown' ? { status, zh: null, en: null, confidence: null } : { status })} /><label>人工置信度<InputNumber aria-label={`${name}置信度`} min={0} max={100} precision={0} disabled={busy || claim.status === 'unknown'} value={claim.confidence} onChange={confidence => change({ confidence })} /></label></Space>
    <Input.TextArea aria-label={`${name}中文`} rows={2} maxLength={4000} disabled={busy || claim.status === 'unknown'} value={claim.zh ?? ''} placeholder="中文人工描述；未知时留空" onChange={event => change({ zh: event.target.value })} />
    <Input.TextArea aria-label={`${name}英文`} rows={2} maxLength={4000} disabled={busy || claim.status === 'unknown'} value={claim.en ?? ''} placeholder="English description; no automatic translation" onChange={event => change({ en: event.target.value })} />
    <Input.TextArea aria-label={`${name}原因`} rows={2} maxLength={4000} disabled={busy} value={claim.reason} placeholder="未知原因或推断理由（人工填写）" onChange={event => change({ reason: event.target.value })} />
    <div>证据为原始帧半开区间 [起点, 终点)，本项范围 {start} 至 {end}</div>
    {claim.evidence.map((range, index) => <Space wrap key={index}><InputNumber aria-label={`${name}证据${index + 1}起点`} min={start} max={end - 1} precision={0} disabled={busy} value={range.start_frame} onChange={number => change({ evidence: claim.evidence.map((item, position) => position === index ? { ...item, start_frame: number ?? start } : item) })} /><span>至</span><InputNumber aria-label={`${name}证据${index + 1}终点`} min={start + 1} max={end} precision={0} disabled={busy} value={range.end_frame} onChange={number => change({ evidence: claim.evidence.map((item, position) => position === index ? { ...item, end_frame: number ?? start + 1 } : item) })} /><Button disabled={busy} onClick={() => onInspect(range.start_frame)}>查看证据起点</Button><Button disabled={busy} onClick={() => change({ evidence: claim.evidence.filter((_, position) => position !== index) })}>移除此区间</Button></Space>)}
    <Button disabled={busy || claim.evidence.length >= 32 || start >= end} onClick={() => change({ evidence: [...claim.evidence, { start_frame: start, end_frame: Math.min(end, start + 1) }] })}>添加原帧证据</Button>
  </Space>
}

/** Editable human DNA lives alongside the actual R1 media, never in a mock page. */
export function DnaPanel({ projectId, analysisId, analysisRevision, initialDraftId, onInspectFrame, onLockChange }: { projectId: string; analysisId: string; analysisRevision: number; initialDraftId?: string; onInspectFrame: (frame: number) => void; onLockChange: (locked: boolean) => void }) {
  const [catalog, setCatalog] = useState<DnaCatalog | null>(null)
  const [draft, setDraft] = useState<DnaDraft | null>(null)
  const [document, setDocument] = useState<Document | null>(null)
  const [snapshot, setSnapshot] = useState<DnaSnapshot | null>(null)
  const [preflight, setPreflight] = useState<DnaPreflight | null>(null)
  const [preparation, setPreparation] = useState<DnaPreparationRead | null>(null)
  const [comparison, setComparison] = useState<DnaCompareRead | null>(null)
  const [baseSnapshotId, setBaseSnapshotId] = useState('')
  const [otherSnapshotId, setOtherSnapshotId] = useState('')
  const [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')
  const [pending, setPending] = useState<Intent | null>(null)
  const [assetKind, setAssetKind] = useState<DnaAsset['kind']>('character')
  const [unknownReason, setUnknownReason] = useState('')
  const alive = useRef(true)
  const operation = useRef(0)
  const formVersion = useRef(0)
  const locked = useRef(false)
  const dirtyNow = useRef(false)
  const modal = useRef<ReturnType<typeof Modal.confirm> | null>(null)
  const currentDraft = useRef(draft)
  currentDraft.current = draft
  const storageKey = `jellyfish:dna-intent:${projectId}:${analysisId}`
  const source = draft?.source ?? snapshot?.payload.source
  const frameCount = source?.frames.length ?? catalog?.analyses.find(item => item.id === analysisId)?.frame_count ?? 0
  /** Reject callbacks after this analysis workspace has been disposed. */
  const current = () => alive.current
  /** Synchronously reserve the editor before an asynchronous operation begins. */
  function start() { if (!current() || locked.current) return false; locked.current = true; setBusy(true); setError(''); return true }
  /** Release the operation lock only while this workspace remains mounted. */
  function finish() { if (current()) { locked.current = false; setBusy(false) } }
  /** Keep the complete document and invalidate confirmations after any local edit. */
  function edit(value: DnaDocument) { formVersion.current++; dirtyNow.current = true; setDirty(true); setDocument(normalize(value)) }
  /** Old confirmations cannot write after any field or scope changes. */
  function confirm(title: string, content: string, action: () => Promise<void>) { const version = formVersion.current; modal.current?.destroy(); modal.current = Modal.confirm({ title, content, onOk: async () => { if (current() && version === formVersion.current) await action() } }) }

  /** Read the actual project source, draft and fixed-version choices. */
  async function loadCatalog() { const result = await V.getVideoDnaCatalogApiV1VideoDnaProjectsProjectIdCatalogGet({ projectId }); if (current()) setCatalog(result.data ?? null); return result.data }
  /** Loading a selected draft always supersedes earlier history and async views. */
  async function loadDraft(id: string) {
    const token = ++operation.current
    const result = await V.getVideoDnaDraftApiV1VideoDnaProjectsProjectIdDraftsDraftIdGet({ projectId, draftId: id })
    if (!current() || token !== operation.current || !result.data) return
    setDraft(result.data); setDocument(normalize(result.data.document)); currentDraft.current = result.data; formVersion.current++; dirtyNow.current = false; setDirty(false); setSnapshot(null); setPreflight(null); setPreparation(null); setComparison(null)
  }
  /** Load a saved source draft and recover any persisted original request. */
  async function load() {
    if (!start()) return
    try { const options = await loadCatalog(); if (!current()) return; const first = options?.drafts.find(item => item.analysis_id === analysisId && item.id === initialDraftId) ?? options?.drafts.find(item => item.analysis_id === analysisId); if (first) await loadDraft(first.id); try { const value = workspaceStorage.getItem(storageKey); if (value && current()) setPending(JSON.parse(value) as Intent) } catch { if (current()) setError('无法读取原创建/导入恢复记录') } } catch (e) { if (current()) setError(explain(e)) } finally { finish() }
  }
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; operation.current++; modal.current?.destroy() } }, [])
  useEffect(() => { onLockChange(busy || dirty) }, [busy, dirty, onLockChange])
  useEffect(() => () => onLockChange(false), [onLockChange])
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (dirtyNow.current) { event.preventDefault(); event.returnValue = '' } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn) }, [])

  /** Write the exact body/key before HTTP so a lost response can replay its identity. */
  async function sendIntent(intent: Intent) {
    if (!start()) return
    try {
      workspaceStorage.setItem(storageKey, JSON.stringify(intent)); setPending(intent)
      const result = intent.kind === 'create' ? await V.createVideoDnaDraftApiV1VideoDnaProjectsProjectIdDraftsPost({ projectId, requestBody: intent.body }) : await V.importVideoDnaDraftApiV1VideoDnaProjectsProjectIdImportsPost({ projectId, requestBody: intent.body })
      if (!current() || !result.data) return
      try { workspaceStorage.removeItem(storageKey); setPending(null) } catch { setError('草稿已创建，但无法清除本地恢复标识；继续核对不会新建草稿') }
      await loadCatalog(); if (!current()) return; await loadDraft(result.data.id); message.success('已取得真实DNA草稿，不代表内容识别或还原通过')
    } catch (e) { if (current()) setError(e instanceof DOMException ? '无法保存恢复标识，未发送新请求' : explain(e)) } finally { finish() }
  }
  /** Confirm a new server-generated unknown template for the selected source. */
  function createTemplate() { if (busy || dirty || pending) return; confirm('按当前人工确认边界新建DNA？', '服务端将按真实确认切点建立未知字段模板，不会自动识别人脸、转写或生成视频。', () => sendIntent({ kind: 'create', body: { analysis_id: analysisId, expected_analysis_revision: analysisRevision, request_key: crypto.randomUUID(), document: null } })) }
  /** Read an inert file under an operation/form fence before offering a new import. */
  async function importFile(file: File) {
    if (dirty || pending || !start()) return
    const token = ++operation.current; const version = formVersion.current; const draftId = draft?.id; const revision = draft?.revision
    /** Fence a delayed file read against intervening form and draft changes. */
    const valid = () => current() && operation.current === token && formVersion.current === version && !dirtyNow.current && currentDraft.current?.id === draftId && currentDraft.current?.revision === revision
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error('file limit')
      const content = parsePortableJson(await file.text())
      if (!content || typeof content !== 'object' || Array.isArray(content)) throw new Error('invalid JSON')
      if (!valid()) return
      confirm('导入为当前源的新草稿？', '仅导入版本1的人工内容。外层通过、评分、任务状态不会成为资格；源由服务器重新核对，历史快照不覆盖。', async () => { if (valid()) await sendIntent({ kind: 'import', body: { analysis_id: analysisId, expected_analysis_revision: analysisRevision, request_key: crypto.randomUUID(), content } }) })
    } catch { if (current()) setError('导入文件无效、超过2MiB，或不是支持的JSON对象；原文件未修改。') } finally { if (current() && operation.current === token) finish() }
  }
  /** Save all controlled fields under the displayed server CAS revision. */
  async function save() {
    if (!draft || !document || !start()) return
    try {
      const result = await V.patchVideoDnaDraftApiV1VideoDnaProjectsProjectIdDraftsDraftIdPatch({ projectId, draftId: draft.id, requestBody: { expected_revision: draft.revision, document } })
      if (!current() || !result.data) return
      setDraft(result.data); currentDraft.current = result.data; setDocument(normalize(result.data.document)); formVersion.current++; dirtyNow.current = false; setDirty(false); await loadCatalog(); message.success('人工DNA已保存，原片与生成计划未改写')
    } catch (e) { if (current()) setError(explain(e)) } finally { finish() }
  }
  /** Historical snapshots remain separate from the mutable form and current R1 cuts. */
  async function loadSnapshot(id: string) {
    const token = ++operation.current
    const [saved, check] = await Promise.all([V.getVideoDnaSnapshotApiV1VideoDnaProjectsProjectIdSnapshotsSnapshotIdGet({ projectId, snapshotId: id }), V.preflightVideoDnaSnapshotApiV1VideoDnaProjectsProjectIdSnapshotsSnapshotIdPreflightGet({ projectId, snapshotId: id })])
    if (!current() || token !== operation.current) return
    setSnapshot(saved.data ?? null); setPreflight(check.data ?? null); setPreparation(null)
  }
  /** Confirm an immutable human-declaration snapshot without granting execution. */
  function freeze() {
    if (!draft || dirty || busy) return
    confirm('锁定此人工DNA快照？', '只固定证据、版本及人工声明，不证明描述正确，也不会执行模型或还原视频。', async () => {
      if (!start()) return
      try { const result = await V.freezeVideoDnaDraftApiV1VideoDnaProjectsProjectIdDraftsDraftIdFreezePost({ projectId, draftId: draft.id, requestBody: { expected_revision: draft.revision } }); if (!current() || !result.data) return; const refreshed = await V.getVideoDnaDraftApiV1VideoDnaProjectsProjectIdDraftsDraftIdGet({ projectId, draftId: draft.id }); if (!current()) return; setDraft(refreshed.data ?? null); currentDraft.current = refreshed.data ?? null; await loadSnapshot(result.data.id) } catch (e) { if (current()) setError(explain(e)) } finally { finish() }
    })
  }
  /** Add a known schema template without inventing any field's contents. */
  function addAsset() { if (!document) return; const asset: DnaAsset = { id: localId(assetKind), kind: assetKind, name_zh: '', name_en: '', fields: Object.fromEntries(fields[assetKind].map(key => [key, unknown('尚未人工记录')])), lock_prompt: unknown('尚未人工记录'), production_version_id: null, binding_note: '' }; edit({ ...document, assets: [...document.assets, asset] }) }
  /** Patch one asset by stable ID and retain all collapsed fields. */
  function updateAsset(id: string, patch: Partial<DnaAsset>) { if (document) edit({ ...document, assets: document.assets.map(item => item.id === id ? { ...item, ...patch } : item) }) }
  /** Patch one shot by stable ID without changing other source intervals. */
  function updateShot(id: string, patch: Partial<DnaShot>) { if (document) edit({ ...document, shots: document.shots.map(item => item.id === id ? { ...item, ...patch } : item) }) }
  /** Bulk unknown reasons are an explicit user action, never a claim of understanding. */
  function explainUnknowns() {
    if (!document || !unknownReason.trim()) return
    confirm('将此原因用于仍空白的未知项？', '仅填写unknown状态中空白的原因，不补内容、翻译、证据或置信度，也不改变观察/推断项。', async () => {
      /** Fill only empty unknown reasons after the user explicitly confirms this action. */
      const apply = (value: unknown): unknown => { if (Array.isArray(value)) return value.map(apply); if (value && typeof value === 'object') { const object = value as Record<string, unknown>; if (object.status === 'unknown' && typeof object.reason === 'string') return { ...object, reason: object.reason.trim() ? object.reason : unknownReason }; return Object.fromEntries(Object.entries(object).map(([key, item]) => [key, apply(item)])) } return value }
      edit(apply(document) as DnaDocument)
    })
  }
  const ownDrafts = catalog?.drafts.filter(item => item.analysis_id === analysisId) ?? []
  const analysis = catalog?.analyses.find(item => item.id === analysisId)
  const characters = document?.assets.filter(item => item.kind === 'character').map(item => ({ value: item.id, label: item.name_zh || item.id })) ?? []
  const scenes = document?.assets.filter(item => item.kind === 'scene').map(item => ({ value: item.id, label: item.name_zh || item.id })) ?? []
  const props = document?.assets.filter(item => item.kind === 'prop').map(item => ({ value: item.id, label: item.name_zh || item.id })) ?? []
  /** Bind the shared claim editor to the correct original-frame evidence range. */
  const ce = (claim: DnaClaim | undefined, change: (value: DnaClaim) => void, name: string, start = 0, end = frameCount) => <ClaimEditor value={claim} onChange={change} name={name} busy={busy} start={start} end={end} onInspect={onInspectFrame} />

  return <ProjectSection headingLevel={3} className="mt-4" title="Video DNA 人工复现准备" data-testid="dna-panel">
    <Alert className="mb-3" type="info" showIcon message="人工圣经与七层准备，不是自动理解或视频还原" description="观察、推断和未知分别保留。置信度是人工声明；准备文本不是原作者提示词，不能触发执行。末帧精确尾长未知，正式视觉/ASR/复现仍待验收。" />
    {error && <Alert className="mb-3" type="error" message={error} />}
    {dirty && <Alert className="mb-3" type="warning" message="DNA有未保存修改；请保存或明确放弃后再切换分析/草稿或导入。" />}
    <Space wrap className="mb-3"><Button disabled={busy || dirty} onClick={() => void load()}>刷新DNA资料</Button><Button disabled={busy || dirty || !!pending || !analysis?.can_create} onClick={createTemplate}>按确认镜头新建DNA模板</Button><Select aria-label="DNA草稿" style={{ minWidth: 240 }} disabled={busy || dirty} value={draft?.id} placeholder="选择本分析的草稿" options={ownDrafts.map(item => ({ value: item.id, label: `${item.title || '未命名'}，修订${item.revision}，${item.id.slice(0, 8)}` }))} onChange={id => { if (!start()) return; void loadDraft(id).catch(e => { if (current()) setError(explain(e)) }).finally(finish) }} /><label>高级JSON导入<input aria-label="导入DNA JSON" type="file" accept=".json,application/json" disabled={busy || dirty || !!pending} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importFile(file) }} /></label></Space>
    {!analysis?.can_create && <Alert type="warning" message="当前分析尚不可新建DNA" description={analysis?.issues.map(issue => issue.message).join('；') || '请先完成解析并保存人工确认切点'} />}
    {pending && <Alert className="mb-3" type="warning" message="原创建/导入结果未确认，先核对原请求" description={<Space><Button disabled={busy || dirty} onClick={() => confirm('核对原DNA请求？', '发送原正文和原标识，不创建新的意图。', () => sendIntent(pending))}>核对原DNA请求</Button>{error && <Button disabled={busy || dirty} onClick={() => confirm('放弃本地恢复标识？', '不会删除已经建立的草稿，请先核对服务器草稿列表。之后的操作会使用新标识。', async () => { try { workspaceStorage.removeItem(storageKey); setPending(null); await loadCatalog() } catch { if (current()) setError('无法清除恢复标识') } })}>放弃本地恢复标识</Button>}</Space>} />}
    {document && draft ? <>
      <Space wrap className="mb-3"><Tag>DNA修订{draft.revision}</Tag><Tag>源分析修订{draft.source.analysis_revision} / 当前{analysisRevision}</Tag><Tag>{draft.source.audio_present ? '源有音轨，内容仍未认证' : '源无音轨，不能观察原声或填写原对白'}</Tag><span>{frameCount}原始帧，时钟{draft.source.frame_clock_hash.slice(0, 12)}，最后终点N的精确时间未知</span></Space>
      <Space direction="vertical" className="w-full mb-3"><Input aria-label="DNA标题" maxLength={4000} disabled={busy} value={document.title} onChange={event => edit({ ...document, title: event.target.value })} placeholder="人工DNA标题" /><Input aria-label="DNA复核人" maxLength={4000} disabled={busy} value={document.reviewer} onChange={event => edit({ ...document, reviewer: event.target.value })} placeholder="复核人" /><Checkbox disabled={busy} checked={document.human_reviewed} onChange={event => edit({ ...document, human_reviewed: event.target.checked })}>我已复核本次人工声明（不是内容质量资格）</Checkbox><Input aria-label="未知项统一原因" maxLength={4000} disabled={busy} value={unknownReason} onChange={event => { formVersion.current++; setUnknownReason(event.target.value) }} placeholder="可选：为仍空白的unknown项填写统一原因" /><Button disabled={busy || !unknownReason.trim()} onClick={explainUnknowns}>为未填写的未知项注明原因</Button></Space>
      <Collapse className="mb-3" items={(['global_style', 'negative_constraints', 'audio_editing'] as const).map(key => ({ key, label: labels[key], children: ce(document[key], value => edit({ ...document, [key]: value }), labels[key]) }))} />
      <ProjectSection headingLevel={4} className="mb-3" title="角色 / 场景 / 道具圣经" extra={<Space><Select aria-label="新增DNA资产类型" disabled={busy} value={assetKind} options={Object.keys(fields).map(value => ({ value, label: labels[value] }))} onChange={value => setAssetKind(value)} /><Button disabled={busy || document.assets.length >= 64} onClick={addAsset}>新增资产模板</Button></Space>}>
        <Collapse items={document.assets.map(asset => ({ key: asset.id, label: `${labels[asset.kind]}，${asset.name_zh || '未命名'}，${asset.id}`, children: <Space direction="vertical" className="w-full"><Input aria-label={`${asset.id}中文名称`} disabled={busy} value={asset.name_zh} maxLength={4000} placeholder="中文名称" onChange={event => updateAsset(asset.id, { name_zh: event.target.value })} /><Input aria-label={`${asset.id}英文名称`} disabled={busy} value={asset.name_en} maxLength={4000} placeholder="English name" onChange={event => updateAsset(asset.id, { name_en: event.target.value })} /><Select aria-label={`${asset.id}生产版本`} allowClear className="w-full" disabled={busy} value={asset.production_version_id ?? undefined} placeholder="可选：人工对应精确生产版本，不复制实体" options={catalog?.versions.filter(version => version.kind === asset.kind).map(version => ({ value: version.version_id, label: `${version.name}，v${version.revision}，${version.content_hash.slice(0, 10)}` }))} onChange={value => updateAsset(asset.id, { production_version_id: value ?? null })} /><Input aria-label={`${asset.id}绑定说明`} disabled={busy} value={asset.binding_note} maxLength={4000} placeholder="生产版本人工对应说明" onChange={event => updateAsset(asset.id, { binding_note: event.target.value })} /><Collapse items={[...fields[asset.kind].map(key => ({ key, label: labels[key], children: ce(asset.fields?.[key], value => updateAsset(asset.id, { fields: { ...asset.fields, [key]: value } }), `${asset.id}${key}`) })), { key: 'lock_prompt', label: '固定形象/场景准备描述', children: ce(asset.lock_prompt, value => updateAsset(asset.id, { lock_prompt: value }), `${asset.id}锁定描述`) }]} /><Button danger disabled={busy} onClick={() => confirm('移除此DNA资产？', '只移除本地分析条目；已有镜头引用必须随后明确修正，否则保存拒绝。生产资产与历史不删除。', async () => edit({ ...document, assets: document.assets.filter(item => item.id !== asset.id) }))}>移除此分析资产</Button></Space> }))} />
      </ProjectSection>
      <ProjectSection headingLevel={4} className="mb-3" title="逐镜人工声明与证据">
        <Collapse items={document.shots.map((shot, index) => ({ key: shot.id, label: `镜头${index + 1}，原始帧[${shot.start_frame}, ${shot.end_frame})${shot.end_frame === frameCount ? '，尾长未知' : ''}`, children: <Space direction="vertical" className="w-full"><Space wrap><Tag>{shot.id}</Tag><Button disabled={busy} onClick={() => onInspectFrame(shot.start_frame)}>查看此镜原始起点</Button></Space><Select aria-label={`镜头${index + 1}DNA角色`} className="w-full" mode="multiple" disabled={busy} value={shot.character_ids} options={characters} placeholder="本镜实际引用的分析角色" onChange={value => updateShot(shot.id, { character_ids: value })} /><Select aria-label={`镜头${index + 1}DNA场景`} className="w-full" allowClear disabled={busy} value={shot.scene_id ?? undefined} options={scenes} placeholder="本镜分析场景" onChange={value => updateShot(shot.id, { scene_id: value ?? null })} /><Select aria-label={`镜头${index + 1}DNA道具`} className="w-full" mode="multiple" disabled={busy} value={shot.prop_ids} options={props} placeholder="本镜分析道具" onChange={value => updateShot(shot.id, { prop_ids: value })} />
          <Collapse items={shotKeys.map(key => ({ key, label: labels[key], children: ce(shot[key], value => updateShot(shot.id, { [key]: value }), `镜头${index + 1}${key}`, key === 'continuity' ? 0 : shot.start_frame, key === 'continuity' ? frameCount : shot.end_frame) }))} />
          <Button disabled={busy || (shot.dialogues?.length ?? 0) >= 64} onClick={() => updateShot(shot.id, { dialogues: [...(shot.dialogues ?? []), { id: localId('dialogue'), speaker_id: null, claim: unknown() }] })}>新增人工对白条目</Button>
          <Collapse items={(shot.dialogues ?? []).map((dialogue, number) => ({ key: dialogue.id, label: `对白${number + 1}，${dialogue.id}`, children: <Space direction="vertical" className="w-full"><Select aria-label={`${dialogue.id}说话人`} className="w-full" allowClear disabled={busy} value={dialogue.speaker_id ?? undefined} options={characters.filter(item => shot.character_ids?.includes(item.value))} placeholder="本镜已引用的实际说话角色" onChange={value => updateShot(shot.id, { dialogues: shot.dialogues?.map(item => item.id === dialogue.id ? { ...item, speaker_id: value ?? null } : item) })} />{ce(dialogue.claim, value => updateShot(shot.id, { dialogues: shot.dialogues?.map(item => item.id === dialogue.id ? { ...item, claim: value } : item) }), `镜头${index + 1}对白${number + 1}`, shot.start_frame, shot.end_frame)}<Button danger disabled={busy} onClick={() => updateShot(shot.id, { dialogues: shot.dialogues?.filter(item => item.id !== dialogue.id) })}>移除本地对白</Button></Space> }))} />
          <Button disabled={busy || (shot.events?.length ?? 0) >= 64} onClick={() => updateShot(shot.id, { events: [...(shot.events ?? []), { id: localId('event'), character_ids: [], claim: unknown() }] })}>新增人工事件条目</Button>
          <Collapse items={(shot.events ?? []).map((event, number) => ({ key: event.id, label: `事件${number + 1}，${event.id}`, children: <Space direction="vertical" className="w-full"><Select aria-label={`${event.id}参与角色`} className="w-full" mode="multiple" disabled={busy} value={event.character_ids} options={characters.filter(item => shot.character_ids?.includes(item.value))} placeholder="本镜已引用的事件角色" onChange={value => updateShot(shot.id, { events: shot.events?.map(item => item.id === event.id ? { ...item, character_ids: value } : item) })} />{ce(event.claim, value => updateShot(shot.id, { events: shot.events?.map(item => item.id === event.id ? { ...item, claim: value } : item) }), `镜头${index + 1}事件${number + 1}`, shot.start_frame, shot.end_frame)}<Button danger disabled={busy} onClick={() => updateShot(shot.id, { events: shot.events?.filter(item => item.id !== event.id) })}>移除本地事件</Button></Space> }))} />
        </Space> }))} />
      </ProjectSection>
      <Space wrap className="mb-3"><Button type="primary" disabled={busy} onClick={() => void save()}>保存人工DNA</Button><Button disabled={busy || dirty || !draft.readiness.can_freeze} onClick={freeze}>锁定DNA快照</Button><Button disabled={busy || !dirty} onClick={() => confirm('放弃本地未保存DNA修改？', '回到服务器当前草稿，不影响已锁定快照。', async () => { if (!start()) return; try { await loadDraft(draft.id) } catch (e) { if (current()) setError(explain(e)) } finally { finish() } })}>放弃本地修改</Button><Button disabled={busy || dirty} onClick={() => download({ document: draft.document }, 'dna-saved-document.json')}>下载已保存草稿JSON</Button></Space>
      <Alert className="mb-3" type={draft.readiness.can_freeze ? 'success' : 'warning'} message={draft.readiness.can_freeze ? '结构与人工声明可锁定，内容正确性未认证' : '尚有结构、源或人工说明缺项'} description={<List size="small" dataSource={draft.readiness.issues} renderItem={issue => <List.Item>{issue.path && `${issue.path}，`}{issue.message}</List.Item>} />} />
      <DnaProductionControls key={`${projectId}-${draft.id}-${draft.revision}`} projectId={projectId} draftId={draft.id} revision={draft.revision} dirty={dirty} busy={busy} />
      <ProjectSection headingLevel={4} title="历史快照、七层准备与影响比较"><Space wrap><Select aria-label="DNA历史快照" style={{ minWidth: 260 }} value={snapshot?.id} disabled={busy} placeholder="选择已锁定快照" options={draft.snapshots.map(item => ({ value: item.id, label: `修订${item.draft_revision}，${item.content_hash.slice(0, 12)}` }))} onChange={id => { if (!start()) return; void loadSnapshot(id).catch(e => { if (current()) setError(explain(e)) }).finally(finish) }} /><Button disabled={busy || !snapshot} onClick={() => { if (snapshot) download(snapshot, 'video-dna-snapshot.json') }}>下载DNA快照JSON</Button><Button disabled={busy || dirty || !!pending || !snapshot} onClick={() => { if (snapshot) confirm('基于此历史快照新建草稿？', '服务端重新验证原片、时钟和确认边界，不覆盖历史、不猜迁移。', () => sendIntent({ kind: 'import', body: { analysis_id: analysisId, expected_analysis_revision: analysisRevision, request_key: crypto.randomUUID(), content: snapshot } })) }}>从历史恢复新草稿</Button></Space>
        {preflight && <Alert className="my-3" type={preflight.usable ? 'info' : 'warning'} message={preflight.usable ? '历史源与版本可用，仍不是质量资格' : '历史源或生产版本媒体不可用'} description={[...preflight.issues.map(issue => issue.message), ...(preflight.source_current ? [] : ['当前分析修订或确认边界已变化；历史内容保持固定'])].join('；')} />}
        <DnaPreparationControls key={snapshot?.id ?? "none"} projectId={projectId} snapshot={snapshot} busy={busy} dirty={dirty} start={start} finish={finish} current={current} setError={setError} preparation={preparation} setPreparation={setPreparation} comparison={comparison} setComparison={setComparison} baseSnapshotId={baseSnapshotId} setBaseSnapshotId={setBaseSnapshotId} otherSnapshotId={otherSnapshotId} setOtherSnapshotId={setOtherSnapshotId} catalog={catalog} />
      </ProjectSection>
    </> : <Empty description="先保存人工确认切点，再建立未知字段模板；无需模型账号" />}
  </ProjectSection>
}

/** Seven-layer preparation and dependency comparison consume only immutable IDs. */
function DnaPreparationControls({ projectId, snapshot, busy, start, finish, current, setError, preparation, setPreparation, comparison, setComparison, baseSnapshotId, setBaseSnapshotId, otherSnapshotId, setOtherSnapshotId, catalog }: { projectId: string; snapshot: DnaSnapshot | null; busy: boolean; dirty: boolean; start: () => boolean; finish: () => void; current: () => boolean; setError: (value: string) => void; preparation: DnaPreparationRead | null; setPreparation: (value: DnaPreparationRead | null) => void; comparison: DnaCompareRead | null; setComparison: (value: DnaCompareRead | null) => void; baseSnapshotId: string; setBaseSnapshotId: (value: string) => void; otherSnapshotId: string; setOtherSnapshotId: (value: string) => void; catalog: DnaCatalog | null }) {
  const [targetDraftId, setTargetDraftId] = useState('')
  const [targetSnapshots, setTargetSnapshots] = useState<DnaDraft['snapshots']>([])
  const alive = useRef(true)
  const request = useRef(0)
  useEffect(() => { alive.current = true; setBaseSnapshotId(snapshot?.id ?? ''); setOtherSnapshotId(''); return () => { alive.current = false; request.current++ } }, [])
  /** Fence preparation responses to this mounted immutable-history view. */
  const valid = () => alive.current && current()
  /** Read deterministic seven-layer preparation for the selected snapshot. */
  async function prepare() {
    if (!snapshot || !start()) return
    const token = ++request.current
    try { const result = await V.prepareVideoDnaSnapshot({ projectId, snapshotId: snapshot.id }); if (valid() && request.current === token) setPreparation(result.data ?? null) } catch (e) { if (valid()) setError(explain(e)) } finally { finish() }
  }
  /** A target draft is read for its actual snapshot choices; no IDs are fabricated. */
  async function target(id: string) {
    if (!start()) return
    const token = ++request.current
    setTargetDraftId(id); setTargetSnapshots([]); setOtherSnapshotId(''); setComparison(null)
    try { const result = await V.getVideoDnaDraftApiV1VideoDnaProjectsProjectIdDraftsDraftIdGet({ projectId, draftId: id }); if (valid() && request.current === token) setTargetSnapshots(result.data?.snapshots ?? []) } catch (e) { if (valid()) setError(explain(e)) } finally { finish() }
  }
  /** Compare two owned snapshot identities without rewriting either version. */
  async function compare() {
    if (!snapshot || !otherSnapshotId || !start()) return
    const token = ++request.current
    try { const result = await V.compareVideoDnaSnapshots({ projectId, requestBody: { base_snapshot_id: snapshot.id, other_snapshot_id: otherSnapshotId } }); if (valid() && request.current === token) setComparison(result.data ?? null) } catch (e) { if (valid()) setError(explain(e)) } finally { finish() }
  }
  const layerNames: Record<string, string> = { GLOBAL_STYLE_PROMPT: '全局风格', CHARACTER_LOCK_PROMPT: '角色固定', SCENE_LOCK_PROMPT: '场景与道具固定', SHOT_IMAGE_PROMPT: '逐镜图像', SHOT_VIDEO_PROMPT: '逐镜视频', NEGATIVE_CONSTRAINTS: '负向约束', AUDIO_AND_EDITING_PROMPTS: '声音与剪辑' }
  return <div className="mt-3" data-testid="dna-preparation">
    <Space wrap className="mb-3"><Button disabled={busy || !snapshot} onClick={() => void prepare()}>查看七层复现准备</Button><Button disabled={busy || !preparation || preparation.snapshot_id !== snapshot?.id} onClick={() => download(preparation, 'dna-seven-layer-preparation.json')}>下载准备清单JSON</Button></Space>
    <Alert className="mb-3" type="info" message="执行关闭，不是原作者提示词，未获模型资格" description="只有人工复现假设，不自动翻译、补未知或触发模型。片尾准确时长、未知关键项及生产版本缺项仍会阻止后续真实复现资格。" />
    {preparation && preparation.snapshot_id === snapshot?.id && <><p>DNA内容hash {preparation.dna_content_hash}；准备hash {preparation.content_hash}</p><Collapse items={preparation.payload.entries.map((entry, index) => ({ key: entry.shot_id, label: `镜头${index + 1}，帧[${entry.start_frame}, ${entry.end_frame})，${entry.end_relative_us === null ? '末端精确时间未知' : `${entry.start_relative_us}–${entry.end_relative_us}us`}`, children: <Space direction="vertical" className="w-full"><List size="small" dataSource={entry.issues} renderItem={issue => <List.Item>{issue.path}，{issue.message}</List.Item>} /><Collapse items={entry.layers.map(layer => ({ key: layer.key, label: `${layerNames[layer.key]}，${layer.key}`, children: <Space direction="vertical" className="w-full"><Input.TextArea readOnly autoSize={{ minRows: 2, maxRows: 14 }} aria-label={`镜头${index + 1}${layer.key}中文准备`} value={layer.zh ?? ''} placeholder="无已填写的中文内容，不自动补全" /><Input.TextArea readOnly autoSize={{ minRows: 2, maxRows: 14 }} aria-label={`镜头${index + 1}${layer.key}英文准备`} value={layer.en ?? ''} placeholder="No completed English content" /><List size="small" dataSource={layer.claims} renderItem={claim => <List.Item><div><Tag>{claim.status === 'unknown' ? '未知' : claim.status === 'observed' ? '人工观察声明' : '复现构想'}</Tag>{claim.path}，{claim.reason}<div>人工置信度：{claim.confidence ?? '未声明'}，证据 {JSON.stringify(claim.evidence)}</div></div></List.Item>} /></Space> }))} /><List size="small" header="仍未知的字段" dataSource={entry.unknowns} renderItem={item => <List.Item>{item.path}，{item.reason}</List.Item>} /></Space> }))} /></>}
    <ProjectSection headingLevel={4} className="mt-3" title="两个固定快照的影响比较">
      <p>基准：{snapshot?.id ?? '先选择历史快照'}{baseSnapshotId && baseSnapshotId !== snapshot?.id ? '（以当前选中快照为准）' : ''}</p>
      <Space wrap><Select aria-label="DNA比较目标草稿" style={{ minWidth: 240 }} value={targetDraftId || undefined} disabled={busy || !snapshot} placeholder="选择同源目标草稿" options={catalog?.drafts.map(draft => ({ value: draft.id, label: `${draft.title || '未命名'}，${draft.id.slice(0, 8)}` }))} onChange={id => void target(id)} /><Select aria-label="DNA比较目标快照" style={{ minWidth: 240 }} value={otherSnapshotId || undefined} disabled={busy || !targetSnapshots.length} placeholder="选择实际锁定快照" options={targetSnapshots.map(item => ({ value: item.id, label: `修订${item.draft_revision}，${item.content_hash.slice(0, 12)}` }))} onChange={id => { setOtherSnapshotId(id); setComparison(null) }} /><Button disabled={busy || !snapshot || !otherSnapshotId} onClick={() => void compare()}>比较版本影响</Button></Space>
      {comparison && comparison.base_snapshot_id === snapshot?.id && comparison.other_snapshot_id === otherSnapshotId && <div className="mt-3"><p>受影响镜头：{comparison.affected_shot_ids.length ? comparison.affected_shot_ids.join('、') : '无（可能仅元数据变化）'}</p><List size="small" header="精确变化路径" dataSource={comparison.changed_paths} renderItem={path => <List.Item>{path}</List.Item>} /><List size="small" header="影响原因" dataSource={comparison.reasons} renderItem={reason => <List.Item>{reason.shot_id}，{reason.path}，{reason.reason}</List.Item>} /><Collapse items={[{ key: 'index', label: '查看稳定依赖索引', children: <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(comparison.dependency_index, null, 2)}</pre> }]} /><Button onClick={() => download(comparison, 'dna-version-impact.json')}>下载影响报告JSON</Button></div>}
    </ProjectSection>
  </div>
}
