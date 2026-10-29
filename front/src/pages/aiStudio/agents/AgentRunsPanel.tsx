import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Input, InputNumber, Modal, Pagination, Select, Space } from 'antd'
import { AgentExecutionService } from '../../../services/generated'
import type { AgentConfigRead, AgentExecutionAvailability, AgentRunRead } from '../../../services/generated'
import AgentRunDetails, { runStatus } from './AgentRunDetails'
import { agentError } from './agentConfigIO'
import ProjectChargePicker, { chargeMoney, readProjectCharge, type ProjectChargeApproval } from '../project/ProjectChargePicker'
import ModelSubmissionRecovery, { useModelSubmissionRecovery } from '../project/ModelSubmissionRecovery'

/** Submit saved sequential workflows explicitly and keep their durable node history after refresh. */
export default function AgentRunsPanel({ agent, dirty }: { agent: AgentConfigRead; dirty: boolean }) {
  const [searchParams] = useSearchParams()
  const requestedRun = searchParams.get('agentRun')
  const [availability, setAvailability] = useState<AgentExecutionAvailability | null>(null)
  const [modelId, setModelId] = useState<string>()
  const [projectId, setProjectId] = useState<string>()
  const [charge, setCharge] = useState<ProjectChargeApproval | null>(null)
  const [input, setInput] = useState('')
  const [maxTokens, setMaxTokens] = useState(512)
  const [runs, setRuns] = useState<AgentRunRead[]>([])
  const [selected, setSelected] = useState<AgentRunRead | null>(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [navigationError, setNavigationError] = useState('')
  const recovery = useModelSubmissionRecovery(`agent:${agent.id}`, setBusy, setError)
  const submission = useRef<{ fingerprint: string; key: string }>()
  const retries = useRef<Record<string, { fingerprint: string; key: string }>>({})
  const llmCount = (agent.workflow ?? []).filter(node => node.type === 'llm').length
  const price = availability?.models?.find(model => model.id === (modelId || availability.default_model_id))
  const upperMinor = price?.max_call_minor && llmCount >= 1 && llmCount <= 8 ? price.max_call_minor * llmCount : undefined
  const chargeReady = !!charge?.ready && charge.projectId === projectId && charge.upperMinor === upperMinor

  /** Refresh readiness without contacting a model or reading a credential in the browser. */
  const refreshAvailability = async () => {
    try { const response = await AgentExecutionService.getAgentExecutionAvailability({ modelId }); setAvailability(response.data ?? null); setError('') }
    catch (failure) { setAvailability(null); setError(agentError(failure, '无法读取文本执行服务状态；未发起模型请求')) }
  }
  useEffect(() => { void refreshAvailability() }, [modelId])
  useEffect(() => { if (availability?.default_model_id && !modelId) setModelId(availability.default_model_id) }, [availability?.default_model_id, modelId])

  /** Preserve the selected attempt while refreshing paginated server history. */
  const load = async () => {
    try {
      const response = await AgentExecutionService.listAgentRuns({ agentId: agent.id, page, pageSize: 10 })
      const items = response.data?.items ?? []
      setRuns(items); setTotal(response.data?.pagination.total ?? 0)
      setSelected(previous => items.find(row => row.id === previous?.id) ?? (previous && page === 1 ? previous : items[0] ?? null))
    } catch (failure) { setError(agentError(failure, '读取执行记录失败')) }
  }
  useEffect(() => { void load() }, [agent.id, page])
  useEffect(() => {
    setNavigationError('')
    if (!requestedRun) return
    let active = true
    void AgentExecutionService.getAgentRun({ runId: requestedRun }).then(response => {
      if (!active) return
      if (!response.data || response.data.agent_id !== agent.id || response.data.kind !== 'workflow') { setNavigationError('回跳记录不属于当前 Agent，未选择其他工作流记录'); return }
      setSelected(response.data); document.getElementById('agent-execution')?.scrollIntoView({ block: 'start' })
    }).catch(() => { if (active) setNavigationError('未找到回跳的 Agent 执行记录；没有发起新的执行') })
    return () => { active = false }
  }, [agent.id, requestedRun])
  useEffect(() => {
    if (!selected || !['queued', 'running'].includes(selected.status)) return
    let active = true
    const timer = window.setInterval(() => { void AgentExecutionService.getAgentRun({ runId: selected.id }).then(response => { if (active && response.data) { setSelected(response.data); setRuns(previous => previous.map(row => row.id === response.data!.id ? response.data! : row)) } }).catch(failure => { if (active) setError(agentError(failure, '暂时无法更新执行状态，记录仍保留在服务端')) }) }, 700)
    return () => { active = false; window.clearInterval(timer) }
  }, [selected?.id, selected?.status])

  /** Reuse an uncertain admission key so a lost response cannot duplicate the same billable work. */
  const run = () => {
    if (!chargeReady || !charge || !price?.price_fingerprint) return
    const approval = charge, policy = price.price_fingerprint, chosenModel = price.id
    Modal.confirm({ title: '执行已保存的 Agent？', content: `费用归属「${approval.projectName}」，为完整 ${llmCount} 次文本调用预留 ${chargeMoney(approval.upperMinor!)}。每次最多输出 ${maxTokens} tokens，使用已保存版本 ${agent.version}；结果未知时继续保留额度。`, okText: '确认预留并执行', cancelText: '取消', onOk: async () => {
    setBusy(true); setError('')
    const fingerprint = JSON.stringify([agent.id, agent.version, chosenModel, input, maxTokens, approval.projectId, policy])
    if (submission.current?.fingerprint !== fingerprint) submission.current = { fingerprint, key: crypto.randomUUID() }
    const requestBody = { request_key: submission.current.key, input_text: input, model_id: chosenModel, expected_version: agent.version, max_tokens: maxTokens, confirm_model_call: true,
      project_id: approval.projectId, expected_price_fingerprint: policy }
    const perform = async () => {
      const response = await AgentExecutionService.createAgentRun({ agentId: agent.id, requestBody })
      if (!response.data) throw new Error()
      if (!recovery.isCurrent()) return
      setSelected(response.data); setRuns(previous => [response.data!, ...previous.filter(row => row.id !== response.data!.id)].slice(0, 10)); setPage(1); void load()
      if (submission.current?.key === requestBody.request_key) submission.current = undefined
      recovery.forget(requestBody.request_key)
    }
    try { await perform() }
    catch (failure) {
      recovery.remember({ key: requestBody.request_key, label: `${agent.name} / 版本 ${agent.version}`, projectName: approval.projectName, upperMinor: approval.upperMinor!, recover: perform })
      setError(agentError(failure, '提交结果未确认，可恢复原提交以避免重复执行'))
    }
    finally { setBusy(false) }
    } })
  }

  /** Cancel durable state first; an already dispatched provider request may still be charged. */
  const cancel = async () => {
    if (!selected) return
    setBusy(true)
    try { const response = await AgentExecutionService.cancelAgentRun({ runId: selected.id }); setSelected(response.data ?? selected) }
    catch (failure) { setError(agentError(failure, '取消失败，请刷新查看真实状态')) }
    finally { setBusy(false) }
  }
  /** A retry is a new full attempt and requires explicit acknowledgement of unknown prior effects. */
  const retry = async () => {
    if (recovery.atCapacity) { setError('请先恢复待确认的原提交，再创建其他尝试'); return }
    const original = selected
    if (!original?.project_id) { setError('这条历史记录没有项目费用授权，请选择项目后新建执行'); return }
    setBusy(true); setError('')
    try {
      const ready = await AgentExecutionService.getAgentExecutionAvailability({ modelId: original.model_id, callKind: original.kind === 'connection_test' ? 'model_connection_test' : 'agent_text' })
      const quote = ready.data?.models?.find(model => model.id === original.model_id)
      if (!ready.data?.available || !quote?.price_fingerprint || !quote.max_call_minor) { setError(ready.data?.message || '原模型费用配置尚未就绪'); return }
      const approval = await readProjectCharge(original.project_id, quote.max_call_minor * (original.nodes ?? []).filter(node => node.type === 'llm').length)
      if (!approval.ready) { setError(approval.reason || '原项目预算尚未就绪'); return }
      const policy = quote.price_fingerprint
      Modal.confirm({ title: '从头重新执行？', content: `仍归属原项目「${approval.projectName}」，再次完整预留 ${chargeMoney(approval.upperMinor!)}。已经成功的节点也会重新调用；先前未知费用继续保留，原任务与记录不变。`, okText: '确认再次预留并重试', cancelText: '取消', onOk: async () => {
        setBusy(true)
        const fingerprint = JSON.stringify([original.id, original.project_id, policy])
        if (retries.current[original.id]?.fingerprint !== fingerprint) retries.current[original.id] = { fingerprint, key: crypto.randomUUID() }
        const requestBody = { request_key: retries.current[original.id].key, acknowledge_duplicate_charge: true, expected_price_fingerprint: policy }
        const perform = async () => {
          const response = await AgentExecutionService.retryAgentRun({ runId: original.id, requestBody })
          if (!response.data) throw new Error('重试结果尚未返回，请核对原请求')
          if (!recovery.isCurrent()) return
          setSelected(response.data); setRuns(previous => [response.data!, ...previous.filter(row => row.id !== response.data!.id)].slice(0, 10)); void load()
          if (retries.current[original.id]?.key === requestBody.request_key) delete retries.current[original.id]
          recovery.forget(requestBody.request_key)
        }
        try { await perform() }
        catch (failure) {
          recovery.remember({ key: requestBody.request_key, label: `${original.agent_name} / 重试 ${original.attempt + 1}`, projectName: approval.projectName, upperMinor: approval.upperMinor!, recover: perform })
          setError(agentError(failure, '重试提交结果未确认，可恢复原提交'))
        }
        finally { setBusy(false) }
      } })
    } catch (failure) { setError(agentError(failure, '无法核对原项目或模型费用，未提交重试')) }
    finally { setBusy(false) }
  }

  const disabledReason = recovery.atCapacity ? '请先恢复待确认的原提交' : dirty ? '请先保存当前修改，再执行对应版本' : llmCount < 1 || llmCount > 8 ? '执行仅支持 1 至 8 个顺序 LLM 节点' : !availability?.available ? availability?.message || '正在检查文本执行服务'
    : !price?.price_fingerprint ? '模型费用配置尚未就绪' : !chargeReady ? charge?.reason || '请选择费用归属项目并核对完整预留' : !input.trim() ? '请填写测试输入' : undefined
  return <section id="agent-execution" className="library-form-group"><h2 className="library-section-title">执行与节点记录</h2>
    <Space direction="vertical" className="w-full" size="middle">
      <Alert type={availability?.available ? 'info' : 'warning'} showIcon message={availability?.message || '正在检查文本执行服务；尚未调用模型'} description="仅执行顺序文本节点，不运行任意代码、工具、图片或视频。输入输出会保存在本地执行记录。" />
      {error && <Alert type="error" showIcon message={error} closable onClose={() => setError('')} />}
      <ModelSubmissionRecovery items={recovery.items} busy={busy} onRecover={recovery.recover} />
      {navigationError && <Alert type="warning" showIcon message={navigationError} />}
      <label htmlFor="agent-run-model">文本模型</label><Select id="agent-run-model" aria-label="Agent 执行文本模型" className="w-full" value={modelId} onChange={setModelId} placeholder="选择文本模型" options={(availability?.models ?? []).map(model => ({ value: model.id, label: `${model.name} / ${model.provider_name}${!model.configured ? '（未配置）' : !model.priced ? '（未登记费用）' : ''}`, disabled: !model.configured || !model.priced }))} />
      <ProjectChargePicker value={projectId} onChange={setProjectId} upperMinor={upperMinor} onApproval={setCharge} disabled={busy} refreshToken={`${selected?.id}:${selected?.status}`} />
      <label htmlFor="agent-run-input">测试输入</label><Input.TextArea id="agent-run-input" aria-label="测试输入（剧本片段或 JSON）" value={input} onChange={event => setInput(event.target.value)} rows={4} maxLength={32000} placeholder="填写将传给第一个模型节点的文本" />
      <label>每个模型节点输出上限 <InputNumber aria-label="Agent 输出 tokens 上限" min={8} max={2048} value={maxTokens} onChange={value => value !== null && setMaxTokens(value)} /></label>
      <Space wrap><Button type="primary" loading={busy} disabled={!!disabledReason} title={disabledReason} onClick={run}>运行已保存版本</Button><Button onClick={() => { void refreshAvailability(); void load() }}>刷新执行状态</Button></Space>
      <Select aria-label="选择 Agent 执行记录" className="w-full" value={selected?.id} onChange={id => setSelected(runs.find(row => row.id === id) ?? null)} placeholder="执行记录" options={runs.map(row => ({ value: row.id, label: `${new Date(row.created_at).toLocaleString()} / ${runStatus(row.status)} / 尝试 ${row.attempt}` }))} />
      {total > 10 && <Pagination size="small" current={page} pageSize={10} total={total} onChange={setPage} showSizeChanger={false} />}
      <AgentRunDetails run={selected} busy={busy} onCancel={() => void cancel()} onRetry={retry} />
    </Space>
  </section>
}
