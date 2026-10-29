import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import enUS from 'antd/locale/en_US'
import App from './App.tsx'
import 'antd/dist/reset.css'
import './index.css'
import './i18n'
import './services/openapi'
import { useAppStore } from './store/useAppStore'
import { useThemeStore, watchThemePreference } from './theme/useThemeStore'
import { createAntdTheme } from './theme/antdTheme'
import { IdentityGate } from './identity/IdentityGate'

/** Provide the same active palette to normal and statically portalled Ant Design UI. */
const RootApp: React.FC = () => {
  const language = useAppStore((state) => state.language)
  const mode = useThemeStore((state) => state.mode)
  const appTheme = React.useMemo(() => createAntdTheme(mode), [mode])
  const antdLocale = language === 'en-US' ? enUS : zhCN
  React.useEffect(() => watchThemePreference(), [])
  React.useLayoutEffect(() => { ConfigProvider.config({ theme: appTheme }) }, [appTheme])

  return (
    <ConfigProvider
      locale={antdLocale}
      theme={appTheme}
    >
      <IdentityGate><App /></IdentityGate>
    </ConfigProvider>
  )
}

/** Keep an actionable error message visible even if a route fails to render. */
class AppErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: Error | null }
> {
  state = { hasError: false, error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError && this.state.error) {
      return (
        <div style={{ padding: 24, fontFamily: 'var(--font-body)' }}>
          <h2>页面加载出错</h2>
          <pre style={{ color: 'var(--color-error)', overflow: 'auto' }}>
            {this.state.error.message}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}

/** Mount promptly without making the optional mock worker block the application. */
function renderApp() {
  const root = document.getElementById('root')
  if (!root) return
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <AppErrorBoundary>
        <RootApp />
      </AppErrorBoundary>
    </React.StrictMode>,
  )
}

// 先立即渲染，避免 MSW 启动阻塞导致白屏
renderApp()

/** Start local mocks only when explicitly enabled for this frontend build. */
async function enableMocking() {
  if (import.meta.env.VITE_USE_MOCK !== 'true') {
    return
  }
  try {
    const { worker } = await import('./mocks/browser')
    await worker.start({ onUnhandledRequest: 'bypass' })
  } catch (error) {
    console.error('MSW start failed:', error)
  }
}

void enableMocking()
