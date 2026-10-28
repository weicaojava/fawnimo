import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Input, InputNumber, Select, Space, Table, Tag, message } from 'antd'
import { OpenAPI, ProductionService as P, ReverseService as R } from '../../../../../services/generated'
import type { AnalysisRead } from '../../../../../services/generated'
import { DnaPanel } from './DnaPanel'

type Frame = { index: number; pts: number; time_base_num: number; time_base_den: number; relative_us: number }
type Manifest = { video: { width: number; height: number; frames: Frame[] }; cuts: { frame_index: number; relative_us: number }[]; keyframes: { frame_index: number }[]; audio: { present: boolean; tracks: { origin_relative_us?: number }[] }; proxy: { warnings?: string[]; degraded?: boolean; frame_mapping: { relative_us: number; source_index: number }[] }; candidate_algorithm?: string }
const labels: Record<string, string> = { queued: '等待解析', analyzing: '正在解析', ready: '解析完成', degraded: '解析完成 · 需复核', failed: '解析失败' }

/** Technical inspection stays separate from model understanding and reconstruction. */
export function ReverseVideoPanel({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<AnalysisRead[]>([])
  const [selected, setSelected] = useState('')
  const [storedAnalysis, setAnalysis] = useState<AnalysisRead | null>(null)
  const analysis = storedAnalysis?.id === selected ? storedAnalysis : null
  const identity = useRef({ projectId, selected })
  identity.current = { projectId, selected }
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const [busy, setBusy] = useState(false)
  const [dnaLocked, setDnaLocked] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [cuts, setCuts] = useState('0')
  const [frameInput, setFrameInput] = useState(0)
  const [previewFrame, setPreviewFrame] = useState<number | null>(null)
  const player = useRef<HTMLVideoElement>(null)
  const manifest = analysis?.manifest as Manifest | null | undefined
  const boundaries = analysis?.confirmed_cuts?.map(cut => cut.frame_index) ?? [0, ...(manifest?.cuts ?? []).map(cut => cut.frame_index)]
  const frames = manifest?.video.frames ?? []
  const timeline = boundaries.map((index, number) => ({ index, number: number + 1, start: frames[index]?.relative_us ?? 0, end: frames[boundaries[number + 1]]?.relative_us ?? null }))
  const base = `${OpenAPI.BASE}/api/v1/reverse/projects/${encodeURIComponent(projectId)}/analyses/${encodeURIComponent(analysis?.id ?? selected)}`

  /** Re-fetch identities without treating any detector output as confirmed shots. */
  async function loadList() {
    const response = await R.getReverseAnalysesApiV1ReverseProjectsProjectIdAnalysesGet({ projectId })
    if (alive.current && identity.current.projectId === projectId) setItems(response.data ?? [])
    return response.data ?? []
  }

  useEffect(() => {
    let cancelled = false
    setAnalysis(null); setSelected(''); setError('')
    R.getReverseAnalysesApiV1ReverseProjectsProjectIdAnalysesGet({ projectId }).then(response => {
      if (!cancelled) { setItems(response.data ?? []); setSelected(response.data?.[0]?.id ?? '') }
    }).catch(() => { if (!cancelled) setError('读取视频分析列表失败') })
    return () => { cancelled = true }
  }, [projectId])

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    let inFlight = false
    const poll = async () => {
      if (inFlight || cancelled) return
      inFlight = true
      try {
        const response = await R.getReverseAnalysisApiV1ReverseProjectsProjectIdAnalysesAnalysisIdGet({ projectId, analysisId: selected })
        if (!cancelled) {
          setAnalysis(previous => previous && response.data && previous.id === response.data.id && previous.revision > response.data.revision ? previous : response.data ?? null); setError('')
          if (!['queued', 'analyzing'].includes(response.data?.state ?? '')) clearInterval(timer)
        }
      } catch { if (!cancelled) { setError('读取分析失败，请检查原视频是否完整'); clearInterval(timer) } }
      finally { inFlight = false }
    }
    const timer = setInterval(() => void poll(), 2000)
    void poll()
    return () => { cancelled = true; clearInterval(timer) }
  }, [projectId, selected, refresh])

  useEffect(() => {
    const report = analysis?.manifest as Manifest | null | undefined
    const confirmed = analysis?.confirmed_cuts?.map(cut => Number(cut.frame_index))
    setCuts((confirmed ?? [0, ...(report?.cuts ?? []).map(cut => cut.frame_index)]).join(','))
    setPreviewFrame(null); setFrameInput(0)
  }, [analysis?.id, analysis?.revision, analysis?.state])

  /** Import original bytes, then persist an analysis before background decoding. */
  async function upload(file?: File) {
    if (!file) return
    if (file.size > 64 * 1024 * 1024) { message.error('视频超过64 MB上限'); return }
    setBusy(true); setError('')
    try {
      const imported = await P.postMediaApiV1StudioProductionProjectsProjectIdMediaPost({ projectId, formData: { file: file as unknown as string } })
      const media = (imported.data as { id: string }[] | undefined)?.[0]
      if (!media?.id) throw new Error('缺少媒体标识')
      const result = await R.postReverseAnalysisApiV1ReverseProjectsProjectIdAnalysesPost({ projectId, requestBody: { media_id: media.id, request_key: crypto.randomUUID() } })
      if (!alive.current || identity.current.projectId !== projectId) return
      await loadList()
      if (!alive.current || identity.current.projectId !== projectId) return
      setSelected(result.data?.id ?? ''); setAnalysis(result.data ?? null); setRefresh(value => value + 1)
    } catch (e) { if (alive.current && identity.current.projectId === projectId) setError((e as { body?: { message?: string } }).body?.message ?? '导入或分析失败，请检查视频与解析器配置') }
    finally { if (alive.current) setBusy(false) }
  }

  /** Save deliberate human edits against the exact displayed report revision. */
  async function saveCuts() {
    if (!analysis) return
    const targetId = analysis.id
    const normalized = cuts.replace(/\s/g, '').replace(/，/g, ',')
    if (!/^\d+(,\d+)*$/.test(normalized)) { message.error('请输入从0开始的原始帧序号，用逗号分隔'); return }
    setBusy(true)
    try {
      const response = await R.patchReverseCutsApiV1ReverseProjectsProjectIdAnalysesAnalysisIdCutsPatch({ projectId, analysisId: analysis.id, requestBody: { frame_indices: normalized.split(',').map(Number), expected_revision: analysis.revision } })
      if (!alive.current || identity.current.projectId !== projectId || identity.current.selected !== targetId) return
      setAnalysis(response.data ?? null); message.success('已保存人工确认切点，自动候选保持不变')
    } catch (e) { message.error((e as { body?: { message?: string } }).body?.message ?? '保存失败，请刷新版本后重试') }
    finally { if (alive.current) setBusy(false) }
  }

  /** Seek via the recorded proxy-to-source map rather than nominal source fps. */
  function inspectFrame(index: number) {
    setFrameInput(index); setPreviewFrame(index)
    const nearest = manifest?.proxy.frame_mapping.reduce((best, frame) => Math.abs(frame.source_index - index) < Math.abs(best.source_index - index) ? frame : best)
    if (nearest && player.current) player.current.currentTime = nearest.relative_us / 1e6
  }

  return <Card title="视频逆向 · 技术解析" extra={<Button disabled={busy || dnaLocked} onClick={() => setRefresh(value => value + 1)}>刷新分析</Button>}>
    <Alert className="mb-3" type="info" showIcon message="先检查原片，再确认镜头边界" description="本步骤提取帧、音轨和候选切点，不调用生成模型。自动候选需要复核，下方DNA只记录人工声明，自动人物识别、台词转写和真实视频还原尚未完成。当前支持单视频轨MP4，最长120秒、64 MB、1920×1920，最多7200个原始帧、256个边界。" />
    <Space wrap className="mb-3"><label>导入并分析视频 <input aria-label="导入并分析视频" type="file" accept=".mp4" disabled={busy || dnaLocked} onChange={event => { void upload(event.target.files?.[0]); event.target.value = '' }} /></label>
      <Select aria-label="选择视频分析" disabled={busy || dnaLocked} className="min-w-64" value={selected || undefined} onChange={value => { setSelected(value); setAnalysis(null) }} placeholder="选择已保存的分析" options={items.map(item => ({ value: item.id, label: `${item.id.slice(0, 8)} · ${labels[item.state] ?? item.state}` }))} />
    </Space>
    {error && <Alert type="error" message={error} className="mb-3" />}
    {analysis && <><Space wrap className="mb-3"><Tag>{labels[analysis.state] ?? analysis.state}</Tag><span>修订 {analysis.revision}</span><span>原片校验 {analysis.source_hash.slice(0, 12)}</span></Space>{analysis.reason && <p>{analysis.reason}</p>}</>}
    {manifest && analysis && <>
      {manifest.proxy.degraded && <Alert type="warning" message="预览映射存在降级，请按原始帧证据复核" className="mb-3" />}
      <p>{manifest.video.width}×{manifest.video.height} · {manifest.video.frames.length}个实际解码帧 · {manifest.cuts.length}个自动候选切点 · {analysis.confirmed_cuts ? `${analysis.confirmed_cuts.length - 1}个已确认切点` : '尚未人工确认'}</p>
      <video ref={player} controls preload="metadata" src={`${base}/proxy`} style={{ maxHeight: 360, maxWidth: '100%' }} />
      {manifest.audio.present ? <div><p>提取音轨（相对视频起点偏移 {((manifest.audio.tracks[0]?.origin_relative_us ?? 0) / 1e6).toFixed(3)} 秒）</p><audio controls preload="metadata" src={`${base}/audio`} /></div> : <p>原视频没有音轨</p>}
      <Space wrap className="my-3">{manifest.keyframes.map(frame => <button key={frame.frame_index} type="button" onClick={() => inspectFrame(frame.frame_index)}><img loading="lazy" src={`${base}/frames/${frame.frame_index}`} alt={`原始帧 ${frame.frame_index}`} style={{ height: 100, maxWidth: 120, objectFit: 'contain' }} /><span>帧{frame.frame_index}</span></button>)}</Space>
      <p>{analysis.confirmed_cuts ? '镜头时间线 · 人工确认' : '镜头时间线 · 自动候选，尚未确认'}</p>
      <Table size="small" rowKey="index" pagination={false} dataSource={timeline} columns={[
        { title: '镜头', dataIndex: 'number' },
        { title: '起始原始帧', dataIndex: 'index' },
        { title: '归一化时间', render: (_, row) => `${(row.start / 1e6).toFixed(3)} – ${row.end === null ? "末端未知（原帧N哨兵）" : `${(row.end / 1e6).toFixed(3)} 秒`}` },
        { title: '检查', render: (_, row) => <Button onClick={() => inspectFrame(row.index)}>查看起点</Button> },
      ]} />
      <Space wrap><label>查看原始帧 <InputNumber aria-label="原始帧序号" min={0} max={manifest.video.frames.length - 1} precision={0} value={frameInput} onChange={value => setFrameInput(Number(value ?? 0))} /></label><Button onClick={() => inspectFrame(frameInput)}>检查该帧</Button></Space>
      {previewFrame !== null && <div className="my-3"><p>原始帧 {previewFrame} · 归一化时间 {((manifest.video.frames[previewFrame]?.relative_us ?? 0) / 1e6).toFixed(6)}秒 · PTS {manifest.video.frames[previewFrame]?.pts}</p><img src={`${base}/frames/${previewFrame}`} alt={`检查原始帧 ${previewFrame}`} style={{ maxHeight: 320, maxWidth: '100%' }} /></div>}
      <label className="block my-3">人工确认切点（原始帧序号，必须包含0，用逗号分隔）<Input aria-label="人工确认切点" disabled={busy || dnaLocked} value={cuts} onChange={event => setCuts(event.target.value)} /></label>
      <Button type="primary" loading={busy} disabled={dnaLocked} onClick={() => void saveCuts()}>保存人工切点</Button>
      {analysis.confirmed_cuts?.length ? <DnaPanel key={`${projectId}:${analysis.id}`} projectId={projectId} analysisId={analysis.id} analysisRevision={analysis.revision} onInspectFrame={inspectFrame} onLockChange={setDnaLocked} /> : <Alert className="mt-3" type="info" message="保存人工确认切点后，可建立人工Video DNA准备工作区" />}
    </>}
  </Card>
}
