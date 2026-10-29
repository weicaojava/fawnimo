import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Modal, Space } from 'antd'
import { Link } from 'react-router-dom'
import { VideoDnaProductionService as P } from '../../../../../services/generated'
import type { DnaProductionRead } from '../../../../../services/generated'
import { ProjectSection } from '../../ProjectSection'

type Preview = { counts: Record<string, number>; source_current: boolean; already_imported?: DnaProductionRead | null; message: string }
const labels: Record<string, string> = { actors: '演员', costumes: '服装', scenes: '场景', props: '道具', shots: '分镜', prompts: '提示词' }

/** Promote the displayed source-backed DNA revision into pending records through the real API. */
export function DnaProductionControls({ projectId, draftId, revision, dirty, busy }: { projectId: string; draftId: string; revision: number; dirty: boolean; busy: boolean }) {
  const [preview, setPreview] = useState<Preview | null>(null)
  const [receipt, setReceipt] = useState<DnaProductionRead | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const alive = useRef(true)
  const requestKey = useRef(crypto.randomUUID())
  useEffect(() => {
    alive.current = true
    setPreview(null)
    setReceipt(null)
    setError('')
    requestKey.current = crypto.randomUUID()
    void P.previewDnaProductionApiV1VideoDnaProjectsProjectIdDraftsDraftIdProductionPreviewGet({ projectId, draftId }).then(response => {
      if (!alive.current) return
      const data = response.data as Preview | null
      setPreview(data)
      setReceipt(data?.already_imported ?? null)
    }).catch(reason => { if (alive.current) setError(explain(reason)) })
    return () => { alive.current = false }
  }, [projectId, draftId, revision])

  /** Keep safe server validation messages available instead of reporting a generic success. */
  function explain(reason: unknown): string {
    const body = (reason as { body?: { message?: unknown; detail?: unknown } })?.body
    return typeof body?.message === 'string' ? body.message : typeof body?.detail === 'string' ? body.detail : '未能读取或导入拆解资料，请刷新后重试。'
  }

  /** Reuse the request identity after an uncertain response so retries cannot duplicate a chapter. */
  async function importPending() {
    setSaving(true)
    setError('')
    try {
      const response = await P.importDnaProductionApiV1VideoDnaProjectsProjectIdDraftsDraftIdProductionImportsPost({ projectId, draftId, requestBody: { expected_revision: revision, request_key: requestKey.current } })
      if (alive.current) setReceipt(response.data ?? null)
    } catch (reason) { if (alive.current) setError(explain(reason)) }
    finally { if (alive.current) setSaving(false) }
  }

  return <ProjectSection headingLevel={4} title="拆解资料进入制作" data-testid="dna-production-import">
    <p>把当前已保存草稿中的人物、服装、场景、道具和逐镜提示词写入资产库，并新建章节和镜头计划。观察和推断均保留，所有镜头仍待确认。</p>
    {error && <Alert className="my-3" showIcon type="error" message={error} />}
    {preview && <div className="project-inline-facts my-3">{Object.entries(labels).map(([key, label]) => <span key={key}>{label} {preview.counts[key] ?? 0}</span>)}</div>}
    {receipt ? <>
      <Alert type="info" className="my-3" message="这份草稿已导入制作资料" description={receipt.message} />
      <Space wrap>
        <Link to={`/projects/${projectId}/chapters/${receipt.chapter_id}/shots`}>查看导入分镜</Link>
        <Link to="/assets?tab=actor">查看演员与服装</Link>
        <Link to="/prompts">查看提示词</Link>
        <Link to={`/projects/${projectId}?tab=production`}>查看版本资产</Link>
      </Space>
      {receipt.draft_revision !== revision && <p className="text-amber-700 mt-3">已导入的是修订 {receipt.draft_revision}。当前新修订不会覆盖已导入章节，请在章节中继续编辑或另建草稿。</p>}
    </> : <>
      {preview && !preview.source_current && <Alert className="my-3" type="warning" message="原片确认边界已变化，请先刷新DNA来源。" />}
      <Button className="mt-3" type="primary" loading={saving} disabled={busy || dirty || saving || !preview?.source_current} onClick={() => Modal.confirm({ title: '导入为待确认制作资料？', content: '会新建演员、服装、场景、道具、提示词和章节。不会覆盖已有章节，不会标记已签核或提交模型生成。', okText: '导入资料', cancelText: '取消', onOk: importPending })}>导入资产、提示词与分镜</Button>
      {dirty && <p className="text-gray-600 mt-2">请先保存当前DNA修改，再导入这一修订。</p>}
    </>}
  </ProjectSection>
}
