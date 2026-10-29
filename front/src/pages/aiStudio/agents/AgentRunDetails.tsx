import { Alert, Button, Collapse, Descriptions, Empty, Space, Tag, Typography } from 'antd'
import type { AgentRunRead } from '../../../services/generated'
import { Link } from 'react-router-dom'
import { chargeMoney } from '../project/ProjectChargePicker'

const LABELS: Record<string, string> = { queued: '排队中', running: '执行中', succeeded: '已完成', failed: '失败', cancelled: '已取消', pending: '待执行', skipped: '未执行' }

/** Keep persistent workflow states readable without mistaking config saves for execution. */
export function runStatus(status: string) { return LABELS[status] ?? status }

/** Inspect actual node dependencies and outputs in their business context, including partial failures. */
export default function AgentRunDetails({ run, busy, onCancel, onRetry }: { run: AgentRunRead | null; busy?: boolean; onCancel?: () => void; onRetry?: () => void }) {
  if (!run) return <Empty description="尚无执行记录；不会显示模拟结果" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  return <Space direction="vertical" className="w-full" size="middle">
    <Space wrap><Tag color={run.status === 'succeeded' ? 'green' : run.status === 'failed' ? 'red' : 'blue'}>{runStatus(run.status)}</Tag><span>{run.model_name}</span><span>第 {run.attempt} 次尝试</span><span>{new Date(run.created_at).toLocaleString()}</span>
      {['queued', 'running'].includes(run.status) && onCancel && <Button danger loading={busy} onClick={onCancel}>取消执行</Button>}
      {['failed', 'cancelled'].includes(run.status) && onRetry && <Button loading={busy} onClick={onRetry}>重新执行</Button>}
    </Space>
    {run.error && <Alert type={run.status === 'cancelled' ? 'warning' : 'error'} showIcon message={run.error} />}
    <Descriptions size="small" column={2} items={[{ key: 'version', label: '配置版本', children: run.agent_version }, { key: 'limit', label: '每个模型节点输出上限', children: `${run.max_tokens} tokens` }]} />
    {run.project_id && <Space wrap><Link to={`/projects/${encodeURIComponent(run.project_id)}?tab=settings`}>查看原项目预算</Link>
      {run.authorized_max_minor && <span>本次授权预留：{chargeMoney(run.authorized_max_minor)}；实际费用另行核对。</span>}
    </Space>}
    <Typography.Paragraph className="library-muted">顺序执行：前一个节点的输出作为下一个节点输入；文本连接成功不代表图片、视频或模型质量验收通过。</Typography.Paragraph>
    <Collapse items={(run.nodes ?? []).map(node => ({ key: node.node_id, label: `${node.position + 1}. ${node.label} / ${runStatus(node.status)}`, children: <Space direction="vertical" className="w-full">
      <div><strong>输入</strong><pre className="library-prompt-code" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{node.input_text || '尚未接收输入'}</pre></div>
      {node.type === 'llm' && <><div><strong>提示词</strong><pre className="library-prompt-code" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{node.prompt || '尚未执行'}</pre></div><div>模型参数：{JSON.stringify(node.parameters)} / 用量：{JSON.stringify(node.usage)}</div></>}
      <div><strong>输出</strong><pre className="library-prompt-code" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{node.output_text || '无输出'}</pre></div>{node.error && <Alert type="warning" message={node.error} />}
    </Space> }))} />
    {run.output_text && <div><strong>最终输出</strong><pre aria-label="Agent 最终输出" className="library-prompt-code" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{run.output_text}</pre></div>}
  </Space>
}
