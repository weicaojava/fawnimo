import { workspaceStorage } from '../../../../../services/workspaceContext'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Collapse, Empty, Form, Input, InputNumber, List, Modal, Progress, Select, Space, Switch, Table, Tag, message } from 'antd'
import { ProjectSection } from '../../ProjectSection'
import { VideoUnderstandingService as V } from '../../../../../services/generated'
import type { AnalysisRead, DnaClaim, UnderstandingConnection, UnderstandingConnectionRead, UnderstandingCreate, UnderstandingJobRead } from '../../../../../services/generated'

const stateLabels: Record<string, string> = { queued: '等待执行', running: '执行中', succeeded: '候选已保存，待审核', failed: '执行失败', cancelled: '已取消' }
const phaseLabels: Record<string, string> = { queued: '等待执行', preparing: '读取原帧和音轨', prepared: '证据已校验', transcribing: '逐段语音转写', understanding: '画面与提示词理解', validating: '检查来源与结构', succeeded: '候选已保存', failed: '执行失败', cancelled: '已取消' }
const kindLabels: Record<string, string> = { character: '人物', scene: '场景', prop: '道具' }
const claimLabels: Record<string, string> = { face: '面部', hair: '发型', body: '体型', costume: '服装', makeup: '妆容', accessories: '配饰', voice: '声音', layout: '空间', architecture: '建筑', props: '场内道具', lighting: '光线', background: '背景', time_and_space: '时空', shape: '形状', material: '材质', state: '状态' }

/** Surface safe server explanations while preserving edits and the last durable task identity. */
function explain(error: unknown): string {
  const body = (error as { body?: { message?: unknown; detail?: unknown } })?.body
  return typeof body?.message === 'string' ? body.message : typeof body?.detail === 'string' ? body.detail : '操作未完成，请保留当前内容，刷新状态后再试。'
}

/** A model claim stays visibly inferred or unknown; its original evidence is directly inspectable. */
function CandidateClaim({ value, onInspect }: { value?: DnaClaim; onInspect: (frame: number) => void }) {
  return <div className="space-y-1"><Tag>{value?.status === 'unknown' || !value ? '未知，待确认' : '模型推断，待确认'}</Tag>
    <p>{value?.zh || value?.en || value?.reason || '抽样证据不足'}</p>
    {value?.zh && value.en && <p className="text-xs text-gray-500">{value.en}</p>}
    <Space wrap>{value?.evidence?.map((item, index) => <Button key={index} size="small" onClick={() => onInspect(item.start_frame)}>查看原帧 {item.start_frame}{item.end_frame > item.start_frame + 1 ? ` 至 ${item.end_frame - 1}` : ''}</Button>)}</Space>
  </div>
}

/** Configure real vision/ASR requests, inspect durable candidates and explicitly open an unreviewed DNA draft. */
export function VideoUnderstandingPanel({ projectId, analysis, requestedJobId, onJobSelected, dnaLocked, onInspectFrame, onDraftCreated, onLockChange }: {
  projectId: string; analysis: AnalysisRead; requestedJobId?: string; dnaLocked: boolean; onInspectFrame: (index: number) => void
  onDraftCreated: (id: string) => void; onJobSelected: (id: string) => void; onLockChange: (locked: boolean) => void
}) {
  const [form] = Form.useForm<UnderstandingConnection>()
  const transcribe = Form.useWatch('transcribe_audio', form)
  const [connection, setConnection] = useState<UnderstandingConnectionRead | null>(null)
  const [formRevision, setFormRevision] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [jobs, setJobs] = useState<UnderstandingJobRead[]>([])
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [pending, setPending] = useState<UnderstandingCreate | null>(null)
  const alive = useRef(true)
  const dirtyNow = useRef(false)
  const operation = useRef(false)
  const selectedNow = useRef('')
  const requestedJobNow = useRef(requestedJobId)
  const appliedRequestedJob = useRef<string>()
  requestedJobNow.current = requestedJobId
  const intentKey = `jellyfish:understanding-intent:${projectId}:${analysis.id}`
  const job = jobs.find(item => item.id === selected && item.analysis_id === analysis.id)
  const sourceCuts: number[] = job && Array.isArray(job.source.cut_indices) ? job.source.cut_indices : []
  const cutsMatch = JSON.stringify(sourceCuts) === JSON.stringify(analysis.confirmed_cuts?.map(item => item.frame_index))
  const jobRunning = jobs.some(item => item.analysis_id === analysis.id && ['queued', 'running'].includes(item.state))
  const receipt = job?.attempts[0]?.input_receipt

  /** Lock synchronous actions before asynchronous React state updates can permit duplicate requests. */
  function start() { if (!alive.current || operation.current) return false; operation.current = true; setBusy(true); setError(''); return true }
  /** Release only the currently mounted analysis workspace. */
  function finish() { operation.current = false; if (alive.current) setBusy(false) }

  /** Refresh persisted tasks independently of configuration edits and select only this video's jobs. */
  async function loadJobs() {
    const response = await V.listVideoUnderstandingJobs({ projectId })
    if (!alive.current) return
    let rows = response.data ?? []
    const requested = requestedJobNow.current
    if (requested && !rows.some(item => item.id === requested)) {
      const target = (await V.getVideoUnderstandingJob({ projectId, jobId: requested })).data
      if (!alive.current) return
      if (target?.analysis_id !== analysis.id) throw new Error('Requested task belongs to another analysis')
      rows = [target, ...rows].slice(0, 31)
    }
    setJobs(rows)
    const wanted = requested && appliedRequestedJob.current !== requested ? requested : selectedNow.current
    const current = rows.find(item => item.id === wanted && item.analysis_id === analysis.id) ?? rows.find(item => item.analysis_id === analysis.id)
    if (requested && current?.id === requested) appliedRequestedJob.current = requested
    selectedNow.current = current?.id ?? ''; setSelected(selectedNow.current)
  }

  /** Read availability without probing a model; never upgrade the revision underlying dirty local form fields. */
  async function loadConnection() {
    const response = await V.getVideoUnderstandingConnection({ projectId })
    if (!alive.current || !response.data) return
    setConnection(response.data)
    if (!dirtyNow.current) { form.setFieldsValue(response.data.connection); setFormRevision(response.data.revision) }
  }

  useEffect(() => {
    alive.current = true
    let inFlight = false
    void Promise.all([loadConnection(), loadJobs()]).catch(reason => { if (alive.current) setError(explain(reason)) }).finally(() => { if (alive.current) setLoading(false) })
    try { const saved = workspaceStorage.getItem(intentKey); if (saved) setPending(JSON.parse(saved) as UnderstandingCreate) } catch { setError('无法读取上次理解提交记录；请先在任务列表核查是否已经建立任务。') }
    const timer = setInterval(() => {
      if (inFlight || !alive.current) return
      inFlight = true
      void loadJobs().catch(reason => { if (alive.current) setError(explain(reason)) }).finally(() => { inFlight = false })
    }, 2000)
    return () => { alive.current = false; clearInterval(timer) }
  }, [])
  useEffect(() => {
    if (requestedJobId) void loadJobs().catch(reason => { if (alive.current) setError(explain(reason)) })
  }, [requestedJobId])
  useEffect(() => {
    if (requestedJobId && selected === requestedJobId) document.getElementById('video-understanding')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [requestedJobId, selected])
  useEffect(() => { onLockChange(dirty || busy); return () => onLockChange(false) }, [dirty, busy, onLockChange])
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirtyNow.current) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])

  /** Save dedicated connection settings through a real revision-checked API without triggering a provider. */
  async function saveConnection(values: UnderstandingConnection) {
    if (!start()) return
    try {
      const response = await V.saveVideoUnderstandingConnection({ projectId, requestBody: { ...values, expected_revision: formRevision } })
      if (!alive.current || !response.data) return
      setConnection(response.data); form.setFieldsValue(response.data.connection); setFormRevision(response.data.revision)
      dirtyNow.current = false; setDirty(false); message.success('理解连接已保存，尚未调用模型')
    } catch (reason) { if (alive.current) setError(explain(reason)) } finally { finish() }
  }

  /** Persist the original submission identity before HTTP, so a lost response cannot cause a second model task. */
  async function submitJob() {
    if (!connection || !start()) return
    const intent = pending ?? { analysis_id: analysis.id, expected_analysis_revision: analysis.revision, expected_connection_revision: connection.revision, request_key: crypto.randomUUID() }
    try {
      workspaceStorage.setItem(intentKey, JSON.stringify(intent)); setPending(intent)
      const response = await V.createVideoUnderstandingJob({ projectId, requestBody: intent })
      if (!alive.current || !response.data) return
      try { workspaceStorage.removeItem(intentKey); setPending(null) } catch { setError('任务已保存，但本地提交记录未能清除；再次核对会回到同一个任务。') }
      selectedNow.current = response.data.id; setSelected(response.data.id)
      onJobSelected(response.data.id)
      await loadJobs(); message.success('真实理解任务已保存，可在这里查看进度与取消')
    } catch (reason) {
      if (alive.current) {
        const status = (reason as { status?: number }).status
        if (status === 409 || status === 422) { try { workspaceStorage.removeItem(intentKey); setPending(null) } catch { /* Keep the last stable identity if storage remains unavailable. */ } }
        setError(explain(reason))
      }
    } finally { finish() }
  }

  /** Request an attempt-scoped cancellation or explicitly authorize replaying the saved original connection. */
  async function control(kind: 'cancel' | 'retry') {
    if (!job || !start()) return
    try {
      const params = { projectId, jobId: job.id, requestBody: { expected_attempt: job.attempt } }
      if (kind === 'cancel') await V.cancelVideoUnderstandingJob(params)
      else await V.retryVideoUnderstandingJob(params)
      if (alive.current) { await loadJobs(); message.success(kind === 'cancel' ? '取消请求已保存；本次候选不会入库' : '已按原配置建立新的执行尝试') }
    } catch (reason) { if (alive.current) setError(explain(reason)) } finally { finish() }
  }

  /** Enter the existing DNA editor only by creating/reopening an owned unreviewed draft. */
  async function openDraft() {
    if (!job || dnaLocked || !start()) return
    try {
      const response = await V.createVideoUnderstandingDraft({ projectId, jobId: job.id, requestBody: { expected_analysis_revision: analysis.revision } })
      if (!alive.current || !response.data) return
      await loadJobs(); onDraftCreated(response.data.draft_id)
      message.success('已打开待人工确认的DNA草稿；可以逐项修改后再入制作库')
    } catch (reason) { if (alive.current) setError(explain(reason)) } finally { finish() }
  }

  /** Export candidate evidence only; connection details, environment references and credentials are excluded. */
  function downloadCandidate() {
    if (!job?.document) return
    const value = { schema_version: 1, artifact_kind: 'unreviewed_video_understanding_candidate', source: job.source, document: job.document, transcripts: job.transcripts, warnings: job.warnings, attempts: job.attempts }
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a'); link.href = url; link.download = 'video-understanding-candidate.json'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <div id="video-understanding"><ProjectSection headingLevel={3} title="新视频自动拆解" loading={loading} data-testid="video-understanding">
    <Alert className="mb-3" type="info" showIcon message="由真实原帧和逐段音轨生成可审核候选" description="人物、服装、场景、道具、动作和提示词来自你选择的模型服务。先配置连接，再开始理解；结果保持待人工确认。没有音轨、没有时标或没有证据的内容会保留为未知。" />
    {error && <Alert className="mb-3" type="error" showIcon message={error} />}
    {connection && <Alert className="mb-3" type="info" showIcon message={connection.message} description={connection.next_steps.length ? <List size="small" dataSource={connection.next_steps} renderItem={item => <List.Item>{item}</List.Item>} /> : '本页不会自动使用此前保存的生成模型账号。'} action={<Button disabled={busy} onClick={() => void loadConnection().catch(reason => setError(explain(reason)))}>刷新连接状态</Button>} />}
    <Collapse className="mb-4" defaultActiveKey={connection?.configured ? [] : ['connection']} items={[{ key: 'connection', label: '1. 配置本项目的理解服务', children: <Form form={form} name={`understanding-${analysis.id}`} layout="vertical" onFinish={saveConnection} disabled={busy} onValuesChange={() => { dirtyNow.current = true; setDirty(true) }}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Form.Item name="base_url" label="模型服务API地址" help="本机服务可直接填写；远程服务需要在服务器运行配置中单独启用。" rules={[{ required: true, message: '请填写服务API地址' }]}><Input aria-label="理解服务API地址" placeholder="http://127.0.0.1:端口/v1" maxLength={1024} /></Form.Item>
        <Form.Item name="vision_model" label="视觉模型名称" rules={[{ required: true, message: '请填写能接收图片并输出JSON的模型名称' }]}><Input aria-label="理解视觉模型" placeholder="填写服务提供方给出的模型名称" maxLength={255} /></Form.Item>
        <Form.Item name="transcribe_audio" label="生成逐段对白候选" valuePropName="checked"><Switch aria-label="启用音轨转写" /></Form.Item>
        {transcribe && <Form.Item name="audio_model" label="语音转写模型名称" help="需要支持verbose_json和逐段时间；仅返回整段文字的服务不会被贴到镜头。" rules={[{ required: true, message: '请填写支持逐段时间的语音转写模型' }]}><Input aria-label="理解转写模型" maxLength={255} /></Form.Item>}
        {transcribe && <Form.Item name="audio_base_url" label="语音服务API地址（可选）" help="语音与视觉使用不同服务时填写；留空沿用视觉服务地址。"><Input aria-label="理解语音服务API地址" placeholder="与视觉服务相同可留空" maxLength={1024} /></Form.Item>}
        <Form.Item name="enabled" label="启用此连接" valuePropName="checked"><Switch aria-label="启用理解连接" /></Form.Item>
      </div>
      <Collapse className="mb-3" items={[{ key: 'advanced', label: '授权与执行上限', children: <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Form.Item name="credential_env" label="新服务的授权引用（本机无授权时可留空）" help="不在此处填写密钥。需要授权时，在本地服务启动配置中新增以DNA_UNDERSTANDING_开头的专用项，再将项名填在这里。" rules={[{ pattern: /^(DNA_UNDERSTANDING_[A-Z0-9_]{1,64})?$/, message: '请输入新的DNA_UNDERSTANDING_专用授权项名' }]}><Input aria-label="理解专用授权引用" placeholder="DNA_UNDERSTANDING_LOCAL_SERVICE" maxLength={96} autoComplete="off" /></Form.Item>
        {transcribe && <Form.Item name="audio_credential_env" label="语音服务授权引用（可选）" help="与视觉服务共用授权时留空；不同服务可以填写另一项新的专用授权引用。" rules={[{ pattern: /^(DNA_UNDERSTANDING_[A-Z0-9_]{1,64})?$/, message: '请输入新的DNA_UNDERSTANDING_专用授权项名' }]}><Input aria-label="理解语音授权引用" maxLength={96} autoComplete="off" /></Form.Item>}
        <Form.Item name="max_frames" label="单次证据帧上限" help="每镜取镜内起点、中间、末帧；超过上限会明确停止，不跳过镜头。" rules={[{ required: true }]}><InputNumber aria-label="理解证据帧上限" min={3} max={96} precision={0} /></Form.Item>
        <Form.Item name="timeout_seconds" label="单个模型请求超时（秒）" rules={[{ required: true }]}><InputNumber aria-label="理解请求超时" min={5} max={300} precision={0} /></Form.Item>
        <Form.Item name="max_output_tokens" label="模型输出长度上限" rules={[{ required: true }]}><InputNumber aria-label="理解输出长度" min={256} max={32768} precision={0} /></Form.Item>
      </div> }]} />
      {dirty && <Alert className="mb-3" type="warning" message="连接信息有未保存修改，请先保存后再开始理解。" />}
      <Button type="primary" htmlType="submit" loading={busy}>保存理解连接</Button>
    </Form> }]} />
    {pending && <Alert className="mb-3" type="warning" message="上次提交结果尚未核对" description="继续核对会使用原请求标识，不会新建第二个任务。请先核查这一笔，再提交新任务。" action={<Button disabled={busy} onClick={() => void submitJob()}>核对上次理解提交</Button>} />}
    <Space wrap className="mb-3"><Button type="primary" disabled={busy || dirty || !!pending || !connection?.available || jobRunning} onClick={() => Modal.confirm({ title: '开始理解这段视频？', content: `将向 ${connection?.connection.base_url} 发送列明的抽样原帧${connection?.connection.transcribe_audio ? '和原片音轨' : ''}，模型为 ${connection?.connection.vision_model}。服务可能按调用计费，候选仍需人工核对。`, okText: '开始理解', cancelText: '取消', onOk: submitJob })}>开始理解原视频</Button>
      <Button disabled={busy} onClick={() => void loadJobs().catch(reason => setError(explain(reason)))}>刷新理解任务</Button>
      <Select aria-label="选择理解任务" className="min-w-64" value={selected || undefined} placeholder="尚无已保存任务" options={jobs.filter(item => item.analysis_id === analysis.id).map(item => ({ value: item.id, label: `${new Date(item.created_at * 1000).toLocaleString()} · ${stateLabels[item.state]}` }))} onChange={value => { selectedNow.current = value; setSelected(value); onJobSelected(value) }} />
    </Space>
    {!job && <Empty description="尚未提交本视频的理解任务；连接未配置时，可继续使用下方手动DNA流程。" />}
    {job && <div className="space-y-3" data-testid="understanding-job" data-job-id={job.id}>
      <Space wrap><Tag color={job.state === 'failed' ? 'red' : job.state === 'succeeded' ? 'blue' : 'default'}>{stateLabels[job.state]}</Tag><span>第{job.attempt}次尝试 · {phaseLabels[job.phase] ?? '执行状态已更新'}</span><span>来源 {String(job.source.sha256).slice(0, 12)}</span></Space>
      <Progress percent={job.progress} status={job.state === 'failed' ? 'exception' : job.state === 'running' ? 'active' : 'normal'} />
      {job.reason && <Alert type={job.state === 'failed' ? 'error' : 'info'} showIcon message={job.reason} />}
      <Space wrap>{['queued', 'running'].includes(job.state) && <Button danger disabled={busy || job.cancel_requested} onClick={() => void control('cancel')}>{job.cancel_requested ? '取消中' : '取消本次理解'}</Button>}
        {['failed', 'cancelled'].includes(job.state) && <Button disabled={busy || dirty || !connection?.worker_enabled} onClick={() => Modal.confirm({ title: '按原配置重新提交？', content: '上一笔可能已在服务侧产生调用或费用，请先核对。重试会使用任务原来保存的服务和模型；如果已修改连接，请建立新任务。', okText: '确认按原配置重试', cancelText: '取消', onOk: () => control('retry') })}>按原配置重试</Button>}
        {job.state === 'succeeded' && <><Button onClick={downloadCandidate}>下载本次候选</Button><Button type="primary" disabled={busy || dnaLocked || (!job.draft_id && !cutsMatch)} onClick={() => void openDraft()}>{job.draft_id ? '打开已建DNA草稿' : '建立可编辑DNA草稿'}</Button></>}
      </Space>
      {job.state === 'succeeded' && !job.draft_id && !cutsMatch && <Alert type="warning" message="请先在本页确认与候选一致的原始帧切点，再建立DNA草稿。" description={`本次候选切点：${sourceCuts.join(', ')}。你可以先检查证据；其他切点不会被候选覆盖。`} />}
      {dnaLocked && job.state === 'succeeded' && <Alert type="info" message="请先保存当前DNA编辑，再打开这份候选的草稿。" />}
      {job.warnings.length > 0 && <List size="small" dataSource={job.warnings} renderItem={item => <List.Item>{item}</List.Item>} />}
      {job.transcripts.length > 0 && <ProjectSection headingLevel={4} title="逐段对白候选，尚未人工核对"><p className="text-gray-500 mb-3">未确认说话者的文字独立保留。建立DNA草稿后，请在逐镜对白中核对角色与文字，再决定是否采用。</p><Table size="small" rowKey="id" pagination={false} dataSource={job.transcripts} scroll={{ x: 600 }} columns={[
        { title: '机器转写候选', dataIndex: 'text' }, { title: '音轨内时间', render: (_, item) => `${(item.audio_start_us / 1e6).toFixed(3)}–${(item.audio_end_us / 1e6).toFixed(3)}秒` },
        { title: '对应原始帧', render: (_, item) => item.start_frame === null ? '视频范围外，待对齐' : <Button onClick={() => onInspectFrame(item.start_frame!)}>帧{item.start_frame}至{(item.end_frame ?? 1) - 1}</Button> },
      ]} /></ProjectSection>}
      {job.document && <Collapse items={[
        { key: 'assets', label: `人物、服装、场景与道具候选（${job.document.assets?.length ?? 0}）`, children: <List dataSource={job.document.assets ?? []} renderItem={asset => <List.Item><div className="w-full"><h4><Tag>{kindLabels[asset.kind]}</Tag>{asset.name_zh || asset.id}</h4><Collapse items={Object.entries(asset.fields ?? {}).map(([key, value]) => ({ key, label: claimLabels[key] ?? key, children: <CandidateClaim value={value} onInspect={onInspectFrame} /> }))} /></div></List.Item>} /> },
        { key: 'shots', label: `逐镜动作与提示词候选（${job.document.shots?.length ?? 0}）`, children: <Collapse items={(job.document.shots ?? []).map(shot => ({ key: shot.id, label: `${shot.id} · 原帧${shot.start_frame}至${shot.end_frame - 1}`, children: <div className="space-y-4"><div><h4>动作推断</h4><CandidateClaim value={shot.action} onInspect={onInspectFrame} /></div><div><h4>图像提示词</h4><CandidateClaim value={shot.image_prompt} onInspect={onInspectFrame} /></div><div><h4>视频提示词</h4><CandidateClaim value={shot.video_prompt} onInspect={onInspectFrame} /></div></div> }))} /> },
      ]} />}
      <Collapse items={[{ key: 'evidence', label: '查看真实输入证据与执行尝试', children: <div className="space-y-3">
        <p>原帧时钟校验：{String(job.source.frame_clock_hash)}；末端N时间保持未知。</p>
        {receipt && <Space wrap>{(Array.isArray(receipt.frames) ? receipt.frames : []).map((frame: { index: number; sha256: string }) => <Button key={frame.index} size="small" onClick={() => onInspectFrame(frame.index)}>原帧 {frame.index} · {frame.sha256.slice(0, 8)}</Button>)}</Space>}
        <List size="small" dataSource={job.attempts} renderItem={item => <List.Item><div>第{item.attempt}次：{stateLabels[item.state] ?? '已记录'}；{item.reason || phaseLabels[item.phase] || '执行状态已记录'}{item.input_receipt && <p className="text-xs text-gray-500">已记录实际帧图、音轨与请求摘要校验值；不包含授权内容。</p>}</div></List.Item>} />
      </div> }]} />
    </div>}
  </ProjectSection></div>
}
