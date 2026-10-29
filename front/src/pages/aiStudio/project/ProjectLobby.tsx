import React, { useEffect, useMemo, useState } from 'react'
import {
  Card,
  Input,
  Button,
  Progress,
  Skeleton,
  Modal,
  Form,
  Select,
  InputNumber,
  Switch,
  message,
  Space,
  Tag,
  Popconfirm,
  Alert,
} from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  EnterOutlined,
  AppstoreOutlined,
  UnorderedListOutlined,
  BarsOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { chapters as mockChapters, projects as mockProjects, type Project } from '../../../mocks/data'
import { StudioChaptersService, StudioProjectsService } from '../../../services/generated'
import type { ChapterRead, ProjectRead, ProjectStyle } from '../../../services/generated'
import {
  ProjectVisualStyleAndStyleFields,
  type ProjectVisualStyleChoice,
} from './ProjectVisualStyleAndStyleFields'
import { useProjectStyleOptions } from './useProjectStyleOptions'
import { ProjectStageTrack } from './ProjectSection'
import { DemoStarter } from '../../../identity/DemoStarter'
import './project-studio.css'
import { getChapterPreparationState } from './ProjectWorkbench/chapterPreparation'
import { ensureHasShotsBeforeShooting } from './ProjectWorkbench/ensureHasShotsBeforeShooting'
import { getChapterShotsPath, getChapterStudioPath } from './ProjectWorkbench/routes'
import { loadProjectFlowStatsForChapters, type ProjectFlowStats } from './ProjectWorkbench/projectFlowStats'

type ViewMode = 'grid' | 'compact' | 'large'
type FilterTab = 'all' | 'editRaw' | 'extractShots' | 'prepareShots' | 'generating' | 'ready'
type SortKey = 'updatedAt' | 'name' | 'createdAt' | 'chapters'
type ChapterPreparationInput = Parameters<typeof getChapterPreparationState>[0]
type ProjectStageSummary = {
  key: ReturnType<typeof getChapterPreparationState>['key'] | 'create_first_chapter'
  stageText: string
  stageColor: string
  nextActionLabel: string
  nextActionHint: string
  chapterId?: string
  storyboardCount?: number
}
type ProjectFlowStatsMap = Record<string, ProjectFlowStats>
type ProjectView = Project & {
  visualStyle?: ProjectVisualStyleChoice
  defaultVideoRatio?: string | null
  createdAt?: string
}

/** Display persisted timestamps and leave absent or invalid historical values visibly unknown. */
function formatProjectUpdatedAt(value: string): string {
  const date = new Date(value)
  if (!value || !Number.isFinite(date.getTime())) return '时间未提供'
  return date.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

/** Keep project discovery and its existing production actions together in one readable studio. */
const ProjectLobby: React.FC = () => {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<ProjectView[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [viewMode, setViewMode] = useState<ViewMode>('grid')
  const [filterTab, setFilterTab] = useState<FilterTab>('all')
  const [sortKey, setSortKey] = useState<SortKey>('updatedAt')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [multiSelectMode, setMultiSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [editModalOpen, setEditModalOpen] = useState(false)
  const [editingProject, setEditingProject] = useState<ProjectView | null>(null)
  const [projectStageMap, setProjectStageMap] = useState<Record<string, ProjectStageSummary>>({})
  const [projectFlowStatsMap, setProjectFlowStatsMap] = useState<ProjectFlowStatsMap>({})
  const {
    options: projectStyleOptions,
    videoRatioOptions,
    defaultVideoRatio,
  } = useProjectStyleOptions()
  const [form] = Form.useForm()
  const [editForm] = Form.useForm()

  const useMock = import.meta.env.VITE_USE_MOCK === 'true'

  const toUIProject = (p: ProjectRead): ProjectView => {
    const stats = (p.stats ?? {}) as Record<string, unknown>
    const getNum = (key: string) => {
      const v = stats[key]
      return typeof v === 'number' && Number.isFinite(v) ? v : 0
    }

    return {
      id: p.id,
      name: p.name,
      description: p.description ?? '',
      style: (p.style as Project['style']) ?? '现实主义',
      seed: p.seed ?? 0,
      unifyStyle: p.unify_style ?? true,
      progress: p.progress ?? 0,
      stats: {
        chapters: getNum('chapters'),
        roles: getNum('roles'),
        scenes: getNum('scenes'),
        props: getNum('props'),
      },
      updatedAt: p.updated_at,
      createdAt: p.created_at,
      visualStyle: (p.visual_style as ProjectVisualStyleChoice | undefined) ?? '现实',
      defaultVideoRatio: p.default_video_ratio ?? null,
    }
  }

  const newProjectId = () => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
    return `p_${Date.now()}_${Math.random().toString(16).slice(2)}`
  }

  /** Read every server page before enabling local search/sort so older projects remain discoverable. */
  const load = async () => {
    setLoading(true)
    setLoadError(null)
    try {
      if (useMock) {
        setProjects(mockProjects)
      } else {
        const items: ProjectRead[] = []
        const seen = new Set<string>()
        let page = 1
        let maxPage = 1
        let total = 0
        do {
          const res = await StudioProjectsService.listProjectsApiV1StudioProjectsGet({
            order: 'updated_at', isDesc: true, page, pageSize: 100,
          })
          if (!res.data) throw new Error('项目列表未返回分页数据')
          const data = res.data
          if (page === 1) total = data.pagination.total
          if (data.pagination.total !== total) throw new Error('项目列表在加载时发生变化，请重试')
          maxPage = data.pagination.max_page
          for (const project of data.items) {
            if (seen.has(project.id)) throw new Error('项目排序在加载时发生变化，请重试')
            seen.add(project.id)
            items.push(project)
          }
          if (!data.items.length && items.length < total) throw new Error('项目列表分页不完整，请重试')
          page += 1
        } while (page <= maxPage)
        if (items.length !== total) throw new Error('项目列表分页不完整，请重试')
        setProjects(items.map(toUIProject))
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '项目列表加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const getProjectStatus = (p: ProjectView): 'draft' | 'inProgress' | 'completed' => {
    if (p.progress >= 90) return 'completed'
    if (p.progress <= 5) return 'draft'
    return 'inProgress'
  }

  useEffect(() => {
    const list = Array.isArray(projects) ? projects : []
    if (!list.length) {
      setProjectStageMap({})
      setProjectFlowStatsMap({})
      return
    }

    const summarizeProjectChapters = (chapters: ChapterPreparationInput[]): ProjectStageSummary => {
      const chaptersByIndex = [...chapters].sort((a, b) => a.index - b.index)
      if (!chaptersByIndex.length) {
        return {
          key: 'create_first_chapter',
          stageText: '待创建章节',
          stageColor: 'default',
          nextActionLabel: '创建第一章',
          nextActionHint: '项目还没有章节，建议先创建第一章',
        }
      }
      const findByState = (key: ReturnType<typeof getChapterPreparationState>['key']) =>
        chaptersByIndex.find((chapter) => getChapterPreparationState(chapter).key === key)
      const chapter =
        findByState('edit_raw') ??
        findByState('extract_shots') ??
        findByState('prepare_shots') ??
        findByState('shoot') ??
        chaptersByIndex[0]
      const state = getChapterPreparationState(chapter)
      return {
        key: state.key,
        stageText: state.text,
        stageColor: state.color,
        nextActionLabel: state.primaryAction,
        nextActionHint: `第${chapter.index}章 · ${state.hint}`,
        chapterId: chapter.id,
        storyboardCount: chapter.storyboardCount,
      }
    }

    const loadSummaries = async () => {
      try {
        if (useMock) {
          const chapterGroups = Object.fromEntries(
            list.map((project) => [
              project.id,
              mockChapters
                .filter((chapter) => chapter.projectId === project.id)
                .map((chapter) => ({
                  id: chapter.id,
                  projectId: chapter.projectId,
                  index: chapter.index,
                  title: chapter.title,
                  summary: chapter.summary ?? '',
                  rawText: chapter.summary ?? '',
                  storyboardCount: chapter.storyboardCount,
                  status: chapter.status,
                  updatedAt: chapter.updatedAt,
                })),
            ]),
          )
          setProjectStageMap(
            Object.fromEntries(
              Object.entries(chapterGroups).map(([projectId, chapters]) => [
                projectId,
                summarizeProjectChapters(chapters),
              ]),
            ),
          )
          const flowStatsEntries = await Promise.all(
            Object.entries(chapterGroups).map(async ([projectId, chapters]) => [
              projectId,
              await loadProjectFlowStatsForChapters(chapters),
            ] as const),
          )
          setProjectFlowStatsMap(Object.fromEntries(flowStatsEntries))
          return
        }

        const chapterResponses = await Promise.all(
          list.map(async (project) => {
            const res = await StudioChaptersService.listChaptersApiV1StudioChaptersGet({
              projectId: project.id,
              page: 1,
              pageSize: 100,
            })
            const items: ChapterRead[] = res.data?.items ?? []
            return [
              project.id,
              summarizeProjectChapters(
                items.map((chapter) => ({
                  id: chapter.id,
                  projectId: chapter.project_id,
                  index: chapter.index,
                  title: chapter.title,
                  summary: chapter.summary ?? '',
                  rawText: chapter.raw_text ?? '',
                  storyboardCount: chapter.shot_count ?? chapter.storyboard_count ?? 0,
                  status: chapter.status ?? 'draft',
                  updatedAt: '',
                })),
              ),
              items.map((chapter) => ({
                id: chapter.id,
                projectId: chapter.project_id,
                index: chapter.index,
                title: chapter.title,
                summary: chapter.summary ?? '',
                rawText: chapter.raw_text ?? '',
                storyboardCount: chapter.shot_count ?? chapter.storyboard_count ?? 0,
                status: chapter.status ?? 'draft',
                updatedAt: '',
              })),
            ] as const
          }),
        )
        setProjectStageMap(
          Object.fromEntries(chapterResponses.map(([projectId, summary]) => [projectId, summary])),
        )
        const flowStatsEntries = await Promise.all(
          chapterResponses.map(async ([projectId, _summary, chapters]) => [
            projectId,
            await loadProjectFlowStatsForChapters(chapters),
          ] as const),
        )
        setProjectFlowStatsMap(Object.fromEntries(flowStatsEntries))
      } catch {
        setProjectStageMap({})
        setProjectFlowStatsMap({})
      }
    }

    void loadSummaries()
  }, [projects, useMock])

  const filteredSorted = useMemo(() => {
    const list = Array.isArray(projects) ? projects : []
    const keyword = search.trim().toLowerCase()

    let next = list.filter((p) => {
      if (keyword) {
        const inText =
          p.name.toLowerCase().includes(keyword) ||
          p.description.toLowerCase().includes(keyword)
        if (!inText) return false
      }

      if (filterTab === 'all') return true
      const stage = projectStageMap[p.id]
      const flowStats = projectFlowStatsMap[p.id]
      if (filterTab === 'editRaw') return stage?.key === 'edit_raw' || stage?.key === 'create_first_chapter'
      if (filterTab === 'extractShots') return stage?.key === 'extract_shots'
      if (filterTab === 'prepareShots') return stage?.key === 'prepare_shots'
      if (filterTab === 'generating') return (flowStats?.generatingShots ?? 0) > 0
      if (filterTab === 'ready') return (flowStats?.readyShots ?? 0) > 0
      return true
    })

    next.sort((a, b) => {
      let av: string | number = ''
      let bv: string | number = ''
      if (sortKey === 'name') {
        av = a.name
        bv = b.name
      } else if (sortKey === 'chapters') {
        av = a.stats.chapters
        bv = b.stats.chapters
      } else if (sortKey === 'createdAt') {
        av = a.createdAt ? Date.parse(a.createdAt) : Number.NEGATIVE_INFINITY
        bv = b.createdAt ? Date.parse(b.createdAt) : Number.NEGATIVE_INFINITY
      } else {
        av = a.updatedAt ? Date.parse(a.updatedAt) : Number.NEGATIVE_INFINITY
        bv = b.updatedAt ? Date.parse(b.updatedAt) : Number.NEGATIVE_INFINITY
      }

      if (typeof av === 'number' && typeof bv === 'number') {
        if (av === bv) return a.id.localeCompare(b.id)
        return sortOrder === 'asc' ? av - bv : bv - av
      }
      const res = String(av).localeCompare(String(bv))
      return sortOrder === 'asc' ? res : -res
    })

    return next
  }, [projects, search, filterTab, sortKey, sortOrder, projectStageMap, projectFlowStatsMap])

  const handleSelectProject = (id: string) => {
    setSelectedProjectId(id)
  }

  const handleToggleSelect = (id: string, checked: boolean) => {
    setSelectedIds((prev) =>
      checked ? Array.from(new Set([...prev, id])) : prev.filter((x) => x !== id)
    )
  }

  const handleBatchDelete = async () => {
    if (!selectedIds.length) return
    try {
      await Promise.all(
        selectedIds.map((id) =>
          StudioProjectsService.deleteProjectApiV1StudioProjectsProjectIdDelete({ projectId: id }),
        ),
      )
      setProjects((prev) =>
        Array.isArray(prev) ? prev.filter((p) => !selectedIds.includes(p.id)) : prev
      )
      setSelectedIds([])
      message.success('已批量删除选中项目')
    } catch {
      message.error('批量删除失败')
    }
  }

  const handleOpenCreate = () => {
    form.resetFields()
    const defaultVisual = (projectStyleOptions.visualStyles[0]?.value ?? '现实') as ProjectVisualStyleChoice
    const defaultStyle =
      projectStyleOptions.defaultStyleByVisual?.[defaultVisual] ??
      projectStyleOptions.stylesByVisual[defaultVisual]?.[0]?.value
    form.setFieldsValue({
      visual_style: defaultVisual,
      style: defaultStyle,
      seed: Math.floor(Math.random() * 99999),
      unifyStyle: true,
      default_video_ratio: defaultVideoRatio,
    })
    setCreateModalOpen(true)
  }

  const handleCreateSubmit = async (values: {
    name: string
    description?: string
    style: string
    visual_style: ProjectVisualStyleChoice
    seed: number
    unifyStyle: boolean
    default_video_ratio?: string
  }) => {
    try {
      const createdId = newProjectId()
      const res = await StudioProjectsService.createProjectApiV1StudioProjectsPost({
        requestBody: {
          id: createdId,
          name: values.name,
          description: values.description ?? '',
          style: values.style as ProjectStyle,
          visual_style: values.visual_style as any,
          seed: values.seed,
          unify_style: values.unifyStyle,
          default_video_ratio: values.default_video_ratio || null,
          progress: 0,
        },
      })
      const created = res.data
      if (!created) throw new Error('empty project')
      const ui = toUIProject(created)
      message.success('项目创建成功')
      setCreateModalOpen(false)
      setProjects((prev) => (Array.isArray(prev) ? [...prev, ui] : [ui]))
      navigate(`/projects/${ui.id}`)
    } catch {
      message.error('创建失败')
    }
  }

  const handleOpenEdit = (e: React.MouseEvent, p: ProjectView) => {
    e.stopPropagation()
    setEditingProject(p)
    editForm.setFieldsValue({
      name: p.name,
      description: p.description,
      style: p.style,
      visual_style: p.visualStyle ?? '现实',
      seed: p.seed,
      unifyStyle: p.unifyStyle,
      default_video_ratio: p.defaultVideoRatio ?? undefined,
    })
    setEditModalOpen(true)
  }

  const handleEditSubmit = async (values: {
    name: string
    description?: string
    style: string
    visual_style: ProjectVisualStyleChoice
    seed: number
    unifyStyle: boolean
    default_video_ratio?: string
  }) => {
    if (!editingProject) return
    try {
      const res = await StudioProjectsService.updateProjectApiV1StudioProjectsProjectIdPatch({
        projectId: editingProject.id,
        requestBody: {
          name: values.name,
          description: values.description ?? '',
          style: values.style as ProjectStyle,
          visual_style: values.visual_style as any,
          seed: values.seed,
          unify_style: values.unifyStyle,
          default_video_ratio: values.default_video_ratio || null,
        },
      })
      const updated = res.data
      if (!updated) throw new Error('empty project')
      const ui = toUIProject(updated)
      message.success('项目已更新')
      setEditModalOpen(false)
      setEditingProject(null)
      setProjects((prev) =>
        Array.isArray(prev) ? prev.map((x) => (x.id === ui.id ? ui : x)) : prev
      )
    } catch {
      message.error('更新失败')
    }
  }

  const handleDelete = async (projectId: string) => {
    try {
      await StudioProjectsService.deleteProjectApiV1StudioProjectsProjectIdDelete({ projectId })
      message.success('已删除')
      setProjects((prev) => (Array.isArray(prev) ? prev.filter((p) => p.id !== projectId) : []))
    } catch {
      message.error('删除失败')
    }
  }

  const renderStatusTag = (p: ProjectView) => {
    const status = getProjectStatus(p)
    if (status === 'completed') return <Tag color="green" className="mr-0 text-xs leading-4">已完成</Tag>
    if (status === 'draft') return <Tag color="default" className="mr-0 text-xs leading-4">草稿</Tag>
    return <Tag color="orange" className="mr-0 text-xs leading-4">进行中</Tag>
  }

  const handlePrimaryAction = (project: ProjectView, stageSummary?: ProjectStageSummary) => {
    if (!stageSummary) {
      navigate(`/projects/${project.id}`)
      return
    }
    if (stageSummary.key === 'create_first_chapter') {
      navigate(`/projects/${project.id}?tab=chapters&create=1`)
      return
    }
    if (!stageSummary.chapterId) {
      navigate(`/projects/${project.id}`)
      return
    }
    if (stageSummary.key === 'edit_raw') {
      navigate(`/projects/${project.id}?tab=chapters&edit=${stageSummary.chapterId}`)
      return
    }
    if (stageSummary.key === 'extract_shots') {
      navigate(getChapterShotsPath(project.id, stageSummary.chapterId))
      return
    }
    if (stageSummary.key === 'prepare_shots') {
      navigate(getChapterStudioPath(project.id, stageSummary.chapterId))
      return
    }
    void ensureHasShotsBeforeShooting({
      projectId: project.id,
      chapterId: stageSummary.chapterId,
      storyboardCount: stageSummary.storyboardCount,
      navigate,
    })
  }

  const selectedProject = filteredSorted.find((p) => p.id === selectedProjectId) ?? filteredSorted[0]

  /** Compose each real project as a production slate while retaining every project action. */
  const renderCard = (p: ProjectView) => {
    const status = getProjectStatus(p)
    const stageSummary = projectStageMap[p.id]
    const flowStats = projectFlowStatsMap[p.id]
    const isCompact = viewMode === 'compact'
    const isSelected = selectedProject?.id === p.id
    const isChecked = selectedIds.includes(p.id)
    const mainActionLabel = stageSummary?.nextActionLabel ?? (status === 'completed' ? '继续剪辑' : p.progress > 0 ? '继续拍摄' : '进入项目')

    return (
      <Card
        key={p.id}
        hoverable
        loading={loading}
        size="small"
        className={`project-tile project-tile--${viewMode}${isSelected ? ' is-selected' : ''}`}
        tabIndex={0}
        aria-label={p.name}
        onClick={() => {
          handleSelectProject(p.id)
          if (!multiSelectMode) navigate(`/projects/${p.id}`)
        }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            handleSelectProject(p.id)
            if (multiSelectMode) handleToggleSelect(p.id, !isChecked)
            else navigate(`/projects/${p.id}`)
          }
        }}
        onFocus={() => handleSelectProject(p.id)}
        onMouseEnter={() => handleSelectProject(p.id)}
      >
        <div className="project-tile-header">
          <h2 className="project-tile-title">{p.name}</h2>
          <Space size="small">
            {renderStatusTag(p)}
            {multiSelectMode && (
              <input
                type="checkbox"
                className="project-tile-check"
                aria-label={`选择项目${p.name}`}
                checked={isChecked}
                onChange={(e) => {
                  e.stopPropagation()
                  handleToggleSelect(p.id, e.target.checked)
                }}
                onClick={(e) => e.stopPropagation()}
              />
            )}
          </Space>
        </div>
        <div className="project-tile-meta">
          <span>{p.style}</span>
          <time dateTime={p.updatedAt} title={p.updatedAt}>{formatProjectUpdatedAt(p.updatedAt)}</time>
        </div>
        {!isCompact && p.description && <p className="project-tile-description">{p.description}</p>}

        <ProjectStageTrack stage={stageSummary?.key} />
        <div className="project-stage-summary">
          <span>当前阶段</span>
          <Tag color={stageSummary?.stageColor ?? 'default'}>{stageSummary?.stageText ?? '待推进'}</Tag>
        </div>
        <p className="project-stage-hint">
          {stageSummary?.nextActionHint ?? '进入项目工作台后继续推进主流程'}
        </p>
        {isCompact ? (
          <p className="project-stage-hint">下一步：{stageSummary?.nextActionLabel ?? '进入项目'}</p>
        ) : (
          <div className="project-shot-counts">
            <span>待确认 <b>{flowStats?.pendingConfirmShots ?? 0}</b></span>
            <span>已就绪 <b>{flowStats?.readyShots ?? 0}</b></span>
            <span>生成中 <b>{flowStats?.generatingShots ?? 0}</b></span>
          </div>
        )}

        {!isCompact && (
          <div className="project-progress">
            <div className="project-progress-label"><span>进度</span><span>{p.progress}%</span></div>
            <Progress percent={p.progress} size="small" showInfo={false} strokeColor="var(--color-accent)" />
          </div>
        )}
        <dl className="project-metric-row">
          <div><dt>章节</dt><dd>{p.stats.chapters}</dd></div>
          <div><dt>角色</dt><dd>{p.stats.roles}</dd></div>
          <div><dt>场景</dt><dd>{p.stats.scenes}</dd></div>
          <div><dt>道具</dt><dd>{p.stats.props}</dd></div>
        </dl>
        <div className="project-tile-footer" onClick={(e) => e.stopPropagation()}>
          <Button type="primary" icon={<EnterOutlined />} onClick={() => handlePrimaryAction(p, stageSummary)}>
            {isCompact ? '进入' : mainActionLabel}
          </Button>
          {!isCompact && (
            <Space size="small">
              <Button type="text" icon={<EditOutlined />} aria-label={`编辑项目${p.name}`} onClick={(e) => handleOpenEdit(e, p)} />
              <Popconfirm
                title="确定删除该项目？"
                description="删除后无法恢复，相关章节与素材将不再关联。"
                onConfirm={() => handleDelete(p.id)}
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button type="text" danger icon={<DeleteOutlined />} aria-label={`删除项目${p.name}`} />
              </Popconfirm>
            </Space>
          )}
        </div>
      </Card>
    )
  }

  return (
    <div className="project-lobby" data-testid="project-lobby">
      <header className="project-page-heading">
        <div className="project-title-with-count">
          <h1>短剧项目</h1>
          <span>{loading ? '加载中…' : `${projects.length} 个项目`}</span>
        </div>
        <Space wrap>
          <Button
            type={multiSelectMode ? 'primary' : 'default'}
            onClick={() => {
              setMultiSelectMode((prev) => !prev)
              setSelectedIds([])
            }}
          >批量</Button>
          {multiSelectMode && (
            <Popconfirm
              title="批量删除项目"
              description="确定删除选中的所有项目？该操作不可恢复。"
              onConfirm={handleBatchDelete}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              disabled={!selectedIds.length}
            >
              <Button danger disabled={!selectedIds.length}>删除选中</Button>
            </Popconfirm>
          )}
          <DemoStarter />
          <Button icon={<PlusOutlined />} onClick={handleOpenCreate}>新建项目</Button>
        </Space>
      </header>

      {loadError && <Alert type="error" showIcon message="项目列表加载失败" description={loadError}
        action={<Button size="small" onClick={() => void load()}>重试</Button>} />}

      <div className="project-lobby-toolbar">
        <Input.Search
          placeholder="搜索项目名称或描述"
          aria-label="搜索项目名称或描述"
          allowClear
          className="project-lobby-search"
          onSearch={setSearch}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="project-toolbar-options">
          <div className="project-toolbar-group">
            <span className="project-toolbar-label">排序</span>
            <Select
              aria-label="项目排序"
              value={sortKey}
              style={{ width: 128 }}
              onChange={(value: SortKey) => setSortKey(value)}
              options={[
                { label: '最近更新', value: 'updatedAt' },
                { label: '创建时间', value: 'createdAt' },
                { label: '名称 A-Z', value: 'name' },
                { label: '章节数量', value: 'chapters' },
              ]}
            />
            <Button
              type="text"
              aria-label={sortOrder === 'asc' ? '改为降序' : '改为升序'}
              onClick={() => setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'))}
            >{sortOrder === 'asc' ? '↑' : '↓'}</Button>
          </div>
          <div className="project-toolbar-group">
            <span className="project-toolbar-label">视图</span>
            <Button type="text" className={`project-view-button${viewMode === 'grid' ? ' is-active' : ''}`} aria-label="卡片视图" aria-pressed={viewMode === 'grid'} icon={<AppstoreOutlined />} onClick={() => setViewMode('grid')} />
            <Button type="text" className={`project-view-button${viewMode === 'compact' ? ' is-active' : ''}`} aria-label="紧凑视图" aria-pressed={viewMode === 'compact'} icon={<BarsOutlined />} onClick={() => setViewMode('compact')} />
            <Button type="text" className={`project-view-button${viewMode === 'large' ? ' is-active' : ''}`} aria-label="大卡片视图" aria-pressed={viewMode === 'large'} icon={<UnorderedListOutlined />} onClick={() => setViewMode('large')} />
          </div>
        </div>
      </div>

      <nav className="project-filter-rail" aria-label="项目制作阶段筛选">
        <button type="button" className="project-filter-button" aria-pressed={filterTab === 'all'} onClick={() => setFilterTab('all')}>全部</button>
        <button type="button" className="project-filter-button" aria-pressed={filterTab === 'editRaw'} onClick={() => setFilterTab('editRaw')}>待补原文</button>
        <button type="button" className="project-filter-button" aria-pressed={filterTab === 'extractShots'} onClick={() => setFilterTab('extractShots')}>待提取分镜</button>
        <button type="button" className="project-filter-button" aria-pressed={filterTab === 'prepareShots'} onClick={() => setFilterTab('prepareShots')}>待准备镜头</button>
        <button type="button" className="project-filter-button" aria-pressed={filterTab === 'generating'} onClick={() => setFilterTab('generating')}>生成中</button>
        <button type="button" className="project-filter-button" aria-pressed={filterTab === 'ready'} onClick={() => setFilterTab('ready')}>可继续推进</button>
      </nav>

      <div className="project-lobby-body">
        <div className={`project-tile-grid project-tile-grid--${viewMode}`} aria-busy={loading}>
          {loading && Array.from({ length: 4 }, (_, index) => (
            <div className="project-tile--loading" key={index}><Skeleton title={{ width: '65%' }} paragraph={{ rows: 5 }} /></div>
          ))}
          {!loading && !loadError && filteredSorted.length === 0 && (
            <div className="project-empty-state">
              <p>{search ? '没有匹配的项目' : '暂无项目，点击「新建项目」开始'}</p>
              {!search && <Button type="primary" icon={<PlusOutlined />} onClick={handleOpenCreate}>新建项目</Button>}
            </div>
          )}
          {!loading && filteredSorted.map(renderCard)}
        </div>

        <aside className="project-quick-view" aria-label="项目速览">
          <h2>项目速览</h2>
          {selectedProject ? (
            <>
              <h3 className="project-quick-title">{selectedProject.name}</h3>
              <p className="project-quick-description">{selectedProject.description || '暂无描述'}</p>
              <dl className="project-quick-details">
                <div><dt>视频风格</dt><dd>{selectedProject.style}</dd></div>
                <div><dt>种子</dt><dd>{selectedProject.seed}</dd></div>
                <div><dt>更新时间</dt><dd><time dateTime={selectedProject.updatedAt} title={selectedProject.updatedAt}>{formatProjectUpdatedAt(selectedProject.updatedAt)}</time></dd></div>
              </dl>
              <div className="project-progress-label"><span>进度</span><span>{selectedProject.progress}%</span></div>
              <Progress percent={selectedProject.progress} size="small" showInfo={false} strokeColor="var(--color-accent)" />
              <dl className="project-metric-row">
                <div><dt>章节</dt><dd>{selectedProject.stats.chapters}</dd></div>
                <div><dt>角色</dt><dd>{selectedProject.stats.roles}</dd></div>
                <div><dt>场景</dt><dd>{selectedProject.stats.scenes}</dd></div>
                <div><dt>道具</dt><dd>{selectedProject.stats.props}</dd></div>
              </dl>
              <Button type="primary" block icon={<EnterOutlined />} onClick={() => navigate(`/projects/${selectedProject.id}`)}>进入章节工作台</Button>
            </>
          ) : (
            <p className="project-quick-description">将鼠标悬停在项目卡片上查看详情</p>
          )}
        </aside>
      </div>

      <Modal
        title="新建短剧项目"
        open={createModalOpen}
        onCancel={() => setCreateModalOpen(false)}
        footer={null}
        width={520}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreateSubmit}
          initialValues={{
            visual_style: projectStyleOptions.visualStyles[0]?.value ?? '现实',
            style:
              projectStyleOptions.defaultStyleByVisual?.[projectStyleOptions.visualStyles[0]?.value ?? '现实'] ??
              projectStyleOptions.stylesByVisual[projectStyleOptions.visualStyles[0]?.value ?? '现实']?.[0]?.value ??
              '真人都市',
            seed: Math.floor(Math.random() * 99999),
            unifyStyle: true,
            default_video_ratio: defaultVideoRatio,
          }}
        >
          <Form.Item
            name="name"
            label="项目名称"
            rules={[{ required: true, message: '请输入项目名称' }]}
          >
            <Input placeholder="例如：现实都市爱情短剧" />
          </Form.Item>
          <Form.Item name="description" label="项目简介（选填）">
            <Input.TextArea rows={4} placeholder="项目简介与风格说明，建议 80–120 字" />
          </Form.Item>
          <ProjectVisualStyleAndStyleFields form={form} options={projectStyleOptions} />
          <Form.Item
            name="seed"
            label="全局种子值"
            tooltip="固定种子可确保整部短剧视觉调性一致"
          >
            <InputNumber min={0} className="w-full" />
          </Form.Item>
          <Form.Item name="default_video_ratio" label="默认视频比例">
            <Select allowClear placeholder="未设置时由模型/供应商决定" options={videoRatioOptions} />
          </Form.Item>
          <Form.Item
            name="unifyStyle"
            label="所有章节强制继承此风格"
            valuePropName="checked"
            tooltip="开启后所有章节继承项目风格"
          >
            <Switch />
          </Form.Item>
          <Form.Item className="mb-0">
            <Space>
              <Button onClick={() => setCreateModalOpen(false)}>取消</Button>
              <Button type="primary" htmlType="submit">
                创建并进入
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑项目"
        open={editModalOpen}
        onCancel={() => { setEditModalOpen(false); setEditingProject(null) }}
        footer={null}
        width={520}
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={handleEditSubmit}
          initialValues={{ style: '真人都市', visual_style: '现实', unifyStyle: true }}
        >
          <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入项目名称' }]}>
            <Input placeholder="项目名称" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="项目简介与风格说明" />
          </Form.Item>
          <ProjectVisualStyleAndStyleFields form={editForm} options={projectStyleOptions} />
          <Form.Item name="seed" label="全局种子值" tooltip="固定种子可确保整部短剧视觉调性一致">
            <InputNumber min={0} className="w-full" />
          </Form.Item>
          <Form.Item name="default_video_ratio" label="默认视频比例">
            <Select allowClear placeholder="未设置时由模型/供应商决定" options={videoRatioOptions} />
          </Form.Item>
          <Form.Item name="unifyStyle" label="风格统一" valuePropName="checked" tooltip="开启后所有章节继承项目风格">
            <Switch />
          </Form.Item>
          <Form.Item className="mb-0">
            <Space>
              <Button onClick={() => { setEditModalOpen(false); setEditingProject(null) }}>取消</Button>
              <Button type="primary" htmlType="submit">保存</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default ProjectLobby
