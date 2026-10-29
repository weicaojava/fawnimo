import { workspaceMediaUrl } from '../../../../../services/workspaceContext'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Collapse, Empty, Form, Input, InputNumber, List, Modal, Select, Space, Tag, message } from 'antd'
import { ProjectSection } from '../../ProjectSection'
import { BenchmarksService as B, OpenAPI, ReverseService as R } from '../../../../../services/generated'
import type { AnalysisRead, BenchmarkDocument, BenchmarkRead, BenchmarkSnapshot } from '../../../../../services/generated'

const rubric = [{ key: 'character', label: '角色' }, { key: 'scene', label: '场景' }, { key: 'composition', label: '构图' }, { key: 'lighting', label: '光影' }, { key: 'action', label: '动作表情' }, { key: 'continuity', label: '跨镜连续' }, { key: 'lipsync', label: '音画口型' }, { key: 'editing', label: '节奏剪辑' }]
const eventKinds = [{ value: 'interaction', label: '人物互动' }, { value: 'action', label: '可观察动作' }, { value: 'camera_change', label: '机位变化' }]
type Frame = { pts: number; time_base_num: number; time_base_den: number; relative_us: number }
type Technical = { video: { frames: Frame[] }; audio: { present: boolean } }

/** New drafts start empty so no prior observation is silently treated as human truth. */
function emptyDocument(): BenchmarkDocument {
  return { schema_version: 1, title: '', source_note: '', license_basis: 'unknown', license_reference: '', license_confirmed: false, independent_from_target: false, continuous_take: false, reviewer: '', human_reviewed: false, roles: [], dialogues: [], mouth_anchors: [], events: [], cut_frame_indices: [], rubric_notes: {} }
}

/** Download only the saved draft or the server's immutable snapshot as inert JSON. */
function download(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Describe validation errors without exposing server implementation details. */
function failure(error: unknown): string {
  const body = (error as { body?: { message?: unknown; detail?: unknown } }).body
  if (typeof body?.message === 'string') return body.message
  if (typeof body?.detail === 'string') return body.detail
  return '保存或读取失败。请检查帧号、角色引用和版本；存在冲突时重新载入最新版本。'
}

/** Account-free human annotation workspace; freezing never certifies formal Q0. */
export function BenchmarkPanel({ projectId }: { projectId: string }) {
  const [analyses, setAnalyses] = useState<AnalysisRead[]>([])
  const [analysisChoice, setAnalysisChoice] = useState('')
  const [drafts, setDrafts] = useState<BenchmarkRead[]>([])
  const [selected, setSelected] = useState('')
  const [stored, setStored] = useState<BenchmarkRead | null>(null)
  const draft = stored?.id === selected ? stored : null
  const [analysis, setAnalysis] = useState<AnalysisRead | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')
  const [frameInput, setFrameInput] = useState(0)
  const [preview, setPreview] = useState<number | null>(null)
  const [snapshot, setSnapshot] = useState<BenchmarkSnapshot | null>(null)
  const [form] = Form.useForm()
  const roles = Form.useWatch('roles', form) as { id: string; name: string }[] | undefined
  const alive = useRef(true)
  const context = useRef({ projectId, selected, analysisChoice })
  context.current = { projectId, selected, analysisChoice }
  const technical = analysis?.manifest as Technical | null | undefined
  const frames = technical?.video.frames ?? []
  const frameMax = Math.max(0, frames.length - 1)
  const last = frames[frameMax]
  const previous = frames[Math.max(0, frameMax - 1)]
  const duration = last ? (last.relative_us + Math.max(1, last.relative_us - previous.relative_us)) / 1000000 : 0
  const roleOptions = (roles ?? []).map(role => ({ value: role.id, label: role.name || role.id }))
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  useEffect(() => {
    /** Browser reload/close must warn while annotations are only in the form. */
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  /** Guard every late response against component disposal and a different selection. */
  function current(id: string) { return alive.current && context.current.projectId === projectId && context.current.selected === id }

  /** Refresh identities without overwriting unsaved form changes. */
  async function refreshList() {
    const [a, d] = await Promise.all([R.getReverseAnalysesApiV1ReverseProjectsProjectIdAnalysesGet({ projectId }), B.listBenchmarkDraftsApiV1BenchmarksProjectsProjectIdDraftsGet({ projectId })])
    if (!alive.current || context.current.projectId !== projectId) return
    setAnalyses(a.data ?? []); setDrafts(d.data ?? [])
    setAnalysisChoice(old => old || a.data?.find(item => ['ready', 'degraded'].includes(item.state))?.id || '')
    return d.data ?? []
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    refreshList().then(items => { if (!cancelled) setSelected(items?.[0]?.id ?? '') }).catch(e => { if (!cancelled) setError(failure(e)) }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [projectId]) // The parent keys this workspace by project; responses are also identity fenced.

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    setStored(null); setAnalysis(null); setPreview(null); setLoading(true); setDirty(false)
    B.getBenchmarkDraftApiV1BenchmarksProjectsProjectIdDraftsDraftIdGet({ projectId, draftId: selected }).then(async response => {
      if (cancelled || !response.data) return
      setStored(response.data)
      const report = await R.getReverseAnalysisApiV1ReverseProjectsProjectIdAnalysesAnalysisIdGet({ projectId, analysisId: response.data.analysis_id })
      if (!cancelled) setAnalysis(report.data ?? null)
    }).catch(e => { if (!cancelled) setError(failure(e)) }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [projectId, selected])

  useEffect(() => {
    if (!draft) return
    form.resetFields(); form.setFieldsValue({ ...draft.document, cut_text: (draft.document.cut_frame_indices ?? []).join(',') }); setDirty(false)
  }, [draft?.id, draft?.revision, form])

  /** Persist a new local draft; imported conclusions and foreign identities are discarded. */
  async function create(document: BenchmarkDocument, imported = false) {
    if (!analysisChoice) { setError('先选择已解析的本工程原视频。新上传视频请在原视频分析中完成解析，再刷新列表。'); return }
    const id = selected
    setBusy(true); setError('')
    try {
      const body = { projectId, requestBody: { analysis_id: analysisChoice, document, request_key: crypto.randomUUID() } }
      const response = imported ? await B.importBenchmarkDraftApiV1BenchmarksProjectsProjectIdImportDraftPost(body) : await B.createBenchmarkDraftApiV1BenchmarksProjectsProjectIdDraftsPost(body)
      if (!current(id)) return
      await refreshList()
      if (!current(id)) return
      setSelected(response.data?.id ?? ''); setStored(response.data ?? null); message.success(imported ? '已作为新草稿重新校验' : '已建立标注草稿')
    } catch (e) { if (current(id)) setError(failure(e)) }
    finally { if (current(id)) setBusy(false); else if (alive.current) setBusy(false) }
  }

  /** Save inclusive original-frame annotations against the displayed revision. */
  async function save(values: BenchmarkDocument & { cut_text: string }) {
    if (!draft) return
    const id = draft.id
    // Collapsed sections may be unmounted; retain the full saved form store.
    const allValues = form.getFieldsValue(true) as BenchmarkDocument & { cut_text?: string }
    const { cut_text = '', ...document } = { ...draft.document, ...allValues, ...values }
    const text = String(cut_text ?? '').trim()
    if (text && !/^\d+(\s*,\s*\d+)*$/.test(text)) { setError('切点请填逗号分隔的非负整数帧号。'); return }
    setBusy(true); setError('')
    try {
      const response = await B.patchBenchmarkDraftApiV1BenchmarksProjectsProjectIdDraftsDraftIdPatch({ projectId, draftId: id, requestBody: { expected_revision: draft.revision, document: { ...document, schema_version: 1, cut_frame_indices: text ? text.split(',').map(Number) : [] } } })
      if (!current(id)) return
      setStored(response.data ?? null); setDirty(false); await refreshList(); message.success('标注已保存，缺项已重新检查')
    } catch (e) { if (current(id)) setError(failure(e)) }
    finally { if (alive.current) setBusy(false) }
  }

  /** Import document content only; the server rechecks ownership, frames and readiness. */
  async function importFile(file?: File) {
    if (!file) return
    // Snapshots include up to 7200 source-frame records; document size is still checked separately by the server.
    if (file.size > 8 * 1024 * 1024) { setError('标注或快照文件不能超过8 MiB。'); return }
    const id = selected
    const choice = analysisChoice
    setBusy(true)
    try {
      const value = JSON.parse(await file.text())
      if (!current(id) || context.current.analysisChoice !== choice) return
      const document = value?.payload?.document ?? value?.document ?? value
      await create(document as BenchmarkDocument, true)
    } catch { if (current(id)) setError('无法读取标注JSON文件。请导入已保存草稿或冻结快照。') }
    finally { if (alive.current) setBusy(false) }
  }

  /** Freeze the saved revision; a snapshot records declarations without certifying them. */
  async function freeze() {
    if (!draft || dirty) return
    const id = draft.id
    setBusy(true); setError('')
    try {
      const response = await B.freezeBenchmarkDraftApiV1BenchmarksProjectsProjectIdDraftsDraftIdFreezePost({ projectId, draftId: id, requestBody: { expected_revision: draft.revision } })
      if (!current(id)) return
      setSnapshot(response.data ?? null)
      const next = await B.getBenchmarkDraftApiV1BenchmarksProjectsProjectIdDraftsDraftIdGet({ projectId, draftId: id })
      if (current(id)) { setStored(next.data ?? null); await refreshList() }
    } catch (e) { if (current(id)) setError(failure(e)) }
    finally { if (alive.current) setBusy(false) }
  }

  /** Retrieve an owned snapshot for inspection or JSON download. */
  async function openSnapshot(id: string) {
    const selection = selected
    setBusy(true)
    try { const response = await B.getBenchmarkSnapshotApiV1BenchmarksProjectsProjectIdSnapshotsSnapshotIdGet({ projectId, snapshotId: id }); if (current(selection)) setSnapshot(response.data ?? null) }
    catch (e) { if (current(selection)) setError(failure(e)) }
    finally { if (alive.current) setBusy(false) }
  }

  /** Confirm leaving a dirty form instead of silently losing annotation changes. */
  function choose(id: string) {
    const change = () => { setSelected(id); setError('') }
    if (dirty) Modal.confirm({ title: '放弃尚未保存的标注修改？', onOk: change })
    else change()
  }

  return <ProjectSection title="验收样本与标注" data-testid="benchmark-panel">
    <Collapse items={[{ key: 'workspace', label: '准备独立对白样本，查看缺项并保存标注', children: <>
      <Alert className="mb-4" type="info" showIcon message="正式验收尚需独立样本" description="请选择有使用授权的连续30–60秒双人对白视频，语音清晰、口型可见，含人物互动、动作及机位变化。现有约10秒样本仅可作草稿。冻结只保存人工声明和标注版本，不代表正式验收通过。" />
      {error && <Alert className="mb-3" type="error" showIcon message={error} />}
      <Space wrap className="mb-3">
        <Select aria-label="基准原视频" placeholder="选择已解析的原视频" style={{ minWidth: 220 }} value={analysisChoice || undefined} disabled={busy || dirty} onChange={setAnalysisChoice} options={analyses.filter(item => ['ready', 'degraded'].includes(item.state)).map(item => ({ value: item.id, label: `原视频 ${item.source_hash.slice(0, 12)} · ${item.state === 'ready' ? '解析完成' : '需复核'}` }))} />
        <Button onClick={() => void create(emptyDocument())} disabled={busy || dirty || !analysisChoice}>新建标注草稿</Button>
        <label>导入标注JSON <input aria-label="导入标注JSON" type="file" accept=".json,application/json" disabled={busy || dirty || !analysisChoice} onChange={e => { void importFile(e.target.files?.[0]); e.target.value = '' }} /></label>
        <Button disabled={busy || loading} onClick={() => { void refreshList().catch(e => setError(failure(e))) }}>刷新样本列表</Button>
      </Space>
      <Space wrap className="mb-4">
        <Select aria-label="标注草稿" placeholder="选择已保存草稿" style={{ minWidth: 240 }} value={selected || undefined} disabled={busy || loading} onChange={choose} options={drafts.map(item => ({ value: item.id, label: `${item.document.title || '未命名草稿'} · 修订 ${item.revision}` }))} />
        {draft && <><Tag>修订 {draft.revision}</Tag><Button disabled={busy || dirty} onClick={() => download({ format: 'benchmark-draft-v1', document: draft.document }, `benchmark-draft-${draft.id}.json`)}>导出已保存草稿</Button></>}
      </Space>
      {!draft && <Empty description={loading ? '正在读取标注' : '选择原视频，新建或导入标注草稿'} />}
      {draft && <>
        <Alert className="mb-4" showIcon type={draft.readiness.can_freeze ? 'info' : 'warning'} message={dirty ? '有未保存修改，缺项清单对应上次保存版本' : draft.readiness.can_freeze ? '声明与结构检查齐全，可保存冻结快照' : `还有 ${draft.readiness.issues.length} 类内容待补充`} description={<List size="small" dataSource={draft.readiness.issues} renderItem={item => <List.Item>{item.message}</List.Item>} />} />
        {analysis && <div className="mb-4">
          <div className="project-inline-facts"><span>原视频 {analysis.source_hash.slice(0, 16)}</span><span>{frames.length} 个实际解码帧</span><span>{duration.toFixed(3)} 秒</span><span>{technical?.audio.present ? '已提取音轨' : '没有音轨'}</span></div>
          <video key={analysis.id} aria-label="标注原视频预览" controls preload="metadata" style={{ maxHeight: 240, maxWidth: '100%' }} src={workspaceMediaUrl(`${OpenAPI.BASE}/api/v1/reverse/projects/${encodeURIComponent(projectId)}/analyses/${encodeURIComponent(analysis.id)}/proxy`)} />
          <Space wrap className="mt-2"><span>检查原始帧</span><InputNumber aria-label="标注原始帧" min={0} max={frameMax} precision={0} value={frameInput} onChange={v => setFrameInput(v ?? 0)} /><Button onClick={() => setPreview(frameInput)}>查看标注帧</Button></Space>
          {preview !== null && <div className="mt-2"><img alt={`标注原始帧 ${preview}`} style={{ maxHeight: 240, maxWidth: '100%' }} src={workspaceMediaUrl(`${OpenAPI.BASE}/api/v1/reverse/projects/${encodeURIComponent(projectId)}/analyses/${encodeURIComponent(analysis.id)}/frames/${preview}`)} /><span> 原始帧 {preview} · {((frames[preview]?.relative_us ?? 0) / 1000000).toFixed(6)} 秒</span></div>}
        </div>}
      </>}
      <Form form={form} layout="vertical" disabled={busy || loading || !draft} onFinish={save} onValuesChange={() => setDirty(true)} style={{ display: draft ? undefined : 'none' }} initialValues={emptyDocument()}>
        <Form.Item name="schema_version" hidden><InputNumber /></Form.Item>
        <Form.Item name="cut_frame_indices" hidden><Input /></Form.Item>
        <Collapse defaultActiveKey={['source']} items={[
          { key: 'source', label: '1. 素材来源、许可与人工复核', children: <>
            <Form.Item name="title" label="样本标题"><Input aria-label="样本标题" maxLength={120} /></Form.Item>
            <Form.Item name="source_note" label="来源说明"><Input.TextArea aria-label="来源说明" maxLength={1000} rows={2} placeholder="原始来源、连续区间与获取方式" /></Form.Item>
            <Form.Item name="license_basis" label="许可依据"><Select options={[{ value: 'unknown', label: '尚未确认' }, { value: 'owned', label: '自有素材' }, { value: 'permission', label: '已取得授权' }, { value: 'open_license', label: '开放许可' }]} /></Form.Item>
            <Form.Item name="license_reference" label="许可或授权记录"><Input.TextArea maxLength={1000} rows={2} placeholder="填写可复核的授权记录或许可说明，请勿放入账号密钥" /></Form.Item>
            <Form.Item name="license_confirmed" valuePropName="checked"><Checkbox>已核对素材使用权限</Checkbox></Form.Item>
            <Form.Item name="independent_from_target" valuePropName="checked"><Checkbox>此样本独立于目标片段</Checkbox></Form.Item>
            <Form.Item name="continuous_take" valuePropName="checked"><Checkbox>来自同一连续片段，未拼凑不同来源（允许片内切镜）</Checkbox></Form.Item>
            <Form.Item name="reviewer" label="人工复核者"><Input aria-label="人工复核者" maxLength={120} /></Form.Item>
            <Form.Item name="human_reviewed" valuePropName="checked"><Checkbox>已人工检查以下标注与评分检查点</Checkbox></Form.Item>
          </> },
          { key: 'roles', label: '2. 稳定角色与逐句对白', children: <>
            <Form.List name="roles">{(fields, { add, remove }) => <>
              {fields.map(field => <Space key={field.key} wrap align="baseline"><Form.Item name={[field.name, 'id']} hidden><Input /></Form.Item><Form.Item name={[field.name, 'name']} label="角色名称"><Input maxLength={80} /></Form.Item><Button onClick={() => remove(field.name)}>删除角色</Button></Space>)}
              <Button disabled={fields.length >= 8 || busy} onClick={() => add({ id: `role-${crypto.randomUUID().slice(0, 8)}`, name: '' })}>添加角色</Button><p>删除角色前先移除引用它的对白与口型锚点；服务端会检查引用。</p>
            </>}</Form.List>
            <Form.List name="dialogues">{(fields, { add, remove }) => <>
              {fields.map(field => <div key={field.key} className="border rounded p-3 my-2"><Form.Item name={[field.name, 'id']} hidden><Input /></Form.Item><Space wrap align="baseline"><Form.Item name={[field.name, 'role_id']} label="说话角色"><Select style={{ width: 160 }} options={roleOptions} /></Form.Item><Form.Item name={[field.name, 'start_frame']} label="开始原帧"><InputNumber min={0} max={frameMax} precision={0} /></Form.Item><Form.Item name={[field.name, 'end_frame']} label="结束原帧（含）"><InputNumber min={0} max={frameMax} precision={0} /></Form.Item><Button onClick={() => remove(field.name)}>删除对白</Button></Space><Form.Item name={[field.name, 'text']} label="逐字对白"><Input.TextArea maxLength={2000} rows={2} /></Form.Item></div>)}
              <Button disabled={fields.length >= 200 || !roleOptions.length || busy} onClick={() => add({ id: `line-${crypto.randomUUID().slice(0, 8)}`, role_id: roleOptions[0]?.value, text: '', start_frame: 0, end_frame: 0 })}>添加对白</Button>
            </>}</Form.List>
          </> },
          { key: 'anchors', label: '3. 口型锚点与关键事件', children: <>
            <p>每个对白角色至少10个可辨锚点，发声与闭口均需记录。同一角色同一帧只能记录一次。</p>
            <Form.List name="mouth_anchors">{(fields, { add, remove }) => <>
              {fields.map(field => <Space key={field.key} wrap align="baseline"><Form.Item name={[field.name, 'role_id']} label="角色"><Select style={{ width: 140 }} options={roleOptions} /></Form.Item><Form.Item name={[field.name, 'frame_index']} label="原帧"><InputNumber min={0} max={frameMax} precision={0} /></Form.Item><Form.Item name={[field.name, 'state']} label="口部状态"><Select style={{ width: 100 }} options={[{ value: 'speaking', label: '发声' }, { value: 'closed', label: '闭口' }]} /></Form.Item><Button onClick={() => remove(field.name)}>删除锚点</Button></Space>)}
              <Button disabled={fields.length >= 200 || !roleOptions.length || busy} onClick={() => add({ role_id: roleOptions[0]?.value, frame_index: preview ?? 0, state: 'speaking' })}>添加口型锚点</Button>
            </>}</Form.List>
            <Form.List name="events">{(fields, { add, remove }) => <>
              {fields.map(field => <div key={field.key} className="border rounded p-3 my-2"><Form.Item name={[field.name, 'id']} hidden><Input /></Form.Item><Space wrap align="baseline"><Form.Item name={[field.name, 'kind']} label="事件类型"><Select style={{ width: 140 }} options={eventKinds} /></Form.Item><Form.Item name={[field.name, 'start_frame']} label="开始原帧"><InputNumber min={0} max={frameMax} precision={0} /></Form.Item><Form.Item name={[field.name, 'end_frame']} label="结束原帧（含）"><InputNumber min={0} max={frameMax} precision={0} /></Form.Item><Button onClick={() => remove(field.name)}>删除事件</Button></Space><Form.Item name={[field.name, 'description']} label="必需事件描述"><Input.TextArea maxLength={1000} rows={2} /></Form.Item></div>)}
              <Button disabled={fields.length >= 100 || busy} onClick={() => add({ id: `event-${crypto.randomUUID().slice(0, 8)}`, kind: 'interaction', description: '', start_frame: 0, end_frame: 0 })}>添加关键事件</Button>
            </>}</Form.List>
          </> },
          { key: 'rubric', label: '4. 切点与八维评分检查点', children: <>
            <Form.Item name="cut_text" label="镜头边界原帧（从0开始，逗号分隔）"><Input placeholder="0,51,97…" /></Form.Item>
            <p>填写本样本每一维要检查的具体内容。评分权重和技术阈值沿用验收计划；尚未生成结果时不打分。</p>
            {rubric.map(item => <Form.Item key={item.key} name={['rubric_notes', item.key]} label={item.label}><Input.TextArea rows={2} maxLength={2000} /></Form.Item>)}
          </> },
        ]} />
        <Space wrap className="mt-4"><Button type="primary" htmlType="submit" loading={busy} disabled={!draft || loading}>保存标注草稿</Button><Button disabled={!draft || busy || loading || dirty || !draft.readiness.can_freeze} onClick={() => Modal.confirm({ title: '冻结当前已保存标注？', content: '冻结快照保存原片证据、标注和固定评分规则。许可与语义仍是人工声明，不代表正式Q0验收通过。之后编辑会保留旧快照。', onOk: freeze })}>冻结已保存版本</Button>{dirty && <Tag color="orange">未保存，请先保存再切换工程</Tag>}</Space>
      </Form>
      {!!draft?.snapshots.length && <List header="冻结版本" dataSource={draft.snapshots} renderItem={item => <List.Item><span>修订 {item.draft_revision} · {item.content_hash.slice(0, 16)}</span><Button disabled={busy} onClick={() => void openSnapshot(item.id)}>查看冻结快照</Button></List.Item>} />}
      <Modal title="冻结标注快照" open={!!snapshot} onCancel={() => setSnapshot(null)} footer={<Button onClick={() => snapshot && download(snapshot, `benchmark-snapshot-${snapshot.id}.json`)}>下载冻结快照JSON</Button>}>
        <Alert type="info" message="仅保存人工声明，不代表正式Q0验收通过" /><p className="my-3 break-all">内容校验值：{snapshot?.content_hash}</p><p>快照包含源视频校验值、原始帧时间证据、标注与固定评分规则；实际素材需随工程另行备份。</p>
      </Modal>
    </> }]} />
  </ProjectSection>
}
