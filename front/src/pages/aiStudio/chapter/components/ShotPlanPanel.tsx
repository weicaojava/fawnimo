import { workspaceStorage } from '../../../../services/workspaceContext'
import { useEffect, useRef, useState } from 'react'
import { Alert, AutoComplete, Button, Card, Collapse, Drawer, Empty, Input, List, Modal, Select, Space, Table, Tag, message } from 'antd'
import { Link, useNavigate } from 'react-router-dom'
import { ExecutionService, ScriptPreparationService, ShotPlansService as S, VideoRuntimeService as V } from '../../../../services/generated'
import type { CompiledPlanRead, PlanCatalog, PlanDocument, PlanPreflight, PlanRead, PlannedJobRead, PlannedSubmission, PlanVersionOption, ScriptCatalog, Seedance25PlanPreview, Seedance25ProfileRead, VideoExecutionPlanPreview, VideoRuntimeSetupRead } from '../../../../services/generated'
import { chargeMoney, readProjectCharge } from '../../project/ProjectChargePicker'
import { resolveAssetUrl } from '../../assets/utils'
import { useTaskUiStore } from '../../components/taskUiStore'

type Props = { projectId: string; chapterId: string; open: boolean; requestedPlanId?: string; onClose: () => void }
type Entry = { shot_id: string; duration_ms: number; entry_hash: string; request: { prompt: string }; [key: string]: unknown }
const kindNames: Record<string, string> = { character: '角色', scene: '场景', appearance: '服装', voice: '声线', prop: '道具' }
const stateNames: Record<string, string> = { queued: '等待执行', submitting: '正在提交', submission_unknown: '等待核对受理', running: '生成中', downloading: '接收结果', ready: '候选已接收', failed: '失败', cancelled: '已取消', paused: '已暂停' }
const SEEDANCE25_MODEL_ID = 'doubao-seedance-2-5-260628'
type NativeIntent = { schema: 1; projectId: string; chapterId: string; planId: string; shotId: string; projectName: string; upperMinor: number; body: PlannedSubmission }

/** Keep an empty saved draft useful even before a publication or account exists. */
function emptyDocument(): PlanDocument { return { schema_version: 1, script_snapshot_id: null, model_id: '', shots: [] } }

/** Recognize the fixed protocol without confusing model identity with execution permission. */
function isSeedance25Plan(value: CompiledPlanRead | null) {
  return (value?.payload as { document?: { model_id?: string } } | undefined)?.document?.model_id === SEEDANCE25_MODEL_ID
}

/** Original unknown-quote preparation plans stay non-executable after host configuration changes. */
function isPreparationOnlySeedancePlan(value: CompiledPlanRead | null) {
  return isSeedance25Plan(value) && (value?.payload as { binding?: { quote_minor?: number | null } })?.binding?.quote_minor == null
}

/** Share the read-only JSON presentation for locked inputs and provider previews. */
function ReadOnlyJson({ value }: { value: unknown }) {
  return <pre style={{ maxHeight: '65vh', overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(value, null, 2)}</pre>
}

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
  const [profile, setProfile] = useState<Seedance25ProfileRead | null>(null)
  const [workspaceSetup, setWorkspaceSetup] = useState<VideoRuntimeSetupRead | null>(null)
  const [profileError, setProfileError] = useState('')
  const [wirePreview, setWirePreview] = useState<Seedance25PlanPreview | VideoExecutionPlanPreview | null>(null)
  const [wireError, setWireError] = useState('')
  const [wireOpen, setWireOpen] = useState(false)
  const [jobs, setJobs] = useState<PlannedJobRead[]>([])
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [jsonOpen, setJsonOpen] = useState(false)
  const [keyRevision, setKeyRevision] = useState(0)
  const [nativeIntent, setNativeIntent] = useState<NativeIntent | null>(null)
  const nativeOperation = useRef(false)
  const nativeIntentKey = `jellyfish:native-planned-intent:${projectId}:${chapterId}`
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
  const seedanceSelected = document.model_id === SEEDANCE25_MODEL_ID
  const seedancePlan = isSeedance25Plan(plan)
  const preparationOnlyPlan = isPreparationOnlySeedancePlan(plan)
  const currentPreview = plan && !dirty && wirePreview?.plan_id === plan.id && wirePreview.content_hash === plan.content_hash ? wirePreview : null
  const previewIssues = currentPreview ? [...currentPreview.issues, ...currentPreview.entries.flatMap(entry => entry.issues)] : []

  /** Attach a concrete shot name to advisory checks while preserving server messages. */
  function issueLabel(issue: { message: string; shot_id?: string | null }) {
    const shot = catalog?.shots.find(item => item.id === issue.shot_id)
    return `${issue.shot_id ? `${shot?.title ?? issue.shot_id}：` : ''}${issue.message}`
  }

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
    const observedForm = formVersion.current
    setPlan(null); setPreflight(null); setJobs([]); setWirePreview(null); setWireError(''); setWireOpen(false)
    const [saved, check, linked] = await Promise.all([
      S.getShotPlanApiV1ShotPlansProjectsProjectIdPlansPlanIdGet({ projectId, planId }),
      S.preflightShotPlanApiV1ShotPlansProjectsProjectIdPlansPlanIdPreflightGet({ projectId, planId }),
      S.listPlannedShotJobsApiV1ShotPlansProjectsProjectIdPlansPlanIdJobsGet({ projectId, planId }),
    ])
    const preview = !isSeedance25Plan(saved.data ?? null) ? { data: null, error: '' }
      : isPreparationOnlySeedancePlan(saved.data ?? null)
        ? await S.previewSeedance25Plan({ projectId, planId }).then(result => ({ data: result.data ?? null, error: '' }), failure => ({ data: null, error: explain(failure) }))
        : await V.previewExecutableVideoPlan({ projectId, planId }).then(result => ({ data: result.data ?? null, error: '' }), failure => ({ data: null, error: explain(failure) }))
    if (!current() || sequence !== request.current || formVersion.current !== observedForm) return
    setPlan(saved.data ?? null); setPreflight(check.data ?? null); setJobs(linked.data ?? [])
    if (preview.data && (preview.data.plan_id !== saved.data?.id || preview.data.content_hash !== saved.data?.content_hash)) setWireError('预览与当前计划不一致，请复查后重试。')
    else { setWirePreview(preview.data); setWireError(preview.error || (isSeedance25Plan(saved.data ?? null) && !preview.data ? '未取得请求预览，请复查后重试。' : '')) }
  }

  /** Refresh saved preparation with an identity fence, without reusing demo data. */
  async function load() {
    if (!current()) return
    const sequence = ++request.current
    const observedForm = formVersion.current
    setLoading(true); setError(''); setWirePreview(null); setWireError(''); setWireOpen(false)
    try {
      const [options, saved, names, prepared, configured] = await Promise.all([
        S.getShotPlanCatalogApiV1ShotPlansProjectsProjectIdCatalogGet({ projectId, chapterId }),
        S.listShotPlanDraftsApiV1ShotPlansProjectsProjectIdDraftsGet({ projectId, chapterId }),
        ScriptPreparationService.getScriptCatalogApiV1ScriptPreparationProjectsProjectIdCatalogGet({ projectId }),
        S.getSeedance25Profile({ projectId }).then(result => ({ data: result.data ?? null, error: '' }), failure => ({ data: null, error: explain(failure) })),
        V.getVideoRuntimeSetup().then(result => result.data ?? null, () => null),
      ])
      if (!current() || sequence !== request.current || formVersion.current !== observedForm) return
      const record = saved.data?.[0] ?? null
      setCatalog(options.data ?? null); setEntities(names.data ?? { characters: [], scenes: [] }); setDraft(record)
      setProfile(prepared.data); setProfileError(prepared.error || (prepared.data ? '' : '准备档案暂不可用，请稍后刷新。'))
      setWorkspaceSetup(configured)
      formVersion.current++; setDocument(record?.document ?? emptyDocument()); setDirty(false); setPlan(null); setPreflight(null); setJobs([]); setWirePreview(null); setWireError(''); setWireOpen(false)
      if (requestedPlanId && record?.plans.some(item => item.id === requestedPlanId)) await loadPlan(requestedPlanId)
      else if (requestedPlanId) setError('回跳的计划不属于本章历史，请核对工程与章节。')
      else if (record?.plans.length) await loadPlan(record.plans[0].id)
    } catch (e) { if (current()) setError(explain(e)) }
    finally { if (current()) setLoading(false) }
  }

  useEffect(() => {
    setBusy(false); setDraft(null); setPlan(null); setPreflight(null); setJobs([]); setCatalog(null); setDirty(false); setJsonOpen(false); setProfile(null); setProfileError(''); setWirePreview(null); setWireError(''); setWireOpen(false)
    if (open) void load()
    else { request.current++; confirmation.current?.destroy(); setBusy(false); setLoading(false) }
    return () => { request.current++; confirmation.current?.destroy() }
  }, [projectId, chapterId, open])

  useEffect(() => {
    nativeOperation.current = false; setNativeIntent(null)
    if (!open) return
    try {
      const saved = workspaceStorage.getItem(nativeIntentKey)
      if (saved) {
        const value = JSON.parse(saved) as NativeIntent
        if (value.schema !== 1 || value.projectId !== projectId || value.chapterId !== chapterId || !value.planId || !value.shotId || !value.body?.request_key || value.body.confirm_project_charges !== true || !/^[a-f0-9]{64}$/.test(value.body.expected_price_fingerprint ?? '') || !Number.isSafeInteger(value.upperMinor) || value.upperMinor <= 0) throw new Error('Invalid native intent')
        setNativeIntent(value)
      }
    } catch { setError('无法读取原视频提交记录，请先在任务历史核查是否已经建立任务。') }
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
  function edit(value: PlanDocument) { formVersion.current++; dirtyNow.current = true; setDocument(value); setDirty(true); setWirePreview(null); setWireError(''); setWireOpen(false) }

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
      setPreflight(null); setWirePreview(null); setWireError(''); setWireOpen(false); message.success('镜头计划已保存，尚未提交生成')
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
    if (isSeedance25Plan(plan)) return ''
    try { return workspaceStorage.getItem(`jellyfish:planned-submit:${projectId}:${plan?.id}:${shotId}`) ?? '' } catch { return '' }
  }

  /** Send only the frozen original project/path/body, independently of edits, current prices or current plan selection. */
  async function sendNativeIntent(intent: NativeIntent) {
    if (!current() || nativeOperation.current || intent.projectId !== projectId || intent.chapterId !== chapterId) return
    nativeOperation.current = true; setBusy(true); setError('')
    try {
      workspaceStorage.setItem(nativeIntentKey, JSON.stringify(intent)); setNativeIntent(intent)
      const result = await S.submitPlannedShotApiV1ShotPlansProjectsProjectIdPlansPlanIdShotsShotIdSubmissionsPost({ projectId: intent.projectId, planId: intent.planId, shotId: intent.shotId, requestBody: intent.body })
      if (!current() || !result.data?.job_id) return
      try { workspaceStorage.removeItem(nativeIntentKey); setNativeIntent(null) } catch { setError('任务已保存，本地记录尚未清除；再次核对仍返回原任务。') }
      const linked = await S.listPlannedShotJobsApiV1ShotPlansProjectsProjectIdPlansPlanIdJobsGet({ projectId: intent.projectId, planId: intent.planId })
      if (!current()) return
      const job = linked.data?.find(item => item.job_id === result.data?.job_id)
      if (planNow.current?.id === intent.planId) setJobs(linked.data ?? [])
      if (job) upsertTask({ taskId: job.task_id, title: '镜头计划执行', status: job.state === 'ready' ? 'succeeded' : ['failed', 'cancelled'].includes(job.state) ? job.state as 'failed' | 'cancelled' : ['queued', 'paused'].includes(job.state) ? 'pending' : 'running', progress: job.state === 'ready' ? 100 : 0, cancelRequested: false, relationType: 'video', relationEntityId: job.shot_id, onNavigate: () => navigate(`/projects/${intent.projectId}/chapters/${intent.chapterId}/studio?plan=${intent.planId}`), onCancel: () => { void cancelJob(intent.projectId, job.job_id) } })
      message.success('已取得原视频任务与项目费用预留，可在任务中心查看'); setTaskCenterOpen(true)
    } catch (cause) {
      if (current()) {
        const status = (cause as { status?: number }).status
        if (status === 409 || status === 422) { try { workspaceStorage.removeItem(nativeIntentKey); setNativeIntent(null) } catch { /* Keep the exact intent if browser persistence is unavailable. */ } }
        setError(cause instanceof DOMException ? '浏览器无法保存原提交记录，未发送。' : explain(cause))
      }
    } finally { if (current()) { nativeOperation.current = false; setBusy(false) } }
  }

  /** Confirm one fixed-project native call using its fresh full quote and exact balance, then freeze the recovery body. */
  async function offerNativeSubmission(entry: Entry, newIteration: boolean) {
    if (!current() || !plan || nativeOperation.current || nativeIntent || busy || dirty || !preflight?.can_submit || !currentPreview?.can_prepare) return
    nativeOperation.current = true; setBusy(true); setError('')
    const planId = plan.id, form = formVersion.current, revision = draftNow.current?.revision
    const originalPolicy = (plan.payload as { binding?: { quote_minor?: number } }).binding
    try {
      const setup = (await V.getVideoRuntimeSetup()).data
      if (!current()) return
      setWorkspaceSetup(setup ?? null)
      if (!setup?.running || !setup.managed_model_id || !setup.price_fingerprint || !setup.quote_minor || setup.quote_minor !== originalPolicy?.quote_minor) { setError('当前视频模型或费用与原计划不符，请刷新配置并重新编译计划。'); nativeOperation.current = false; setBusy(false); return }
      const approval = await readProjectCharge(projectId, setup.quote_minor)
      if (!current()) return
      if (!approval.ready) { setError(approval.reason ?? '本项目费用尚未核对'); nativeOperation.current = false; setBusy(false); return }
      if (planNow.current?.id !== planId || dirtyNow.current || formVersion.current !== form || draftNow.current?.revision !== revision) { nativeOperation.current = false; setBusy(false); return }
      const intent: NativeIntent = { schema: 1, projectId, chapterId, planId, shotId: entry.shot_id, projectName: approval.projectName, upperMinor: setup.quote_minor,
        body: { request_key: crypto.randomUUID(), confirm_project_charges: true, expected_price_fingerprint: setup.price_fingerprint } }
      confirm({ title: newIteration ? '重新生成一个视频候选？' : '确认本项目的视频费用？', content: <div><p>费用项目：{approval.projectName}</p><p>本次完整预留：{chargeMoney(intent.upperMinor)}</p><p>核对账单后的可用额度：{chargeMoney(approval.budget.available_after_bills_minor ?? String(approval.budget.available_minor))}</p><p>{newIteration ? '这会创建新任务并新增一次预留，原任务的未知费用仍保留。' : '只使用此项目的预算。候选接收成功后仍需核对实际账单与结果。'}</p></div>,
        onCancel: () => { confirmation.current = null; if (current()) { nativeOperation.current = false; setBusy(false) } },
        onOk: async () => {
          confirmation.current = null
          if (!current()) return
          nativeOperation.current = false; setBusy(false)
          if (planNow.current?.id !== planId || dirtyNow.current || formVersion.current !== form || draftNow.current?.revision !== revision) { message.warning('计划已修改，请重新核对费用。'); return }
          await sendNativeIntent(intent)
        } })
    } catch (cause) { if (current()) { setError(explain(cause)); nativeOperation.current = false; setBusy(false) } }
  }

  /** Confirm each new generation intent; recovery keeps the same durable identity. */
  function offerSubmission(entry: Entry, newIteration = false) {
    if (isSeedance25Plan(plan)) { void offerNativeSubmission(entry, newIteration); return }
    if (!current() || !plan || busy || dirty || isPreparationOnlySeedancePlan(plan)) return
    const replay = !newIteration && !!storedKey(entry.shot_id)
    if (!replay && (!preflight?.can_submit || (isSeedance25Plan(plan) && !currentPreview?.can_prepare))) return
    const planId = plan.id
    const confirmedForm = formVersion.current
    const confirmedRevision = draftNow.current?.revision
    const policy = (plan.payload as { binding?: { quote_minor?: number; currency?: string } }).binding
    confirm({ title: replay ? '核对并继续原提交？' : newIteration ? '再生成一个候选？' : '提交此镜头计划？', content: <div>{replay ? '继续使用原请求标识，系统会先查找原任务。' : '这会新增一次生成，并按当前受信报价预留额度。候选不会自动采用。'}{policy && <p>预留：{policy.quote_minor == null ? '未知' : policy.currency === 'CNY' ? `${(policy.quote_minor / 100).toFixed(2)}元` : `${policy.quote_minor} ${policy.currency}（最小计价单位）`}</p>}</div>, onOk: async () => {
      if (!current()) return
      if (planNow.current?.id !== planId || dirtyNow.current || formVersion.current !== confirmedForm || draftNow.current?.revision !== confirmedRevision) { message.warning('计划已切换或修改，请重新核对后确认。'); return }
      setBusy(true); setError('')
      try {
        const storage = `jellyfish:planned-submit:${projectId}:${planId}:${entry.shot_id}`
        const key = replay ? storedKey(entry.shot_id) : crypto.randomUUID()
        if (!key) throw new Error('recovery-key')
        workspaceStorage.setItem(storage, key); setKeyRevision(value => value + 1)
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
    <div className="production-plan-panel" data-testid="shot-plan-panel">
      <Alert className="mb-3" type="info" showIcon message={preparationOnlyPlan || (seedanceSelected && !workspaceSetup?.running) ? '先锁定输入，再检查请求预览' : '先锁定输入，再逐镜提交'} description="此处检查的是剧本、资产和任务准备。无需账号也可保存和编译准备计划；实际提交须有独立的服务配置与额度，候选结果仍须单独检查和采用，不代表模型质量已通过。" />
      {error && <Alert className="mb-3" type="error" showIcon message={error} />}
      {nativeIntent && <Alert className="mb-3" type="warning" showIcon message="上次视频提交结果尚未核对" description={`核对「${nativeIntent.projectName}」原来确认的 ${chargeMoney(nativeIntent.upperMinor)} 完整预留。使用原计划、原模型与原请求；已经接收时返回原任务。当前编辑内容保留。`} action={<Button disabled={busy} onClick={() => void sendNativeIntent(nativeIntent)}>核对上次视频提交</Button>} />}
      <Space wrap className="mb-3">
        <Button loading={loading} disabled={busy || dirty} onClick={() => void load()}>刷新计划与资料</Button>
        <Link to={`/projects/${projectId}?tab=chapters`}>制作剧本与覆盖</Link>
        <Link to={`/projects/${projectId}/chapters/${chapterId}/shots`}>分镜编辑与确认</Link>
        <Button onClick={() => setTaskCenterOpen(true)}>打开任务中心</Button>
      </Space>
      {workspaceSetup?.running && !preparationOnlyPlan && <Alert className="mb-3" type="success" showIcon message="本机生成服务已启用，逐镜提交仍需明确确认" description={`${workspaceSetup.output_description} 单次预留：${workspaceSetup.quote_minor == null ? '未知' : (workspaceSetup.quote_minor / 100).toFixed(2) + '元'}。模型质量尚未验收；旧准备计划不会自动获得提交资格。`} />}
      {profile && (!workspaceSetup?.running || preparationOnlyPlan) && <Card size="small" title="Seedance 2.5 / 准备档案" className="mb-3" data-testid="seedance25-profile">
        <Space wrap className="mb-2"><Tag>仅文生视频</Tag><Tag>生成未启用</Tag><Tag>模型资格未验证</Tag><Tag>报价未知</Tag></Space>
        <p className="mb-2">{profile.model_id} / {profile.min_duration ?? 4} 至 {profile.max_duration ?? 30} 整数秒 / {profile.ratio ?? '9:16'} / {profile.resolution ?? '480p'} / {(profile.output_format ?? 'mp4').toUpperCase()} / {profile.generate_audio ? '生成音频开启' : '生成音频关闭'} / {(profile.watermark ?? true) ? '带水印' : '无水印'}</p>
        <Alert type="info" showIcon message="当前仅准备和检查输入，付费生成尚未启用。" description="此模式不支持首尾帧、图片、视频或音频参考。选定资产版本自带媒体也会作为参考阻断检查，系统不会删除或转换这些素材。连接或任务列表读取成功不能证明模型生成资格。" />
        <p className="mt-2 mb-0 text-xs text-gray-500">本片不支持：{profile.unsupported_modes.join('、')}</p>
        <Space wrap className="mt-2"><span className="text-xs text-gray-500" title={profile.profile_hash}>准备档案 {profile.profile_hash.slice(0, 12)}</span><Button size="small" disabled={!draft || busy || loading || seedanceSelected} onClick={() => edit({ ...document, model_id: profile.model_id })}>使用此准备模型</Button></Space>
      </Card>}
      {profileError && <Alert className="mb-3" type="warning" message="Seedance 2.5 准备档案暂不可用" description={profileError} />}
      {!loading && !draft && <Empty description="本章尚无镜头计划草稿"><Button type="primary" disabled={busy} onClick={() => void save(emptyDocument())}>新建镜头计划草稿</Button></Empty>}
      {draft && <>
        <Space wrap className="mb-3"><Tag>草稿修订 {draft.revision}</Tag>{dirty && <Tag color="orange">尚未保存</Tag>}<Tag>{catalog?.models.length ? '有受信准备配置' : '模型尚未配置'}</Tag></Space>
        <Card size="small" title="1. 选择已锁定剧本与模型" className="mb-3">
          <label>制作剧本准备快照<Select aria-label="计划制作剧本快照" className="w-full mt-1 mb-3" allowClear disabled={busy || loading} value={document.script_snapshot_id ?? undefined} onChange={chooseScript} options={[...(catalog?.script ? [{ value: catalog.script.id, label: `${catalog.script.title} / 修订${catalog.script.draft_revision}${catalog.script.usable ? '' : '（当前失配）'}` }] : []), ...(document.script_snapshot_id && document.script_snapshot_id !== catalog?.script?.id ? [{ value: document.script_snapshot_id, label: '历史准备快照，请核对当前版本' }] : [])]} /></label>
          {!catalog?.script && <Alert type="warning" message="先在制作剧本页补全覆盖并锁定准备版本。" />}
          <label>计划使用的模型<AutoComplete aria-label="计划模型" className="w-full mt-1" disabled={busy || loading} value={document.model_id} onChange={value => edit({ ...document, model_id: value })} options={(catalog?.models ?? []).map(model => ({ value: model.model_id }))} placeholder="选择已配置模型，或先填写拟使用的模型标识" /></label>
        </Card>
        {!!missing.length && <Card size="small" title="已有角色或场景尚无固定资产版本" className="mb-3"><List size="small" dataSource={missing} renderItem={item => <List.Item actions={[<Button key="register" disabled={busy} onClick={() => void register(item.kind, item.id)}>登记资产版本</Button>]}>{kindNames[item.kind]}：{entityNames.get(item.id) ?? item.id}</List.Item>} /></Card>}
        <Collapse className="mb-3" items={(document.shots ?? []).map((shot, index) => {
          const actual = catalog?.shots.find(item => item.id === shot.shot_id)
          const binding = bindings.find(item => item.shot_id === shot.shot_id)
          // Draft hints include version-owned media; only the server certifies saved inputs.
          const versionHasMedia = catalog?.versions.some(version => shot.version_ids?.includes(version.version_id) && version.media_count > 0)
          const referenceBlocked = seedanceSelected && (!!shot.reference_media_ids?.length || !!versionHasMedia)
          const seconds = binding ? binding.duration_ms / 1000 : null
          const durationBlocked = seedanceSelected && seconds !== null && (!Number.isInteger(seconds) || seconds < (profile?.min_duration ?? 4) || seconds > (profile?.max_duration ?? 30))
          /** Replace only one controlled shot while retaining all collapsed fields. */
          const change = (value: Partial<typeof shot>) => edit({ ...document, shots: document.shots!.map((item, i) => i === index ? { ...item, ...value } : item) })
          return { key: shot.shot_id, label: `${actual?.index ?? index + 1}. ${actual?.title ?? shot.shot_id} / ${binding ? (binding.duration_ms / 1000).toFixed(3) + '秒' : '需核对快照映射'} / ${actual?.status === 'ready' ? '信息已确认' : '待编辑页确认'}`, children: <Space direction="vertical" className="w-full">
            {durationBlocked && <Alert type="warning" message="Seedance 2.5 本片只支持 4–30 整数秒" description="请回制作剧本修订镜头时长并发布新快照；系统不会自动取整或裁切内容。" />}
            {referenceBlocked && <Alert type="warning" message="本片不支持此镜头的参考素材" description="选定资产版本自带媒体或额外参考文件均会阻断文生视频检查。请核对版本和参考选择；当前素材已保留，不会自动删除、转换或忽略。" />}
            <label className="w-full">固定资产版本<Select mode="multiple" aria-label={`镜头${actual?.index ?? index + 1}资产版本`} className="w-full" disabled={busy || loading} value={shot.version_ids} onChange={value => change({ version_ids: value })} options={(catalog?.versions ?? []).filter(version => versionAllowed(version, shot.shot_id)).map(version => ({ value: version.version_id, label: `${kindNames[version.kind] ?? version.kind} / ${version.name} / v${version.revision}` }))} /></label>
            <label className="w-full">额外参考文件<Select mode="multiple" aria-label={`镜头${actual?.index ?? index + 1}参考文件`} className="w-full" disabled={busy || loading} value={shot.reference_media_ids} onChange={value => change({ reference_media_ids: value })} options={(catalog?.media ?? []).map(media => ({ value: media.id, label: `${media.name} / ${media.mime}` }))} /></label>
            <label className="w-full">镜头补充要求<Input.TextArea aria-label={`镜头${actual?.index ?? index + 1}补充要求`} rows={3} maxLength={4000} disabled={busy || loading} value={shot.prompt_addition} onChange={event => change({ prompt_addition: event.target.value })} placeholder="补充动作、构图等要求；原剧情和对白仍会由系统保留" /></label>
          </Space> }
        })} />
        <Space wrap className="mb-3">
          <Button type="primary" disabled={busy || loading} onClick={() => void save()}>保存镜头计划</Button>
          <Button disabled={busy || dirty || !draft.readiness.can_compile} onClick={() => void compile()}>编译并锁定计划</Button>
          <Button disabled={busy || dirty} onClick={() => download({ document: draft.document }, 'shot-plan-inputs.json')}>导出已保存参数</Button>
          <label>导入计划JSON <input aria-label="导入镜头计划JSON" type="file" accept="application/json,.json" disabled={busy || dirty} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importJson(file) }} /></label>
        </Space>
        {dirty && <Alert className="mb-3" type="warning" message="请先保存，再核对检查结果或锁定计划；切换工程或页签前也请保存。" />}
        <Alert className="mb-3" type={draft.readiness.can_compile ? 'success' : 'warning'} message={draft.readiness.can_compile ? '已保存参数的内容检查齐全，可编译准备计划' : '已保存参数还有准备缺项'} description={draft.readiness.issues.length ? <List size="small" dataSource={draft.readiness.issues} renderItem={issue => <List.Item>{issueLabel(issue)}</List.Item>} /> : undefined} />
        {!!draft.readiness.runtime_issues.length && <Alert className="mb-3" type="info" message="生成服务条件（与输入准备分开检查）" description={<List size="small" dataSource={draft.readiness.runtime_issues} renderItem={issue => <List.Item>{issueLabel(issue)}</List.Item>} />} />}
        {!!draft.plans.length && <Card size="small" title="2. 已锁定计划与镜头任务">
          <Space wrap className="mb-3"><Select aria-label="已锁定镜头计划" style={{ minWidth: 280 }} disabled={busy} value={plan?.id} options={draft.plans.map(item => ({ value: item.id, label: `草稿修订${item.draft_revision} / ${item.content_hash.slice(0, 10)} / ${item.model_bound ? '已固定模型配置' : '未绑定模型'}` }))} onChange={value => { setBusy(true); void loadPlan(value).catch(e => { if (current()) setError(explain(e)) }).finally(() => { if (current()) setBusy(false) }) }} /><Button disabled={!plan || busy} onClick={() => { if (plan) download(plan, 'compiled-shot-plan.json') }}>下载计划JSON</Button><Button disabled={!plan} onClick={() => setJsonOpen(true)}>查看固定输入</Button><Button disabled={!plan || busy} onClick={() => { if (plan) { setBusy(true); void loadPlan(plan.id).catch(e => { if (current()) setError(explain(e)) }).finally(() => { if (current()) setBusy(false) }) } }}>复查与刷新任务</Button></Space>
          {preflight && <Alert className="mb-3" type={preparationOnlyPlan && preflight.usable ? 'info' : preflight.can_submit ? 'success' : 'warning'} message={!preflight.usable ? '当前计划已失配，请修订后重新编译' : preparationOnlyPlan ? '内容准备可用，生成未启用' : preflight.can_submit ? '当前可逐镜提交，仍需单独确认' : '内容准备可用，生成服务条件未满足'} description={[...preflight.issues, ...preflight.runtime_issues].map(issueLabel).join('；')} />}
          {wireError && <Alert className="mb-3" type="warning" message="未取得 Seedance 2.5 实际请求预览" description={wireError} />}
          {currentPreview && <Card size="small" title="Seedance 2.5 / 实际请求预览" className="mb-3" data-testid="seedance25-preview-summary">
            <Alert type={currentPreview.can_prepare ? 'success' : 'warning'} className="mb-2" message={currentPreview.can_prepare ? '本模式的软件输入检查通过；尚未提交生成' : '本计划不能形成当前配置的请求'} description={previewIssues.length ? <List size="small" dataSource={previewIssues} renderItem={issue => <List.Item>{issueLabel(issue)}</List.Item>} /> : preparationOnlyPlan ? '生成未启用，模型资格未验证。此预览和下载仅供检查，不会创建任务或预留费用。' : '此预览不会创建任务、预留费用或授予模型质量资格；提交条件由服务器独立校验。'} />
            <Space wrap><Button disabled={busy || dirty} onClick={() => setWireOpen(true)}>查看实际请求预览</Button><Button disabled={busy || dirty} onClick={() => download(currentPreview, 'seedance25-preparation-preview.json')}>下载准备预览JSON</Button><span className="text-xs text-gray-500" title={currentPreview.content_hash}>计划 {currentPreview.content_hash.slice(0, 12)}</span>{currentPreview.wire_hash && <span className="text-xs text-gray-500" title={currentPreview.wire_hash}>请求 {currentPreview.wire_hash.slice(0, 12)}</span>}</Space>
          </Card>}
          <Table<Entry> rowKey="shot_id" dataSource={entries} pagination={false} scroll={{ x: 650 }} columns={[
            { title: '镜头', render: (_, entry) => catalog?.shots.find(shot => shot.id === entry.shot_id)?.title ?? entry.shot_id },
            { title: '计划时长', render: (_, entry) => `${entry.duration_ms / 1000}秒` },
            { title: '任务与候选', render: (_, entry) => <Space direction="vertical">{jobs.filter(job => job.shot_id === entry.shot_id).map(job => <div key={job.job_id}><Tag>{stateNames[job.state] ?? job.state}</Tag><span title={job.job_id}>{job.job_id.slice(0, 8)}</span>{job.reason && <div>{job.reason}</div>}{job.media_available && job.media_id && <a href={resolveAssetUrl(`/api/v1/studio/production/projects/${encodeURIComponent(projectId)}/media/${encodeURIComponent(job.media_id)}/content`) ?? undefined} target="_blank" rel="noreferrer"> 查看候选（未采用）</a>}{job.media_available && job.media_id && <Link to={`/projects/${projectId}/editor?chapter=${encodeURIComponent(chapterId)}&plan=${encodeURIComponent(plan?.id ?? "")}&job=${encodeURIComponent(job.job_id)}`}> 去剪辑页确认采用</Link>}</div>)}</Space> },
            { title: '操作', render: (_, entry) => <Space direction="vertical"><Button disabled={preparationOnlyPlan || busy || dirty || !!nativeIntent || (!storedKey(entry.shot_id) && (!preflight?.can_submit || (seedancePlan && !currentPreview?.can_prepare)))} onClick={() => offerSubmission(entry)}>{storedKey(entry.shot_id) ? '核对原提交' : '提交此镜头'}</Button>{preparationOnlyPlan && <span className="text-xs text-gray-500">生成未启用；可查看准备预览</span>}{jobs.some(job => job.shot_id === entry.shot_id && ['ready', 'failed', 'cancelled'].includes(job.state)) && !jobs.some(job => job.shot_id === entry.shot_id && !['ready', 'failed', 'cancelled'].includes(job.state)) && <Button disabled={preparationOnlyPlan || busy || dirty || !!nativeIntent || !preflight?.can_submit || (seedancePlan && !currentPreview?.can_prepare)} onClick={() => offerSubmission(entry, true)}>另生成一个候选</Button>}</Space> },
          ]} />
        </Card>}
      </>}
      <Modal title="固定的镜头执行输入" open={jsonOpen} footer={null} width={900} onCancel={() => setJsonOpen(false)}><ReadOnlyJson value={plan} /></Modal>
      <Modal title={preparationOnlyPlan ? 'Seedance 2.5 实际请求预览（仅准备）' : 'Seedance 2.5 实际请求预览（只读）'} open={wireOpen && !!currentPreview} footer={null} width={900} onCancel={() => setWireOpen(false)}>
        <Alert type="info" className="mb-3" message="只读预览，尚未提交生成" description={preparationOnlyPlan ? '以下内容来自当前不可变计划；模型资格未验证，生成未启用。下载 JSON 不会执行请求。' : '以下内容来自当前不可变计划与受信本机配置；下载不提交请求，不授予模型质量资格。'} />
        {currentPreview && !!currentPreview.issues.length && <Alert type="warning" className="mb-3" message="当前计划预览已阻断" description={<List size="small" dataSource={currentPreview.issues} renderItem={issue => <List.Item>{issueLabel(issue)}</List.Item>} />} />}
        {currentPreview?.entries.map(entry => <Card size="small" className="mb-3" key={entry.shot_id} title={catalog?.shots.find(shot => shot.id === entry.shot_id)?.title ?? entry.shot_id}>
          <p className="text-xs text-gray-500 mb-2" title={entry.entry_hash}>镜头 {entry.shot_id} / 输入 {entry.entry_hash.slice(0, 12)}</p>
          {!!entry.issues.length && <Alert type="warning" className="mb-2" message="本镜头请求已阻断" description={<List size="small" dataSource={entry.issues} renderItem={issue => <List.Item>{issueLabel(issue)}</List.Item>} />} />}
          {entry.body ? <ReadOnlyJson value={entry.body} /> : <p>没有可预览的请求。请按检查结果修订并保存，再编译新计划。</p>}
        </Card>)}
      </Modal>
    </div>
  </Drawer>
}
