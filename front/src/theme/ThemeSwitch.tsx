import { useTranslation } from 'react-i18next'
import { useThemeStore } from './useThemeStore'

/** Provide always-named theme controls that remain usable when mobile labels hide. */
export function ThemeSwitch() {
  const { i18n } = useTranslation()
  const mode = useThemeStore((state) => state.mode)
  const setMode = useThemeStore((state) => state.setMode)
  const english = i18n.resolvedLanguage?.startsWith('en')
  return (
    <div className="studio-theme-switch" role="group" aria-label={english ? 'Appearance' : '界面主题'} data-testid="theme-switch">
      <button type="button" aria-label={english ? 'Light theme' : '亮色主题'} aria-pressed={mode === 'light'} onClick={() => setMode('light')}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1" /></svg>
        <span>{english ? 'Light' : '亮色'}</span>
      </button>
      <button type="button" aria-label={english ? 'Dark theme' : '暗色主题'} aria-pressed={mode === 'dark'} onClick={() => setMode('dark')}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 15a8.5 8.5 0 0 1-11-11A8.5 8.5 0 1 0 20 15z" /></svg>
        <span>{english ? 'Dark' : '暗色'}</span>
      </button>
    </div>
  )
}
