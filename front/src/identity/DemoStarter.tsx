import React, { useState } from 'react'
import { Alert, Button, Modal, Space } from 'antd'
import { useNavigate } from 'react-router-dom'
import { ApiError, IdentityService } from '../services/generated'
import { workspaceStorage } from '../services/workspaceContext'
import { useIdentity } from './IdentityGate'

const STORAGE_KEY = 'studio-demo-request-v1'

/** Keep one durable sample intent until the server confirms its original project. */
export const DemoStarter: React.FC = () => {
  const identity = useIdentity()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(() => {
    try { return workspaceStorage.getItem(STORAGE_KEY) ?? '' } catch { return '' }
  })
  if (!identity) return null

  /** Submit or recover only the saved key; no model or media generation is part of this example. */
  const create = async () => {
    setBusy(true); setError('')
    try {
      const key = pending || crypto.randomUUID()
      workspaceStorage.setItem(STORAGE_KEY, key)
      setPending(key)
      const result = await IdentityService.createExampleApiV1IdentityDemoPost({ requestBody: { request_key: key } })
      if (!result.data) throw new Error('示例未返回工程入口，请核对原请求')
      workspaceStorage.removeItem(STORAGE_KEY)
      setPending('')
      navigate(result.data.entry)
    } catch (cause) {
      setError(cause instanceof ApiError ? String(cause.body?.message ?? cause.message) : cause instanceof Error ? cause.message : '示例创建结果未知，请核对原请求')
    } finally { setBusy(false) }
  }

  return <Space direction="vertical" size="small">
    <Button loading={busy} onClick={() => {
      if (pending) { void create(); return }
      Modal.confirm({ title: '建立可编辑体验示例', content: '将新建《点亮小岛》：7个待确认分镜和21条可编辑提示词。保留全部旧工程和编辑，不调用模型。每个空间最多建立20次。', okText: '建立新示例', cancelText: '取消', onOk: create })
    }}>{pending ? '核对上次示例请求' : '建立 / 重置体验示例'}</Button>
    {error && <Alert type="error" message={error} showIcon />}
  </Space>
}
