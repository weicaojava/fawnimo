import React, { createContext, useContext, useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Space, Spin, Typography } from 'antd'
import { ApiError, IdentityService, OpenAPI, type IdentityRead, type TrialRegistrationRead } from '../services/generated'
import { bindWorkspaceContext } from '../services/workspaceContext'
import { useAppStore } from '../store/useAppStore'
import { useThemeStore } from '../theme/useThemeStore'
import { ThemeSwitch } from '../theme/ThemeSwitch'
import i18n from '../i18n'

const IdentityContext = createContext<IdentityRead | null>(null)

/** Expose only the authenticated profile; all authorization remains on the server. */
export function useIdentity() { return useContext(IdentityContext) }

/** Describe the exact configured lifetime instead of rounding a short trial up to a full day. */
function trialDuration(seconds: number) {
  if (seconds % 86400 === 0) return `${seconds / 86400} 天`
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`
  if (seconds % 60 === 0) return `${seconds / 60} 分钟`
  return `${seconds} 秒`
}

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
  const [trialRegistration, setTrialRegistration] = useState<TrialRegistrationRead | null>(null)
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
        setTrialRegistration(status.data?.trial_registration ?? null)
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
    if (!identity?.trial) return
    let current = true
    const user = identity.user.id
    const workspace = identity.workspace_id
    /** Refresh only the same trusted scope's trial status, preserving open project state and namespace-bound actions. */
    const refresh = async () => {
      try {
        const result = await IdentityService.meApiV1IdentityMeGet()
        if (!current || !result.data) return
        if (result.data.user.id !== user || result.data.workspace_id !== workspace) { window.location.reload(); return }
        const trial = result.data.trial
        setIdentity(previous => previous?.user.id === user && previous.workspace_id === workspace ? { ...previous, trial } : previous)
      } catch (cause) {
        if (current && cause instanceof ApiError && [401, 409].includes(cause.status)) window.location.reload()
      }
    }
    const interval = window.setInterval(() => { void refresh() }, 60000)
    /** Recheck after returning to the page; expiration is always enforced by the server even before this notice refreshes. */
    const focus = () => { void refresh() }
    window.addEventListener('focus', focus)
    return () => { current = false; window.clearInterval(interval); window.removeEventListener('focus', focus) }
  }, [identity?.user.id, identity?.workspace_id, Boolean(identity?.trial)])

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
  if (identity) return <IdentityContext.Provider value={identity}><div className="identity-session">{error && <Alert type="error" message={error} />}
    {identity.trial && <Alert banner showIcon data-testid="trial-access-notice" type={identity.trial.read_only ? 'warning' : 'info'}
      message={identity.trial.read_only ? '体验已到期：资料仍可查看和下载，项目编辑已暂停。如需继续使用，请联系维护者。'
        : `体验账号 · 有效期至 ${new Date(identity.trial.expires_at * 1000).toLocaleString()}${identity.trial.project_limit ? ` · 同时最多保留 ${identity.trial.project_limit} 个项目` : ''}`} />}
    {children}</div></IdentityContext.Provider>
  return <main className="identity-entry"><Card className="identity-card">
    <Space className="identity-title"><img src="/fawnimo.svg" width={36} height={36} alt="" /><Typography.Title level={2}>Fawnimo 鹿帧</Typography.Title><ThemeSwitch /></Space>
    <Typography.Paragraph type="secondary">进入创作空间，保存项目、资产与制作记录。登录后默认进入自己的个人空间。</Typography.Paragraph>
    {error && <Alert type="error" showIcon message={error} className="mb-4" />}
    {createAccount && trialRegistration && <Alert type="info" showIcon className="mb-4" data-testid="trial-registration-notice"
      message={trialRegistration.remaining === 0 ? '本轮体验名额已满，已有账号仍可登录。'
        : `体验有效期 ${trialDuration(trialRegistration.duration_seconds)}，可同时保留 ${trialRegistration.project_limit} 个项目。到期后可查看和下载资料。`} />}
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
      <Button type="primary" htmlType="submit" loading={busy} disabled={createAccount && trialRegistration?.remaining === 0} block>{createAccount ? '注册并进入个人空间' : '登录'}</Button>
    </Form>
    {registration ? <><Button type="link" disabled={busy || (!createAccount && trialRegistration?.remaining === 0)} onClick={() => { setCreateAccount(!createAccount); setError('') }}>{createAccount ? '已有账号，返回登录' : '创建独立体验账号'}</Button>
      {!createAccount && trialRegistration?.remaining === 0 && <Typography.Paragraph type="secondary">本轮体验名额已满，已有账号仍可登录。</Typography.Paragraph>}</>
      : <Typography.Paragraph type="secondary" className="mt-4">体验账号由维护者单独发放。</Typography.Paragraph>}
  </Card></main>
}
