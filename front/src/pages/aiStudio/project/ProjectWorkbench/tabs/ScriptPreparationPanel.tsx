import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Checkbox, Collapse, Empty, Form, Input, InputNumber, List, Modal, Select, Space, Tag, message } from 'antd'
import { ScriptPreparationService as S, StudioShotsService } from '../../../../../services/generated'
import type { ScriptDocument, ScriptRead, ScriptSnapshot, ScriptCatalog } from '../../../../../services/generated'

type ChapterOption = { id: string; title: string; index: number; rawText?: string }
type ShotOption = { value: string; label: string }
const phases = [{ value: 'conflict', label: '核心冲突' }, { value: 'development', label: '剧情发展' }, { value: 'resolution', label: '收束或钩子' }]

/** Manual defaults deliberately contain no fabricated story beats or dialogue. */
function emptyScript(title = ''): ScriptDocument {
  return { schema_version: 1, title, script_text: '', target_duration_ms: 30000, character_ids: [], scene_ids: [], beats: [], dialogues: [], bindings: [] }
}

/** Export saved data only; imported JSON never starts model work. */
function exportJson(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Explain a backend validation failure without showing implementation internals. */
function errorMessage(error: unknown) {
  const body = (error as { body?: { message?: unknown; detail?: unknown } }).body
  if (typeof body?.message === 'string') return body.message
  if (typeof body?.detail === 'string') return body.detail
  return '操作失败，请检查引用、时长和修订版本。发生冲突时请重新载入。'
}

/** Prepare narrative coverage without presenting manual declarations as generated footage. */
export function ScriptPreparationPanel({ projectId, chapters }: { projectId: string; chapters: ChapterOption[] }) {
  const [chapterId, setChapterId] = useState('')
  const [draft, setDraft] = useState<ScriptRead | null>(null)
  const [catalog, setCatalog] = useState<ScriptCatalog>({ characters: [], scenes: [] })
  const [shots, setShots] = useState<ShotOption[]>([])
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')
  const [publishOpen, setPublishOpen] = useState(false)
  const [ack, setAck] = useState(false)
  const [note, setNote] = useState('')
  const [clearImportedBindings, setClearImportedBindings] = useState(false)
  const [snapshot, setSnapshot] = useState<ScriptSnapshot | null>(null)
  const [form] = Form.useForm()
  const characterIds = Form.useWatch('character_ids', { form, preserve: true }) as string[] | undefined
  const sceneIds = Form.useWatch('scene_ids', { form, preserve: true }) as string[] | undefined
  const beats = Form.useWatch('beats', { form, preserve: true }) as { id: string; text: string }[] | undefined
  const dialogues = Form.useWatch('dialogues', { form, preserve: true }) as { id: string; text: string }[] | undefined
  const alive = useRef(true)
  const confirmation = useRef<ReturnType<typeof Modal.confirm> | null>(null)
  const identity = useRef({ projectId, chapterId })
  identity.current = { projectId, chapterId }
  const displayed = draft?.chapter_id === chapterId ? draft : null
  const characters = catalog.characters.map(item => ({ value: item.id, label: item.name }))
  const scenes = catalog.scenes.map(item => ({ value: item.id, label: item.name }))
  const selectedCharacters = characters.filter(item => (characterIds ?? []).includes(item.value))
  const selectedScenes = scenes.filter(item => (sceneIds ?? []).includes(item.value))
  useEffect(() => { alive.current = true; return () => { alive.current = false; confirmation.current?.destroy(); confirmation.current = null } }, [])
  useEffect(() => { if (!chapterId && chapters.length) setChapterId(chapters[0].id) }, [chapterId, chapters])

  useEffect(() => {
    /** Protect refresh/close; ordinary SPA navigation still requires saving first. */
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  /** Reject late responses from a disposed workspace or a different chapter. */
  function current(chapter: string) { return alive.current && identity.current.projectId === projectId && identity.current.chapterId === chapter }

  /** Own static confirmation lifetime so navigation cannot leave an active old-project action. */
  function showConfirmation(options: Parameters<typeof Modal.confirm>[0]) {
    confirmation.current?.destroy()
    confirmation.current = Modal.confirm(options)
  }

  /** Read all chapter shots with an explicit editor bound instead of silently truncating. */
  async function loadShots(chapter: string) {
    const result: ShotOption[] = []
    for (let page = 1; page <= 3; page++) {
      const response = await StudioShotsService.listShotsApiV1StudioShotsGet({ chapterId: chapter, page, pageSize: 100 })
      const items = response.data?.items ?? []
      result.push(...items.map(item => ({ value: item.id, label: `${item.index}. ${item.title}` })))
      if (result.length > 200 || (response.data?.pagination.total ?? 0) > 200) throw new Error('chapter-shot-limit')
      if (!items.length || result.length >= (response.data?.pagination.total ?? 0)) break
    }
    return result
  }

  useEffect(() => {
    if (!chapterId) return
    let cancelled = false
    setLoading(true); setDraft(null); setShots([]); setDirty(false); setError('')
    Promise.all([S.listScriptDraftsApiV1ScriptPreparationProjectsProjectIdDraftsGet({ projectId }), S.getScriptCatalogApiV1ScriptPreparationProjectsProjectIdCatalogGet({ projectId }), loadShots(chapterId)]).then(([list, options, shotOptions]) => {
      if (cancelled) return
      setDraft(list.data?.find(item => item.chapter_id === chapterId) ?? null); setCatalog(options.data ?? { characters: [], scenes: [] }); setShots(shotOptions)
    }).catch(e => { if (!cancelled) setError(e instanceof Error && e.message === 'chapter-shot-limit' ? '当前准备工具最多支持每章200个镜头。' : errorMessage(e)) }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [projectId, chapterId])

  useEffect(() => {
    if (!displayed) return
    const document = displayed.document
    form.resetFields(); form.setFieldsValue({ ...document, target_duration_seconds: (document.target_duration_ms ?? 30000) / 1000, bindings: (document.bindings ?? []).map(item => ({ ...item, duration_seconds: item.duration_ms / 1000 })) }); setDirty(false)
  }, [displayed?.id, displayed?.revision, form])

  /** Reload current facts and coverage while retaining the selected chapter identity. */
  async function refresh() {
    if (!chapterId) return
    const chapter = chapterId
    setBusy(true); setError('')
    try {
      const [list, options, shotOptions] = await Promise.all([S.listScriptDraftsApiV1ScriptPreparationProjectsProjectIdDraftsGet({ projectId }), S.getScriptCatalogApiV1ScriptPreparationProjectsProjectIdCatalogGet({ projectId }), loadShots(chapter)])
      if (current(chapter)) { setDraft(list.data?.find(item => item.chapter_id === chapter) ?? null); setCatalog(options.data ?? { characters: [], scenes: [] }); setShots(shotOptions) }
    } catch (e) { if (current(chapter)) setError(errorMessage(e)) }
    finally { if (alive.current) setBusy(false) }
  }

  /** Create one chapter draft and durably register its project for narrative-controlled generation. */
  async function create(document: ScriptDocument) {
    const chapter = chapterId
    if (!current(chapter)) return
    setBusy(true); setError('')
    try {
      const response = await S.createScriptDraftApiV1ScriptPreparationProjectsProjectIdDraftsPost({ projectId, requestBody: { chapter_id: chapter, document } })
      if (current(chapter)) setDraft(response.data ?? null)
    } catch (e) { if (current(chapter)) setError(errorMessage(e)) }
    finally { if (alive.current) setBusy(false) }
  }

  /** Preserve collapsed fields and convert displayed seconds into exact integer milliseconds. */
  function formDocument(): ScriptDocument {
    const values = form.getFieldsValue(true) as Omit<ScriptDocument, 'bindings'> & { target_duration_seconds: number; bindings: (NonNullable<ScriptDocument['bindings']>[number] & { duration_seconds?: number })[] }
    return { schema_version: 1, title: values.title, script_text: values.script_text, target_duration_ms: Math.round(Number(values.target_duration_seconds) * 1000), character_ids: values.character_ids ?? [], scene_ids: values.scene_ids ?? [], beats: values.beats ?? [], dialogues: values.dialogues ?? [], bindings: (values.bindings ?? []).map(item => ({ shot_id: item.shot_id, beat_ids: item.beat_ids ?? [], dialogue_ids: item.dialogue_ids ?? [], character_ids: item.character_ids ?? [], scene_id: item.scene_id ?? null, duration_ms: Math.round(Number(item.duration_seconds ?? item.duration_ms / 1000) * 1000) })) }
  }

  /** Save a revision without silently changing the previously published required set. */
  async function save(document?: ScriptDocument) {
    if (!displayed) return
    const chapter = chapterId
    setBusy(true); setError('')
    try {
      const response = await S.patchScriptDraftApiV1ScriptPreparationProjectsProjectIdDraftsDraftIdPatch({ projectId, draftId: displayed.id, requestBody: { expected_revision: displayed.revision, document: document ?? formDocument() } })
      if (current(chapter)) { setDraft(response.data ?? null); setDirty(false); message.success('制作剧本与镜头覆盖已保存') }
    } catch (e) { if (current(chapter)) setError(errorMessage(e)) }
    finally { if (alive.current) setBusy(false) }
  }

  /** Import only document content and revalidate every local reference on the server. */
  async function importFile(file?: File) {
    if (!file) return
    if (file.size > 8 * 1024 * 1024) { setError('剧本JSON不能超过8 MiB，正文文档上限256 KiB。'); return }
    const chapter = chapterId
    setBusy(true)
    try {
      const value = JSON.parse(await file.text())
      if (!current(chapter)) return
      const document = value?.payload?.document ?? value?.document ?? value
      if (clearImportedBindings && document && typeof document === 'object') document.bindings = []
      if (displayed) await save(document as ScriptDocument)
      else await create(document as ScriptDocument)
    } catch { if (current(chapter)) setError('无法读取剧本JSON，请检查文件格式。') }
    finally { if (alive.current) setBusy(false) }
  }

  /** Publish an immutable preparation snapshot and explicitly record requirement changes. */
  async function publish() {
    if (!displayed) return
    const chapter = chapterId
    setBusy(true); setError('')
    try {
      const response = await S.publishScriptDraftApiV1ScriptPreparationProjectsProjectIdDraftsDraftIdPublishPost({ projectId, draftId: displayed.id, requestBody: { expected_revision: displayed.revision, acknowledge_requirement_changes: ack, requirement_change_note: note } })
      if (!current(chapter)) return
      setPublishOpen(false); setSnapshot(response.data ?? null)
      const next = await S.getScriptDraftApiV1ScriptPreparationProjectsProjectIdDraftsDraftIdGet({ projectId, draftId: displayed.id })
      if (current(chapter)) setDraft(next.data ?? null)
    } catch (e) { if (current(chapter)) setError(errorMessage(e)) }
    finally { if (alive.current) setBusy(false) }
  }

  /** Inspect old preparation versions without replacing the current draft. */
  async function viewSnapshot(id: string) {
    const chapter = chapterId
    setBusy(true)
    try { const response = await S.getScriptSnapshotApiV1ScriptPreparationProjectsProjectIdSnapshotsSnapshotIdGet({ projectId, snapshotId: id }); if (current(chapter)) setSnapshot(response.data ?? null) }
    catch (e) { if (current(chapter)) setError(errorMessage(e)) }
    finally { if (alive.current) setBusy(false) }
  }

  /** Confirm discarding unsaved edits when changing the working chapter. */
  function chooseChapter(id: string) {
    if (dirty) showConfirmation({ title: '放弃尚未保存的制作剧本修改？', onOk: () => { if (current(chapterId)) setChapterId(id) } })
    else setChapterId(id)
  }

  const coverage = displayed?.readiness.coverage
  const changes = displayed?.readiness.requirement_changes ?? []
  return <Card title="制作剧本与分镜覆盖" className="mb-4" data-testid="script-preparation-panel">
    <Collapse items={[{ key: 'editor', label: '编辑制作剧本，检查每段剧情和对白是否都有镜头', children: <>
      <Alert className="mb-3" type="info" showIcon message="无需账号的剧本准备" description="制作剧本与小说原文分别保存。覆盖检查只核对人工绑定，不代表模型已经生成或剧情已在成片中表达。登记后，本工程普通无镜头生成入口将暂停，后续需接入绑定剧本版本的镜头计划。已有任务可继续查询恢复。" />
      {error && <Alert type="error" className="mb-3" showIcon message={error} />}
      <Space wrap className="mb-3"><Select aria-label="制作剧本章节" style={{ minWidth: 220 }} placeholder="先创建章节" value={chapterId || undefined} disabled={busy || loading || publishOpen} onChange={chooseChapter} options={chapters.map(item => ({ value: item.id, label: `${item.index}. ${item.title}` }))} /><Button disabled={busy || loading || dirty || !chapterId} onClick={() => void refresh()}>刷新资料与覆盖</Button>{!displayed && <Button disabled={busy || loading || !chapterId} onClick={() => showConfirmation({ title: '为本章登记结构化剧本？', content: '登记后本工程的新生成请求必须绑定剧本版本，当前普通无镜头提交入口将暂停。原章节和已有任务保持可读。', onOk: () => create(emptyScript(chapters.find(item => item.id === chapterId)?.title)) })}>新建制作剧本草稿</Button>}</Space>
      <Space wrap className="mb-4"><label>导入制作剧本JSON <input aria-label="导入制作剧本JSON" type="file" accept=".json,application/json" disabled={busy || loading || dirty || !chapterId} onChange={e => { void importFile(e.target.files?.[0]); e.target.value = '' }} /></label><Checkbox checked={clearImportedBindings} disabled={busy || dirty} onChange={e => setClearImportedBindings(e.target.checked)}>导入时清空旧章镜头绑定</Checkbox>{displayed && <Button disabled={busy || dirty || loading} onClick={() => exportJson({ format: 'script-preparation-draft-v1', document: displayed.document }, `script-draft-${displayed.id}.json`)}>导出已保存制作剧本</Button>}</Space>
      {!displayed && <Empty description={loading ? '正在读取制作剧本' : '选择章节并新建草稿。角色、场景和实际镜头可先在对应页面准备。'} />}
      {displayed && <>
        <Space wrap className="mb-3"><Tag>修订 {displayed.revision}</Tag><Tag>必需剧情 {coverage?.covered_beats}/{coverage?.required_beats}</Tag><Tag>必需对白 {coverage?.covered_dialogues}/{coverage?.required_dialogues}</Tag><Tag color={displayed.readiness.latest_snapshot_usable ? 'green' : 'orange'}>{displayed.readiness.latest_snapshot_usable ? '当前准备快照可用' : '尚无匹配当前草稿与资料的准备快照'}</Tag></Space>
        <Alert className="mb-3" type={displayed.readiness.can_publish ? 'info' : 'warning'} showIcon message={dirty ? '有未保存修改，检查结果对应上次保存版本' : displayed.readiness.can_publish ? '结构与人工绑定检查齐全，可锁定准备版本' : '仍有内容需要补齐'} description={<List size="small" dataSource={displayed.readiness.issues} renderItem={item => <List.Item>{item.message}</List.Item>} />} />
      </>}
      <Form form={form} layout="vertical" disabled={!displayed || busy || loading} onFinish={() => void save()} onValuesChange={() => setDirty(true)} initialValues={emptyScript()} style={{ display: displayed ? undefined : 'none' }}>
        <Collapse defaultActiveKey={['script']} items={[
          { key: 'script', label: '1. 制作剧本、角色与场景', children: <>
            <Form.Item name="title" label="制作剧本标题"><Input aria-label="制作剧本标题" maxLength={160} /></Form.Item>
            <Form.Item name="target_duration_seconds" label="计划总时长（秒）"><InputNumber aria-label="计划总时长" min={1} max={300} precision={3} /></Form.Item>
            <Button className="mb-2" disabled={busy || !displayed || !chapters.find(item => item.id === chapterId)?.rawText} onClick={() => { form.setFieldValue('script_text', chapters.find(item => item.id === chapterId)?.rawText ?? ''); setDirty(true) }}>从本章原文填入正文</Button>
            <Form.Item name="script_text" label="制作剧本正文"><Input.TextArea aria-label="制作剧本正文" rows={6} maxLength={64000} /></Form.Item>
            <Form.Item name="character_ids" label="本章稳定角色"><Select aria-label="本章稳定角色" mode="multiple" options={characters} /></Form.Item>
            <Form.Item name="scene_ids" label="本章场景"><Select aria-label="本章场景" mode="multiple" options={scenes} /></Form.Item>
            <p>如果列表为空，先到角色/场景页建立或关联本工程资料，再刷新。切换工程前请保存。</p>
          </> },
          { key: 'beats', label: '2. 剧情节拍与逐句对白', children: <>
            <Form.List name="beats">{(fields, { add, remove }) => <>
              {fields.map(field => <div key={field.key} className="border rounded p-3 my-2"><Form.Item name={[field.name, 'id']} hidden><Input /></Form.Item><Space wrap align="baseline"><Form.Item name={[field.name, 'phase']} label="剧情阶段"><Select style={{ width: 150 }} options={phases} /></Form.Item><Form.Item name={[field.name, 'required']} valuePropName="checked"><Checkbox>必需剧情</Checkbox></Form.Item><Button onClick={() => remove(field.name)}>删除剧情节拍</Button></Space><Form.Item name={[field.name, 'text']} label="剧情节拍内容"><Input.TextArea maxLength={4000} rows={2} /></Form.Item></div>)}
              <Button disabled={busy || fields.length >= 100} onClick={() => add({ id: `beat-${crypto.randomUUID().slice(0, 8)}`, text: '', phase: 'conflict', required: true })}>添加剧情节拍</Button>
            </>}</Form.List>
            <Form.List name="dialogues">{(fields, { add, remove }) => <>
              {fields.map(field => <div key={field.key} className="border rounded p-3 my-2"><Form.Item name={[field.name, 'id']} hidden><Input /></Form.Item><Space wrap align="baseline"><Form.Item name={[field.name, 'speaker_id']} label="说话角色"><Select style={{ width: 180 }} options={selectedCharacters} /></Form.Item><Form.Item name={[field.name, 'required']} valuePropName="checked"><Checkbox>必需对白</Checkbox></Form.Item><Button onClick={() => remove(field.name)}>删除剧本对白</Button></Space><Form.Item name={[field.name, 'text']} label="剧本对白内容"><Input.TextArea rows={2} maxLength={4000} /></Form.Item></div>)}
              <Button disabled={busy || !selectedCharacters.length || fields.length >= 200} onClick={() => add({ id: `line-${crypto.randomUUID().slice(0, 8)}`, speaker_id: selectedCharacters[0]?.value, text: '', required: true })}>添加剧本对白</Button>
            </>}</Form.List>
          </> },
          { key: 'bindings', label: '3. 绑定本章实际镜头', children: <>
            {!shots.length && <Alert type="warning" message="本章尚无镜头，请先到分镜编辑页建立实际镜头。" />}
            <Form.List name="bindings">{(fields, { add, remove }) => <>
              {fields.map(field => <div key={field.key} className="border rounded p-3 my-2"><Space wrap align="baseline"><Form.Item name={[field.name, 'shot_id']} label="本章镜头"><Select style={{ width: 240 }} options={shots} /></Form.Item><Form.Item name={[field.name, 'duration_seconds']} label="镜头时长（秒）"><InputNumber min={0.001} max={120} precision={3} /></Form.Item><Button onClick={() => remove(field.name)}>删除镜头绑定</Button></Space><Form.Item name={[field.name, 'beat_ids']} label="本镜头覆盖剧情"><Select mode="multiple" options={(beats ?? []).map(item => ({ value: item.id, label: item.text || '未填写剧情' }))} /></Form.Item><Form.Item name={[field.name, 'dialogue_ids']} label="本镜头覆盖对白"><Select mode="multiple" options={(dialogues ?? []).map(item => ({ value: item.id, label: item.text || '未填写对白' }))} /></Form.Item><Form.Item name={[field.name, 'character_ids']} label="镜头内角色"><Select mode="multiple" options={selectedCharacters} /></Form.Item><Form.Item name={[field.name, 'scene_id']} label="镜头场景"><Select allowClear options={selectedScenes} /></Form.Item></div>)}
              <Button disabled={busy || !shots.length || fields.length >= 200} onClick={() => add({ shot_id: shots[0]?.value, beat_ids: [], dialogue_ids: [], character_ids: [], scene_id: null, duration_seconds: 5 })}>添加镜头绑定</Button>
            </>}</Form.List>
          </> },
        ]} />
        <Space wrap className="mt-4"><Button type="primary" htmlType="submit" loading={busy} disabled={!displayed || loading}>保存制作剧本</Button><Button disabled={!displayed || loading || busy || dirty || !displayed.readiness.can_publish} onClick={() => { setAck(false); setNote(''); setPublishOpen(true) }}>锁定准备版本</Button>{dirty && <Tag color="orange">未保存，请先保存再切换页面</Tag>}</Space>
      </Form>
      {!!displayed?.snapshots.length && <List header="已锁定版本" dataSource={displayed.snapshots} renderItem={item => <List.Item><span>修订 {item.draft_revision} · {item.content_hash.slice(0, 16)}</span><Button disabled={busy} onClick={() => void viewSnapshot(item.id)}>查看剧本准备快照</Button></List.Item>} />}
      <Modal title="锁定制作剧本准备版本" open={publishOpen} onCancel={() => setPublishOpen(false)} confirmLoading={busy} okButtonProps={{ disabled: !!changes.length && (!ack || !note.trim()) }} onOk={() => void publish()}>
        <p>锁定人工制作剧本、覆盖和当前资料校验值。该快照不代表实际模型输出或成片覆盖。之后编辑保留旧版本。</p>
        {!!changes.length && <><Alert type="warning" className="my-3" message="与上一锁定版本的必需要求发生变化" description={<List size="small" dataSource={changes} renderItem={item => <List.Item>{item}</List.Item>} />} /><Checkbox checked={ack} onChange={e => setAck(e.target.checked)}>明确修订必需剧情或对白要求，并保留旧版本</Checkbox><Input.TextArea aria-label="要求变更说明" value={note} maxLength={1000} rows={3} placeholder="说明此次要求变更的原因" onChange={e => setNote(e.target.value)} /></>}
      </Modal>
      <Modal title="制作剧本准备快照" open={!!snapshot} onCancel={() => setSnapshot(null)} footer={<Button onClick={() => snapshot && exportJson(snapshot, `script-snapshot-${snapshot.id}.json`)}>下载剧本准备快照JSON</Button>}>
        <Alert type="info" message="人工准备版本，不代表模型生成或正式M4通过" /><p className="my-3 break-all">校验值：{snapshot?.content_hash}</p><p>角色、场景或镜头资料后续改变时，当前准备检查会提示失配；此历史文件保留原内容。</p>
      </Modal>
    </> }]} />
  </Card>
}
