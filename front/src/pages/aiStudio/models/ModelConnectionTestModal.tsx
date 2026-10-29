import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Modal, Pagination, Select, Space } from 'antd'
import { AgentExecutionService } from '../../../services/generated'
import type { AgentExecutionAvailability, AgentRunRead, ModelRead } from '../../../services/generated'
import AgentRunDetails, { runStatus } from '../agents/AgentRunDetails'
import { agentError } from '../agents/agentConfigIO'
import ProjectChargePicker, { chargeMoney, type ProjectChargeApproval } from '../project/ProjectChargePicker'
import ModelSubmissionRecovery, { useModelSubmissionRecovery } from '../project/ModelSubmissionRecovery'

/** Inspect prior real probes before explicitly authorizing one fixed, low-volume text request. */
export default function ModelConnectionTestModal({ model, onClose, requestedRun }: { model: ModelRead | null; onClose: () => void; requestedRun?: string | null }) {
  const [state, setState] = useState<AgentExecutionAvailability | null>(null)
  const [runs, setRuns] = useState<AgentRunRead[]>([])
  const [selected, setSelected] = useState<AgentRunRead | null>(null)
  const [error, setError] = useState('')
  const [navigationError, setNavigationError] = useState('')
  const [busy, setBusy] = useState(false)
  const [projectId, setProjectId] = useState<string>()
  const [charge, setCharge] = useState<ProjectChargeApproval | null>(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const recovery = useModelSubmissionRecovery(`probe:${model?.id ?? ''}`, setBusy, setError)
  const key = useRef<{ fingerprint: string; key: string }>()
  const price = state?.models?.find(row => row.id === model?.id)
  const upperMinor = price?.max_call_minor ?? undefined
  const chargeReady = !!charge?.ready && charge.projectId === projectId && charge.upperMinor === upperMinor

  /** Configuration inspection and history reads issue no request to any provider. */
  const load = async () => {
    if (!model) return
    try {
      const [ready, history] = await Promise.all([AgentExecutionService.getAgentExecutionAvailability({ modelId: model.id, callKind: 'model_connection_test' }), AgentExecutionService.listModelConnectionTests({ modelId: model.id, page, pageSize: 10 })])
      setState(ready.data ?? null); setRuns(history.data?.items ?? []); setTotal(history.data?.pagination.total ?? 0)
      setSelected(previous => history.data?.items.find(row => row.id === previous?.id) ?? (previous?.id === requestedRun ? previous : history.data?.items[0] ?? null))
    } catch (failure) { setError(agentError(failure, '无法读取模型测试记录')) }
  }
  useEffect(() => { setState(null); setRuns([]); setSelected(null); setError(''); setPage(1); setProjectId(undefined); setCharge(null); key.current = undefined }, [model?.id])
  useEffect(() => { void load() }, [model?.id, page])
  useEffect(() => {
    setNavigationError('')
    if (!model || !requestedRun) return
    let active = true
    void AgentExecutionService.getAgentRun({ runId: requestedRun }).then(response => {
      if (!active) return
      if (!response.data || response.data.model_id !== model.id || response.data.kind !== 'connection_test') { setNavigationError('回跳记录不属于当前模型的连接测试，未选择该记录'); return }
      setSelected(response.data)
    }).catch(() => { if (active) setNavigationError('未找到回跳的模型连接测试记录；没有重新发送测试') })
    return () => { active = false }
  }, [model?.id, requestedRun])
  useEffect(() => {
    if (!selected || !['queued', 'running'].includes(selected.status)) return
    let active = true
    const timer = window.setInterval(() => { void AgentExecutionService.getAgentRun({ runId: selected.id }).then(response => { if (active && response.data) { setSelected(response.data); setRuns(previous => previous.map(row => row.id === response.data!.id ? response.data! : row)) } }).catch(failure => { if (active) setError(agentError(failure, '测试状态暂时无法更新，记录仍保留')) }) }, 700)
    return () => { active = false; window.clearInterval(timer) }
  }, [selected?.id, selected?.status])

  /** A deliberate confirmation is required before any model HTTP POST can leave the server. */
  const probe = () => {
    if (!model || !chargeReady || !charge || !price?.price_fingerprint) return
    const approval = charge, policy = price.price_fingerprint
    Modal.confirm({ title: '发起一次真实文本连接测试？', content: `向 ${model.name} 发送固定短文本，输出上限 8 tokens。费用归属「${approval.projectName}」，预留 ${chargeMoney(approval.upperMinor!)}。只验证文本响应，不验证内容质量、图片或视频能力。`, okText: '确认预留并发送一次', cancelText: '取消', onOk: async () => {
    setBusy(true); setError('')
    const fingerprint = JSON.stringify([model.id, approval.projectId, policy])
    if (key.current?.fingerprint !== fingerprint) key.current = { fingerprint, key: crypto.randomUUID() }
    const requestBody = { request_key: key.current.key, confirm_model_call: true, project_id: approval.projectId, expected_price_fingerprint: policy }
    const perform = async () => {
      const response = await AgentExecutionService.createModelConnectionTest({ modelId: model.id, requestBody })
      if (!response.data) throw new Error()
      if (!recovery.isCurrent()) return
      setSelected(response.data); setRuns(previous => [response.data!, ...previous.filter(row => row.id !== response.data!.id)].slice(0, 10)); setPage(1); void load()
      if (key.current?.key === requestBody.request_key) key.current = undefined
      recovery.forget(requestBody.request_key)
    }
    try { await perform() }
    catch (failure) {
      recovery.remember({ key: requestBody.request_key, label: '文本连接测试 / ' + model.name, projectName: approval.projectName, upperMinor: approval.upperMinor!, recover: perform })
      setError(agentError(failure, '提交结果未确认，可恢复原提交'))
    }
    finally { setBusy(false) }
    } })
  }
  /** Cancel one queued or in-flight probe without assuming upstream billing can be revoked. */
  const cancel = async () => {
    if (!selected) return
    setBusy(true)
    try { const response = await AgentExecutionService.cancelAgentRun({ runId: selected.id }); setSelected(response.data ?? selected) }
    catch (failure) { setError(agentError(failure, '取消失败，请刷新查看状态')) }
    finally { setBusy(false) }
  }

  return <Modal title={`文本连接测试 / ${model?.name ?? ''}`} open={!!model} onCancel={onClose} width={900} footer={null} destroyOnClose><Space direction="vertical" className="w-full" size="middle">
    <Alert type="info" showIcon message="测试会真实调用模型，输出上限 8 tokens" description="本地测试服务通过仅证明软件接线；真实模型响应与图片、视频生成资格需要分别验收。打开此面板只读取配置与历史。" />
    <Alert type={state?.available ? 'info' : 'warning'} message={state?.message || '正在检查文本连接配置'} />
    {error && <Alert type="error" message={error} />}
    <ModelSubmissionRecovery items={recovery.items} busy={busy} onRecover={recovery.recover} />
    {navigationError && <Alert type="warning" showIcon message={navigationError} />}
    <ProjectChargePicker value={projectId} onChange={setProjectId} upperMinor={upperMinor} onApproval={setCharge} disabled={busy} refreshToken={`${selected?.id}:${selected?.status}`} />
    <Space><Button type="primary" disabled={recovery.atCapacity || !state?.available || !price?.price_fingerprint || !chargeReady} title={recovery.atCapacity ? '请先恢复待确认的原提交' : !state?.available ? state?.message : charge?.reason} loading={busy} onClick={probe}>发送一次连接测试</Button><Button onClick={() => void load()}>刷新</Button></Space>
    <Select aria-label="选择模型连接测试记录" className="w-full" placeholder="连接测试记录" value={selected?.id} onChange={id => setSelected(runs.find(row => row.id === id) ?? null)} options={runs.map(row => ({ value: row.id, label: `${new Date(row.created_at).toLocaleString()} / ${runStatus(row.status)}` }))} />
    {total > 10 && <Pagination size="small" current={page} total={total} pageSize={10} onChange={setPage} showSizeChanger={false} />}
    <AgentRunDetails run={selected} busy={busy} onCancel={() => void cancel()} />
  </Space></Modal>
}
