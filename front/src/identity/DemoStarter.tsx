import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Modal, Space } from 'antd'
import { useNavigate } from 'react-router-dom'
import { ApiError, IdentityService } from '../services/generated'
import { workspaceRequestScope, workspaceStorage } from '../services/workspaceContext'
import { useIdentity } from './IdentityGate'

const MODES = {
  exportable: { storage: 'studio-demo-exportable-v1', label: '创建可导出示例', pending: '核对上次可导出示例',
    title: '创建可编辑、可导出的原创示例',
    description: '将新建《点亮小岛》：7段原创无声视频、7张图片和28秒剪辑草稿。请在视频编辑中确认剧情保留标记，再保存版本并导出。不调用模型，保留已有工程和编辑。文字与视频示例共用每个空间20次的创建上限。' },
  text: { storage: 'studio-demo-request-v1', label: '创建文字分镜示例', pending: '核对上次文字示例',
    title: '创建文字分镜示例',
    description: '将新建《点亮小岛》：7个待确认分镜和21条可编辑提示词。不含视频素材，不调用模型；保留已有工程和编辑。文字与视频示例共用每个空间20次的创建上限。' },
}
type Mode = keyof typeof MODES

/** Recover each mode's original intent from the currently bound user and space. */
function savedRequests(): Record<Mode, string> {
  try { return { text: workspaceStorage.getItem(MODES.text.storage) ?? '', exportable: workspaceStorage.getItem(MODES.exportable.storage) ?? '' } }
  catch { return { text: '', exportable: '' } }
}

/** Keep separate durable intents and fence all completions to the original visible identity. */
export const DemoStarter: React.FC = () => {
  const identity = useIdentity()
  const navigate = useNavigate()
  const [busy, setBusy] = useState<Mode | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(savedRequests)
  const mounted = useRef(true)
  const operation = useRef(0)
  const busyNow = useRef(false)
  const scope = workspaceRequestScope()
  useEffect(() => {
    mounted.current = true
    operation.current++; busyNow.current = false
    setBusy(null); setError(''); setPending(savedRequests())
    return () => { mounted.current = false; operation.current++ }
  }, [scope])
  if (!identity) return null

  /** Reuse the saved intent and clear it only after an exact scoped server confirmation. */
  const create = async (mode: Mode, expectedScope: string) => {
    if (!mounted.current || expectedScope !== workspaceRequestScope() || busyNow.current) return
    const current = ++operation.current
    const fresh = () => mounted.current && current === operation.current && expectedScope === workspaceRequestScope()
    busyNow.current = true; setBusy(mode); setError('')
    try {
      const storage = MODES[mode].storage
      const key = workspaceStorage.getItem(storage) || pending[mode] || crypto.randomUUID()
      workspaceStorage.setItem(storage, key)
      setPending(previous => ({ ...previous, [mode]: key }))
      const request = { requestBody: { request_key: key } }
      const result = mode === 'exportable'
        ? await IdentityService.createExportableExampleApiV1IdentityDemoExportablePost(request)
        : await IdentityService.createExampleApiV1IdentityDemoPost(request)
      if (!fresh()) return
      if (!result.data) throw new Error('示例未返回工程入口，请核对原请求')
      // A second tab can replace the HttpOnly session while this request is in
      // flight. Re-attest it before clearing this original identity's intent.
      const active = await IdentityService.meApiV1IdentityMeGet()
      if (!fresh()) return
      if (!active.data || active.data.user.id !== identity.user.id || active.data.workspace_id !== identity.workspace_id) {
        throw new Error('登录身份或空间已变化，请刷新后核对原示例请求')
      }
      if (workspaceStorage.getItem(storage) === key) workspaceStorage.removeItem(storage)
      setPending(savedRequests())
      navigate(result.data.entry)
    } catch (cause) {
      if (fresh()) setError(cause instanceof ApiError ? String(cause.body?.message ?? cause.message) : cause instanceof Error ? cause.message : '示例创建结果未知，请核对原请求')
    } finally { if (fresh()) { busyNow.current = false; setBusy(null) } }
  }

  return <Space direction="vertical" size="small">
    <Space wrap>{(Object.keys(MODES) as Mode[]).map(mode => <Button key={mode} type={mode === 'exportable' ? 'primary' : 'default'}
      disabled={busy !== null || identity.trial?.read_only === true} loading={busy === mode} onClick={() => {
        const expectedScope = workspaceRequestScope()
        if (pending[mode]) { void create(mode, expectedScope); return }
        Modal.confirm({ title: MODES[mode].title, content: MODES[mode].description, okText: '创建新示例', cancelText: '取消', onOk: () => create(mode, expectedScope) })
      }}>{pending[mode] ? MODES[mode].pending : MODES[mode].label}</Button>)}</Space>
    {busy === 'exportable' && <Alert type="info" showIcon message="正在本地制作并保存示例，完成后进入视频编辑。请稍候。" />}
    {error && <Alert type="error" message={error} showIcon />}
  </Space>
}
