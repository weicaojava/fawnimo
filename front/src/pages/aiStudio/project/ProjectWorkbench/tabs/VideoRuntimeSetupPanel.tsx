import { useEffect, useState } from 'react'
import { Alert, Button, Collapse, Select, Space, message } from 'antd'
import { VideoRuntimeService as V, type NativeVideoModelChoice, type VideoRuntimeSetupRead } from '../../../../../services/generated'
import { agentError, downloadAgentConfig } from '../../../agents/agentConfigIO'
import { ProjectBudgetPanel } from './ProjectBudgetPanel'

/** Pair the project's own ledger with a separate, non-secret video service readiness view. */
export function VideoRuntimeSetupPanel({ projectId }: { projectId: string; onSaved: () => void }) {
  const [setup, setSetup] = useState<VideoRuntimeSetupRead | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [models, setModels] = useState<NativeVideoModelChoice[]>([])
  const [templateModel, setTemplateModel] = useState<string>()
  useEffect(() => {
    let active = true
    V.getVideoRuntimeSetup().then(result => { if (active && result.data) setSetup(result.data) })
      .catch(cause => { if (active) setError(agentError(cause, '读取视频服务状态失败')) })
    V.listNativeVideoModels().then(result => { if (active) setModels(result.data ?? []) })
      .catch(() => { if (active) setError('原生视频模型清单暂不可用，未测试或调用模型') })
    return () => { active = false }
  }, [projectId])

  /** Download the disabled configuration template without starting any remote work. */
  async function download() {
    setBusy(true)
    try { downloadAgentConfig({ ...(await V.getVideoConfigurationTemplate()), ...(templateModel ? { model_id: templateModel } : {}) }, 'video-runtime.example') }
    catch (cause) { message.error(agentError(cause, '配置模板下载失败')) }
    finally { setBusy(false) }
  }

  return <Space direction="vertical" className="w-full mb-3">
    <ProjectBudgetPanel key={projectId} projectId={projectId} />
    <Collapse items={[{ key: 'service', label: '视频服务配置', children: <Space direction="vertical" className="w-full">
      <Alert showIcon type={setup?.running ? 'success' : 'info'} message={error || setup?.message || '正在读取视频服务状态'} description={setup?.output_description} />
      <label>模板使用的原生视频模型<Select aria-label="模板使用的原生视频模型" className="w-full mt-1" allowClear value={templateModel} onChange={setTemplateModel} options={models.map(model => ({ value: model.id, label: `${model.name} · ${model.provider_name}${model.priced ? '' : '（费用待登记）'}` }))} placeholder="先在模型管理标记原生视频用途" /></label>
      <p className="mb-0 text-sm text-gray-500">模板保持关闭，由本机管理员安装并核对费用后启用。选择或下载不会调用模型；关闭新生成后，原任务仍可使用未变化的原账号查询。</p>
      <Button loading={busy} onClick={() => void download()}>下载本机视频配置模板</Button>
    </Space> }]} />
  </Space>
}
