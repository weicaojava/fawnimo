import { Button } from 'antd'

/** Keep the existing destination visible when a workbench feature lives on another page. */
export function PlaceholderTab({
  title,
  description,
  actionLabel,
  onAction,
}: {
  title: string
  description: string
  actionLabel: string
  onAction: () => void
}) {
  return (
    <section className="project-placeholder">
      <h2>{title}</h2>
      <p>{description}</p>
      <Button type="primary" onClick={onAction}>{actionLabel}</Button>
    </section>
  )
}
