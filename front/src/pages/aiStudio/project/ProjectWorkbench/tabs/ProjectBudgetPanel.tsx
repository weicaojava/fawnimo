import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Descriptions, InputNumber, Space, Table, Tag, Typography, message } from 'antd'
import { VideoRuntimeService as Budget, type ProjectBudgetRead, type ProjectBudgetPage, type ProjectBudgetEntryRead } from '../../../../../services/generated'
import { agentError } from '../../../agents/agentConfigIO'
import { useIdentity } from '../../../../../identity/IdentityGate'

/** Format the ledger's integer minor units without changing its accounting precision. */
const money = (minor: number) => `${(minor / 100).toFixed(2)} 元`
/** Preserve imported bill amounts beyond floating-point precision using server decimal strings. */
const exactMoney = (minor: string) => {
  const negative = minor.startsWith('-')
  const digits = (negative ? minor.slice(1) : minor).padStart(3, '0')
  return `${negative ? '-' : ''}${digits.slice(0, -2)}.${digits.slice(-2)} 元`
}
const states = { reserved: '已预留', unknown: '待核实，保留额度', released: '已释放', settled: '账本已结算' }
const callLabels = { agent_text: '文本工作流', model_connection_test: '连接测试', asset_image: '图片候选', video_vision: '视频理解', video_asr: '语音转写' }
/** Explain each ledger row without exposing a private provider request identifier. */
const sourceLabel = (row: ProjectBudgetEntryRead) => row.source === 'model_call' ? row.call_kind ? callLabels[row.call_kind] : '模型调用'
  : row.source === 'bill_adjustment' ? '账单差额' : row.source === 'durable_video' ? '视频任务' : '来源待核对'

/** Let members inspect one project's ceiling and allow only the trusted owner to change it. */
export function ProjectBudgetPanel({ projectId }: { projectId: string }) {
  const identity = useIdentity()
  const [budget, setBudget] = useState<ProjectBudgetRead | null>(null)
  const [page, setPage] = useState<ProjectBudgetPage | null>(null)
  const [cursors, setCursors] = useState<Array<string | undefined>>([undefined])
  const [ceiling, setCeiling] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const mounted = useRef(true)
  const canEdit = !identity || identity.role === 'owner'
  useEffect(() => () => { mounted.current = false; generation.current++ }, [])

  /** Refresh one project accounting snapshot and one bounded page without fabricating empty balances on failure. */
  const load = useCallback(async (after?: string, resetInput = true) => {
    const request = ++generation.current
    setLoading(true); setError('')
    try {
      const [balance, entries] = await Promise.all([
        Budget.getVideoProjectBudget({ projectId }), Budget.getProjectBudgetEntries({ projectId, after, limit: 20 }),
      ])
      if (!mounted.current || request !== generation.current) return
      if (!balance.data || !entries.data) throw new Error('预算明细尚未返回，请刷新重试')
      setBudget(balance.data); setPage(entries.data)
      if (resetInput) setCeiling(balance.data.limit_minor / 100)
    } catch (cause) {
      if (mounted.current && request === generation.current) setError(agentError(cause, '读取项目预算失败，请刷新重试'))
    } finally { if (mounted.current && request === generation.current) setLoading(false) }
  }, [projectId])
  useEffect(() => { mounted.current = true; void load() }, [load])

  /** Save the explicit ceiling only; a stale revision keeps the entered amount for review. */
  async function save() {
    if (!budget || !canEdit || ceiling === null || !Number.isFinite(ceiling)) return
    setSaving(true); setError('')
    try {
      const result = await Budget.saveVideoProjectBudget({ projectId, requestBody: {
        expected_revision: budget.revision, limit_minor: Math.round(ceiling * 100),
      } })
      if (!mounted.current) return
      if (!result.data) throw new Error('保存结果未知，请刷新核对后再操作')
      setBudget(result.data); setCeiling(result.data.limit_minor / 100)
      message.success('项目预算已保存，没有发起生成或扣费')
    } catch (cause) {
      if (mounted.current) setError(agentError(cause, '保存失败，输入仍保留；请刷新核对最新预算'))
    } finally { if (mounted.current) setSaving(false) }
  }

  return <Card title="项目预算" className="mt-4">
    <Space direction="vertical" size="middle" className="w-full">
      <Typography.Paragraph type="secondary" className="!mb-0">
        预算仅用于当前项目，各项目独立设置上限、预留和结算，彼此不占额度。保存预算不会启动生成或扣费。
        文本、图片及视频理解等调用仍待接入，当前保持关闭。
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message={error} />}
      {budget && <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }} items={[
        { key: 'limit', label: '项目累计上限', children: money(budget.limit_minor) },
        { key: 'held', label: '任务预留', children: money(budget.reserved_minor) },
        { key: 'spent', label: '账本已结算', children: money(budget.spent_minor) },
        { key: 'available', label: '剩余额度', children: money(budget.available_minor) },
        { key: 'unknown', label: '待核实费用', children: `${budget.uncertain_jobs} 条（继续保留额度）` },
        { key: 'verification', label: '账单记录', children: (budget.bill_receipt_count ?? 0) > 0 ? `${budget.bill_receipt_count} 条已核对` : '尚无核对记录' },
        ...((budget.bill_receipt_count ?? 0) > 0 ? [{ key: 'billed', label: '账单已核对金额', children: exactMoney(budget.bill_receipt_total_minor ?? '0') }] : []),
        ...((budget.unfunded_bill_minor ?? '0') !== '0' ? [{ key: 'unfunded', label: '账单待补入金额', children: exactMoney(budget.unfunded_bill_minor ?? '0') }] : []),
        ...((budget.unfunded_bill_minor ?? '0') !== '0' ? [{ key: 'after-bills', label: '计入待核对账单后', children: exactMoney(budget.available_after_bills_minor ?? '0') }] : []),
      ]} />}
      {budget && (budget.pending_bill_reviews ?? 0) > 0 && <Alert type="warning" showIcon
        message={(budget.available_after_bills_minor ?? '').startsWith('-') ? '账单金额超出当前项目授权' : '项目账单仍待核对'}
        description={(budget.available_after_bills_minor ?? '').startsWith('-')
          ? `当前授权还差 ${exactMoney((budget.available_after_bills_minor ?? '').slice(1))}。请先补足预算，再核对原账单；新模型调用保持暂停。`
          : '请先核对重复、冲突或尚未补入的账单记录，再继续新的模型调用。'} />}
      {budget && (budget.price_violation_count ?? 0) > 0 && <Alert type="info" showIcon message="曾有账单超过原预留金额" description="后续调用需先核对该模型的费用上界；账单全额与原记录均已保留。" />}
      <Space wrap>
        {canEdit && <><Typography.Text>项目累计上限（元）</Typography.Text>
          <InputNumber aria-label="项目累计上限（元）" min={0} max={1000000} precision={2} value={ceiling}
            disabled={loading || saving || (!budget || budget.currency !== 'CNY')} onChange={value => setCeiling(value)} />
          <Button type="primary" loading={saving} disabled={loading || (!budget || budget.currency !== 'CNY') || ceiling === null} onClick={() => void save()}>保存项目预算</Button></>}
        <Button loading={loading} disabled={saving} onClick={() => { setCursors([undefined]); void load() }}>刷新项目预算</Button>
        {!canEdit && <Typography.Text type="secondary">当前角色可查看，调整上限请联系所有者。</Typography.Text>}
      </Space>
      <Typography.Text strong>本项目额度明细</Typography.Text>
      <Table size="small" loading={loading} rowKey="job_id" pagination={false} scroll={{ x: 760 }} dataSource={page?.items ?? []}
        locale={{ emptyText: error ? '暂时无法读取明细' : '本项目尚无费用预留' }} columns={[
          { title: '费用记录', dataIndex: 'job_id', ellipsis: true, width: 200 },
          { title: '来源', render: (_value, row) => sourceLabel(row), width: 110 },
          { title: '状态', dataIndex: 'state', render: (state: keyof typeof states) => <Tag color={state === 'unknown' ? 'orange' : 'default'}>{states[state]}</Tag> },
          { title: '预留 / 补录', dataIndex: 'quote_minor', render: (minor: number, row) => `${row.source === 'bill_adjustment' ? '补录 ' : ''}${row.quote_minor_exact ? exactMoney(row.quote_minor_exact) : money(minor)}` },
          { title: '结算', dataIndex: 'actual_minor', render: (minor: number | null, row) => minor === null ? row.state === 'released' ? '未结算' : '待确认' : row.actual_minor_exact ? exactMoney(row.actual_minor_exact) : money(minor) },
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
