/** Appearance: Dark (default — it is a control room) or Light. Your choice sticks.
 *  Colours live in CSS variables (index.css); this file switches them and resolves them for
 *  <canvas>, which cannot read CSS variables itself. */

const KEY = 'synapse_theme'
const EVENT = 'synapse-theme'

export const css = (name, a) => (a == null ? `rgb(var(--${name}))` : `rgb(var(--${name}) / ${a})`)

export function getTheme() {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'light' || v === 'dark') return v
  } catch { /* private mode */ }
  return 'dark'
}

export const isDark = () => document.documentElement.dataset.theme === 'dark'

export function applyTheme(mode) {
  document.documentElement.dataset.theme = mode === 'dark' ? 'dark' : 'light'
  try { localStorage.setItem(KEY, mode) } catch { /* private mode */ }
  cache = null
  document.querySelector('meta[name=theme-color]')?.setAttribute('content', mode === 'dark' ? '#0d0d0c' : '#f1efe9')
  window.dispatchEvent(new Event(EVENT))
}

export function onThemeChange(fn) {
  window.addEventListener(EVENT, fn)
  return () => window.removeEventListener(EVENT, fn)
}

let cache = null
/** Resolved colour for canvas drawing: ink('ink', .5) -> "rgba(237,234,226,0.5)". */
export function ink(name, a = 1) {
  if (!cache) cache = { dark: isDark() }
  if (!(name in cache)) {
    const raw = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim()
    cache[name] = raw.split(/\s+/).join(',') || '128,128,128'
  }
  return `rgba(${cache[name]},${a})`
}

export const darkNow = () => { if (!cache) cache = { dark: isDark() }; return cache.dark }
