import { useEffect, useRef, useState } from 'react'
import { Alert, AutoComplete, Button, Card, Collapse, Drawer, Empty, Input, List, Modal, Select, Space, Table, Tag, message } from 'antd'
import { Link, useNavigate } from 'react-router-dom'
import { ExecutionService, ScriptPreparationService, ShotPlansService as S } from '../../../../services/generated'
import type { CompiledPlanRead, PlanCatalog, PlanDocument, PlanPreflight, PlanRead, PlannedJobRead, PlanVersionOption, ScriptCatalog } from '../../../../services/generated'
import { resolveAssetUrl } from '../../assets/utils'
import { useTaskUiStore } from '../../components/taskUiStore'

type Props = { projectId: string; chapterId: string; open: boolean; requestedPlanId?: string; onClose: () => void }
type Entry = { shot_id: string; duration_ms: number; entry_hash: string; request: { prompt: string }; [key: string]: unknown }
const kindNames: Record<string, string> = { character: '角色', scene: '场景', appearance: '服装', voice: '声线', prop: '道具' }
const stateNames: Record<string, string> = { queued: '等待执行', submitting: '正在提交', submission_unknown: '等待核对受理', running: '生成中', downloading: '接收结果', ready: '候选已接收', failed: '失败', cancelled: '已取消', paused: '已暂停' }

/** Keep an empty saved draft useful even before a publication or account exists. */
function emptyDocument(): PlanDocument { return { schema_version: 1, script_snapshot_id: null, model_id: '', shots: [] } }

/** Show validation feedback without exposing stacks or implementation details. */
function explain(error: unknown) {
  const body = (error as { body?: { message?: unknown; detail?: unknown } }).body
  return typeof body?.message === 'string' ? body.message : typeof body?.detail === 'string' ? body.detail : '操作失败，请检查当前版本、资产引用和服务状态。'
}

/** Export saved inputs; downloading a plan does not submit generation. */
function download(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Task-center cancellation records intent through the durable job API. */
async function cancelJob(projectId: string, jobId: string) {
  try {
    const response = await ExecutionService.cancelExecutionJobApiV1ExecutionProjectsProjectIdJobsJobIdCancelPost({ projectId, jobId })
    message.success(response.data?.state === 'cancelled' ? '任务已取消' : '已记录取消请求，仍需核对服务结果')
  } catch (error) { message.error(explain(error)) }
}

/** Prepare complete chapter plans and explicitly submit individual immutable entries. */
export function ShotPlanPanel({ projectId, chapterId, open, requestedPlanId, onClose }: Props) {
  const [catalog, setCatalog] = useState<PlanCatalog | null>(null)
  const [entities, setEntities] = useState<ScriptCatalog>({ characters: [], scenes: [] })
  const [draft, setDraft] = useState<PlanRead | null>(null)
  const [document, setDocument] = useState<PlanDocument>(emptyDocument)
  const [plan, setPlan] = useState<CompiledPlanRead | null>(null)
  const [preflight, setPreflight] = useState<PlanPreflight | null>(null)
  const [jobs, setJobs] = useState<PlannedJobRead[]>([])
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [jsonOpen, setJsonOpen] = useState(false)
  const [keyRevision, setKeyRevision] = useState(0)
  const identity = useRef({ projectId, chapterId, open })
  const contextEpoch = useRef(0)
  const contextKey = useRef('')
  const formVersion = useRef(0)
  const importOperation = useRef(0)
  const dirtyNow = useRef(dirty)
  const draftNow = useRef(draft)
  const planNow = useRef(plan)
  const alive = useRef(true)
  const request = useRef(0)
  const confirmation = useRef<ReturnType<typeof Modal.confirm> | null>(null)
  const navigate = useNavigate()
  const upsertTask = useTaskUiStore(state => state.upsertTask)
  const setTaskCenterOpen = useTaskUiStore(state => state.setOpen)
  const scope = `${projectId}:${chapterId}:${open}`
  if (contextKey.current !== scope) { contextKey.current = scope; contextEpoch.current++ }
  const epoch = contextEpoch.current
  identity.current = { projectId, chapterId, open }
  dirtyNow.current = dirty; draftNow.current = draft; planNow.current = plan
  const entries = ((plan?.payload as { entries?: Entry[] } | undefined)?.entries ?? [])
  const bindings = catalog?.script && catalog.script.id === document.script_snapshot_id ? catalog.script.document.bindings ?? [] : []
  const entityNames = new Map([...entities.characters, ...entities.scenes].map(entity => [entity.id, entity.name]))

  /** A late response or confirmation must never act on a disposed workspace. */
  function current() { return alive.current && contextEpoch.current === epoch && identity.current.open && identity.current.projectId === projectId && identity.current.chapterId === chapterId }

  /** Own only this panel's confirmation, including when the route changes. */
  function confirm(options: Parameters<typeof Modal.confirm>[0]) { confirmation.current?.destroy(); confirmation.current = Modal.confirm(options) }

  useEffect(() => { alive.current = true; return () => { alive.current = false; confirmation.current?.destroy() } }, [])
  useEffect(() => {
    /** Browser refresh/close protection complements the explicit panel-close guard. */
    const warn = (event: BeforeUnloadEvent) => { if (open && dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [open, dirty])

  /** Load one immutable history entry and current advisory submission checks. */
  async function loadPlan(planId: string) {
    const sequence = ++request.current
    const [saved, check, linked] = await Promise.all([
      S.getShotPlanApiV1ShotPlansProjectsProjectIdPlansPlanIdGet({ projectId, planId }),
      S.preflightShotPlanApiV1ShotPlansProjectsProjectIdPlansPlanIdPreflightGet({ projectId, planId }),
      S.listPlannedShotJobsApiV1ShotPlansProjectsProjectIdPlansPlanIdJobsGet({ projectId, planId }),
    ])
    if (!current() || sequence !== request.current) return
    setPlan(saved.data ?? null); setPreflight(check.data ?? null); setJobs(linked.data ?? [])
  }

  /** Refresh saved preparation with an identity fence, without reusing demo data. */
  async function load() {
    if (!current()) return
    const sequence = ++request.current
    setLoading(true); setError('')
    try {
      const [options, saved, names] = await Promise.all([
        S.getShotPlanCatalogApiV1ShotPlansProjectsProjectIdCatalogGet({ projectId, chapterId }),
        S.listShotPlanDraftsApiV1ShotPlansProjectsProjectIdDraftsGet({ projectId, chapterId }),
        ScriptPreparationService.getScriptCatalogApiV1ScriptPreparationProjectsProjectIdCatalogGet({ projectId }),
      ])
      if (!current() || sequence !== request.current) return
      const record = saved.data?.[0] ?? null
      setCatalog(options.data ?? null); setEntities(names.data ?? { characters: [], scenes: [] }); setDraft(record)
      formVersion.current++; setDocument(record?.document ?? emptyDocument()); setDirty(false); setPlan(null); setPreflight(null); setJobs([])
      if (requestedPlanId && record?.plans.some(item => item.id === requestedPlanId)) await loadPlan(requestedPlanId)
      else if (requestedPlanId) setError('回跳的计划不属于本章历史，请核对工程与章节。')
      else if (record?.plans.length) await loadPlan(record.plans[0].id)
    } catch (e) { if (current()) setError(explain(e)) }
    finally { if (current()) setLoading(false) }
  }

  useEffect(() => {
    setDraft(null); setPlan(null); setPreflight(null); setJobs([]); setCatalog(null); setDirty(false); setJsonOpen(false)
    if (open) void load()
    else { request.current++; confirmation.current?.destroy(); setBusy(false); setLoading(false) }
    return () => { request.current++; confirmation.current?.destroy() }
  }, [projectId, chapterId, open])

  useEffect(() => {
    if (!open || !draft || !requestedPlanId) return
    if (dirty) { setError('当前有未保存修改，请保存后再查看回跳计划。'); return }
    if (!draft.plans.some(item => item.id === requestedPlanId)) { setError('回跳的计划不属于本章历史。'); return }
    setBusy(true)
    void loadPlan(requestedPlanId).catch(error => { if (current()) setError(explain(error)) }).finally(() => { if (current()) setBusy(false) })
  }, [requestedPlanId])

  useEffect(() => {
    if (!open || !plan) return
    let cancelled = false
    let inFlight = false
    const planId = plan.id
    /** Poll lightweight status only; no automatic submissions or new recovery keys. */
    const timer = window.setInterval(() => {
      if (inFlight) return
      inFlight = true
      const observedRequest = request.current
      void S.listPlannedShotJobsApiV1ShotPlansProjectsProjectIdPlansPlanIdJobsGet({ projectId, planId }).then(result => {
        if (!cancelled && current() && observedRequest === request.current) setJobs(result.data ?? [])
      }).catch(() => { /* A manual refresh remains available when status polling fails. */ }).finally(() => { inFlight = false })
    }, 5000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [projectId, chapterId, open, plan?.id, epoch])

  /** Update controlled fields so collapsed sections retain their complete values. */
  function edit(value: PlanDocument) { formVersion.current++; dirtyNow.current = true; setDocument(value); setDirty(true) }

  /** Use the latest actual script's binding set, preserving compatible explicit choices. */
  function chooseScript(id: string | undefined) {
    const previous = new Map(document.shots?.map(shot => [shot.shot_id, shot]) ?? [])
    edit({ ...document, script_snapshot_id: id ?? null, shots: catalog?.script && id === catalog.script.id ? (catalog.script.document.bindings ?? []).map(binding => previous.get(binding.shot_id) ?? { shot_id: binding.shot_id, version_ids: [], reference_media_ids: [], prompt_addition: '' }) : [] })
  }

  /** Save complete inert parameters with CAS; incomplete drafts are deliberately allowed. */
  async function save(next: PlanDocument = document) {
    if (!current()) return
    setBusy(true); setError('')
    try {
      const result = draft
        ? await S.patchShotPlanDraftApiV1ShotPlansProjectsProjectIdDraftsDraftIdPatch({ projectId, draftId: draft.id, requestBody: { expected_revision: draft.revision, document: next } })
        : await S.createShotPlanDraftApiV1ShotPlansProjectsProjectIdDraftsPost({ projectId, requestBody: { chapter_id: chapterId, document: next } })
      if (!current()) return
      formVersion.current++; setDraft(result.data ?? null); setDocument(result.data?.document ?? next); setDirty(false)
      setPreflight(null); message.success('镜头计划已保存，尚未提交生成')
    } catch (e) { if (current()) { setError(explain(e)); message.error(explain(e)) } }
    finally { if (current()) setBusy(false) }
  }

  /** Register a fixed version for the existing character/scene, preserving its identity. */
  async function register(kind: 'character' | 'scene', entityId: string) {
    if (!current()) return
    setBusy(true); setError('')
    try {
      await S.registerShotPlanAssetApiV1ShotPlansProjectsProjectIdAssetRegistrationsPost({ projectId, requestBody: { kind, entity_id: entityId } })
      const options = await S.getShotPlanCatalogApiV1ShotPlansProjectsProjectIdCatalogGet({ projectId, chapterId })
      if (!current()) return
      setCatalog(options.data ?? null); message.success('已登记资产版本，请在镜头中明确选用')
    } catch (e) { if (current()) setError(explain(e)) }
    finally { if (current()) setBusy(false) }
  }

  /** Compilation creates immutable inputs only and never starts a provider request. */
  async function compile() {
    if (!current() || !draft || dirty) return
    setBusy(true); setError('')
    try {
      const result = await S.compileShotPlanApiV1ShotPlansProjectsProjectIdDraftsDraftIdCompilePost({ projectId, draftId: draft.id, requestBody: { expected_revision: draft.revision } })
      if (!current() || !result.data) return
      const saved = await S.getShotPlanDraftApiV1ShotPlansProjectsProjectIdDraftsDraftIdGet({ projectId, draftId: draft.id })
      if (!current()) return
      setDraft(saved.data ?? null); await loadPlan(result.data.id); message.success('已锁定镜头计划；生成仍需单独检查与确认')
    } catch (e) { if (current()) setError(explain(e)) }
    finally { if (current()) setBusy(false) }
  }

  /** Import only inert document fields and save a fresh current revision. */
  async function importJson(file: File) {
    if (!current() || busy || dirty) return
    const operation = ++importOperation.current
    const version = formVersion.current
    const revision = draft?.revision
    const draftId = draft?.id
    /** A delayed file read or old confirmation cannot replace intervening local work. */
    const importCurrent = () => current() && importOperation.current === operation && formVersion.current === version && !dirtyNow.current && draftNow.current?.id === draftId && draftNow.current?.revision === revision
    setBusy(true)
    try {
      if (file.size > 8 * 1024 * 1024) throw new Error('file-limit')
      const value = JSON.parse(await file.text())
      const imported = value.document ?? value.payload?.document
      if (!imported || typeof imported !== 'object' || Array.isArray(imported)) throw new Error('missing-document')
      if (!importCurrent()) return
      confirm({ title: '导入为本章的新修订？', content: '仅导入计划选择与参数。原文件的任务、通过状态和模型绑定不会获得执行资格。已有历史计划保留。', onOk: async () => { if (importCurrent()) await save(imported as PlanDocument) } })
    } catch { if (current()) message.error('文件不是有效的计划JSON，或超过8MiB。') }
    finally { if (current() && importOperation.current === operation) setBusy(false) }
  }

  /** Persist one recovery identity before sending; never silently retry under a new key. */
  function storedKey(shotId: string) {
    try { return window.localStorage.getItem(`jellyfish:planned-submit:${projectId}:${plan?.id}:${shotId}`) ?? '' } catch { return '' }
  }

  /** Confirm each new generation intent; recovery keeps the same durable identity. */
  function offerSubmission(entry: Entry, newIteration = false) {
    if (!current() || !plan || busy || dirty) return
    const replay = !newIteration && !!storedKey(entry.shot_id)
    if (!replay && !preflight?.can_submit) return
    const planId = plan.id
    const confirmedForm = formVersion.current
    const confirmedRevision = draftNow.current?.revision
    const policy = (plan.payload as { binding?: { quote_minor?: number; currency?: string } }).binding
    confirm({ title: replay ? '核对并继续原提交？' : newIteration ? '再生成一个候选？' : '提交此镜头计划？', content: <div>{replay ? '继续使用原请求标识，系统会先查找原任务。' : '这会新增一次生成，并按当前受信报价预留额度。候选不会自动采用。'}{policy && <p>预留：{policy.quote_minor ?? '未知'} {policy.currency}（最小计价单位）</p>}</div>, onOk: async () => {
      if (!current()) return
      if (planNow.current?.id !== planId || dirtyNow.current || formVersion.current !== confirmedForm || draftNow.current?.revision !== confirmedRevision) { message.warning('计划已切换或修改，请重新核对后确认。'); return }
      setBusy(true); setError('')
      try {
        const storage = `jellyfish:planned-submit:${projectId}:${planId}:${entry.shot_id}`
        const key = replay ? storedKey(entry.shot_id) : crypto.randomUUID()
        if (!key) throw new Error('recovery-key')
        window.localStorage.setItem(storage, key); setKeyRevision(value => value + 1)
        const result = await S.submitPlannedShotApiV1ShotPlansProjectsProjectIdPlansPlanIdShotsShotIdSubmissionsPost({ projectId, planId, shotId: entry.shot_id, requestBody: { request_key: key } })
        if (!current()) return
        await loadPlan(planId)
        const linked = await S.listPlannedShotJobsApiV1ShotPlansProjectsProjectIdPlansPlanIdJobsGet({ projectId, planId })
        if (!current()) return
        const job = linked.data?.find(item => item.job_id === result.data?.job_id)
        if (job) upsertTask({ taskId: job.task_id, title: '镜头计划执行', status: job.state === 'ready' ? 'succeeded' : ['failed', 'cancelled'].includes(job.state) ? job.state as 'failed' | 'cancelled' : job.state === 'queued' || job.state === 'paused' ? 'pending' : 'running', progress: job.state === 'ready' ? 100 : 0, cancelRequested: false, relationType: 'video', relationEntityId: job.shot_id, onNavigate: () => navigate(`/projects/${projectId}/chapters/${chapterId}/studio?plan=${planId}`), onCancel: () => { void cancelJob(projectId, job.job_id) } })
        message.success('已取得持久任务标识，可在任务中心查看'); setTaskCenterOpen(true)
      } catch (e) { if (current()) setError(e instanceof DOMException ? '浏览器无法保存恢复标识，未提交。' : explain(e)) }
      finally { if (current()) setBusy(false) }
    } })
  }

  /** Explicit close confirmation protects saved preparation from accidental loss. */
  function close() {
    if (busy) return
    if (!dirty) onClose()
    else confirm({ title: '放弃尚未保存的计划修改？', content: '已保存草稿和历史计划仍会保留。', onOk: () => { if (current()) { setDirty(false); onClose() } } })
  }

  /** Restrict visible choices to the actual bound characters/scene; server revalidates. */
  function versionAllowed(version: PlanVersionOption, shotId: string) {
    const binding = bindings.find(item => item.shot_id === shotId)
    if (!binding) return true
    return version.kind === 'character' ? !!version.entity_id && (binding.character_ids ?? []).includes(version.entity_id) : version.kind === 'scene' ? version.entity_id === binding.scene_id : ['appearance', 'voice'].includes(version.kind) ? !!version.subject_id && (binding.character_ids ?? []).includes(version.subject_id) : version.kind === 'prop'
  }

  const missing = bindings.flatMap(binding => [...(binding.character_ids ?? []).map(id => ({ kind: 'character' as const, id })), ...(binding.scene_id ? [{ kind: 'scene' as const, id: binding.scene_id }] : [])]).filter((item, index, all) => all.findIndex(other => item.kind === other.kind && item.id === other.id) === index && !catalog?.versions.some(version => version.kind === item.kind && version.entity_id === item.id))
  void keyRevision
  return <Drawer title="镜头执行计划" open={open} onClose={close} width="min(1100px, 96vw)" maskClosable={!busy} keyboard={!busy} destroyOnClose={false}>
    <div data-testid="shot-plan-panel">
      <Alert className="mb-3" type="info" showIcon message="先锁定输入，再逐镜提交" description="此处检查的是剧本、资产和任务准备。无需账号也可保存和编译准备计划；真实生成需要已配置的模型与额度，候选结果仍须单独检查和采用。" />
      {error && <Alert className="mb-3" type="error" showIcon message={error} />}
      <Space wrap className="mb-3">
        <Button loading={loading} disabled={busy || dirty} onClick={() => void load()}>刷新计划与资料</Button>
        <Link to={`/projects/${projectId}?tab=chapters`}>制作剧本与覆盖</Link>
        <Link to={`/projects/${projectId}/chapters/${chapterId}/shots`}>分镜编辑与确认</Link>
        <Button onClick={() => setTaskCenterOpen(true)}>打开任务中心</Button>
      </Space>
      {!loading && !draft && <Empty description="本章尚无镜头计划草稿"><Button type="primary" disabled={busy} onClick={() => void save(emptyDocument())}>新建镜头计划草稿</Button></Empty>}
      {draft && <>
        <Space wrap className="mb-3"><Tag>草稿修订 {draft.revision}</Tag>{dirty && <Tag color="orange">尚未保存</Tag>}<Tag>{catalog?.models.length ? '有受信模型配置' : '模型尚未配置'}</Tag></Space>
        <Card size="small" title="1. 选择已锁定剧本与模型" className="mb-3">
          <label>制作剧本准备快照<Select aria-label="计划制作剧本快照" className="w-full mt-1 mb-3" allowClear disabled={busy || loading} value={document.script_snapshot_id ?? undefined} onChange={chooseScript} options={[...(catalog?.script ? [{ value: catalog.script.id, label: `${catalog.script.title} · 修订${catalog.script.draft_revision}${catalog.script.usable ? '' : '（当前失配）'}` }] : []), ...(document.script_snapshot_id && document.script_snapshot_id !== catalog?.script?.id ? [{ value: document.script_snapshot_id, label: '历史准备快照，请核对当前版本' }] : [])]} /></label>
          {!catalog?.script && <Alert type="warning" message="先在制作剧本页补全覆盖并锁定准备版本。" />}
          <label>计划使用的模型<AutoComplete aria-label="计划模型" className="w-full mt-1" disabled={busy || loading} value={document.model_id} onChange={value => edit({ ...document, model_id: value })} options={(catalog?.models ?? []).map(model => ({ value: model.model_id }))} placeholder="选择已配置模型，或先填写拟使用的模型标识" /></label>
        </Card>
        {!!missing.length && <Card size="small" title="已有角色或场景尚无固定资产版本" className="mb-3"><List size="small" dataSource={missing} renderItem={item => <List.Item actions={[<Button key="register" disabled={busy} onClick={() => void register(item.kind, item.id)}>登记资产版本</Button>]}>{kindNames[item.kind]}：{entityNames.get(item.id) ?? item.id}</List.Item>} /></Card>}
        <Collapse className="mb-3" items={(document.shots ?? []).map((shot, index) => {
          const actual = catalog?.shots.find(item => item.id === shot.shot_id)
          const binding = bindings.find(item => item.shot_id === shot.shot_id)
          /** Replace only one controlled shot while retaining all collapsed fields. */
          const change = (value: Partial<typeof shot>) => edit({ ...document, shots: document.shots!.map((item, i) => i === index ? { ...item, ...value } : item) })
          return { key: shot.shot_id, label: `${actual?.index ?? index + 1}. ${actual?.title ?? shot.shot_id} · ${binding ? (binding.duration_ms / 1000).toFixed(3) + '秒' : '需核对快照映射'} · ${actual?.status === 'ready' ? '信息已确认' : '待编辑页确认'}`, children: <Space direction="vertical" className="w-full">
            <label className="w-full">固定资产版本<Select mode="multiple" aria-label={`镜头${actual?.index ?? index + 1}资产版本`} className="w-full" disabled={busy} value={shot.version_ids} onChange={value => change({ version_ids: value })} options={(catalog?.versions ?? []).filter(version => versionAllowed(version, shot.shot_id)).map(version => ({ value: version.version_id, label: `${kindNames[version.kind] ?? version.kind} · ${version.name} · v${version.revision}` }))} /></label>
            <label className="w-full">额外参考文件<Select mode="multiple" aria-label={`镜头${actual?.index ?? index + 1}参考文件`} className="w-full" disabled={busy} value={shot.reference_media_ids} onChange={value => change({ reference_media_ids: value })} options={(catalog?.media ?? []).map(media => ({ value: media.id, label: `${media.name} · ${media.mime}` }))} /></label>
            <label className="w-full">镜头补充要求<Input.TextArea aria-label={`镜头${actual?.index ?? index + 1}补充要求`} rows={3} maxLength={4000} disabled={busy} value={shot.prompt_addition} onChange={event => change({ prompt_addition: event.target.value })} placeholder="补充动作、构图等要求；原剧情和对白仍会由系统保留" /></label>
          </Space> }
        })} />
        <Space wrap className="mb-3">
          <Button type="primary" disabled={busy || loading} onClick={() => void save()}>保存镜头计划</Button>
          <Button disabled={busy || dirty || !draft.readiness.can_compile} onClick={() => void compile()}>编译并锁定计划</Button>
          <Button disabled={busy || dirty} onClick={() => download({ document: draft.document }, 'shot-plan-inputs.json')}>导出已保存参数</Button>
          <label>导入计划JSON <input aria-label="导入镜头计划JSON" type="file" accept="application/json,.json" disabled={busy || dirty} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importJson(file) }} /></label>
        </Space>
        {dirty && <Alert className="mb-3" type="warning" message="请先保存，再核对检查结果或锁定计划；切换工程或页签前也请保存。" />}
        <Alert className="mb-3" type={draft.readiness.can_compile ? 'success' : 'warning'} message={draft.readiness.can_compile ? '已保存参数的内容检查齐全，可编译计划' : '已保存参数还有准备缺项'} description={draft.readiness.issues.length ? <List size="small" dataSource={draft.readiness.issues} renderItem={issue => <List.Item>{issue.message}</List.Item>} /> : undefined} />
        {!!draft.readiness.runtime_issues.length && <Alert className="mb-3" type="info" message="生成服务条件" description={<List size="small" dataSource={draft.readiness.runtime_issues} renderItem={issue => <List.Item>{issue.message}</List.Item>} />} />}
        {!!draft.plans.length && <Card size="small" title="2. 已锁定计划与镜头任务">
          <Space wrap className="mb-3"><Select aria-label="已锁定镜头计划" style={{ minWidth: 280 }} disabled={busy} value={plan?.id} options={draft.plans.map(item => ({ value: item.id, label: `草稿修订${item.draft_revision} · ${item.content_hash.slice(0, 10)} · ${item.model_bound ? '已固定模型配置' : '未绑定模型'}` }))} onChange={value => { setBusy(true); void loadPlan(value).catch(e => { if (current()) setError(explain(e)) }).finally(() => { if (current()) setBusy(false) }) }} /><Button disabled={!plan || busy} onClick={() => { if (plan) download(plan, 'compiled-shot-plan.json') }}>下载计划JSON</Button><Button disabled={!plan} onClick={() => setJsonOpen(true)}>查看固定输入</Button><Button disabled={!plan || busy} onClick={() => { if (plan) { setBusy(true); void loadPlan(plan.id).catch(e => { if (current()) setError(explain(e)) }).finally(() => { if (current()) setBusy(false) }) } }}>复查与刷新任务</Button></Space>
          {preflight && <Alert className="mb-3" type={preflight.can_submit ? 'success' : 'warning'} message={preflight.can_submit ? '当前可逐镜提交，仍需单独确认' : preflight.usable ? '内容准备可用，生成服务条件未满足' : '当前计划已失配，请修订后重新编译'} description={[...preflight.issues, ...preflight.runtime_issues].map(item => item.message).join('；')} />}
          <Table<Entry> rowKey="shot_id" dataSource={entries} pagination={false} scroll={{ x: 650 }} columns={[
            { title: '镜头', render: (_, entry) => catalog?.shots.find(shot => shot.id === entry.shot_id)?.title ?? entry.shot_id },
            { title: '计划时长', render: (_, entry) => `${entry.duration_ms / 1000}秒` },
            { title: '任务与候选', render: (_, entry) => <Space direction="vertical">{jobs.filter(job => job.shot_id === entry.shot_id).map(job => <div key={job.job_id}><Tag>{stateNames[job.state] ?? job.state}</Tag><span title={job.job_id}>{job.job_id.slice(0, 8)}</span>{job.reason && <div>{job.reason}</div>}{job.media_available && job.media_id && <a href={resolveAssetUrl(`/api/v1/studio/production/projects/${encodeURIComponent(projectId)}/media/${encodeURIComponent(job.media_id)}/content`) ?? undefined} target="_blank" rel="noreferrer"> 查看候选（未采用）</a>}{job.media_available && job.media_id && <Link to={`/projects/${projectId}/editor?chapter=${encodeURIComponent(chapterId)}&plan=${encodeURIComponent(plan?.id ?? "")}&job=${encodeURIComponent(job.job_id)}`}> 去剪辑页确认采用</Link>}</div>)}</Space> },
            { title: '操作', render: (_, entry) => <Space direction="vertical"><Button disabled={busy || dirty || (!preflight?.can_submit && !storedKey(entry.shot_id))} onClick={() => offerSubmission(entry)}>{storedKey(entry.shot_id) ? '核对原提交' : '提交此镜头'}</Button>{jobs.some(job => job.shot_id === entry.shot_id && ['ready', 'failed', 'cancelled'].includes(job.state)) && !jobs.some(job => job.shot_id === entry.shot_id && !['ready', 'failed', 'cancelled'].includes(job.state)) && <Button disabled={busy || dirty || !preflight?.can_submit} onClick={() => offerSubmission(entry, true)}>另生成一个候选</Button>}</Space> },
          ]} />
        </Card>}
      </>}
      <Modal title="固定的镜头执行输入" open={jsonOpen} footer={null} width={900} onCancel={() => setJsonOpen(false)}><pre style={{ maxHeight: '65vh', overflow: 'auto', whiteSpace: 'pre-wrap' }}>{JSON.stringify(plan, null, 2)}</pre></Modal>
    </div>
  </Drawer>
}
