import { useEffect, useId, useRef, useState } from 'react'
import { Alert, Button, Select, Space, Tag } from 'antd'
import { OpenAPI, ProductionService } from '../../../../services/generated'
import type { AssetImageReferenceInput } from '../../../../services/generated'
import { workspaceMediaUrl, workspaceRequestScope } from '../../../../services/workspaceContext'
import { agentError } from '../../agents/agentConfigIO'
import { useImageActionScope } from './useImageActionScope'

type Media = { id: string; name: string; mime: string; sha256: string; size: number }
const purposes = [
  { value: 'appearance' as const, label: '外观与主体细节' },
  { value: 'composition' as const, label: '构图与空间关系' },
  { value: 'style' as const, label: '画面风格与色调' },
]

/** Select exact original-project images in an explicit order; uploads never submit a model call. */
export default function AssetImageReferencePicker({ projectId, value, onChange, onReady, disabled, maximum }: {
  projectId?: string; value: AssetImageReferenceInput[]; onChange: (value: AssetImageReferenceInput[]) => void
  onReady: (ready: boolean) => void; disabled: boolean; maximum: number
}) {
  const inputId = useId()
  const [media, setMedia] = useState<Media[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const actionScope = useImageActionScope(JSON.stringify(['reference-picker', projectId]))
  const current = useRef({ projectId, scope: workspaceRequestScope(), active: true, generation: 0 })
  if (current.current.projectId !== projectId || current.current.scope !== workspaceRequestScope()) current.current.generation += 1
  current.current.projectId = projectId; current.current.scope = workspaceRequestScope()

  useEffect(() => {
    current.current.active = true
    return () => { current.current.active = false }
  }, [])
  useEffect(() => {
    setMedia([]); setError(''); setLoading(!!projectId); setUploading(false)
    if (!projectId) return
    let active = true
    const scope = workspaceRequestScope()
    const stamp = actionScope.capture()
    void ProductionService.listMediaApiV1StudioProductionProjectsProjectIdMediaGet({ projectId }).then(async response => {
      await actionScope.attest(stamp)
      if (!active || !actionScope.isCurrent(stamp) || current.current.projectId !== projectId || current.current.scope !== scope) return
      const valid = (response.data ?? []).filter(row => row.project_id === projectId && typeof row.id === 'string' && typeof row.name === 'string'
        && ['image/png', 'image/jpeg'].includes(String(row.mime)) && typeof row.sha256 === 'string' && /^[a-f0-9]{64}$/.test(row.sha256)
        && typeof row.size === 'number' && row.size > 0 && row.size <= 4 * 1024 * 1024)
      setMedia(valid)
    }).catch(failure => { if (active && actionScope.isCurrent(stamp)) setError(agentError(failure, '读取原项目参考图片失败')) })
      .finally(() => { if (active && actionScope.isCurrent(stamp)) setLoading(false) })
    return () => { active = false }
  }, [projectId, refresh, actionScope.key])
  const missing = value.some(reference => !media.some(row => row.id === reference.media_id && row.sha256 === reference.expected_sha256))
  useEffect(() => { onReady(!uploading && !missing && (value.length === 0 || !loading)) }, [uploading, missing, loading, value.length, onReady])

  /** Retain selection order and hash; refreshing cannot silently rebind an existing selected input. */
  const select = (ids: string[]) => {
    if (ids.length > maximum) { setError(`最多选择 ${maximum} 张参考图片`); return }
    setError('')
    onChange(ids.flatMap(id => {
      const prior = value.find(reference => reference.media_id === id)
      const row = media.find(item => item.id === id)
      return prior ? [prior] : row ? [{ media_id: id, expected_sha256: row.sha256, purpose: 'appearance' as const }] : []
    }))
  }
  /** Move one exact reference without changing its purpose, content hash or original project. */
  const move = (index: number, step: number) => {
    const result = [...value]
    const target = index + step
    if (target < 0 || target >= result.length) return
    ;[result[index], result[target]] = [result[target], result[index]]
    onChange(result)
  }
  /** Upload only into the project visible at the click; stale completion cannot select files in another project. */
  const upload = async (file?: File) => {
    if (!file || !projectId) return
    if (!file.size || file.size > 4 * 1024 * 1024) { setError('请选择不超过4 MiB的 PNG/JPEG 图片'); return }
    const originalProject = projectId, scope = workspaceRequestScope(), generation = current.current.generation
    const stamp = actionScope.capture()
    const isCurrent = () => actionScope.isCurrent(stamp) && current.current.active && current.current.projectId === originalProject && current.current.scope === scope && current.current.generation === generation
    setUploading(true); setError('')
    try {
      await actionScope.attest(stamp)
      await ProductionService.postMediaApiV1StudioProductionProjectsProjectIdMediaPost({ projectId: originalProject, formData: { file: file as unknown as string } })
      await actionScope.attest(stamp)
      if (isCurrent()) setRefresh(previous => previous + 1)
    } catch (failure) { if (isCurrent()) setError(agentError(failure, '参考图片上传失败；未发起模型请求')) }
    finally { if (isCurrent()) setUploading(false) }
  }

  return <Space direction="vertical" className="w-full">
    <strong>参考图片（可选，最多 {maximum} 张）</strong>
    <p className="library-muted">仅发送下面明确选择的原项目图片。每张不超过4 MiB；顺序和用途会保存在任务记录中，人物一致性与画面质量仍需人工确认。</p>
    {!projectId && <Alert type="info" message="请先选择费用归属项目，再选择或上传参考图片" />}
    {error && <Alert type="error" message={error} />}
    {missing && <Alert type="warning" message="已选参考图片缺失或内容版本已变化，请重新选择；不会自动换用其他图片" />}
    <Select mode="multiple" aria-label="原项目参考图片" value={value.map(reference => reference.media_id)} loading={loading}
      disabled={disabled || uploading || !projectId} className="w-full" placeholder="选择已上传到原项目的 PNG/JPEG 图片"
      onChange={select} options={media.map(row => ({ value: row.id, label: row.name }))} />
    <Space wrap><label htmlFor={inputId}>上传到原项目素材</label><input id={inputId} aria-label="上传原项目参考图片" type="file" accept="image/png,image/jpeg"
      disabled={disabled || uploading || !projectId} onChange={event => { void upload(event.target.files?.[0]); event.target.value = '' }} />
      <Button disabled={disabled || uploading || !projectId} loading={loading || uploading} onClick={() => setRefresh(previous => previous + 1)}>刷新参考图片</Button></Space>
    {value.map((reference, index) => {
      const row = media.find(item => item.id === reference.media_id)
      return <Space wrap key={reference.media_id} align="center">
        {row && projectId && <img alt={`参考图${index + 1}：${row.name}`} src={workspaceMediaUrl(`${OpenAPI.BASE}/api/v1/studio/production/projects/${encodeURIComponent(projectId)}/media/${encodeURIComponent(row.id)}/content`)} style={{ width: 64, height: 64, objectFit: 'contain' }} />}
        <Tag>图{index + 1}</Tag><span>{row?.name ?? reference.media_id}</span>
        <Select aria-label={`参考图${index + 1}用途`} disabled={disabled || uploading} value={reference.purpose} options={purposes}
          onChange={purpose => onChange(value.map((item, position) => position === index ? { ...item, purpose } : item))} style={{ minWidth: 180 }} />
        <Button disabled={disabled || uploading || index === 0} onClick={() => move(index, -1)}>上移</Button>
        <Button disabled={disabled || uploading || index === value.length - 1} onClick={() => move(index, 1)}>下移</Button>
        <Button disabled={disabled || uploading} onClick={() => onChange(value.filter((_, position) => position !== index))}>移除参考</Button>
      </Space>
    })}
  </Space>
}
