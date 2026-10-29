import { workspaceMediaUrl, workspaceStorage } from '../../../services/workspaceContext'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Input, InputNumber, List, Modal, Select, Space, Tag, message } from 'antd'
import { EditingV2Service as T, OpenAPI } from '../../../services/generated'
import type { EditPublicationRead, ExportCatalog, ExportRead, TransitionDocument, TransitionDraftRead, TransitionEdge, TransitionPublicationRead } from '../../../services/generated'

type Document = TransitionDocument & { title: string; edges: TransitionEdge[] }
type BaseClip = { shot_id: string; source_in_ms: number; source_out_ms: number }

/** Preserve saved boundary values and explicitly initialize every actual adjacent v1 boundary. */
function initialDocument(base: EditPublicationRead): Document {
  const clips = (base.payload.clips ?? []) as BaseClip[]
  return { schema_version: 2, title: '交叉淡化版本', edges: clips.slice(0, -1).map((clip, index) => ({ left_shot_id: clip.shot_id, right_shot_id: clips[index + 1].shot_id, kind: 'hard_cut', duration_ms: 0 })) }
}
/** Show safe actionable API feedback without exposing private processing traces. */
function explain(error: unknown): string {
  const body = (error as { body?: { message?: unknown; detail?: unknown } }).body
  return typeof body?.message === 'string' ? body.message : typeof body?.detail === 'string' ? body.detail : '转场操作未完成，请核对修订、素材与本地工具。'
}

/** An independent v2 form, pinned to the selected immutable v1 version throughout its lifetime. */
export function TransitionEditor({ projectId, chapterId, base, onLocked }: { projectId: string; chapterId: string; base: EditPublicationRead; onLocked: (value: boolean) => void }) {
  const [saved, setSaved] = useState<TransitionDraftRead | null>(null)
  const [document, setDocument] = useState<Document>(() => initialDocument(base))
  const [publication, setPublication] = useState<TransitionPublicationRead | null>(null)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const alive = useRef(true), inFlight = useRef(false)
  const ownModal = useRef<ReturnType<typeof Modal.confirm> | null>(null)
  const originalDuration = Number(base.payload.duration_ms ?? 0)
  const overlap = document.edges.reduce((sum, edge) => sum + (edge.duration_ms ?? 0), 0)

  /** Guard rapid double clicks synchronously, before React can render disabled controls. */
  function start() { if (!alive.current || inFlight.current) return false; inFlight.current = true; setBusy(true); setError(''); return true }
  function finish() { inFlight.current = false; if (alive.current) setBusy(false) }
  /** A local form change never edits the original v1 publication. */
  function change(value: Document) { setDocument(value); setDirty(true) }
  /** Reload only by explicit user intent; CAS failures retain unsaved boundary edits. */
  async function load() {
    const result = await T.listTransitionDrafts({ projectId, chapterId, basePublicationId: base.id })
    if (!alive.current) return
    const row = result.data?.[0] ?? null
    setSaved(row); setDocument(row ? { ...row.document, title: row.document.title ?? '', edges: row.document.edges ?? [] } : initialDocument(base)); setDirty(false)
    if (row?.publications.length) {
      const locked = await T.getTransitionPublication({ projectId, publicationId: row.publications[0].id })
      if (alive.current) setPublication(locked.data ?? null)
    } else setPublication(null)
  }
  useEffect(() => {
    alive.current = true
    setBusy(true)
    void load().catch(error => { if (alive.current) setError(explain(error)) }).finally(() => { if (alive.current) setBusy(false) })
    return () => { alive.current = false; ownModal.current?.destroy() }
  }, [projectId, chapterId, base.id])
  useEffect(() => { onLocked(busy || dirty) }, [busy, dirty, onLocked])
  useEffect(() => () => onLocked(false), [onLocked])

  /** Persist only v2 boundary choices using the actual saved revision. */
  async function save() {
    if (!start()) return
    try {
      const result = saved
        ? await T.patchTransitionDraft({ projectId, draftId: saved.id, requestBody: { expected_revision: saved.revision, document } })
        : await T.createTransitionDraft({ projectId, requestBody: { base_publication_id: base.id, document } })
      if (alive.current && result.data) { setSaved(result.data); setDirty(false); message.success('转场草稿已保存，原硬切版本保留。') }
    } catch (error) { if (alive.current) setError(explain(error)) } finally { finish() }
  }
  /** Lock one inspected v2 revision after acknowledging its exact shorter output duration. */
  function freeze() {
    if (!saved || busy || dirty) return
    const revision = saved.revision
    ownModal.current?.destroy()
    ownModal.current = Modal.confirm({ title: '锁定转场版本？', content: `原时长 ${(saved.base_duration_ms / 1000).toFixed(1)} 秒，重叠 ${(saved.overlap_ms / 1000).toFixed(1)} 秒，输出 ${(saved.duration_ms / 1000).toFixed(1)} 秒。源入出点和原片时钟不变。剧情/对白标记不得进入混合区；字幕单独导出。`, okText: '确认时长并锁定', cancelText: '取消', onOk: async () => {
      if (!start()) return
      try {
        const result = await T.freezeTransitionDraft({ projectId, draftId: saved.id, requestBody: { expected_revision: revision, accept_overlap_change: true } })
        if (alive.current) { setPublication(result.data ?? null); await load(); message.success('转场版本及来源已锁定，尚未开始渲染。') }
      } catch (error) { if (alive.current) setError(explain(error)) } finally { finish() }
    } })
  }

  return <Card title="6. 转场版本与实际渲染" data-testid="transition-editor">
    <Alert className="mb-3" type="info" showIcon message="独立转场版本，原硬切版本保留" description="交叉淡化会缩短输出总时长。画面与声音线性混合；所有已确认剧情/对白必须避开混合区。保留原声须有完整实际音轨覆盖，静音或无音轨按零信号处理。源时间和采用资格不变。" />
    {error && <Alert type="error" showIcon className="mb-3" message={error} />}
    <Space direction="vertical" className="w-full">
      <Input aria-label="转场版本标题" disabled={busy} value={document.title} maxLength={120} onChange={event => change({ ...document, title: event.target.value })} />
      <List dataSource={document.edges} locale={{ emptyText: '此版本只有一个片段，没有相邻转场边界。' }} renderItem={(edge, index) => <List.Item><Space wrap><span>镜头 {index + 1} → {index + 2}</span><Select aria-label={`边界${index + 1}转场`} disabled={busy} value={edge.kind} style={{ width: 130 }} options={[{ label: '硬切', value: 'hard_cut' }, { label: '交叉淡化', value: 'crossfade' }]} onChange={kind => change({ ...document, edges: document.edges.map((item, position) => position === index ? { ...item, kind, duration_ms: kind === 'hard_cut' ? 0 : item.duration_ms || 500 } : item) })} /><InputNumber aria-label={`边界${index + 1}重叠毫秒`} disabled={busy || edge.kind !== 'crossfade'} min={100} max={1000} step={100} precision={0} value={edge.duration_ms} onChange={duration => change({ ...document, edges: document.edges.map((item, position) => position === index ? { ...item, duration_ms: duration ?? 100 } : item) })} /><span>毫秒</span></Space></List.Item>} />
      <div data-testid="transition-duration">原时长 {(originalDuration / 1000).toFixed(1)} 秒 − 重叠 {(overlap / 1000).toFixed(1)} 秒 = 输出 {((originalDuration - overlap) / 1000).toFixed(1)} 秒</div>
      <Space wrap><Button type="primary" disabled={busy} onClick={() => void save()}>保存转场草稿</Button><Tag>修订 {saved?.revision ?? '未保存'}{dirty ? ' · 有未保存修改' : ''}</Tag><Button disabled={busy || dirty || !saved?.can_freeze} onClick={freeze}>锁定转场版本</Button><Button disabled={busy} onClick={() => { const run = () => { if (!start()) return; void load().catch(error => { if (alive.current) setError(explain(error)) }).finally(finish) }; if (dirty) ownModal.current = Modal.confirm({ title: '读取已保存的转场草稿？', content: '当前未保存的边界修改将丢弃，原硬切版本不受影响。', onOk: run }); else run() }}>重新读取转场草稿</Button></Space>
      {saved && <Alert type={saved.can_freeze ? 'success' : 'warning'} message={saved.can_freeze ? '已保存边界可锁定；这不证明剧情、口型或模型质量' : '已保存转场仍需调整'} description={saved.issues.map(issue => issue.message).join('；')} />}
      <Select aria-label="已锁定转场版本" disabled={busy || dirty} value={publication?.id} placeholder="选择真实已锁定的转场版本" style={{ minWidth: 280 }} options={saved?.publications.map(row => ({ value: row.id, label: `修订${row.draft_revision} / ${(row.duration_ms / 1000).toFixed(1)}秒 / ${row.content_hash.slice(0, 8)}` }))} onChange={id => { if (!start()) return; void T.getTransitionPublication({ projectId, publicationId: id }).then(result => { if (alive.current) setPublication(result.data ?? null) }).catch(error => { if (alive.current) setError(explain(error)) }).finally(finish) }} />
      {publication && <TransitionExports key={publication.id} projectId={projectId} chapterId={chapterId} publication={publication} />}
    </Space>
  </Card>
}

/** Real resumable local v2 rendering; one persisted key identifies each export intention. */
function TransitionExports({ projectId, chapterId, publication }: { projectId: string; chapterId: string; publication: TransitionPublicationRead }) {
  const [catalog, setCatalog] = useState<ExportCatalog | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [requestKey, setRequestKey] = useState('')
  const alive = useRef(true), submitting = useRef(false), polling = useRef(false)
  const ownModal = useRef<ReturnType<typeof Modal.confirm> | null>(null)
  const storageKey = `jellyfish:transition-v2-export:${projectId}:${publication.id}`
  /** Poll actual durable job state without starting an export on page load. */
  async function refresh() { const result = await T.listTransitionExports({ projectId, chapterId }); if (alive.current) setCatalog(result.data ?? null) }
  useEffect(() => {
    alive.current = true
    try { setRequestKey(workspaceStorage.getItem(storageKey) ?? '') } catch { setError('无法读取转场导出恢复标识') }
    void refresh().catch(error => { if (alive.current) setError(explain(error)) })
    const timer = window.setInterval(() => { if (polling.current) return; polling.current = true; void refresh().catch(() => {}).finally(() => { polling.current = false }) }, 1500)
    return () => { alive.current = false; window.clearInterval(timer); ownModal.current?.destroy() }
  }, [projectId, chapterId, publication.id])
  /** The explicit confirmation persists a replay key before the first server mutation. */
  function submit() {
    if (busy) return
    ownModal.current?.destroy()
    ownModal.current = Modal.confirm({ title: requestKey ? '核对原转场导出？' : '实际渲染此转场版本？', content: `输出 ${(Number(publication.payload.duration_ms) / 1000).toFixed(1)} 秒。本地实际合成画面和声音，不调用模型。原硬切版本及源文件保留，字幕单独下载。`, okText: '确认本地渲染', cancelText: '取消', onOk: async () => {
      if (!alive.current || submitting.current) return
      submitting.current = true; setBusy(true); setError('')
      try {
        const key = requestKey || crypto.randomUUID()
        workspaceStorage.setItem(storageKey, key); setRequestKey(key)
        await T.createTransitionExport({ projectId, publicationId: publication.id, requestBody: { request_key: key } })
        if (alive.current) await refresh()
      } catch (error) { if (alive.current) setError(error instanceof DOMException ? '无法保存导出恢复标识，未创建新请求。' : explain(error)) }
      finally { submitting.current = false; if (alive.current) setBusy(false) }
    } })
  }
  /** Request actual queue/worker cancellation through the v2 owner. */
  async function cancel(job: ExportRead) { if (busy) return; setBusy(true); try { await T.cancelTransitionExport({ projectId, exportId: job.id, requestBody: {} }); if (alive.current) await refresh() } catch (error) { if (alive.current) setError(explain(error)) } finally { if (alive.current) setBusy(false) } }
  const jobs = catalog?.exports.filter(job => job.publication_id === publication.id) ?? []
  const state: Record<string, string> = { queued: '等待渲染', running: '正在实际合成', ready: '转场渲染完成', failed: '渲染失败', cancelled: '已取消' }
  const labels: Record<string, string> = { video: 'MP4转场视频', subtitles_srt: 'SRT字幕', subtitles_vtt: 'VTT字幕', package: '来源与转场ZIP' }
  return <div data-testid="transition-exports">
    {error && <Alert type="error" message={error} />}
    <Alert className="my-2" type={catalog?.enabled ? 'info' : 'warning'} message={catalog?.enabled ? '本地转场工具可用；不会调用模型' : catalog?.reason || '正在检查本地转场工具'} />
    <Button type="primary" disabled={busy || (!requestKey && !catalog?.enabled)} onClick={submit}>{requestKey ? '核对原转场导出' : '渲染此转场版本'}</Button>
    {requestKey && jobs.length > 0 && jobs.every(job => ['ready', 'failed', 'cancelled'].includes(job.state)) && <Button className="ml-2" disabled={busy || !catalog?.enabled} onClick={() => { ownModal.current?.destroy(); ownModal.current = Modal.confirm({ title: '准备另一次转场渲染？', content: '原任务和产物保留。下一次确认将使用新的本地导出请求，不会重复采用或调用模型。', onOk: () => { if (!alive.current) return; try { workspaceStorage.removeItem(storageKey); setRequestKey('') } catch { setError('无法清除浏览器恢复标识') } } }) }}>准备重新渲染</Button>}
    <List dataSource={jobs} locale={{ emptyText: '此转场版本尚无实际渲染任务' }} renderItem={job => <List.Item><Space direction="vertical"><Tag>{state[job.state]}</Tag>{job.reason && <span>{job.reason}</span>}{['queued', 'running'].includes(job.state) && <Button disabled={busy} onClick={() => void cancel(job)}>取消转场渲染</Button>}{job.state === 'ready' && <><Space wrap>{job.files.map(file => <a key={file.kind} href={workspaceMediaUrl(`${OpenAPI.BASE}${file.content_url}`)} target="_blank" rel="noreferrer">下载{labels[file.kind]}</a>)}</Space>{job.files.find(file => file.kind === 'video') && <video aria-label="已渲染转场视频" crossOrigin="anonymous" controls preload="metadata" style={{ maxHeight: 320, maxWidth: '100%' }} src={workspaceMediaUrl(`${OpenAPI.BASE}${job.files.find(file => file.kind === 'video')!.content_url}`)} />}</>}</Space></List.Item>} />
  </div>
}
