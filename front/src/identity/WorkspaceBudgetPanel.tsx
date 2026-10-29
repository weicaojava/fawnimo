import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Descriptions, InputNumber, Space, Table, Tag, Typography, message } from 'antd'
import { WorkspaceBudgetService as Budget, type WorkspaceBudgetRead, type WorkspaceBudgetPage } from '../services/generated'
import { agentError } from '../pages/aiStudio/agents/agentConfigIO'
import { useIdentity } from './IdentityGate'

/** Format the ledger's integer minor units without changing its accounting precision. */
const money = (minor: number) => `${(minor / 100).toFixed(2)} 元`
const states = { reserved: '已预留', unknown: '待核实，保留额度', released: '已释放', settled: '账本已结算' }

/** Let members inspect their shared ceiling and allow only the trusted owner to change it. */
export function WorkspaceBudgetPanel() {
  const identity = useIdentity()
  const [budget, setBudget] = useState<WorkspaceBudgetRead | null>(null)
  const [page, setPage] = useState<WorkspaceBudgetPage | null>(null)
  const [cursors, setCursors] = useState<Array<string | undefined>>([undefined])
  const [ceiling, setCeiling] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const mounted = useRef(true)
  const canEdit = identity?.role === 'owner'
  useEffect(() => () => { mounted.current = false; generation.current++ }, [])

  /** Refresh one accounting snapshot and one bounded page without fabricating empty balances on failure. */
  const load = useCallback(async (after?: string, resetInput = true) => {
    const request = ++generation.current
    setLoading(true); setError('')
    try {
      const [balance, entries] = await Promise.all([
        Budget.getWorkspaceBudget(), Budget.getWorkspaceBudgetEntries({ after, limit: 20 }),
      ])
      if (!mounted.current || request !== generation.current) return
      if (!balance.data || !entries.data) throw new Error('预算明细尚未返回，请刷新重试')
      setBudget(balance.data); setPage(entries.data)
      if (resetInput) setCeiling(balance.data.limit_minor / 100)
    } catch (cause) {
      if (mounted.current && request === generation.current) setError(agentError(cause, '读取空间预算失败，请刷新重试'))
    } finally { if (mounted.current && request === generation.current) setLoading(false) }
  }, [])
  useEffect(() => { mounted.current = true; void load() }, [load, identity?.workspace_id])

  /** Save the explicit ceiling only; a stale revision keeps the entered amount for review. */
  async function save() {
    if (!budget || !canEdit || ceiling === null || !Number.isFinite(ceiling)) return
    setSaving(true); setError('')
    try {
      const result = await Budget.saveWorkspaceBudget({ requestBody: {
        expected_revision: budget.revision, limit_minor: Math.round(ceiling * 100),
      } })
      if (!mounted.current) return
      if (!result.data) throw new Error('保存结果未知，请刷新核对后再操作')
      setBudget(result.data); setCeiling(result.data.limit_minor / 100)
      message.success('空间预算已保存，没有发起生成或扣费')
    } catch (cause) {
      if (mounted.current) setError(agentError(cause, '保存失败，输入仍保留；请刷新核对最新预算'))
    } finally { if (mounted.current) setSaving(false) }
  }

  return <Card title="工作空间预算" className="mt-4">
    <Space direction="vertical" size="middle" className="w-full">
      <Typography.Paragraph type="secondary" className="!mb-0">
        本空间的视频任务共用此上限，并受各工程预算限制。保存上限不会启动生成或扣费。
        文本、图片及视频理解等调用仍待接入，当前保持关闭。
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message={error} />}
      {budget && <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }} items={[
        { key: 'limit', label: '空间累计上限', children: money(budget.limit_minor) },
        { key: 'held', label: '任务预留', children: money(budget.reserved_minor) },
        { key: 'spent', label: '账本已结算', children: money(budget.spent_minor) },
        { key: 'available', label: '可预留余额', children: money(budget.available_minor) },
        { key: 'unknown', label: '待核实任务', children: `${budget.uncertain_jobs} 项（继续保留额度）` },
        { key: 'verification', label: '供应商账单', children: '尚未核对' },
      ]} />}
      <Space wrap>
        {canEdit && <><Typography.Text>空间累计上限（元）</Typography.Text>
          <InputNumber aria-label="空间累计上限（元）" min={0} max={1000000} precision={2} value={ceiling}
            disabled={loading || saving || !budget?.managed} onChange={value => setCeiling(value)} />
          <Button type="primary" loading={saving} disabled={loading || !budget?.managed || ceiling === null} onClick={() => void save()}>保存空间上限</Button></>}
        <Button loading={loading} disabled={saving} onClick={() => { setCursors([undefined]); void load() }}>刷新空间预算</Button>
        {!canEdit && <Typography.Text type="secondary">当前角色可查看，调整上限请联系空间所有者。</Typography.Text>}
      </Space>
      <Typography.Text strong>额度明细</Typography.Text>
      <Table size="small" loading={loading} rowKey="job_id" pagination={false} scroll={{ x: 720 }} dataSource={page?.items ?? []}
        locale={{ emptyText: error ? '暂时无法读取明细' : '尚无视频费用预留；保存预算不会创建任务' }} columns={[
          { title: '工程', dataIndex: 'project_name', render: (name, row) => name || row.project_id },
          { title: '任务标识', dataIndex: 'job_id', ellipsis: true, width: 200 },
          { title: '状态', dataIndex: 'state', render: (state: keyof typeof states) => <Tag color={state === 'unknown' ? 'orange' : 'default'}>{states[state]}</Tag> },
          { title: '预留', dataIndex: 'quote_minor', render: money },
          { title: '结算', dataIndex: 'actual_minor', render: (minor: number | null, row) => minor === null ? row.state === 'released' ? '未结算' : '待确认' : money(minor) },
        ]} />
      <Space>
        <Button disabled={loading || saving || cursors.length < 2} onClick={() => {
          const previous = cursors.slice(0, -1); setCursors(previous); void load(previous[previous.length-1], false)
        }}>上一页</Button>
        <Button disabled={loading || saving || !page?.next_after} onClick={() => {
          if (!page?.next_after) return
          setCursors([...cursors, page.next_after]); void load(page.next_after, false)
        }}>下一页</Button>
      </Space>
      <Typography.Paragraph type="secondary" className="!mb-0">取消意图或结果就绪不等于免费。提交结果未知时继续预留，只有确认未被接受或可信费用结算后才释放相应额度。</Typography.Paragraph>
    </Space>
  </Card>
}
