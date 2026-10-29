import { theme, type ThemeConfig } from 'antd'
import type { ThemeMode } from './useThemeStore'

/** Resolve application-owned CSS tokens into colours Ant Design 5.10 can derive. */
export function themeColor(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(`--color-${name}`).trim()
}

/** Keep Ant Design surfaces and semantic states aligned with the selected CSS palette. */
export function createAntdTheme(mode: ThemeMode): ThemeConfig {
  const color = themeColor
  return {
    algorithm: mode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: color('accent'), colorInfo: color('accent'), colorSuccess: color('success'),
      colorWarning: color('warning'), colorError: color('error'),
      colorBgBase: color('paper-2'), colorBgLayout: color('paper'),
      colorBgContainer: color('paper-2'), colorBgElevated: color('paper-2'),
      colorBgSpotlight: color('paper-3'), colorFillAlter: color('paper-3'),
      colorFillSecondary: color('paper-3'), colorFillTertiary: color('paper-3'),
      colorText: color('ink'), colorTextHeading: color('ink'), colorTextSecondary: color('ink-2'),
      colorTextTertiary: color('muted'), colorTextQuaternary: color('muted'),
      colorTextPlaceholder: color('muted'), colorTextLightSolid: color('accent-ink'),
      colorBorder: color('rule-2'), colorBorderSecondary: color('rule'),
      colorLink: color('accent'), colorLinkHover: color('accent-hover'),
      controlItemBgActive: color('accent-soft'), controlItemBgHover: color('paper-3'),
      colorPrimaryBg: color('accent-soft'), colorPrimaryText: color('accent'),
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue('--font-body').trim(),
      fontSize: 14, lineHeight: 1.6, borderRadius: 6, borderRadiusLG: 10,
      controlHeight: 38, controlHeightSM: 32, controlHeightLG: 44,
      motionDurationFast: '0.12s', motionDurationMid: '0.18s', motionDurationSlow: '0.24s',
    },
    components: {
      Layout: { headerBg: color('paper-2'), siderBg: color('paper-2'), bodyBg: color('paper'), headerHeight: 64 },
      Menu: { itemBg: color('paper-2'), itemColor: color('ink-2'), itemSelectedBg: color('accent-soft'), itemSelectedColor: color('accent'), itemHoverColor: color('ink'), itemHeight: 44, itemBorderRadius: 6 },
      Card: { headerBg: color('paper-2'), paddingLG: 20 },
      Table: { headerBg: color('paper-3'), rowHoverBg: color('paper-3'), borderColor: color('rule') },
      Tabs: { inkBarColor: color('accent'), itemSelectedColor: color('accent'), itemHoverColor: color('accent') },
      Tooltip: { colorBgSpotlight: color('paper-3'), colorTextLightSolid: color('ink') },
      Button: { primaryShadow: 'none', defaultShadow: 'none', dangerShadow: 'none', fontWeight: 500 },
    },
  }
}
