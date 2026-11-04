import { EditOutlined, FileSearchOutlined, ScissorOutlined, VideoCameraOutlined } from '@ant-design/icons'
import type { Chapter } from './hooks/useProjectData'
import type { ProjectFlowStats } from './projectFlowStats'
import { chapterFlowStep } from './productionFlow'

/** Present the shared fact-based next action with its matching icon. */
export function getChapterPreparationState(chapter: Chapter, flow?: ProjectFlowStats) {
  const step = chapterFlowStep(chapter, flow)
  const icons = { edit_raw: <EditOutlined />, extract_shots: <ScissorOutlined />, prepare_shots: <FileSearchOutlined />, shoot: <VideoCameraOutlined /> }
  return { ...step, primaryIcon: icons[step.key] }
}

export type ChapterPreparationState = ReturnType<typeof getChapterPreparationState>
