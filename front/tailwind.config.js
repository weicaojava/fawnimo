/** Keep existing utility names compatible while resolving their colours through app themes. */
const token = (name) => `rgb(var(--rgb-${name}) / <alpha-value>)`
const neutral = Object.fromEntries([50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((step) => [step, token(`neutral-${step}`)]))
/** Use one semantic hue per state rather than bringing the default multi-hue palette back. */
const semantic = (name) => Object.fromEntries([50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((step) => [step, token(step <= 200 ? `${name}-soft` : name)]))

module.exports = {
  content: { relative: true, files: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ] },
  theme: {
    colors: {
      inherit: 'inherit', current: 'currentColor', transparent: 'transparent',
      white: token('paper-2'), black: token('black'),
      gray: neutral, slate: neutral, zinc: neutral, neutral, stone: neutral,
      blue: semantic('accent'), sky: semantic('accent'), cyan: semantic('accent'), indigo: semantic('accent'), purple: semantic('accent'), violet: semantic('accent'),
      green: semantic('success'), emerald: semantic('success'), teal: semantic('success'),
      yellow: semantic('warning'), amber: semantic('warning'), orange: semantic('warning'),
      red: semantic('error'), rose: semantic('error'), pink: semantic('error'),
      paper: token('paper'), surface: token('paper-2'), ink: token('ink'), muted: token('muted'), accent: token('accent'),
    },
    fontFamily: { sans: ['var(--font-body)'], mono: ['var(--font-mono)'], serif: ['var(--font-body)'] },
    extend: {
      boxShadow: { sm: 'var(--shadow-subtle)', DEFAULT: 'var(--shadow-subtle)', md: 'var(--shadow-card)', lg: 'var(--shadow-popup)', xl: 'var(--shadow-popup)' },
      borderRadius: { lg: 'var(--radius-card)', xl: 'var(--radius-popup)' },
    },
  },
  plugins: [],
  corePlugins: {
    preflight: false,
  },
}
