import React, { createContext, useContext, useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Space, Spin, Typography } from 'antd'
import { ApiError, IdentityService, OpenAPI, type IdentityRead } from '../services/generated'
import { bindWorkspaceContext } from '../services/workspaceContext'
import { useAppStore } from '../store/useAppStore'
import { useThemeStore } from '../theme/useThemeStore'
import { ThemeSwitch } from '../theme/ThemeSwitch'
import i18n from '../i18n'

const IdentityContext = createContext<IdentityRead | null>(null)

/** Expose only the authenticated profile; all authorization remains on the server. */
export function useIdentity() { return useContext(IdentityContext) }

/** Keep the old local workspace usable while its pre-identity process is still running. */
function legacyLoopback(error: unknown) {
  try {
    return error instanceof ApiError && error.status === 404 &&
      ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname) &&
      ['localhost', '127.0.0.1', '[::1]'].includes(new URL(OpenAPI.BASE || location.origin).hostname)
  } catch { return false }
}

/** Mount private routes only after the server has selected and validated the personal workspace. */
export const IdentityGate: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [identity, setIdentity] = useState<IdentityRead | null>(null)
  const [local, setLocal] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [registration, setRegistration] = useState(false)
  const [createAccount, setCreateAccount] = useState(false)
  const [error, setError] = useState('')

  /** Apply trusted context and user-owned preferences before rendering project components. */
  const accept = (next: IdentityRead) => {
    bindWorkspaceContext(next.user.id, next.workspace_id)
    useAppStore.getState().setUser({ name: next.user.display_name, role: next.workspaces.find(item => item.id === next.workspace_id)?.name ?? '个人空间' })
    useAppStore.getState().setLanguage(next.user.language)
    void i18n.changeLanguage(next.user.language)
    useThemeStore.getState().setMode(next.user.theme)
    setIdentity(next)
  }

  useEffect(() => {
    let current = true
    OpenAPI.WITH_CREDENTIALS = true
    OpenAPI.CREDENTIALS = 'include'
    /** Read identity without creating projects, initializing slots or submitting model tasks. */
    const load = async () => {
      try {
        const status = await IdentityService.identityStatusApiV1IdentityStatusGet()
        if (!current) return
        if (status.data?.mode === 'local') { setLocal(true); return }
        setRegistration(status.data?.registration_enabled ?? false)
        try {
          const result = await IdentityService.meApiV1IdentityMeGet()
          if (current && result.data) accept(result.data)
        } catch (cause) {
          if (!(cause instanceof ApiError && cause.status === 401)) throw cause
        }
      } catch (cause) {
        if (!current) return
        if (legacyLoopback(cause)) setLocal(true)
        else setError(cause instanceof Error ? cause.message : '无法连接身份服务')
      } finally { if (current) setLoading(false) }
    }
    void load()
    return () => { current = false }
  }, [])

  useEffect(() => {
    if (!identity) return
    /** Persist appearance and language to the user, without remounting or submitting creation jobs. */
    const save = () => {
      void IdentityService.profileApiV1IdentityMePatch({ requestBody: {
        language: useAppStore.getState().language, theme: useThemeStore.getState().mode,
      } }).catch(() => { setError('个人偏好保存失败，请重新登录后重试') })
    }
    const stopTheme = useThemeStore.subscribe((state, previous) => { if (state.mode !== previous.mode) save() })
    const stopLanguage = useAppStore.subscribe((state, previous) => { if (state.language !== previous.language) save() })
    return () => { stopTheme(); stopLanguage() }
  }, [identity?.user.id])

  if (loading) return <div className="identity-entry"><Spin tip="正在读取个人空间…" /></div>
  if (local) return <>{children}</>
  if (identity) return <IdentityContext.Provider value={identity}>{error && <Alert type="error" message={error} />}{children}</IdentityContext.Provider>
  return <main className="identity-entry"><Card className="identity-card">
    <Space className="identity-title"><img src="/fawnimo.svg" width={36} height={36} alt="" /><Typography.Title level={2}>Fawnimo 鹿帧</Typography.Title><ThemeSwitch /></Space>
    <Typography.Paragraph type="secondary">进入创作空间，保存项目、资产与制作记录。登录后默认进入自己的个人空间。</Typography.Paragraph>
    {error && <Alert type="error" showIcon message={error} className="mb-4" />}
    <Form layout="vertical" onFinish={async (values: { account: string; password: string }) => {
      setBusy(true); setError('')
      try {
        const result = createAccount
          ? await IdentityService.registerApiV1IdentityRegisterPost({ requestBody: values })
          : await IdentityService.loginApiV1IdentityLoginPost({ requestBody: values })
        if (result.data) accept(result.data)
      } catch (cause) { setError(cause instanceof ApiError ? String(cause.body?.message ?? cause.message) : '登录失败，请重试') }
      finally { setBusy(false) }
    }}>
      <Form.Item name="account" label="账号" rules={[{ required: true, min: 3, max: 64 }]}><Input autoComplete="username" /></Form.Item>
      <Form.Item name="password" label="密码" rules={[{ required: true, min: 12, max: 256 }]}><Input.Password autoComplete={createAccount ? 'new-password' : 'current-password'} /></Form.Item>
      <Button type="primary" htmlType="submit" loading={busy} block>{createAccount ? '注册并进入个人空间' : '登录'}</Button>
    </Form>
    {registration ? <Button type="link" disabled={busy} onClick={() => { setCreateAccount(!createAccount); setError('') }}>{createAccount ? '已有账号，返回登录' : '创建独立账号'}</Button>
      : <Typography.Paragraph type="secondary" className="mt-4">体验账号由维护者单独发放。</Typography.Paragraph>}
  </Card></main>
}
