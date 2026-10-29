import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Modal, Pagination, Select, Space } from 'antd'
import { AgentExecutionService } from '../../../services/generated'
import type { AgentExecutionAvailability, AgentRunRead, ModelRead } from '../../../services/generated'
import AgentRunDetails, { runStatus } from '../agents/AgentRunDetails'
import { agentError } from '../agents/agentConfigIO'

/** Inspect prior real probes before explicitly authorizing one fixed, low-volume text request. */
export default function ModelConnectionTestModal({ model, onClose, requestedRun }: { model: ModelRead | null; onClose: () => void; requestedRun?: string | null }) {
  const [state, setState] = useState<AgentExecutionAvailability | null>(null)
  const [runs, setRuns] = useState<AgentRunRead[]>([])
  const [selected, setSelected] = useState<AgentRunRead | null>(null)
  const [error, setError] = useState('')
  const [navigationError, setNavigationError] = useState('')
  const [busy, setBusy] = useState(false)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const key = useRef<string>()

  /** Configuration inspection and history reads issue no request to any provider. */
  const load = async () => {
    if (!model) return
    try {
      const [ready, history] = await Promise.all([AgentExecutionService.getAgentExecutionAvailability({ modelId: model.id }), AgentExecutionService.listModelConnectionTests({ modelId: model.id, page, pageSize: 10 })])
      setState(ready.data ?? null); setRuns(history.data?.items ?? []); setTotal(history.data?.pagination.total ?? 0)
      setSelected(previous => history.data?.items.find(row => row.id === previous?.id) ?? (previous?.id === requestedRun ? previous : history.data?.items[0] ?? null))
    } catch (failure) { setError(agentError(failure, '无法读取模型测试记录')) }
  }
  useEffect(() => { setState(null); setRuns([]); setSelected(null); setError(''); setPage(1); key.current = undefined }, [model?.id])
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
  const probe = () => model && Modal.confirm({ title: '发起一次真实文本连接测试？', content: `将向 ${model.name} 发送固定短文本，输出上限 8 tokens，可能产生少量费用。只验证文本 HTTP 响应，不验证内容质量、图片或视频能力。`, okText: '确认发送一次', cancelText: '取消', onOk: async () => {
    setBusy(true); setError(''); key.current ??= crypto.randomUUID()
    try { const response = await AgentExecutionService.createModelConnectionTest({ modelId: model.id, requestBody: { request_key: key.current, confirm_model_call: true } }); if (!response.data) throw new Error(); setSelected(response.data); setRuns(previous => [response.data!, ...previous.filter(row => row.id !== response.data!.id)].slice(0, 10)); setTotal(previous => previous + 1); setPage(1); key.current = undefined }
    catch (failure) { setError(agentError(failure, '提交结果未确认；再次发送将复用同一标识，避免重复请求')) }
    finally { setBusy(false) }
  } })
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
    {navigationError && <Alert type="warning" showIcon message={navigationError} />}
    <Space><Button type="primary" disabled={!state?.available} title={!state?.available ? state?.message : undefined} loading={busy} onClick={probe}>发送一次连接测试</Button><Button onClick={() => void load()}>刷新</Button></Space>
    <Select aria-label="选择模型连接测试记录" className="w-full" placeholder="连接测试记录" value={selected?.id} onChange={id => setSelected(runs.find(row => row.id === id) ?? null)} options={runs.map(row => ({ value: row.id, label: `${new Date(row.created_at).toLocaleString()} / ${runStatus(row.status)}` }))} />
    {total > 10 && <Pagination size="small" current={page} total={total} pageSize={10} onChange={setPage} showSizeChanger={false} />}
    <AgentRunDetails run={selected} busy={busy} onCancel={() => void cancel()} />
  </Space></Modal>
}
