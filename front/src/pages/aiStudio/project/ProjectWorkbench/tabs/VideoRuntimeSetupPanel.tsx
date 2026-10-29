import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Collapse, Descriptions, InputNumber, Space, Tag, Typography, message } from 'antd'
import { VideoRuntimeService as V } from '../../../../../services/generated'
import type { ProjectBudgetRead, VideoRuntimeSetupRead } from '../../../../../services/generated'
import { agentError, downloadAgentConfig } from '../../../agents/agentConfigIO'
import { Link } from 'react-router-dom'
import { useIdentity } from '../../../../../identity/IdentityGate'

/** Display one project's actual ledger and the separately configured video executor. */
export function VideoRuntimeSetupPanel({ projectId, onSaved }: { projectId: string; onSaved: () => void }) {
  const identity = useIdentity()
  const [setup, setSetup] = useState<VideoRuntimeSetupRead | null>(null)
  const [budget, setBudget] = useState<ProjectBudgetRead | null>(null)
  const [ceiling, setCeiling] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(true)
  const sequence = useRef(0)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; sequence.current++ } }, [])

  /** Use server clocks and balances; a failed fetch never becomes a fabricated zero budget. */
  const load = useCallback(async () => {
    const current = ++sequence.current
    setLoading(true); setError('')
    try {
      const [runtimeResponse, budgetResponse] = await Promise.all([
        V.getVideoRuntimeSetup(), V.getVideoProjectBudget({ projectId }),
      ])
      if (!mounted.current || current !== sequence.current) return
      if (!runtimeResponse.data || !budgetResponse.data) throw new Error('预算或服务配置没有返回数据')
      setSetup(runtimeResponse.data); setBudget(budgetResponse.data)
      setCeiling(budgetResponse.data.limit_minor / 100)
    } catch (failure) {
      if (mounted.current && current === sequence.current) setError(agentError(failure, '读取预算或执行配置失败，请刷新重试。'))
    } finally { if (mounted.current && current === sequence.current) setLoading(false) }
  }, [projectId])
  useEffect(() => { void load() }, [load])

  /** Save only the explicit ceiling with CAS; preserve entered values on conflicts. */
  async function save() {
    if (!budget || ceiling === null || !Number.isFinite(ceiling)) return
    setBusy(true); setError('')
    try {
      const result = await V.saveVideoProjectBudget({ projectId, requestBody: {
        expected_revision: budget.revision, limit_minor: Math.round(ceiling * 100),
      } })
      if (!mounted.current) return
      if (!result.data) throw new Error('预算保存没有返回数据')
      setBudget(result.data); setCeiling(result.data.limit_minor / 100)
      message.success('工程预算已保存；没有发起生成或扣费。'); onSaved()
    } catch (failure) {
      if (mounted.current) setError(agentError(failure, '预算保存失败。输入仍保留，请核对最新金额后重试。'))
    } finally { if (mounted.current) setBusy(false) }
  }

  /** Download the actual inert server template; a template contains neither a key nor a price. */
  async function downloadTemplate() {
    setBusy(true)
    try { downloadAgentConfig(await V.getVideoConfigurationTemplate(), 'video-runtime.example') }
    catch (failure) { if (mounted.current) setError(agentError(failure, '配置模板下载失败。')) }
    finally { if (mounted.current) setBusy(false) }
  }

  const money = (minor: number) => `${(minor / 100).toFixed(2)} 元`
  return <Collapse className="mb-3" items={[{ key: 'video-budget', label: <Space wrap>
    <span>视频服务与工程预算</span><Tag color={setup?.running ? 'green' : 'default'}>{setup?.running ? '生成服务已启用' : '准备阶段'}</Tag>
  </Space>, children: <Space direction="vertical" size="middle" className="w-full">
    <Alert type={setup?.running ? 'success' : 'info'} showIcon message={setup?.message || '正在读取生成服务配置'}
      description={setup?.output_description} />
    {error && <Alert type="error" showIcon message={error} />}
    {budget && <Descriptions column={{ xs: 1, sm: 2, md: 3 }} size="small" items={[
      { key: 'limit', label: '累计预算上限', children: money(budget.limit_minor) },
      { key: 'reserved', label: '任务预留', children: money(budget.reserved_minor) },
      { key: 'spent', label: '账本已结算', children: money(budget.spent_minor) },
      { key: 'available', label: '可预留余额', children: money(budget.available_minor) },
      { key: 'holds', label: '未结清任务', children: `${budget.unsettled_jobs} 项（其中 ${budget.uncertain_jobs} 项结果或费用待确认）` },
      { key: 'quote', label: '单次预留', children: setup?.quote_minor != null ? money(setup.quote_minor) : '尚未核定' },
    ]} />}
    <Space wrap>
      <Typography.Text>累计预算上限（元）</Typography.Text>
      <InputNumber aria-label="累计预算上限（元）" min={0} max={1000000} precision={2} step={1} value={ceiling}
        disabled={loading || busy || !budget || budget.currency !== 'CNY'} onChange={value => setCeiling(value)} />
      <Button type="primary" loading={busy} disabled={loading || !budget || ceiling === null || budget.currency !== 'CNY'} onClick={() => void save()}>保存预算（不生成）</Button>
      <Button loading={loading} disabled={busy} onClick={() => void load()}>刷新预算与配置</Button>
      <Button disabled={busy} onClick={() => void downloadTemplate()}>下载本机配置模板</Button>
    </Space>
    {identity && <Typography.Paragraph type="secondary" className="!mb-0">
      本工程的视频预留同时受空间总预算限制。请到<Link to="/settings">系统设置 → 工作空间预算</Link>查看所有工程共用的余额与明细。
    </Typography.Paragraph>}
    <Typography.Paragraph type="secondary" className="!mb-0">
      预算限制本工程的视频任务预留；这里的账本金额尚未与服务商账单核对。保存预算不会启动任务，未确认的预留不会被重置。
      {setup?.quote_note ? ` 预留依据：${setup.quote_note}` : ' 实际模型调用前须先核定单次预留，再启用本机执行配置。'}
    </Typography.Paragraph>
    <Typography.Paragraph type="secondary" className="!mb-0">执行器已启用也不代表模型画面质量已经验收。请到分镜工作室保存并编译新的镜头计划，再明确提交单镜；原准备计划不会自动转换或自动生成。</Typography.Paragraph>
  </Space> }]} />
}
