import { useEffect, useState, useCallback, useRef } from 'react'
import { projects as mockProjects, chapters as mockChapters, type Project, type Chapter as MockChapter } from '../../../../../mocks/data'
import { StudioProjectsService } from '../../../../../services/generated'
import type { ChapterRead, ProjectRead } from '../../../../../services/generated'
import { StudioEntitiesApi } from '../../../../../services/studioEntities'
import { loadCompleteProjectChapters } from '../projectFlowStats'

const useMock = import.meta.env.VITE_USE_MOCK === 'true'

function newId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`
}

/** Preserve the saved project timestamp; aggregate stats are not an authoritative clock. */
function toUIProject(p: ProjectRead): Project {
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
  }
}

export type Chapter = MockChapter & {
  rawText?: string
}

const EMPTY_CHAPTERS: Chapter[] = []

/** Adapt saved chapter content without fabricating an update time absent from its current API. */
function toUIChapter(c: ChapterRead): Chapter {
  return {
    id: c.id,
    projectId: c.project_id,
    index: c.index,
    title: c.title,
    summary: c.summary ?? '',
    rawText: c.raw_text ?? '',
    storyboardCount: c.shot_count ?? c.storyboard_count ?? 0,
    status: c.status ?? 'draft',
    updatedAt: '',
  }
}

/** Keep project metadata tied to the route whose request produced it, with explicit read failures and late-response rejection. */
export function useProject(projectId: string | undefined) {
  const sequence = useRef(0)
  const mounted = useRef(true)
  const activeProject = useRef(projectId)
  activeProject.current = projectId
  const [state, setState] = useState<{ projectId?: string; project: Project | null; loading: boolean; error: string }>({ project: null, loading: true, error: '' })
  /** Read the selected project; changing routes cannot expose a previous project's late metadata. */
  const load = useCallback(async () => {
    if (!mounted.current || activeProject.current !== projectId) return
    const request = ++sequence.current
    setState({ projectId, project: null, loading: !!projectId, error: '' })
    if (!projectId) return
    try {
      const result = useMock ? mockProjects.find(project => project.id === projectId) ?? null
        : (await StudioProjectsService.getProjectApiV1StudioProjectsProjectIdGet({ projectId })).data ?? null
      const project = result ? (useMock ? result as Project : toUIProject(result as ProjectRead)) : null
      if (request === sequence.current && activeProject.current === projectId) setState({ projectId, project, loading: false, error: '' })
    } catch {
      if (request === sequence.current && activeProject.current === projectId) setState({ projectId, project: null, loading: false, error: '项目资料读取失败，请重试。' })
    }
  }, [projectId])
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; sequence.current++ } }, [load])
  const current = state.projectId === projectId ? state : { project: null, loading: true, error: '' }
  return { ...current, refresh: load }
}

export type ProjectState = ReturnType<typeof useProject>

/** Read a complete, scope-bound chapter list; a failed or late request never becomes an empty-project claim. */
export function useChapters(projectId: string | undefined) {
  const sequence = useRef(0)
  const mounted = useRef(true)
  const activeProject = useRef(projectId)
  activeProject.current = projectId
  const [state, setState] = useState<{ projectId?: string; chapters: Chapter[]; loading: boolean; error: string }>({ chapters: EMPTY_CHAPTERS, loading: true, error: '' })

  /** Apply a local update only to the project whose server list is currently displayed. */
  const patchChapterLocal = useCallback((chapterId: string, patch: Partial<Chapter>) => {
    if (!mounted.current || activeProject.current !== projectId) return
    setState(previous => previous.projectId === projectId ? { ...previous, chapters: previous.chapters.map(chapter => chapter.id === chapterId ? { ...chapter, ...patch } : chapter) } : previous)
  }, [projectId])

  /** Replace the visible list only after all pages succeed for the current request. */
  const load = useCallback(async () => {
    if (!mounted.current || activeProject.current !== projectId) return
    const request = ++sequence.current
    setState({ projectId, chapters: EMPTY_CHAPTERS, loading: !!projectId, error: '' })
    if (!projectId) return
    try {
      const chapters = useMock ? mockChapters.filter(chapter => chapter.projectId === projectId)
        : (await loadCompleteProjectChapters(projectId, () => request === sequence.current && activeProject.current === projectId)).map(toUIChapter)
      if (request === sequence.current && activeProject.current === projectId) setState({ projectId, chapters, loading: false, error: '' })
    } catch {
      if (request === sequence.current && activeProject.current === projectId) setState({ projectId, chapters: EMPTY_CHAPTERS, loading: false, error: '章节读取失败，请重试。尚不能确认当前项目是否为空。' })
    }
  }, [projectId])

  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; sequence.current++ } }, [load])
  const current = state.projectId === projectId ? state : { chapters: EMPTY_CHAPTERS, loading: true, error: '' }
  return { ...current, refresh: load, patchChapterLocal }
}

export type ChaptersState = ReturnType<typeof useChapters>

export function useProjectCharacters(projectId: string | undefined) {
  const [characters, setCharacters] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    if (!projectId) {
      setCharacters([])
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const res = await StudioEntitiesApi.list('character', {
        page: 1,
        pageSize: 100,
        q: null,
      })
      const items = (res.data?.items ?? []).filter((x) => x.project_id === projectId)
      setCharacters(items)
    } catch {
      setCharacters([])
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  return { characters, loading, refresh: load }
}

export { newId }
export type { Project }
