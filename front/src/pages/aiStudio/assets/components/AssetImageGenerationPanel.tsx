import { workspaceMediaUrl } from '../../../../services/workspaceContext'
import { useEffect, useRef, useState } from 'react'
import ProjectChargePicker, { chargeMoney, readProjectCharge, type ProjectChargeApproval } from '../../project/ProjectChargePicker'
import ModelSubmissionRecovery, { useModelSubmissionRecovery } from '../../project/ModelSubmissionRecovery'
import AssetImageReferencePicker from './AssetImageReferencePicker'
import { useImageActionScope } from './useImageActionScope'
import { Link } from 'react-router-dom'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Descriptions, Input, Pagination, Select, Space, Tag } from 'antd'
import { AssetImageGenerationService as I, OpenAPI } from '../../../../services/generated'
import type { AssetImageAvailability, AssetImageJobRead, AssetImageSlotRead, AssetImageSubmit, AssetImageReferenceInput } from '../../../../services/generated'
import { agentError } from '../../agents/agentConfigIO'

type Kind = 'actor' | 'costume' | 'scene' | 'prop'
const labels: Record<string, string> = { queued: '排队中', running: '生成中', candidate: '候选待采用', adopted: '已采用', failed: '失败', cancelled: '已取消' }
const angles: Record<string, string> = { FRONT: '正面', LEFT: '左侧', RIGHT: '右侧', BACK: '背面', THREE_QUARTER: '四分之三侧面', TOP: '俯视', DETAIL: '细节', front: '正面', left: '左侧', right: '右侧', back: '背面' }
const qualities: Record<string, string> = { LOW: '基础', MEDIUM: '中等', HIGH: '高清', ULTRA: '超清' }

/** Generate bounded candidates with only explicitly selected inputs, then separately confirm one slot replacement. */
export default function AssetImageGenerationPanel({ kind, assetId, initialPrompt, onAdopted }: { kind: Kind; assetId: string; initialPrompt: string; onAdopted: () => void }) {
  const [searchParams] = useSearchParams()
  const requestedJob = searchParams.get('imageJob')
  const [availability, setAvailability] = useState<AssetImageAvailability | null>(null)
  const [modelId, setModelId] = useState<string>()
  const [projectId, setProjectId] = useState<string>()
  const [charge, setCharge] = useState<ProjectChargeApproval | null>(null)
  const [slots, setSlots] = useState<AssetImageSlotRead[]>([])
  const [slotId, setSlotId] = useState<number>()
  const [prompt, setPrompt] = useState(initialPrompt)
  const [size, setSize] = useState<NonNullable<AssetImageSubmit['size']>>('1024x1024')
  const [references, setReferences] = useState<AssetImageReferenceInput[]>([])
  const [referencesReady, setReferencesReady] = useState(true)
  const [responseFormat, setResponseFormat] = useState<'b64_json' | 'url'>('b64_json')
  const [jobs, setJobs] = useState<AssetImageJobRead[]>([])
  const [selected, setSelected] = useState<AssetImageJobRead | null>(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [navigationError, setNavigationError] = useState('')
  const recovery = useModelSubmissionRecovery(`image:${kind}:${assetId}`, setBusy, setError)
  const pending = useRef<{ fingerprint: string; key: string }>()
  const retries = useRef<Record<string, { fingerprint: string; key: string }>>({})
  const adoptions = useRef<Record<string, { fingerprint: string; key: string }>>({})
  const readScope = useImageActionScope(JSON.stringify([kind, assetId]))
  const actionScope = useImageActionScope(JSON.stringify([kind, assetId, projectId, modelId, slotId]))
  const loads = useRef(0)
  const price = availability?.models.find(model => model.id === (modelId || availability.default_model_id))
  const allowedSizes = price?.allowed_sizes ?? []
  const responseFormats = price?.response_formats ?? ['b64_json']
  const selectedReferences = selected?.references ?? []
  const upperMinor = price?.max_call_minor ?? undefined
  const chargeReady = !!charge?.ready && charge.projectId === projectId && charge.upperMinor === upperMinor
  useEffect(() => { setReferences([]); setReferencesReady(true) }, [kind, assetId, projectId, modelId])
  useEffect(() => { setBusy(false) }, [kind, assetId, projectId, modelId, slotId])
  useEffect(() => { setSelected(null); setJobs([]); setSlots([]); setNavigationError(''); setBusy(false) }, [kind, assetId])
  useEffect(() => {
    if (!price) return
    if (allowedSizes.length && !allowedSizes.includes(size)) setSize(allowedSizes[0] as NonNullable<AssetImageSubmit['size']>)
    if (!responseFormats.includes(responseFormat)) setResponseFormat('b64_json')
  }, [price?.id, price?.protocol, allowedSizes.join(','), responseFormats.join(','), size, responseFormat])

  /** Read readiness, exact slots and durable history without contacting any model. */
  const load = async () => {
    const stamp = readScope.capture(), serial = ++loads.current
    const fresh = () => readScope.isCurrent(stamp) && loads.current === serial
    setError(''); setAvailability(null)
    try {
      const [ready, currentSlots, history] = await Promise.all([I.getAssetImageAvailability({ modelId }), I.listAssetImageSlots({ kind, assetId }), I.listAssetImageJobs({ kind, assetId, page, pageSize: 10 })])
      await readScope.attest(stamp)
      if (!fresh()) return
      setAvailability(ready.data ?? null); setSlots(currentSlots.data ?? []); setSlotId(previous => currentSlots.data?.some(row => row.id === previous) ? previous : currentSlots.data?.[0]?.id)
      const items = history.data?.items ?? []; setJobs(items); setTotal(history.data?.pagination.total ?? 0)
      setSelected(previous => items.find(row => row.id === previous?.id) ?? (previous?.id === requestedJob && previous.asset_kind === kind && previous.asset_id === assetId ? previous : items[0] ?? null))
    } catch (failure) { if (fresh()) setError(agentError(failure, '读取独立图片执行服务失败；未发起模型请求')) }
  }
  useEffect(() => { void load() }, [kind, assetId, modelId, page, readScope.key])
  useEffect(() => {
    setNavigationError('')
    if (!requestedJob) return
    let active = true
    const stamp = readScope.capture()
    void Promise.all([I.getAssetImageJob({ jobId: requestedJob }), I.listAssetImageSlots({ kind, assetId })]).then(async ([response, current]) => {
      await readScope.attest(stamp)
      if (!active || !readScope.isCurrent(stamp)) return
      if (!response.data || response.data.asset_kind !== kind || response.data.asset_id !== assetId || !current.data?.some(row => row.id === response.data!.slot_id)) { setNavigationError('回跳的图片任务不属于当前资产槽位，未选择该记录'); return }
      setSelected(response.data); setSlotId(response.data.slot_id); document.getElementById('asset-image-generation')?.scrollIntoView({ block: 'start' })
    }).catch(() => { if (active && readScope.isCurrent(stamp)) setNavigationError('未找到当前身份与资产的回跳任务，未重新生成或采用') })
    return () => { active = false }
  }, [kind, assetId, requestedJob, readScope.key])
  useEffect(() => { if (!modelId && availability?.default_model_id) setModelId(availability.default_model_id) }, [availability?.default_model_id, modelId])
  useEffect(() => {
    if (!selected || !['queued', 'running'].includes(selected.status)) return
    let active = true
    const stamp = readScope.capture()
    const timer = window.setInterval(() => {
      if (!active || !readScope.isCurrent(stamp)) return
      void I.getAssetImageJob({ jobId: selected.id }).then(async response => {
        await readScope.attest(stamp)
        if (active && readScope.isCurrent(stamp) && response.data?.asset_kind === kind && response.data.asset_id === assetId) {
          setSelected(response.data); setJobs(previous => previous.map(row => row.id === response.data!.id ? response.data! : row))
        }
      }).catch(failure => { if (active && readScope.isCurrent(stamp)) setError(agentError(failure, '暂时无法更新图片状态，服务端记录仍保留')) })
    }, 700)
    return () => { active = false; window.clearInterval(timer) }
  }, [selected?.id, selected?.status, readScope.key])

  /** Authorize one explicit model request; reuse uncertain admission identity after a network failure. */
  const generate = () => {
    if (!slotId || !chargeReady || !charge || !price?.price_fingerprint || !referencesReady || !allowedSizes.includes(size) || !responseFormats.includes(responseFormat)) return
    const approval = charge, policy = price.price_fingerprint, chosenModel = price.id
    const frozenReferences = references.map(reference => ({ ...reference }))
    const stamp = actionScope.capture(), originalReadStamp = readScope.capture()
    actionScope.confirm({ title: '生成一张图片候选？', content: `费用归属「${approval.projectName}」，预留 ${chargeMoney(approval.upperMinor!)}。发送下方提示词${frozenReferences.length ? `及按所列顺序选择的 ${frozenReferences.length} 张原项目参考图片` : '，未选择参考图片'}；生成结果不会自动替换当前图片，结果未知时继续保留额度。`, okText: '确认预留并生成候选', cancelText: '取消', onOk: async () => {
    setBusy(true); setError('')
    const fingerprint = JSON.stringify([kind, assetId, slotId, chosenModel, prompt, size, approval.projectId, policy, frozenReferences, responseFormat])
    if (pending.current?.fingerprint !== fingerprint) pending.current = { fingerprint, key: crypto.randomUUID() }
    const requestBody = { request_key: pending.current.key, model_id: chosenModel, prompt, size, confirm_model_call: true,
      project_id: approval.projectId, expected_price_fingerprint: policy, references: frozenReferences, response_format: responseFormat }
    const perform = async (operationStamp = actionScope.capture()) => {
      readScope.ensure(originalReadStamp)
      await actionScope.attest(operationStamp)
      const response = await I.createAssetImageJob({ kind, assetId, slotId, requestBody })
      if (!response.data) throw new Error()
      await actionScope.attest(operationStamp)
      readScope.ensure(originalReadStamp)
      setSelected(response.data); setJobs(previous => [response.data!, ...previous.filter(row => row.id !== response.data!.id)].slice(0, 10)); setPage(1); void load()
      if (pending.current?.key === requestBody.request_key) pending.current = undefined
      recovery.forget(requestBody.request_key)
    }
    try { await perform(stamp) }
    catch (failure) {
      if (!actionScope.isCurrent(stamp)) return
      recovery.remember({ key: requestBody.request_key, label: '图片候选 / ' + chosenModel, projectName: approval.projectName, upperMinor: approval.upperMinor!, recover: perform })
      setError(agentError(failure, '生成提交结果未确认，可恢复原提交'))
    }
    finally { if (actionScope.isCurrent(stamp)) setBusy(false) }
    } }, stamp)
  }
  /** Cancel persistent work before any late model bytes can become a candidate. */
  const cancel = async () => {
    if (!selected || selected.asset_kind !== kind || selected.asset_id !== assetId) return
    const original = selected, stamp = actionScope.capture()
    setBusy(true)
    try {
      await actionScope.attest(stamp)
      const response = await I.cancelAssetImageJob({ jobId: original.id })
      await actionScope.attest(stamp)
      setSelected(response.data ?? original)
    } catch (failure) { if (actionScope.isCurrent(stamp)) setError(agentError(failure, '取消失败，请刷新查看真实状态')) }
    finally { if (actionScope.isCurrent(stamp)) setBusy(false) }
  }
  /** An unknown prior call is never replayed until the user accepts possible duplicate charges. */
  const retry = async () => {
    if (recovery.atCapacity) { setError('请先恢复待确认的原提交，再创建其他尝试'); return }
    const original = selected
    if (original && (original.asset_kind !== kind || original.asset_id !== assetId)) return
    if (!original?.project_id) { setError('这条历史记录没有项目费用授权，请选择项目后新建生成'); return }
    const stamp = actionScope.capture(), originalReadStamp = readScope.capture()
    setBusy(true); setError('')
    try {
      const ready = await I.getAssetImageAvailability({ modelId: original.model_id })
      actionScope.ensure(stamp)
      const quote = ready.data?.models.find(model => model.id === original.model_id)
      if (!ready.data?.available || !quote?.price_fingerprint || !quote.max_call_minor) { setError(ready.data?.message || '原图片模型费用配置尚未就绪'); return }
      const approval = await readProjectCharge(original.project_id, quote.max_call_minor)
      await actionScope.attest(stamp)
      if (!approval.ready) { setError(approval.reason || '原项目预算尚未就绪'); return }
      const policy = quote.price_fingerprint
      actionScope.confirm({ title: '重新生成一个候选？', content: `仍归属原项目「${approval.projectName}」，再次预留 ${chargeMoney(approval.upperMinor!)}。先前请求可能已计费并继续保留额度；原任务与原图片均保留。`, okText: '确认再次预留并重试', cancelText: '取消', onOk: async () => {
        setBusy(true)
        const fingerprint = JSON.stringify([original.id, original.project_id, policy])
        if (retries.current[original.id]?.fingerprint !== fingerprint) retries.current[original.id] = { fingerprint, key: crypto.randomUUID() }
        const requestBody = { request_key: retries.current[original.id].key, acknowledge_duplicate_charge: true, expected_price_fingerprint: policy }
        const perform = async (operationStamp = actionScope.capture()) => {
          readScope.ensure(originalReadStamp)
          await actionScope.attest(operationStamp)
          const response = await I.retryAssetImageJob({ jobId: original.id, requestBody })
          if (!response.data) throw new Error('重试结果尚未返回，请核对原请求')
          await actionScope.attest(operationStamp)
          readScope.ensure(originalReadStamp)
          setSelected(response.data); setJobs(previous => [response.data!, ...previous.filter(row => row.id !== response.data!.id)].slice(0, 10)); void load()
          if (retries.current[original.id]?.key === requestBody.request_key) delete retries.current[original.id]
          recovery.forget(requestBody.request_key)
        }
        try { await perform(stamp) }
        catch (failure) {
          if (!actionScope.isCurrent(stamp)) return
          recovery.remember({ key: requestBody.request_key, label: `图片候选 / 重试 ${original.attempt + 1}`, projectName: approval.projectName, upperMinor: approval.upperMinor!, recover: perform })
          setError(agentError(failure, '显式重试结果未确认，可恢复原提交'))
        }
        finally { if (actionScope.isCurrent(stamp)) setBusy(false) }
      } }, stamp)
    } catch (failure) { if (actionScope.isCurrent(stamp)) setError(agentError(failure, '无法核对原项目或图片模型费用，未提交重试')) }
    finally { if (actionScope.isCurrent(stamp)) setBusy(false) }
  }
  /** Inspect the current target immediately before confirmation, then use its exact CAS token. */
  const prepareAdoption = async () => {
    if (!selected || selected.asset_kind !== kind || selected.asset_id !== assetId) return
    const original = selected, stamp = actionScope.capture()
    setBusy(true); setError('')
    try {
      const response = await I.listAssetImageSlots({ kind, assetId })
      await actionScope.attest(stamp)
      const current = response.data?.find(row => row.id === original.slot_id)
      if (!current) throw new Error('目标槽位不存在，请刷新')
      setSlots(response.data ?? [])
      actionScope.confirm({ title: `将候选采用到${angles[current.view_angle] ?? current.view_angle}槽位？`, content: <Space direction="vertical"><span>将替换此槽位的图片关联。原文件继续保留，不更改资产主图或其他槽位。</span>{current.file_id ? <img alt="采用前当前槽位图片" style={{ maxWidth: 220, maxHeight: 180 }} src={workspaceMediaUrl(`${OpenAPI.BASE}/api/v1/studio/files/${encodeURIComponent(current.file_id)}/download`)} /> : <span>当前槽位为空</span>}<span>如果图片在确认期间发生变化，采用会被拒绝。</span></Space>, okText: '确认采用到此槽位', cancelText: '取消', onOk: async () => {
        setBusy(true)
        const fingerprint = JSON.stringify([current.file_id, current.updated_at])
        if (adoptions.current[original.id]?.fingerprint !== fingerprint) adoptions.current[original.id] = { fingerprint, key: crypto.randomUUID() }
        try {
          await actionScope.attest(stamp)
          const result = await I.adoptAssetImageCandidate({ jobId: original.id, requestBody: { request_key: adoptions.current[original.id].key, expected_file_id: current.file_id, expected_updated_at: current.updated_at, confirm_replace: true } })
          await actionScope.attest(stamp)
          if (result.data) { setSelected(result.data); setJobs(previous => previous.map(row => row.id === result.data!.id ? result.data! : row)); onAdopted() }
        } catch (failure) { if (actionScope.isCurrent(stamp)) setError(agentError(failure, '采用失败；请刷新当前槽位后重新确认，原图未被覆盖')) }
        finally { if (actionScope.isCurrent(stamp)) setBusy(false) }
      } }, stamp)
    } catch (failure) { if (actionScope.isCurrent(stamp)) setError(agentError(failure, '无法确认当前图片，未执行采用')) }
    finally { if (actionScope.isCurrent(stamp)) setBusy(false) }
  }

  const recoveryStamp = actionScope.capture()
  const reason = recovery.atCapacity ? '请先恢复待确认的原提交' : !availability?.available ? availability?.message || '正在检查独立图片执行服务' : !price?.price_fingerprint ? '图片模型费用尚未就绪'
    : !chargeReady ? charge?.reason || '请选择费用归属项目并核对预留' : !referencesReady ? '请先完成参考图片选择与上传' : !allowedSizes.includes(size) || !responseFormats.includes(responseFormat) ? '请核对所选模型支持的尺寸与返回方式' : !slotId ? '请先创建并选择图片槽位' : !prompt.trim() ? '请填写图片提示词' : undefined
  return <section className="library-form-group mb-4" id="asset-image-generation"><h2 className="library-section-title">生成图片候选</h2><Space direction="vertical" size="middle" className="w-full">
    <Alert type="info" showIcon message="选择输入 → 单张候选 → 明确采用" description={price?.supports_references ? '此模型支持明确选择的原项目参考图。参考内容、顺序与用途将固定保存；返回图片会完整解码校验后成为候选，人物一致性与画面质量仍需人工确认。' : '此模型仅发送提示词并直接返回图片内容。所选协议不支持参考图时会明确拒绝；生成后需另外确认采用。'} />
    <Alert type={availability?.available ? 'info' : 'warning'} message={availability?.message || '正在读取图片执行配置'} />
    {error && <Alert type="error" showIcon message={error} />}
    <ModelSubmissionRecovery items={recovery.items} busy={busy} confirm={options => actionScope.confirm(options)} onRecover={async action => { if (actionScope.isCurrent(recoveryStamp)) await recovery.recover(action, () => actionScope.isCurrent(recoveryStamp)) }} />
    {navigationError && <Alert type="warning" showIcon message={navigationError} />}
    <label htmlFor="asset-image-model">图片模型</label><Select id="asset-image-model" aria-label="图片候选模型" className="w-full" value={modelId} disabled={busy} onChange={setModelId} placeholder="选择支持的图片模型" options={(availability?.models ?? []).map(model => ({ value: model.id, label: model.name + (!model.configured ? '（需配置）' : !model.priced ? '（未登记费用）' : ''), disabled: !model.configured || !model.priced }))} />
    <ProjectChargePicker value={projectId} onChange={setProjectId} upperMinor={upperMinor} onApproval={setCharge} disabled={busy} refreshToken={`${selected?.id}:${selected?.status}`} />
    {price?.supports_references && <AssetImageReferencePicker key={`${modelId}:${projectId}`} projectId={projectId} value={references} onChange={setReferences} onReady={setReferencesReady} maximum={price.max_reference_images ?? 0} disabled={busy} />}
    <TypographyGuide />
    <Space wrap><Select aria-label="图片候选目标槽位" disabled={busy} style={{ minWidth: 240 }} value={slotId} onChange={setSlotId} options={slots.map(row => ({ value: row.id, label: `${angles[row.view_angle] ?? row.view_angle} / ${qualities[row.quality_level] ?? row.quality_level} / ${row.file_id ? '已有图片' : '空槽位'}` }))} /><Select aria-label="图片生成尺寸" disabled={busy} value={size} onChange={setSize} options={allowedSizes.map(value => ({ value, label: value }))} />
      <Select aria-label="图片返回方式" disabled={busy} value={responseFormat} onChange={setResponseFormat} options={responseFormats.map(value => ({ value, label: value === 'url' ? '受限网址下载' : '直接图片内容' }))} /></Space>
    <label htmlFor="asset-image-prompt">图片提示词</label><Input.TextArea id="asset-image-prompt" disabled={busy} rows={4} maxLength={8000} value={prompt} onChange={event => setPrompt(event.target.value)} />
    <Space wrap><Button type="primary" loading={busy} disabled={!!reason} title={reason} onClick={generate}>生成一张候选图片</Button><Button onClick={() => void load()} disabled={busy}>刷新图片任务</Button></Space>
    <Select aria-label="图片候选任务记录" disabled={busy} className="w-full" value={selected?.id} placeholder="尚无图片生成记录" onChange={id => { actionScope.invalidate(); setSelected(jobs.find(row => row.id === id) ?? null) }} options={jobs.map(row => ({ value: row.id, label: `${new Date(row.created_at).toLocaleString()} / ${labels[row.status]} / 尝试${row.attempt}` }))} />
    {total > 10 && <Pagination size="small" current={page} pageSize={10} total={total} onChange={setPage} showSizeChanger={false} />}
    {selected && <Space direction="vertical" className="w-full"><Space wrap><Tag color={selected.status === 'candidate' ? 'blue' : selected.status === 'adopted' ? 'green' : 'default'}>{labels[selected.status]}</Tag><span>{selected.model_name} / 尝试 {selected.attempt}</span>{['queued', 'running'].includes(selected.status) && <Button danger loading={busy} onClick={() => void cancel()}>取消图片生成</Button>}{['failed', 'cancelled'].includes(selected.status) && <Button loading={busy} onClick={retry}>重新生成</Button>}</Space>
      {selected.error && <Alert type="warning" message={selected.error} />}
      {!!selectedReferences.length && <Descriptions size="small" column={1} items={selectedReferences.map(reference => ({ key: reference.position,
        label: `原参考图${reference.position + 1}`, children: `${reference.purpose === 'appearance' ? '外观与主体细节' : reference.purpose === 'composition' ? '构图与空间关系' : '画面风格与色调'} / ${reference.width} × ${reference.height} / ${reference.sha256}` }))} />}
      {selected.project_id && <Space wrap><Link to={`/projects/${encodeURIComponent(selected.project_id)}?tab=settings`}>查看原项目预算</Link>
        {selected.authorized_max_minor && <span>本次授权预留：{chargeMoney(selected.authorized_max_minor)}；实际费用另行核对，采用不再次预留。</span>}
      </Space>}
      {selected.content_url && <><img alt="已校验的生成图片候选" src={workspaceMediaUrl(`${OpenAPI.BASE}${selected.content_url}`)} style={{ maxWidth: '100%', maxHeight: 360, objectFit: 'contain' }} /><Descriptions size="small" column={2} items={[{ key: 'dimensions', label: '实际尺寸', children: `${selected.width} × ${selected.height}` }, { key: 'bytes', label: '真实字节数', children: selected.byte_size }, { key: 'hash', label: 'SHA-256', children: <span style={{ overflowWrap: 'anywhere' }}>{selected.sha256}</span> }]} />{selected.status === 'candidate' && <Button type="primary" loading={busy} onClick={() => void prepareAdoption()}>查看当前图片并采用候选</Button>}{selected.status === 'adopted' && <Alert type="success" message="已明确采用到原定槽位，原文件仍保留" />}</>}
    </Space>}
  </Space></section>
}

/** Give a precise local configuration path without collecting or displaying any API key. */
function TypographyGuide() { return <p className="library-muted">在“模型管理”的图片模型里打开“用于本机单张图片生成”并选择对应协议。网址下载另需维护者设置准确的下载域名；保存配置不会自动调用模型，实际生成前会再次确认。</p> }
