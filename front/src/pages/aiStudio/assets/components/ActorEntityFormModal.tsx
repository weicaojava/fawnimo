import { useEffect, useId, useState } from 'react'
import { Input, InputNumber, Modal, message } from 'antd'
import { StudioEntitiesApi } from '../../../../services/studioEntities'
import { ProjectVisualStyleAndStyleFields } from '../../project/ProjectVisualStyleAndStyleFields'
import { useProjectStyleOptions } from '../../project/useProjectStyleOptions'

export type ActorEntityLike = {
  id: string
  name: string
  description?: string | null
  tags?: string[]
  view_count?: number | null
  visual_style?: string
  style?: string
}

function normalizeTags(input: string): string[] {
  return input
    .split(/[,，\n]/g)
    .map((t) => t.trim())
    .filter(Boolean)
}

/** Retain linked actor creation and editing in a compact, labelled form. */
export function ActorEntityFormModal({
  open,
  editing,
  linkProjectId,
  linkChapterId,
  linkShotId,
  onCancel,
  onSuccess,
}: {
  open: boolean
  editing: ActorEntityLike | null
  linkProjectId?: string
  linkChapterId?: string
  linkShotId?: string
  onCancel: () => void
  onSuccess: (detail?: { created?: unknown }) => void | Promise<void>
}) {
  const fieldPrefix = useId()
  const { options: projectStyleOptions, defaultVisualStyle, getDefaultStyle } = useProjectStyleOptions()
  const [formName, setFormName] = useState('')
  const [formDesc, setFormDesc] = useState('')
  const [formTags, setFormTags] = useState('')
  const [formViewCount, setFormViewCount] = useState<number | null>(null)
  const [formVisualStyle, setFormVisualStyle] = useState<'现实' | '动漫'>(defaultVisualStyle as '现实' | '动漫')
  const [formStyle, setFormStyle] = useState<string>(getDefaultStyle(defaultVisualStyle))

  useEffect(() => {
    if (!open) return
    if (editing) {
      setFormName(editing.name)
      setFormDesc(editing.description ?? '')
      setFormTags((editing.tags ?? []).join(', '))
      setFormViewCount(editing.view_count ?? null)
      const nextVisual = ((editing.visual_style as '现实' | '动漫' | undefined) ?? defaultVisualStyle) as '现实' | '动漫'
      setFormVisualStyle(nextVisual)
      setFormStyle(
        (editing.style as string | undefined) ?? getDefaultStyle(nextVisual),
      )
    } else {
      setFormName('')
      setFormDesc('')
      setFormTags('')
      setFormViewCount(null)
      setFormVisualStyle(defaultVisualStyle as '现实' | '动漫')
      setFormStyle(getDefaultStyle(defaultVisualStyle))
    }
  }, [open, editing, defaultVisualStyle, getDefaultStyle])

  const handleOk = async () => {
    const name = formName.trim()
    if (!name) {
      message.warning('请输入名称')
      return Promise.reject(new Error('validation'))
    }
    try {
      if (!editing) {
        const created = await StudioEntitiesApi.create('actor', {
          id: crypto?.randomUUID?.() ?? `actor_${Date.now()}`,
          name,
          description: formDesc.trim() || undefined,
          tags: normalizeTags(formTags),
          view_count: formViewCount ?? undefined,
          visual_style: formVisualStyle,
          style: formStyle,
          prompt_template_id: null,
          ...(linkProjectId ? { project_id: linkProjectId } : {}),
          ...(linkProjectId && linkChapterId ? { chapter_id: linkChapterId } : {}),
          ...(linkProjectId && linkShotId ? { shot_id: linkShotId } : {}),
        })
        message.success('创建成功')
        onCancel()
        await onSuccess({ created: created.data })
      } else {
        await StudioEntitiesApi.update('actor', editing.id, {
          name,
          description: formDesc.trim() || null,
          tags: normalizeTags(formTags),
          view_count: formViewCount ?? null,
          visual_style: formVisualStyle,
          style: formStyle,
        })
        message.success('更新成功')
        onCancel()
        await onSuccess()
      }
    } catch {
      message.error(editing ? '更新失败' : '创建失败')
      return Promise.reject(new Error('api'))
    }
  }

  return (
    <Modal
      className="library-dialog"
      title={editing ? '编辑演员' : '新建演员'}
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      okText={editing ? '保存' : '创建'}
    >
      <div className="library-asset-modal-form">
        <div className="library-field-span">
          <label htmlFor={`${fieldPrefix}-name`} className="block text-sm library-secondary mb-1">名称</label>
          <Input id={`${fieldPrefix}-name`} value={formName} onChange={(e) => setFormName(e.target.value)} />
        </div>
        <div className="library-field-span">
          <label htmlFor={`${fieldPrefix}-description`} className="block text-sm library-secondary mb-1">描述</label>
          <Input.TextArea id={`${fieldPrefix}-description`} rows={3} value={formDesc} onChange={(e) => setFormDesc(e.target.value)} />
        </div>
        <div>
          <label htmlFor={`${fieldPrefix}-tags`} className="block text-sm library-secondary mb-1">标签（逗号分隔）</label>
          <Input id={`${fieldPrefix}-tags`} value={formTags} onChange={(e) => setFormTags(e.target.value)} />
        </div>
        <div>
          <label htmlFor={`${fieldPrefix}-views`} className="block text-sm library-secondary mb-1">视角数（可选）</label>
          <InputNumber id={`${fieldPrefix}-views`} className="w-full" min={1} max={4} value={formViewCount} onChange={(v) => setFormViewCount(v ?? null)} />
        </div>
        <div className="library-field-span">
          <div className="text-sm library-secondary mb-1">视觉风格</div>
          <ProjectVisualStyleAndStyleFields
            visual_style={formVisualStyle}
            style={formStyle}
            options={projectStyleOptions}
            onChange={(next) => {
              setFormVisualStyle(next.visual_style)
              setFormStyle(next.style)
            }}
          />
        </div>
      </div>
    </Modal>
  )
}
