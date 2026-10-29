import { create } from 'zustand'

export type ThemeMode = 'light' | 'dark'
export const THEME_STORAGE_KEY = 'jellyfish_theme'
let sessionChoice = false

/** Accept only the two application palettes, including values from another browser tab. */
export function isThemeMode(value: unknown): value is ThemeMode {
  return value === 'light' || value === 'dark'
}

/** Read the pre-paint preference safely when browser storage is unavailable. */
function initialMode(): ThemeMode {
  if (typeof document !== 'undefined' && isThemeMode(document.documentElement.dataset.mode)) {
    return document.documentElement.dataset.mode
  }
  if (typeof window === 'undefined') return 'light'
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY)
    if (isThemeMode(saved)) return saved
  } catch { /* Theme selection also works without persistent browser storage. */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/** Apply the palette before React renders so token consumers see the same colours. */
export function applyThemeMode(mode: ThemeMode, persist = true): void {
  if (typeof document !== 'undefined') document.documentElement.dataset.mode = mode
  if (persist && typeof window !== 'undefined') {
    try { localStorage.setItem(THEME_STORAGE_KEY, mode) } catch { /* The active palette remains usable. */ }
  }
}

interface ThemeState {
  mode: ThemeMode
  setMode: (mode: ThemeMode) => void
  toggleMode: () => void
}

/** Share one persisted appearance choice between the header, settings and portalled UI. */
export const useThemeStore = create<ThemeState>((set, get) => ({
  mode: initialMode(),
  setMode: (mode) => {
    if (!isThemeMode(mode)) return
    sessionChoice = true
    applyThemeMode(mode)
    set({ mode })
  },
  toggleMode: () => get().setMode(get().mode === 'light' ? 'dark' : 'light'),
}))

applyThemeMode(useThemeStore.getState().mode, false)

/** Follow another tab's explicit choice and the OS only while no choice is stored. */
export function watchThemePreference(): () => void {
  const query = window.matchMedia('(prefers-color-scheme: dark)')
  const sync = (event?: StorageEvent) => {
    if (event && event.key !== THEME_STORAGE_KEY && event.key !== null) return
    if (event) sessionChoice = false
    let saved: string | null = null
    try { saved = localStorage.getItem(THEME_STORAGE_KEY) } catch { /* Use the session or OS choice. */ }
    if (!event && sessionChoice && !isThemeMode(saved)) return
    const next = isThemeMode(saved) ? saved : query.matches ? 'dark' : 'light'
    applyThemeMode(next, false)
    useThemeStore.setState({ mode: next })
  }
  const onSystemChange = () => sync()
  window.addEventListener('storage', sync)
  query.addEventListener('change', onSystemChange)
  return () => {
    window.removeEventListener('storage', sync)
    query.removeEventListener('change', onSystemChange)
  }
}
