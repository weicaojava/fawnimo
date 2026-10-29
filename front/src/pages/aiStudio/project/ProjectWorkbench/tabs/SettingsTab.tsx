import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Form, Input, InputNumber, Select, Space, Switch, message } from 'antd'
import { useParams } from 'react-router-dom'
import { LlmService, StudioProjectsService } from '../../../../../services/generated'
import type { ProjectRead, ProjectUpdate } from '../../../../../services/generated'
import { ProjectSection } from '../../ProjectSection'
import { ProjectVisualStyleAndStyleFields, type ProjectStyleFieldOptions } from '../../ProjectVisualStyleAndStyleFields'
import { ProjectBudgetPanel } from './ProjectBudgetPanel'

/** Surface the actual API explanation without exposing payloads or substituting global settings. */
function explain(error: unknown): string {
  const body = (error as { body?: { message?: unknown } })?.body
  return typeof body?.message === 'string' ? body.message : '项目设置加载或保存失败，请重试。'
}

/** Edit the current project through its real GET/PATCH APIs and server-supplied style options. */
export function SettingsTab({ onSaved }: { onSaved?: () => void | Promise<void> } = {}) {
  const { projectId } = useParams<{ projectId: string }>()
  const [form] = Form.useForm<ProjectUpdate>()
  const [project, setProject] = useState<ProjectRead | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [options, setOptions] = useState<ProjectStyleFieldOptions | null>(null)
  const [ratios, setRatios] = useState<{ value: string; label: string }[]>([])
  const session = useRef(0)

  useEffect(() => {
    const token = ++session.current
    let active = true
    setLoading(true)
    setSaving(false)
    setError('')
    setProject(null)
    setOptions(null)
    form.resetFields()
    if (!projectId) { setError('项目不存在'); setLoading(false); return }
    void Promise.all([
      StudioProjectsService.getProjectApiV1StudioProjectsProjectIdGet({ projectId }),
      StudioProjectsService.getProjectStyleOptionsApiV1StudioProjectsStyleOptionsGet(),
      LlmService.getVideoGenerationOptionsApiV1LlmVideoGenerationOptionsGet(),
    ]).then(([projectResponse, styleResponse, videoResponse]) => {
      if (!active || token !== session.current) return
      const data = projectResponse.data
      const style = styleResponse.data
      if (!data || !style?.visual_styles?.length || !style.styles_by_visual_style) throw new Error('项目或风格资料不完整')
      setProject(data)
      setOptions({ visualStyles: style.visual_styles, stylesByVisual: style.styles_by_visual_style, defaultStyleByVisual: style.default_style_by_visual_style })
      const available = videoResponse.data?.allowed_ratios ?? []
      setRatios([...new Set([...available, ...(data.default_video_ratio ? [data.default_video_ratio] : [])])].map(value => ({ value, label: value })))
      form.setFieldsValue({ name: data.name, description: data.description, visual_style: data.visual_style, style: data.style,
        seed: data.seed, unify_style: data.unify_style, default_video_ratio: data.default_video_ratio ?? undefined })
    }).catch(reason => {
      if (active && token === session.current) setError(explain(reason))
    }).finally(() => {
      if (active && token === session.current) setLoading(false)
    })
    return () => { active = false; ++session.current }
  }, [projectId, form, reload])

  /** Save only editable project fields, leaving counters, assets and global preferences untouched. */
  async function save(values: ProjectUpdate) {
    if (!projectId || !project || !options || loading || saving) return
    const token = session.current
    setSaving(true)
    setError('')
    try {
      const response = await StudioProjectsService.updateProjectApiV1StudioProjectsProjectIdPatch({ projectId,
        requestBody: { name: values.name?.trim(), description: values.description ?? '', visual_style: values.visual_style,
          style: values.style, seed: values.seed, unify_style: values.unify_style, default_video_ratio: values.default_video_ratio || null } })
      if (token !== session.current) return
      if (!response.data) throw new Error('项目设置保存未返回结果')
      setProject(response.data)
      message.success('项目设置已保存')
      void onSaved?.()
    } catch (reason) {
      if (token === session.current) setError(explain(reason))
    } finally {
      if (token === session.current) setSaving(false)
    }
  }

  return (
    <ProjectSection title="项目设置" loading={loading} data-testid="project-settings">
      {error && <Alert type="error" showIcon className="mb-4" message={error} action={!project ? <Button onClick={() => setReload(value => value + 1)}>重新加载</Button> : undefined} />}
      {project && options && <Form form={form} layout="vertical" onFinish={save} disabled={loading || saving} className="max-w-4xl">
        <Form.Item name="name" label="项目名称" rules={[{ required: true, whitespace: true, message: '请输入项目名称' }, { max: 255, message: '名称不能超过255字' }]}><Input maxLength={255} /></Form.Item>
        <Form.Item name="description" label="项目简介"><Input.TextArea rows={4} /></Form.Item>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <ProjectVisualStyleAndStyleFields form={form} options={options} disabled={loading || saving} />
          <Form.Item name="seed" label="项目种子值" tooltip="保存项目的默认种子；实际生成效果取决于模型支持。" rules={[{ required: true, message: '请输入种子值，可填写0' }, { validator: async (_rule, value) => { if (value !== null && value !== undefined && (!Number.isSafeInteger(value) || value < 0)) throw new Error('请输入非负安全整数') } }]}><InputNumber min={0} precision={0} className="w-full" /></Form.Item>
          <Form.Item name="default_video_ratio" label="默认视频比例"><Select allowClear placeholder="由模型支持情况决定" options={ratios} /></Form.Item>
        </div>
        <Form.Item name="unify_style" label="章节沿用项目风格" valuePropName="checked"><Switch /></Form.Item>
        <p className="text-sm text-gray-500 mb-4">这里修改当前项目。已经保存的资产版本、生成记录与其他项目不会被改写。</p>
        <Form.Item><Space><Button type="primary" htmlType="submit" loading={saving}>保存项目设置</Button></Space></Form.Item>
      </Form>}
      {project && projectId && <ProjectBudgetPanel key={projectId} projectId={projectId} />}
    </ProjectSection>
  )
}
