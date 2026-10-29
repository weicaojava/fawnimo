import type { ReactNode } from 'react'
import { Skeleton } from 'antd'

type ProjectSectionProps = {
  title: ReactNode
  extra?: ReactNode
  children: ReactNode
  className?: string
  loading?: boolean
  headingLevel?: 2 | 3 | 4
  'data-testid'?: string
}

/** Keep collection headings and actions consistent without enclosing their items in another card. */
export function ProjectSection({ title, extra, children, className = '', loading = false, headingLevel = 2, ...props }: ProjectSectionProps) {
  const Heading = headingLevel === 4 ? 'h4' : headingLevel === 3 ? 'h3' : 'h2'
  return (
    <section className={`project-collection ${className}`} aria-busy={loading} {...props}>
      <header className="project-collection-heading">
        <Heading>{title}</Heading>
        {extra ? <div className="project-collection-actions">{extra}</div> : null}
      </header>
      <div className="project-collection-body">{loading ? <Skeleton paragraph={{ rows: 3 }} /> : children}</div>
    </section>
  )
}

const productionStages = ['原文', '分镜', '镜头准备', '拍摄']
const stagePositions: Record<string, number> = {
  create_first_chapter: 0,
  edit_raw: 0,
  extract_shots: 1,
  prepare_shots: 2,
  shoot: 3,
}

/** Show the real chapter preparation stage as a compact production rail, without inventing progress. */
export function ProjectStageTrack({ stage }: { stage?: string }) {
  const current = stage ? stagePositions[stage] ?? -1 : -1
  return (
    <ol className="project-stage-track" aria-label="项目制作阶段">
      {productionStages.map((label, index) => (
        <li
          key={label}
          className={index === current ? 'is-current' : index < current ? 'is-past' : undefined}
          aria-current={index === current ? 'step' : undefined}
        >
          {label}
        </li>
      ))}
    </ol>
  )
}
