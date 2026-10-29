import { Button, Statistic, Progress, Space, Skeleton } from 'antd'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  FileSearchOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import type { TabKey } from '../constants'
import { getChapterShotsPath, getChapterStudioPath, getProjectChaptersPath, getProjectEditorPath } from '../routes'
import { useProject, useChapters } from '../hooks/useProjectData'
import { ensureHasShotsBeforeShooting } from '../ensureHasShotsBeforeShooting'
import { getChapterPreparationState } from '../chapterPreparation'
import {
  loadChapterFlowStats,
  loadProjectFlowStatsForChapters,
  type ChapterFlowStats,
  type ProjectFlowStats,
} from '../projectFlowStats'

/** Summarize real chapter preparation and shot runtime data as a production ledger. */
export function DashboardTab({ onSelectTab }: { onSelectTab: (tab: TabKey) => void }) {
  const navigate = useNavigate()
  const { projectId } = useParams<{ projectId: string }>()
  const { project, loading: projectLoading } = useProject(projectId)
  const { chapters, loading: chaptersLoading } = useChapters(projectId)
  const [flowStats, setFlowStats] = useState<ProjectFlowStats>({
    totalShots: 0,
    pendingConfirmShots: 0,
    readyShots: 0,
    generatingShots: 0,
  })
  const [chapterFlowStats, setChapterFlowStats] = useState<ChapterFlowStats[]>([])
  const [flowStatsLoading, setFlowStatsLoading] = useState(false)

  const loading = projectLoading || chaptersLoading
  const chaptersByIndex = [...chapters].sort((a, b) => a.index - b.index)
  const incompleteChapters = chaptersByIndex.filter((c) => c.status !== 'done')
  const recommendedChapter =
    chaptersByIndex.find((chapter) => getChapterPreparationState(chapter).key === 'edit_raw') ??
    chaptersByIndex.find((chapter) => getChapterPreparationState(chapter).key === 'extract_shots') ??
    chaptersByIndex.find((chapter) => getChapterPreparationState(chapter).key === 'prepare_shots') ??
    chaptersByIndex.find((chapter) => getChapterPreparationState(chapter).key === 'shoot') ??
    chaptersByIndex[0] ??
    null

  useEffect(() => {
    let cancelled = false
    if (!projectId || !chapters.length) {
      setFlowStats({
        totalShots: 0,
        pendingConfirmShots: 0,
        readyShots: 0,
        generatingShots: 0,
      })
      setChapterFlowStats([])
      return () => {
        cancelled = true
      }
    }

    const run = async () => {
      setFlowStatsLoading(true)
      try {
        const [stats, chapterStats] = await Promise.all([
          loadProjectFlowStatsForChapters(chapters),
          loadChapterFlowStats(chapters),
        ])
        if (!cancelled) setFlowStats(stats)
        if (!cancelled) setChapterFlowStats(chapterStats)
      } catch {
        if (!cancelled) {
          setFlowStats({
            totalShots: 0,
            pendingConfirmShots: 0,
            readyShots: 0,
            generatingShots: 0,
          })
          setChapterFlowStats([])
        }
      } finally {
        if (!cancelled) setFlowStatsLoading(false)
      }
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [chapters, projectId])

  if (loading && !project) {
    return (
      <div className="project-dashboard" aria-busy="true" aria-label="加载项目概览">
        <Skeleton title={{ width: '35%' }} paragraph={{ rows: 2 }} />
        <Skeleton title={false} paragraph={{ rows: 3 }} />
      </div>
    )
  }
  if (!project) {
    return null
  }

  const incompleteCount = incompleteChapters.length
  const recommendedState = recommendedChapter ? getChapterPreparationState(recommendedChapter) : null
  const chaptersNeedingRawText = chaptersByIndex.filter((chapter) => getChapterPreparationState(chapter).key === 'edit_raw').length
  const chaptersNeedingShotExtract = chaptersByIndex.filter((chapter) => getChapterPreparationState(chapter).key === 'extract_shots').length
  const chaptersNeedingShotPrep = chaptersByIndex.filter((chapter) => getChapterPreparationState(chapter).key === 'prepare_shots').length
  const chaptersReadyForShoot = chaptersByIndex.filter((chapter) => getChapterPreparationState(chapter).key === 'shoot').length
  const topPendingChapter = [...chapterFlowStats].sort((a, b) => b.pendingConfirmShots - a.pendingConfirmShots)[0]
  const topGeneratingChapter = [...chapterFlowStats].sort((a, b) => b.generatingShots - a.generatingShots)[0]
  const topReadyChapter = [...chapterFlowStats].sort((a, b) => b.readyShots - a.readyShots)[0]

  /** Continue the same recommended chapter through the existing preparation and studio routes. */
  const handleRecommendedAction = () => {
    if (!projectId) return
    if (!recommendedChapter || !recommendedState) {
      onSelectTab('chapters')
      return
    }
    if (recommendedState.key === 'edit_raw') {
      onSelectTab('chapters')
      navigate(`/projects/${projectId}?tab=chapters&edit=${recommendedChapter.id}`, { replace: false })
      return
    }
    if (recommendedState.key === 'extract_shots') {
      navigate(getChapterShotsPath(projectId, recommendedChapter.id))
      return
    }
    if (recommendedState.key === 'prepare_shots') {
      navigate(getChapterStudioPath(projectId, recommendedChapter.id))
      return
    }
    void ensureHasShotsBeforeShooting({
      projectId,
      chapterId: recommendedChapter.id,
      storyboardCount: recommendedChapter.storyboardCount,
      navigate,
    })
  }

  const chapterTodoCards = [
    {
      key: 'edit_raw',
      title: '待补原文',
      count: chaptersNeedingRawText,
      hint: '这些章节还没进入分镜流程',
      icon: <ClockCircleOutlined />,
    },
    {
      key: 'extract_shots',
      title: '待提取分镜',
      count: chaptersNeedingShotExtract,
      hint: '已有原文，可直接进入分镜提取',
      icon: <ClockCircleOutlined />,
    },
    {
      key: 'prepare_shots',
      title: '待准备镜头',
      count: chaptersNeedingShotPrep,
      hint: '已有分镜，建议进入工作室继续处理',
      icon: <ClockCircleOutlined />,
    },
    {
      key: 'shoot',
      title: '可继续拍摄',
      count: chaptersReadyForShoot,
      hint: '这部分章节已具备继续拍摄条件',
      icon: <CheckCircleOutlined />,
    },
  ] as const

  return (
    <div className="project-dashboard">
      <section className="project-next-action">
        <div>
          <h2>当前推荐动作</h2>
          <p>
            {recommendedChapter && recommendedState
              ? `第${recommendedChapter.index}章：${recommendedState.hint}`
              : '暂无章节，可先创建第一章'}
          </p>
        </div>
        <Space wrap>
          <Button onClick={() => onSelectTab('chapters')}>进入章节管理</Button>
          <Button onClick={() => projectId && navigate(getProjectEditorPath(projectId))}>进入后期剪辑</Button>
          <Button
            type="primary"
            icon={recommendedState?.primaryIcon ?? <VideoCameraOutlined />}
            onClick={handleRecommendedAction}
          >
            {recommendedChapter && recommendedState ? recommendedState.primaryAction : '创建第一章'}
          </Button>
        </Space>
      </section>

      <div className="project-dashboard-stats">
        <div><Statistic title="未完成章节" value={incompleteCount} /></div>
        <div>
          <Statistic
            title="待确认分镜"
            value={flowStats.pendingConfirmShots}
            suffix={flowStats.totalShots ? `/ ${flowStats.totalShots}` : undefined}
            loading={flowStatsLoading}
          />
        </div>
        <div><Statistic title="已就绪分镜" value={flowStats.readyShots} loading={flowStatsLoading} /></div>
        <div>
          <Statistic title="生成中分镜" value={flowStats.generatingShots} loading={flowStatsLoading} />
          <Progress
            percent={flowStats.totalShots ? Math.round((flowStats.readyShots / flowStats.totalShots) * 100) : project.progress}
            showInfo={false}
            size="small"
            strokeColor="var(--color-accent)"
          />
        </div>
      </div>

      <section className="project-dashboard-section">
        <div className="project-dashboard-section-heading">
          <h2>当前待办</h2>
          <Button type="link" onClick={() => projectId && navigate(getProjectChaptersPath(projectId))}>查看全部</Button>
        </div>
        {chapters.length === 0 ? (
          <div className="project-empty-state">
            还没有任何章节，
            <Button type="link" onClick={() => onSelectTab('chapters')}>立即创建第一章</Button>
          </div>
        ) : (
          <div className="project-phase-ledger">
            {chapterTodoCards.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`project-phase-item${recommendedState?.key === item.key ? ' is-current' : ''}`}
                onClick={() => onSelectTab('chapters')}
              >
                <span className="project-phase-title">{item.title}</span>
                <strong>{item.count}</strong>
                <small>{item.hint}</small>
              </button>
            ))}
          </div>
        )}
      </section>

      <div className="project-dashboard-bottom">
        <section className="project-dashboard-section">
          <div className="project-dashboard-section-heading"><h2>动态摘要</h2></div>
          <div className="project-activity-list">
            <div className="project-activity-row">
              <ClockCircleOutlined />
              <div>
                <h3>待确认压力最大</h3>
                <p>{topPendingChapter && topPendingChapter.pendingConfirmShots > 0
                  ? `第${topPendingChapter.chapterIndex ?? '-'}章还有 ${topPendingChapter.pendingConfirmShots} 条分镜待确认，建议优先处理。`
                  : '当前没有待确认分镜积压。'}</p>
              </div>
            </div>
            <div className="project-activity-row">
              <VideoCameraOutlined />
              <div>
                <h3>当前生成最活跃</h3>
                <p>{topGeneratingChapter && topGeneratingChapter.generatingShots > 0
                  ? `第${topGeneratingChapter.chapterIndex ?? '-'}章有 ${topGeneratingChapter.generatingShots} 条分镜正在生成，可以继续关注结果。`
                  : '当前没有分镜处于生成中。'}</p>
              </div>
            </div>
            <div className="project-activity-row">
              <CheckCircleOutlined />
              <div>
                <h3>最适合继续推进</h3>
                <p>{topReadyChapter && topReadyChapter.readyShots > 0
                  ? `第${topReadyChapter.chapterIndex ?? '-'}章已有 ${topReadyChapter.readyShots} 条已就绪分镜，适合继续推进视频生成。`
                  : '当前还没有明显可继续推进的视频生成批次。'}</p>
              </div>
            </div>
          </div>
          <Button type="link" icon={<FileSearchOutlined />} onClick={() => onSelectTab('chapters')}>去章节里继续推进</Button>
        </section>
        <section className="project-dashboard-section">
          <div className="project-dashboard-section-heading"><h2>资产健康快照</h2></div>
          <dl className="project-asset-counts">
            <div><dt>角色</dt><dd>{project.stats.roles} 项</dd></div>
            <div><dt>场景</dt><dd>{project.stats.scenes} 项</dd></div>
            <div><dt>道具</dt><dd>{project.stats.props} 项</dd></div>
          </dl>
          <Button type="link" onClick={() => navigate('/assets')}>管理资产</Button>
        </section>
      </div>
    </div>
  )
}
