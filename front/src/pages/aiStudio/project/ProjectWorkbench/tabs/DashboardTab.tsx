import { Alert, Button, Statistic, Progress, Space, Skeleton } from 'antd'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  FileSearchOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import type { TabKey } from '../constants'
import { getChapterShotsPath, getChapterShotEditPath, getProjectChaptersPath, getProjectEditorPath } from '../routes'
import type { ChaptersState, ProjectState } from '../hooks/useProjectData'
import type { ProductionFlowState } from '../hooks/useProductionFlow'
import { ensureHasShotsBeforeShooting } from '../ensureHasShotsBeforeShooting'
import { getChapterPreparationState } from '../chapterPreparation'

/** Summarize real chapter preparation and shot runtime data as a production ledger. */
export function DashboardTab({ onSelectTab, projectState, chaptersState, flowState }: { onSelectTab: (tab: TabKey) => void; projectState: ProjectState; chaptersState: ChaptersState; flowState: ProductionFlowState }) {
  const navigate = useNavigate()
  const { projectId } = useParams<{ projectId: string }>()
  const { project, loading: projectLoading } = projectState
  const { chapters, loading: chaptersLoading, error: chaptersError, refresh: refreshChapters } = chaptersState
  const flowStats = flowState.totals
  const chapterFlowStats = Object.values(flowState.byChapter)
  const flowStatsLoading = flowState.loading
  const loading = projectLoading || chaptersLoading
  const chaptersByIndex = [...chapters].sort((a, b) => a.index - b.index)
  const incompleteChapters = chaptersByIndex.filter(chapter => chapter.status !== 'done')
  const recommendedChapter = ['edit_raw', 'extract_shots', 'prepare_shots', 'shoot']
    .map(key => chaptersByIndex.find(chapter => getChapterPreparationState(chapter, flowState.byChapter[chapter.id]).key === key))
    .find(Boolean) ?? null

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
  const recommendedState = recommendedChapter ? getChapterPreparationState(recommendedChapter, flowState.byChapter[recommendedChapter.id]) : null
  const chaptersNeedingRawText = chaptersByIndex.filter((chapter) => getChapterPreparationState(chapter, flowState.byChapter[chapter.id]).key === 'edit_raw').length
  const chaptersNeedingShotExtract = chaptersByIndex.filter((chapter) => getChapterPreparationState(chapter, flowState.byChapter[chapter.id]).key === 'extract_shots').length
  const chaptersNeedingShotPrep = chaptersByIndex.filter((chapter) => getChapterPreparationState(chapter, flowState.byChapter[chapter.id]).key === 'prepare_shots').length
  const chaptersReadyForShoot = chaptersByIndex.filter((chapter) => getChapterPreparationState(chapter, flowState.byChapter[chapter.id]).key === 'shoot').length
  const topPendingChapter = [...chapterFlowStats].sort((a, b) => b.pendingConfirmShots - a.pendingConfirmShots)[0]
  const topGeneratingChapter = [...chapterFlowStats].sort((a, b) => b.generatingShots - a.generatingShots)[0]
  const topReadyChapter = [...chapterFlowStats].sort((a, b) => b.readyShots - a.readyShots)[0]

  /** Continue the same recommended chapter through the existing preparation and studio routes. */
  const handleRecommendedAction = () => {
    if (!projectId) return
    if (chaptersError) { void refreshChapters(); return }
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
      navigate(`/projects/${projectId}?tab=chapters&edit=${encodeURIComponent(recommendedChapter.id)}&ai=divide`)
      return
    }
    if (recommendedState.key === 'prepare_shots') {
      const pendingId = flowState.byChapter[recommendedChapter.id]?.firstPendingShotId
      navigate(pendingId ? getChapterShotEditPath(projectId, recommendedChapter.id, pendingId) : getChapterShotsPath(projectId, recommendedChapter.id))
      return
    }
    void ensureHasShotsBeforeShooting({
      projectId,
      chapterId: recommendedChapter.id,
      storyboardCount: flowState.byChapter[recommendedChapter.id]?.totalShots,
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
      title: '待核对与确认',
      count: chaptersNeedingShotPrep,
      hint: '先在分镜编辑页完成信息确认',
      icon: <ClockCircleOutlined />,
    },
    {
      key: 'shoot',
      title: '可进入生成准备',
      count: chaptersReadyForShoot,
      hint: '分镜信息已确认，生成条件到工作室核对',
      icon: <CheckCircleOutlined />,
    },
  ] as const

  return (
    <div className="project-dashboard">
      {chaptersError && <Alert type="error" showIcon message={chaptersError} action={<Button onClick={() => void refreshChapters()}>重试读取章节</Button>} />}

      <section className="project-next-action">
        <div>
          <h2>当前推荐动作</h2>
          <p>
            {chaptersError ? '章节尚未读取成功，请重试后继续。' : chaptersLoading ? '正在读取实际章节。' : recommendedChapter && recommendedState
              ? `第${recommendedChapter.index}章：${recommendedState.hint}`
              : '暂无章节，可先创建第一章'}
          </p>
        </div>
        <Space wrap>
          <Button onClick={() => onSelectTab('chapters')}>进入章节管理</Button>
          <Button onClick={() => projectId && navigate(getProjectEditorPath(projectId))}>进入后期剪辑</Button>
          <Button
            type="primary"
            disabled={chaptersLoading}
            icon={recommendedState?.primaryIcon ?? <VideoCameraOutlined />}
            onClick={handleRecommendedAction}
          >
            {chaptersLoading ? '读取制作资料…' : chaptersError ? '重试读取章节' : recommendedChapter && recommendedState ? recommendedState.primaryAction : '创建第一章'}
          </Button>
        </Space>
      </section>

      <div className="project-dashboard-stats">
        <div><Statistic title="未标记完成章节" value={chaptersLoading || chaptersError ? '—' : incompleteCount} /></div>
        <div>
          <Statistic
            title="待确认分镜"
            value={flowStats?.pendingConfirmShots ?? '—'}
            suffix={flowStats?.totalShots ? `/ ${flowStats.totalShots}` : undefined}
            loading={flowStatsLoading}
          />
        </div>
        <div><Statistic title="已确认分镜" value={flowStats?.readyShots ?? '—'} loading={flowStatsLoading} /></div>
        <div>
          <Statistic title="视频任务未结束" value={flowStats?.generatingShots ?? '—'} loading={flowStatsLoading} />
          {flowStats && <Progress
            percent={flowStats?.totalShots ? Math.round((flowStats.readyShots / flowStats.totalShots) * 100) : 0}
            showInfo={false}
            size="small"
            strokeColor="var(--color-accent)"
          />}
        </div>
      </div>

      <section className="project-dashboard-section">
        <div className="project-dashboard-section-heading">
          <h2>当前待办</h2>
          <Button type="link" onClick={() => projectId && navigate(getProjectChaptersPath(projectId))}>查看全部</Button>
        </div>
        {chaptersLoading ? <Skeleton title={false} paragraph={{ rows: 1 }} /> : chaptersError ? <p>章节尚未读取成功。</p> : chapters.length === 0 ? (
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
                <p>{!flowStats ? '分镜进度尚未核对。' : topPendingChapter && topPendingChapter.pendingConfirmShots > 0
                  ? `第${topPendingChapter.chapterIndex ?? '-'}章还有 ${topPendingChapter.pendingConfirmShots} 条分镜待确认，建议优先处理。`
                  : '当前没有待确认分镜积压。'}</p>
              </div>
            </div>
            <div className="project-activity-row">
              <VideoCameraOutlined />
              <div>
                <h3>视频任务待处理最多</h3>
                <p>{!flowStats ? '分镜任务尚未核对。' : topGeneratingChapter && topGeneratingChapter.generatingShots > 0
                  ? `第${topGeneratingChapter.chapterIndex ?? '-'}章有 ${topGeneratingChapter.generatingShots} 条分镜的视频任务未结束，可到工作室查看等待、运行或暂停状态。`
                  : '当前没有未结束的视频任务。'}</p>
              </div>
            </div>
            <div className="project-activity-row">
              <CheckCircleOutlined />
              <div>
                <h3>最适合继续推进</h3>
                <p>{!flowStats ? '分镜进度尚未核对。' : topReadyChapter && topReadyChapter.readyShots > 0
                  ? `第${topReadyChapter.chapterIndex ?? '-'}章已有 ${topReadyChapter.readyShots} 条已确认分镜，可到工作室核对生成条件。`
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
