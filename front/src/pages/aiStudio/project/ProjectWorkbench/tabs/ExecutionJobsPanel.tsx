import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Empty, Popconfirm, Space, Table, Tag, message } from 'antd'
import { ExecutionService as E } from '../../../../../services/generated'
import type { ExecutionRead } from '../../../../../services/generated'

/** Inspect existing durable jobs without offering submission or worker controls. */
export function ExecutionJobsPanel({ projectId }: { projectId: string }) {
  const [jobs, setJobs] = useState<ExecutionRead[]>([])
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const response = await E.getExecutionJobsApiV1ExecutionProjectsProjectIdJobsGet({ projectId, offset, limit: 50 })
      setJobs(response.data ?? [])
    } catch { setError('读取执行任务失败，请重试。') }
    finally { setLoading(false) }
  }, [projectId, offset])
  useEffect(() => { void load() }, [load])

  async function control(job: ExecutionRead, action: 'pause' | 'resume' | 'cancel') {
    setBusy(true)
    try {
      const args = { projectId, jobId: job.id }
      const response = action === 'pause'
        ? await E.pauseExecutionJobApiV1ExecutionProjectsProjectIdJobsJobIdPausePost(args)
        : action === 'resume'
          ? await E.resumeExecutionJobApiV1ExecutionProjectsProjectIdJobsJobIdResumePost(args)
          : await E.cancelExecutionJobApiV1ExecutionProjectsProjectIdJobsJobIdCancelPost(args)
      message.success(action === 'cancel' && response.data?.state !== 'cancelled' ? '已记录取消意图，仍需确认原任务结果' : '任务状态已更新')
      await load()
    } catch (e) { message.error((e as { body?: { message?: string } }).body?.message ?? '操作失败，请刷新状态后重试') }
    finally { setBusy(false) }
  }

  return <Card title="执行任务" extra={<Button loading={loading} disabled={busy} onClick={() => void load()}>刷新任务</Button>}>
    <Alert className="mb-3" type="info" showIcon message="此处仅管理已有任务" description="当前阶段尚未开放模型生成提交；真实模型账号接通并验收后再开放。恢复只继续原任务的查询或接收，取消请求需等服务确认。" />
    {error && <Alert className="mb-3" type="error" message={error} />}
    <Table<ExecutionRead> rowKey="id" loading={loading} dataSource={jobs} pagination={false} scroll={{ x: 600 }} locale={{ emptyText: <Empty description="本工程暂无执行任务" /> }} columns={[
      { title: '任务', render: (_, job) => <span title={job.id}>{job.id.slice(0, 8)}</span> },
      { title: '状态', render: (_, job) => <Space wrap><Tag>{job.state_label}</Tag>{job.cancel_requested && !['ready', 'failed', 'cancelled'].includes(job.state) && <Tag color="orange">取消待确认</Tag>}</Space> },
      { title: '说明', dataIndex: 'reason' },
      { title: '操作', render: (_, job) => <Space wrap>
        {job.can_pause && <Button disabled={busy} onClick={() => void control(job, 'pause')}>暂停</Button>}
        {job.can_resume && <Button disabled={busy} onClick={() => void control(job, 'resume')}>恢复原任务</Button>}
        {job.can_cancel && !job.cancel_requested && <Popconfirm title="请求取消此任务？" description="已提交的任务不会立即标为已取消，也不会重新提交。" onConfirm={() => control(job, 'cancel')}><Button disabled={busy}>请求取消</Button></Popconfirm>}
      </Space> },
    ]} />
    <Space className="mt-3"><Button disabled={offset === 0 || loading || busy} onClick={() => setOffset(value => Math.max(0, value - 50))}>上一页</Button><span>第 {offset / 50 + 1} 页</span><Button disabled={jobs.length < 50 || loading || busy} onClick={() => setOffset(value => value + 50)}>下一页</Button></Space>
  </Card>
}
