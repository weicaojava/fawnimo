import { useEffect, useId, useRef, useState } from 'react'
import { Alert, Button, Select, Space, Tag } from 'antd'
import { OpenAPI, ProductionService } from '../../../../services/generated'
import type { ProductionMediaRead, VideoReferenceInput, VideoReferenceSelection } from '../../../../services/generated'
import { workspaceMediaUrl } from '../../../../services/workspaceContext'
import { agentError } from '../../agents/agentConfigIO'
import { useImageActionScope } from '../../assets/components/useImageActionScope'

type Mode = VideoReferenceSelection['mode']
const names = { first_frame: '首帧', last_frame: '尾帧', reference_image: '参考图片', reference_video: '参考视频' }

/** A partial frame selection stays visibly incomplete and never becomes a fabricated valid saved selection. */
function complete(mode: Mode, references: VideoReferenceInput[]) {
  const roles = references.map(item => item.role)
  if (mode === 'first_frame') return roles.join(',') === 'first_frame'
  if (mode === 'first_last_frames') return roles.join(',') === 'first_frame,last_frame'
  return roles.length > 0 && roles.length <= 4 && roles.every(role => ['reference_image', 'reference_video'].includes(role))
    && roles.filter(role => role === 'reference_image').length <= 3 && roles.filter(role => role === 'reference_video').length <= 1
    && new Set(references.map(item => item.media_id)).size === roles.length
}

/** Choose only exact owned inputs; mode changes, roles and order are explicit, while uploads merely add local material. */
export default function VideoReferencePicker({ projectId, context, value, disabled, videoAvailable, imageOnly = false, onChange, onReady }: {
  projectId: string; context: string; value?: VideoReferenceSelection | null; disabled: boolean; videoAvailable: boolean; imageOnly?: boolean
  onChange: (value: VideoReferenceSelection | null) => void; onReady: (ready: boolean) => void
}) {
  const inputId = useId()
  const scope = useImageActionScope(JSON.stringify(['video-reference', projectId, context]))
  const [media, setMedia] = useState<ProductionMediaRead[]>([])
  const [mode, setMode] = useState<Mode>(value?.mode ?? 'reference')
  const [references, setReferences] = useState<VideoReferenceInput[]>(value?.references ?? [])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const incoming = JSON.stringify(value ?? null)
  const emitted = useRef(incoming)
  const stamp = scope.capture()

  useEffect(() => {
    if (incoming !== emitted.current) { setMode(value?.mode ?? 'reference'); setReferences(value?.references ?? []); emitted.current = incoming }
  }, [incoming])
  useEffect(() => {
    const observed = scope.capture()
    let active = true
    setMedia([]); setLoading(true); setUploading(false); setError('')
    void ProductionService.listMediaApiV1StudioProductionProjectsProjectIdMediaGet({ projectId }).then(async response => {
      await scope.attest(observed)
      if (!active || !scope.isCurrent(observed)) return
      setMedia((response.data ?? []).filter(row => row.project_id === projectId && /^[a-f0-9]{64}$/.test(row.sha256)
        && row.size > 0 && (['image/png', 'image/jpeg'].includes(row.mime) ? row.size <= 4 * 1024 * 1024 : row.mime === 'video/mp4' && row.size <= 32 * 1024 * 1024)))
    }).catch(failure => { if (active && scope.isCurrent(observed)) setError(agentError(failure, '读取原项目参考素材失败')) })
      .finally(() => { if (active && scope.isCurrent(observed)) setLoading(false) })
    return () => { active = false }
  }, [projectId, scope.key, refresh])
  const missing = references.some(item => !media.some(row => row.id === item.media_id && row.sha256 === item.expected_sha256))
  const videoBlocked = !videoAvailable && references.some(item => item.role === 'reference_video')
  const ready = complete(mode, references) && !missing && !videoBlocked && !loading && !uploading
  useEffect(() => { onReady(ready) }, [ready, onReady])

  /** Retain partial visible choices locally, publishing only an exact mode/role contract to the plan form. */
  function change(nextMode: Mode, next: VideoReferenceInput[]) {
    if (disabled || uploading || !scope.isCurrent(stamp)) return
    setMode(nextMode); setReferences(next); setError('')
    const result: VideoReferenceSelection | null = complete(nextMode, next) ? { schema_version: 1, mode: nextMode, references: next } : null
    emitted.current = JSON.stringify(result); onChange(result)
  }
  /** Keep first and last roles in their fixed order, allowing the same explicitly chosen image in both positions. */
  function frame(role: 'first_frame' | 'last_frame', id?: string) {
    const row = media.find(item => item.id === id)
    const next = references.filter(item => item.role !== role)
    if (row) next.push({ media_id: row.id, expected_sha256: row.sha256, role })
    next.sort((a, b) => (a.role === 'first_frame' ? 0 : 1) - (b.role === 'first_frame' ? 0 : 1))
    change(mode, next)
  }
  /** New selections capture the current hash; existing selections never silently switch to replacement bytes. */
  function select(ids: string[]) {
    const next = ids.flatMap(id => {
      const previous = references.find(item => item.media_id === id)
      const row = media.find(item => item.id === id)
      return previous ? [previous] : row ? [{ media_id: id, expected_sha256: row.sha256, role: row.mime === 'video/mp4' ? 'reference_video' as const : 'reference_image' as const }] : []
    })
    if (next.length && !complete('reference', next)) { setError('最多选择三张图片和一个视频，同一素材只能选择一次'); return }
    change('reference', next)
  }
  /** Move semantic references without changing their roles or original content identities. */
  function move(index: number, step: number) {
    const next = [...references], target = index + step
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    change(mode, next)
  }
  /** Re-attest the original identity before upload and before accepting its completion; never auto-select its result. */
  async function upload(file?: File) {
    if (!file || disabled || uploading) return
    const video = file.type === 'video/mp4' || /\.mp4$/i.test(file.name)
    if (!file.size || file.size > (video ? 32 : 4) * 1024 * 1024 || (video && !videoAvailable)) { setError('图片须不超过4 MiB；交付入口就绪后可上传不超过32 MiB的 MP4'); return }
    const observed = scope.capture()
    setUploading(true); setError('')
    try {
      await scope.attest(observed)
      await ProductionService.postMediaApiV1StudioProductionProjectsProjectIdMediaPost({ projectId, formData: { file: file as unknown as string } })
      await scope.attest(observed)
      if (scope.isCurrent(observed)) setRefresh(previous => previous + 1)
    } catch (failure) { if (scope.isCurrent(observed)) setError(agentError(failure, '素材上传失败；未调用模型')) }
    finally { if (scope.isCurrent(observed)) setUploading(false) }
  }

  const images = media.filter(row => ['image/png', 'image/jpeg'].includes(row.mime))
  const options = (rows: ProductionMediaRead[]) => rows.map(row => ({ value: row.id, label: `${row.name} / ${row.mime === 'video/mp4' ? '视频' : '图片'}` }))
  const locked = disabled || uploading || loading
  return <Space direction="vertical" className="w-full" data-testid="video-reference-picker">
    <label className="w-full">参考方式<Select aria-label="视频参考方式" className="w-full" disabled={disabled || uploading} value={mode} onChange={next => change(next, [])} options={[
      { value: 'reference', label: imageOnly ? '素材参考（最多三张图片）' : '素材参考（最多三张图片和一个视频）' }, { value: 'first_frame', label: '只指定首帧' }, { value: 'first_last_frames', label: '指定首帧和尾帧' },
    ]} /></label>
    <p className="library-muted">切换参考方式会清空此处选择。只发送明确选中的素材；资产版本的其他图片仅保留为来源记录。</p>
    {mode === 'reference' ? <Select mode="multiple" aria-label="视频生成参考素材" className="w-full" disabled={locked} loading={loading} value={references.map(item => item.media_id)} onChange={select} options={options(media.filter(row => row.mime !== 'video/mp4' || videoAvailable))} placeholder="按需要的顺序选择原项目素材" />
      : (mode === 'first_frame' ? ['first_frame'] as const : ['first_frame', 'last_frame'] as const).map(role => <label className="w-full" key={role}>{names[role]}<Select allowClear aria-label={`视频${names[role]}图片`} className="w-full" disabled={locked} value={references.find(item => item.role === role)?.media_id} onChange={id => frame(role, id)} options={options(images)} /></label>)}
    {error && <Alert type="error" message={error} />}
    {missing && <Alert type="warning" message="所选素材缺失或内容已变化，请重新选择；不会自动换用新内容" />}
    {!ready && !loading && !missing && <Alert type="info" message="请补全此镜头的参考选择，再保存并锁定计划" />}
    {!videoAvailable && <p className="library-muted">{imageOnly ? '当前万相档案仅支持图片、首帧或首尾帧，不接受参考视频。' : '参考视频交付入口尚未就绪，目前可选择图片、首帧或首尾帧。'}</p>}
    <p className="library-muted">PNG/JPEG 每张≤4 MiB，素材参考图片合计≤12 MiB。首尾帧须为9:16。{imageOnly ? '图片须无透明通道，不会自动裁剪或改写提示词。' : '视频须为2–10秒、24/25/30帧的 MP4，≤32 MiB；原声仅作为输入，输出关闭生成音频。'}接受任务前会实际解码核对。</p>
    <Space wrap><label htmlFor={inputId}>上传到原项目素材</label><input id={inputId} aria-label="上传视频参考素材" type="file" accept={videoAvailable ? 'image/png,image/jpeg,video/mp4' : 'image/png,image/jpeg'} disabled={disabled || uploading} onChange={event => { void upload(event.target.files?.[0]); event.target.value = '' }} /><Button disabled={disabled || uploading} loading={loading} onClick={() => setRefresh(previous => previous + 1)}>刷新参考素材</Button></Space>
    {references.map((item, index) => {
      const row = media.find(candidate => candidate.id === item.media_id)
      return <Space wrap key={`${item.role}:${index}`}>
        {row && row.mime !== 'video/mp4' && <img alt={`${names[item.role]}：${row.name}`} src={workspaceMediaUrl(`${OpenAPI.BASE}/api/v1/studio/production/projects/${encodeURIComponent(projectId)}/media/${encodeURIComponent(row.id)}/content`)} style={{ width: 72, height: 72, objectFit: 'contain' }} />}
        <Tag>{index + 1}. {names[item.role]}</Tag><span>{row?.name ?? item.media_id}</span>
        {mode === 'reference' && <><Button disabled={locked || index === 0} onClick={() => move(index, -1)}>上移</Button><Button disabled={locked || index === references.length - 1} onClick={() => move(index, 1)}>下移</Button></>}
        <Button disabled={locked} onClick={() => change(mode, references.filter((_, position) => position !== index))}>移除参考</Button>
      </Space>
    })}
  </Space>
}
