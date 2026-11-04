import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Space, Spin } from 'antd'
import { Link } from 'react-router-dom'
import { StudioChaptersService, StudioProjectsService, StudioShotDetailsService, StudioShotsService } from '../../../services/generated'
import { getChapterShotsPath } from '../project/ProjectWorkbench/routes'

/** Recover an older manually created shot only after checking its full route ownership and explicit user intent. */
export function ShotDetailRecovery({ projectId, chapterId, shotId, onRecovered }: { projectId: string; chapterId: string; shotId: string; onRecovered: () => void }) {
  const [checked, setChecked] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const mounted = useRef(false)
  const inFlight = useRef(false)
  useEffect(() => {
    mounted.current = true
    let active = true
    void Promise.all([
      StudioProjectsService.getProjectApiV1StudioProjectsProjectIdGet({ projectId }),
      StudioChaptersService.getChapterApiV1StudioChaptersChapterIdGet({ chapterId }),
      StudioShotsService.getShotApiV1StudioShotsShotIdGet({ shotId }),
    ]).then(([project, chapter, shot]) => {
      if (!active) return
      if (project.data?.id !== projectId || chapter.data?.project_id !== projectId || shot.data?.chapter_id !== chapterId) throw new Error('分镜不属于当前工程或章节。')
      setChecked(true)
    }).catch(() => { if (active) setError('无法核对分镜归属，请返回列表重新打开。') })
    return () => { active = false; mounted.current = false }
  }, [projectId, chapterId, shotId])

  /** Create only the absent detail; an already saved result is read back rather than overwritten. */
  async function recover() {
    if (!checked || inFlight.current) return
    inFlight.current = true; setBusy(true); setError('')
    try {
      try {
        await StudioShotDetailsService.createShotDetailApiV1StudioShotDetailsPost({ requestBody: { id: shotId, camera_shot: 'MS', angle: 'EYE_LEVEL', movement: 'STATIC' } })
      } catch {
        // A lost acknowledgement or concurrent explicit recovery may already have created it.
        const saved = await StudioShotDetailsService.getShotDetailApiV1StudioShotDetailsShotIdGet({ shotId })
        if (saved.data?.id !== shotId) throw new Error('镜头详情尚未取得。')
      }
      if (mounted.current) onRecovered()
    } catch {
      if (mounted.current) setError('尚未取得镜头详情。可核对后再次尝试，已有详情不会被覆盖。')
    } finally {
      inFlight.current = false
      if (mounted.current) setBusy(false)
    }
  }

  return <Card title="这条旧分镜还没有镜头详情" style={{ margin: 24 }}>
    <Space direction="vertical">
      <span>建立空白详情后即可继续编辑。原分镜标题、剧本摘录、确认状态和历史保留；景别先设为中景、平视、静止，可在编辑页调整。</span>
      {!checked && !error && <Spin />}
      {error && <Alert type="error" showIcon message={error} />}
      <Space><Button type="primary" disabled={!checked} loading={busy} onClick={() => void recover()}>建立空白镜头详情</Button><Link to={getChapterShotsPath(projectId, chapterId)}>返回分镜列表</Link></Space>
    </Space>
  </Card>
}
