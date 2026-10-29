import { useEffect, useId, useState } from 'react'
import { Alert, Button, Descriptions, Pagination, Select, Space } from 'antd'
import { Link } from 'react-router-dom'
import { StudioProjectsService as Projects, VideoRuntimeService as Budgets, type ProjectRead, type ProjectBudgetRead } from '../../../services/generated'
import { agentError } from '../agents/agentConfigIO'

export type ProjectChargeApproval = {
  projectId: string
  projectName: string
  upperMinor: number | undefined
  budget: ProjectBudgetRead
  ready: boolean
  reason?: string
}

/** Format exact integer minor units without rounding imported billing evidence. */
export function chargeMoney(value: string | number) {
  if (typeof value === 'number' && !Number.isSafeInteger(value)) return '金额需核对'
  const amount = String(value)
  if (!/^-?[0-9]+$/.test(amount)) return '金额需核对'
  const negative = amount.startsWith('-')
  const digits = (negative ? amount.slice(1) : amount).padStart(3, '0')
  return `${negative ? '-' : ''}${digits.slice(0, -2)}.${digits.slice(-2)} 元`
}

/** Read and assess one named project using generated clients; this never changes its ceiling or calls a model. */
export async function readProjectCharge(projectId: string, upperMinor: number | undefined): Promise<ProjectChargeApproval> {
  const [project, budget] = await Promise.all([
    Projects.getProjectApiV1StudioProjectsProjectIdGet({ projectId }), Budgets.getVideoProjectBudget({ projectId }),
  ])
  if (!project.data || !budget.data) throw new Error('项目或预算尚未返回，请刷新核对')
  const row = budget.data
  const exact = row.available_after_bills_minor ?? (Number.isSafeInteger(row.available_minor) ? String(row.available_minor) : '')
  const reason = !row.exists ? '请先在该项目设置预算' : row.currency !== 'CNY' ? '当前仅支持人民币项目预算'
    : (row.pending_bill_reviews ?? 0) > 0 ? '该项目存在待核对账单，请先核对后继续'
      : upperMinor === undefined || !Number.isSafeInteger(upperMinor) || upperMinor <= 0 ? '请先选择已登记费用的模型'
        : !/^-?[0-9]+$/.test(exact) ? '项目余额需要刷新核对'
          : BigInt(exact) < BigInt(upperMinor) ? '该项目剩余额度不足以预留完整调用，请先调整项目预算' : undefined
  return { projectId, projectName: project.data.name, upperMinor, budget: row, ready: !reason, reason }
}

/** Choose one owned cost project and show its complete reservation before a billable business action. */
export default function ProjectChargePicker({ value, onChange, upperMinor, onApproval, disabled, refreshToken }: {
  value: string | undefined
  onChange: (projectId: string | undefined) => void
  upperMinor: number | undefined
  onApproval: (approval: ProjectChargeApproval | null) => void
  disabled?: boolean
  refreshToken?: string
}) {
  const id = useId()
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [approval, setApproval] = useState<ProjectChargeApproval | null>(null)
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    const timer = window.setTimeout(() => {
      void Projects.listProjectsApiV1StudioProjectsGet({ q: query || undefined, page, pageSize: 20 }).then(response => {
        if (!active) return
        if (!response.data) throw new Error('项目列表尚未返回')
        setProjects(response.data.items); setTotal(response.data.pagination.total); setListError('')
      }).catch(failure => { if (active) setListError(agentError(failure, '无法读取费用归属项目')) })
        .finally(() => { if (active) setLoading(false) })
    }, 200)
    return () => { active = false; window.clearTimeout(timer) }
  }, [query, page])
  useEffect(() => {
    let active = true
    setApproval(null); onApproval(null); setError('')
    if (value) void readProjectCharge(value, upperMinor).then(result => {
      if (active) { setApproval(result); onApproval(result) }
    }).catch(failure => { if (active) setError(agentError(failure, '无法核对该项目预算，未授权模型调用')) })
    return () => { active = false }
  }, [value, upperMinor, refreshToken, revision, onApproval])

  const options = projects.map(project => ({ value: project.id, label: project.name }))
  if (approval && !options.some(option => option.value === approval.projectId)) options.unshift({ value: approval.projectId, label: approval.projectName })
  return <div className="library-form-group"><Space direction="vertical" className="w-full" size="small">
    <label htmlFor={id}>费用归属项目</label>
    <Select id={id} aria-label="费用归属项目" value={value} onChange={onChange} disabled={disabled} loading={loading}
      className="w-full" showSearch allowClear filterOption={false} onSearch={text => { setQuery(text); setPage(1) }}
      placeholder="选择本次调用使用的项目预算" options={options} dropdownRender={menu => <>{menu}{total > 20 && <Pagination
        size="small" simple current={page} pageSize={20} total={total} showSizeChanger={false} onChange={setPage} />}</>} />
    {error && <Alert type="error" showIcon message={error} />}
    {listError && <Alert type="error" showIcon message={listError} />}
    {approval && <Descriptions size="small" column={2} items={[
      { key: 'project', label: '归属', children: approval.projectName },
      { key: 'quote', label: '本次完整预留', children: upperMinor === undefined ? '待确认模型费用' : chargeMoney(upperMinor) },
      { key: 'held', label: '项目已有预留', children: chargeMoney(approval.budget.reserved_minor) },
      { key: 'available', label: '计入待核对账单后的余额', children: chargeMoney(approval.budget.available_after_bills_minor ?? approval.budget.available_minor) },
    ]} />}
    {approval?.reason && <Alert type="warning" showIcon message={approval.reason} />}
    <Space wrap><Button size="small" disabled={!value || disabled} onClick={() => setRevision(previous => previous + 1)}>刷新项目额度</Button>
      {value && <Link target="_blank" rel="noreferrer" to={`/projects/${encodeURIComponent(value)}?tab=settings`}>查看该项目预算</Link>}
      <Link target="_blank" rel="noreferrer" to="/projects">管理或建立项目</Link>
    </Space>
    <span className="library-muted">仅预留所选项目的额度。结果未知时继续保留，实际费用需另外核对账单；保存或查看预算不会调用模型。</span>
  </Space></div>
}
