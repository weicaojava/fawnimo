import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Collapse, Empty, Select, Space, Tag, Typography, message, Modal } from 'antd'
import { ScriptCandidatesService } from '../../../../services/generated'
import type { ScriptCandidateAvailability, ScriptCandidateCreate, ScriptCandidateRead } from '../../../../services/generated'
import { workspaceRequestScope, workspaceStorage } from '../../../../services/workspaceContext'

type Operation = ScriptCandidateCreate['operation']
const titles: Record<Operation, string> = { optimize: '优化剧本', simplify: '精简剧本', divide: '提取分镜', extract_shot: '提取资产和对白' }
const statuses: Record<string, string> = { queued: '待处理', running: '生成中', succeeded: '候选已返回', failed: '失败', cancelled: '已取消' }
type Intent = { projectId: string; chapterId: string; requestBody: ScriptCandidateCreate }
type PageIntent = { scope: string; identity: string; epoch: number }

/** Format exact integer minor units without losing precision on large project limits. */
function money(value: string): string {
  try { const amount = BigInt(value); return `${amount / 100n}.${String(amount % 100n).padStart(2, '0')}` } catch { return '待核对' }
}

/** Keep network failures actionable without showing raw provider or database diagnostics. */
function errorText(error: unknown, fallback: string): string {
  const body = (error as { body?: { message?: unknown; detail?: unknown } })?.body
  return typeof body?.message === 'string' ? body.message : typeof body?.detail === 'string' ? body.detail : fallback
}

/** Show useful proposal content instead of exposing the underlying model JSON protocol. */
function ResultPreview({ row }: { row: ScriptCandidateRead }) {
  const result = row.result
  if (!result) return <Typography.Text type="secondary">{row.message}</Typography.Text>
  if ('text' in result) return <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', maxHeight: 280, overflow: 'auto' }}>{result.text}</Typography.Paragraph>
  if ('shots' in result) return <ol className="space-y-3 pl-5">{result.shots.map((shot, index) => <li key={index}><strong>{shot.title}</strong><p className="whitespace-pre-wrap">{shot.script_excerpt}</p></li>)}</ol>
  return <div className="space-y-2">
    {([['角色', result.characters], ['场景', result.scenes], ['道具', result.props], ['服装', result.costumes]] as const).map(([label, values]) => <div key={label}><strong>{label}：</strong>{values?.length ? values.map(item => `${item.name}${item.description ? `（${item.description}）` : ''}`).join('；') : '未提取'}</div>)}
    <strong>对白</strong>{result.dialogues?.length ? result.dialogues.map((line, index) => <p key={index}>{line.speaker_name || '未标注人物'}：{line.text}</p>) : <p>未提取</p>}
  </div>
}

/** Authorize one project-billed saved-source proposal, then require a separate human adoption. */
export function ScriptCandidatePanel({ projectId, chapterId, shotId, preferredOperation, focusCandidateId, hasUnsavedChanges = false, onAdopted, onBusyChange }: {
  projectId: string; chapterId: string; shotId?: string; hasUnsavedChanges?: boolean
  preferredOperation?: 'optimize' | 'simplify' | 'divide'
  focusCandidateId?: string
  onAdopted?: (row: ScriptCandidateRead, isCurrent: () => boolean) => void | Promise<void>
  onBusyChange?: (busy: boolean) => void
}) {
  const identity = workspaceRequestScope()
  const scope = `${identity}${projectId}:${chapterId}:${shotId || 'chapter'}`
  const storageKey = `fawnimo:script-intent:${projectId}:${chapterId}:${shotId || 'chapter'}`
  const [operation, setOperation] = useState<Operation>(shotId ? 'extract_shot' : 'optimize')
  const [sourceField, setSourceField] = useState<'raw_text' | 'condensed_text'>('raw_text')
  const [modelId, setModelId] = useState<string>()
  const [availability, setAvailability] = useState<ScriptCandidateAvailability | null>(null)
  const [rows, setRows] = useState<ScriptCandidateRead[]>([])
  const [error, setError] = useState('')
  const [quoteError, setQuoteError] = useState('')
  const [busy, setBusy] = useState(false)
  const [intent, setIntent] = useState<Intent | null>(null)
  const [opened, setOpened] = useState<string[]>([])
  const consumedFocus = useRef<string>()
  const live = useRef({ scope, dirty: hasUnsavedChanges, identity })
  live.current = { scope, dirty: hasUnsavedChanges, identity }
  const mounted = useRef(false)
  const epoch = useRef(0)
  const renderedScope = useRef(scope)
  // Invalidate old work during render as well as cleanup: A → B → A is not
  // the same visible intent, even when its path and identity strings return.
  if (renderedScope.current !== scope) { renderedScope.current = scope; epoch.current++ }
  const inFlight = useRef(false)
  const requestVersion = useRef(0)
  const busyListener = useRef(onBusyChange); busyListener.current = onBusyChange
  const capture = useCallback((): PageIntent => ({ scope: live.current.scope, identity: live.current.identity, epoch: epoch.current }), [])
  const isCurrent = useCallback((value: PageIntent) => mounted.current && epoch.current === value.epoch &&
    live.current.scope === value.scope && workspaceRequestScope() === value.identity, [])

  const refresh = useCallback(async () => {
    const captured = capture(); const request = ++requestVersion.current
    if (!isCurrent(captured)) return
    const values = await Promise.allSettled([
      ScriptCandidatesService.listScriptCandidatesApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesGet({ projectId, chapterId }),
      ScriptCandidatesService.candidateAvailabilityApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesAvailabilityGet({ projectId, chapterId, operation, modelId, shotId, sourceField: operation === 'divide' ? sourceField : 'raw_text' }),
    ])
    if (!isCurrent(captured) || request !== requestVersion.current) return
    if (values[0].status === 'fulfilled') { setRows(values[0].value.data || []); setError('') }
    else setError(errorText(values[0].reason, '读取候选记录失败，请重新加载'))
    if (values[1].status === 'fulfilled') { setAvailability(values[1].value.data || null); setQuoteError('') }
    else { setAvailability(null); setQuoteError(errorText(values[1].reason, '请先保存文本，再重新检查模型费用')) }
  }, [scope, projectId, chapterId, operation, sourceField, modelId, shotId, isCurrent, capture])

  useEffect(() => {
    mounted.current = true; epoch.current++
    const notify = busyListener.current
    requestVersion.current++; inFlight.current = false; consumedFocus.current = undefined; setBusy(false); busyListener.current?.(false); setRows([]); setOpened([]); setAvailability(null)
    setOperation(shotId ? 'extract_shot' : preferredOperation || 'optimize'); setSourceField('raw_text'); setModelId(undefined); setIntent(null); setError(''); setQuoteError('')
    try {
      const saved = workspaceStorage.getItem(storageKey)
      if (saved) {
        const value = JSON.parse(saved) as Intent
        if (value.projectId !== projectId || value.chapterId !== chapterId || value.requestBody?.confirm_model_call !== true || value.requestBody?.shot_id !== (shotId ?? null)) throw new Error('Wrong saved scope')
        setIntent(value)
      }
    } catch { setError('无法读取上次提交记录，请保留当前页面并检查浏览器存储') }
    return () => { mounted.current = false; epoch.current++; requestVersion.current++; notify?.(false) }
  }, [scope, storageKey, projectId, chapterId, shotId])

  useEffect(() => { if (!shotId && preferredOperation) setOperation(preferredOperation) }, [shotId, preferredOperation])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    // A task-center return may open only a row already read within this exact chapter/shot scope.
    if (!focusCandidateId || consumedFocus.current === focusCandidateId || !rows.some(row => row.id === focusCandidateId && row.shot_id === (shotId ?? null))) return
    consumedFocus.current = focusCandidateId
    setOpened(previous => [...new Set([...previous, focusCandidateId])])
  }, [scope, focusCandidateId, rows, shotId])
  useEffect(() => {
    if (!rows.some(row => row.status === 'queued' || row.status === 'running')) return
    const timer = window.setTimeout(() => void refresh(), 1000)
    return () => window.clearTimeout(timer)
  }, [rows, refresh])

  /** Keep a lost acknowledgement recoverable with its complete original path and body. */
  const submitIntent = async (value: Intent) => {
    const captured = capture()
    if (inFlight.current || !isCurrent(captured)) return
    inFlight.current = true; setBusy(true); onBusyChange?.(true)
    try {
      const response = await ScriptCandidatesService.createScriptCandidateApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesPost(value)
      if (!isCurrent(captured)) return
      if (!response.data) throw new Error('Missing persisted result')
      try { workspaceStorage.removeItem(storageKey); setIntent(null); setError('') }
      catch { setError('任务已保存，原提交记录尚未清除；再次核对仍返回原任务') }
      setOpened([response.data.id])
      await refresh()
    } catch (failure) {
      if (!isCurrent(captured)) return
      const status = (failure as { status?: number }).status
      if (status === 409 || status === 422) { try { workspaceStorage.removeItem(storageKey); setIntent(null) } catch { /* Keep the exact intent if browser storage cannot be changed. */ } }
      setError(errorText(failure, '提交响应中断，请核对上次提交；不要重复确认新费用'))
    } finally {
      if (isCurrent(captured)) { inFlight.current = false; setBusy(false); onBusyChange?.(false) }
    }
  }

  /** Freeze the current reviewed quote; the confirmation cannot follow later model or operation changes. */
  const confirmCreate = () => {
    if (!availability?.available || !availability.selected_model_id || !availability.selected_price_fingerprint || !availability.authorized_max_minor || hasUnsavedChanges || intent || busy) return
    const captured = capture()
    const body: ScriptCandidateCreate = { request_key: crypto.randomUUID(), operation, source_field: operation === 'divide' ? sourceField : 'raw_text', shot_id: shotId ?? null,
      expected_source_revision: availability.source_revision, expected_source_hash: availability.source_hash,
      model_id: availability.selected_model_id, expected_price_fingerprint: availability.selected_price_fingerprint, confirm_model_call: true }
    const value: Intent = { projectId, chapterId, requestBody: body }
    const selected = availability.models.find(row => row.id === body.model_id)
    Modal.confirm({ title: `确认${titles[operation]}的项目费用`, okText: '确认费用并生成候选', cancelText: '返回',
      content: <div className="space-y-2"><p>项目：{availability.project_name} · {availability.chapter_title}</p><p>模型：{selected?.name}</p><p>本次预留 ¥{money(availability.authorized_max_minor)}，只使用本项目额度。</p><p>使用已保存的{body.source_field === 'condensed_text' ? '精简内容' : '原文'}，共 {availability.input_characters} 字。结果先供查看，明确采用后才写入。</p></div>,
      onOk: async () => {
        if (!isCurrent(captured) || live.current.dirty) { message.warning('页面或编辑内容已改变，请保存后重新确认'); return }
        try { workspaceStorage.setItem(storageKey, JSON.stringify(value)); setIntent(value) }
        catch { setError('无法保存原提交记录，尚未发送请求；请检查浏览器存储'); return }
        await submitIntent(value)
      },
    })
  }

  /** Adopt only the exact server proposal the user opened, or dismiss and cancel its unsent work. */
  const decide = (row: ScriptCandidateRead, adopt: boolean) => {
    const captured = capture()
    Modal.confirm({ title: adopt ? `采用这次${titles[row.operation]}候选？` : '忽略这次候选？', okText: adopt ? '确认采用' : '确认忽略', cancelText: '返回',
      content: adopt ? row.operation === 'extract_shot' ? '将刷新本镜的提取候选。已有正式资产和对白保留，新增资产/对白仍需逐项确认。' : row.operation === 'divide' ? '将按候选建立本章分镜；已有镜头时不会覆盖。' : '将更新已保存的章节文本，并保留可恢复的文本历史。' : '未发送的请求会取消；已发出的模型请求仍可能计费，未知费用继续保留。',
      onOk: async () => {
        if (inFlight.current || !isCurrent(captured) || (adopt && live.current.dirty)) return
        inFlight.current = true; setBusy(true); onBusyChange?.(true)
        try {
          const response = adopt ? await ScriptCandidatesService.adoptScriptCandidateApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesCandidateIdAdoptPost({ projectId, chapterId, candidateId: row.id,
            requestBody: { expected_source_revision: row.source_revision, expected_source_hash: row.source_hash, expected_output_hash: row.output_hash! } })
            : await ScriptCandidatesService.dismissScriptCandidateApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesCandidateIdDismissPost({ projectId, chapterId, candidateId: row.id })
          if (!isCurrent(captured)) return
          setError(''); if (adopt && response.data) await onAdopted?.(response.data, () => isCurrent(captured))
          if (!isCurrent(captured)) return
          await refresh()
        } catch (failure) { if (isCurrent(captured)) setError(errorText(failure, '确认响应尚未核对，请刷新候选记录；原文不会被旧结果覆盖')) }
        finally { if (isCurrent(captured)) { inFlight.current = false; setBusy(false); onBusyChange?.(false) } }
      },
    })
  }

  const visibleRows = rows.filter(row => shotId ? row.shot_id === shotId : row.shot_id === null)
  return <Card id="script-candidates" size="small" title={shotId ? 'AI 镜头候选' : 'AI 剧本候选'} className="my-3" extra={<Button size="small" onClick={() => void refresh()} disabled={busy}>刷新记录与费用</Button>}>
    <Space wrap className="mb-3">
      {!shotId && <Select aria-label="候选操作" value={operation} onChange={setOperation} disabled={busy} options={(['optimize', 'simplify', 'divide'] as Operation[]).map(value => ({ value, label: titles[value] }))} />}
      {operation === 'divide' && <Select aria-label="分镜文本来源" value={sourceField} onChange={setSourceField} disabled={busy} options={[{ value: 'raw_text', label: '已保存原文' }, { value: 'condensed_text', label: '已保存精简内容' }]} />}
      <Select aria-label="候选文本模型" placeholder="选择文本模型" style={{ minWidth: 220 }} value={modelId || availability?.selected_model_id || undefined} disabled={busy} onChange={setModelId}
        options={(availability?.models || []).map(row => ({ value: row.id, label: row.name, disabled: !row.configured || !row.priced }))} />
      <Button type="primary" onClick={confirmCreate} disabled={busy || !!intent || hasUnsavedChanges || !availability?.available}>生成候选并确认费用</Button>
    </Space>
    {hasUnsavedChanges && <Alert type="info" className="mb-2" message="先保存或撤销当前编辑，再生成或采用候选；已保存的旧请求仍可核对。" />}
    {intent && <Alert type="warning" className="mb-2" message="有一笔提交响应待核对" description="核对会使用原项目、原保存文本和原确认费用，不会按当前表单创建另一个请求。" action={<Button disabled={busy} onClick={() => void submitIntent(intent)}>核对上次提交</Button>} />}
    {(quoteError || (availability && !availability.available)) && <Alert type="info" className="mb-2" message={quoteError || availability?.message} />}
    {error && <Alert type="error" className="mb-2" message={error} />}
    {!visibleRows.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无候选；手工编辑与保存仍可使用" /> : <Collapse activeKey={opened} onChange={value => setOpened(typeof value === 'string' ? [value] : value)} items={visibleRows.map(row => ({ key: row.id,
      label: <Space wrap><span>{titles[row.operation]}</span><Tag>{row.decision === 'adopted' ? '已采用' : row.decision === 'dismissed' ? '已忽略' : (statuses[row.status] || row.status)}</Tag><span>{row.model_name} · 预留 ¥{money(row.authorized_max_minor)}</span></Space>,
      children: <div className="space-y-3"><details><summary>生成时保存的原文</summary><pre className="whitespace-pre-wrap max-h-48 overflow-auto p-2">{row.source_text}</pre></details><ResultPreview row={row} />
        <Typography.Paragraph type="secondary">{row.message}</Typography.Paragraph><Space><Button type="primary" disabled={busy || hasUnsavedChanges || !row.can_adopt} onClick={() => decide(row, true)}>采用这次候选</Button><Button disabled={busy || row.decision !== 'pending'} onClick={() => decide(row, false)}>忽略{row.status === 'queued' || row.status === 'running' ? '并取消' : ''}</Button></Space></div>,
    }))} />}
  </Card>
}
