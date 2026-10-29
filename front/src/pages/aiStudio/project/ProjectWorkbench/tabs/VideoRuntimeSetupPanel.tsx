import { useEffect, useState } from 'react'
import { Alert, Button, Collapse, Space, message } from 'antd'
import { VideoRuntimeService as V, type VideoRuntimeSetupRead } from '../../../../../services/generated'
import { agentError, downloadAgentConfig } from '../../../agents/agentConfigIO'
import { ProjectBudgetPanel } from './ProjectBudgetPanel'

/** Pair the project's own ledger with a separate, non-secret video service readiness view. */
export function VideoRuntimeSetupPanel({ projectId }: { projectId: string; onSaved: () => void }) {
  const [setup, setSetup] = useState<VideoRuntimeSetupRead | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    V.getVideoRuntimeSetup().then(result => { if (active && result.data) setSetup(result.data) })
      .catch(cause => { if (active) setError(agentError(cause, '读取视频服务状态失败')) })
    return () => { active = false }
  }, [projectId])

  /** Download the disabled configuration template without starting any remote work. */
  async function download() {
    setBusy(true)
    try { downloadAgentConfig(await V.getVideoConfigurationTemplate(), 'video-runtime.example') }
    catch (cause) { message.error(agentError(cause, '配置模板下载失败')) }
    finally { setBusy(false) }
  }

  return <Space direction="vertical" className="w-full mb-3">
    <ProjectBudgetPanel key={projectId} projectId={projectId} />
    <Collapse items={[{ key: 'service', label: '视频服务配置', children: <Space direction="vertical" className="w-full">
      <Alert showIcon type={setup?.running ? 'success' : 'info'} message={error || setup?.message || '正在读取视频服务状态'} description={setup?.output_description} />
      <Button loading={busy} onClick={() => void download()}>下载本机视频配置模板</Button>
    </Space> }]} />
  </Space>
}
