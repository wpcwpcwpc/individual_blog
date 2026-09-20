import { create } from 'zustand'

// ── Types ────────────────────────────────────────────────────────
export type FontSizePreset = 'small' | 'medium' | 'large'
export type LineHeightPreset = 'compact' | 'normal' | 'relaxed'

interface DisplaySettings {
  fontSize: FontSizePreset
  lineHeight: LineHeightPreset
  codeFont: string
}

interface DisplayState extends DisplaySettings {
  setFontSize: (v: FontSizePreset) => void
  setLineHeight: (v: LineHeightPreset) => void
  setCodeFont: (v: string) => void
  reset: () => void
}

// ── Preset values ────────────────────────────────────────────────
export const FONT_SIZE_MAP: Record<FontSizePreset, { text: number; code: number; label: string }> = {
  small:  { text: 13, code: 12, label: '小' },
  medium: { text: 14, code: 13, label: '中' },
  large:  { text: 16, code: 15, label: '大' },
}

export const LINE_HEIGHT_MAP: Record<LineHeightPreset, { value: number; label: string }> = {
  compact: { value: 1.5, label: '紧凑' },
  normal:  { value: 1.7, label: '标准' },
  relaxed: { value: 1.9, label: '宽松' },
}

export const CODE_FONT_OPTIONS = [
  { value: "'Cascadia Code', 'JetBrains Mono', 'Fira Code', 'Consolas', monospace", label: '等宽（默认）' },
  { value: "system-ui, -apple-system, sans-serif", label: '系统字体' },
  { value: "'JetBrains Mono', monospace", label: 'JetBrains Mono' },
  { value: "'Fira Code', monospace", label: 'Fira Code' },
]

// ── Persistence ──────────────────────────────────────────────────
const STORAGE_KEY = 'qa_agent_display_settings'

const DEFAULT: DisplaySettings = {
  fontSize: 'medium',
  lineHeight: 'normal',
  codeFont: CODE_FONT_OPTIONS[0].value,
}

function loadFromStorage(): DisplaySettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<DisplaySettings>
      return { ...DEFAULT, ...parsed }
    }
  } catch { /* ignore */ }
  return DEFAULT
}

function saveToStorage(settings: DisplaySettings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  } catch { /* ignore */ }
}

// ── Apply CSS variables to document root ─────────────────────────
export function applyDisplaySettings(settings: DisplaySettings) {
  const root = document.documentElement
  const fs = FONT_SIZE_MAP[settings.fontSize]
  const lh = LINE_HEIGHT_MAP[settings.lineHeight]

  root.style.setProperty('--md-font-size', `${fs.text}px`)
  root.style.setProperty('--md-code-font-size', `${fs.code}px`)
  root.style.setProperty('--md-line-height', String(lh.value))
  root.style.setProperty('--md-code-font-family', settings.codeFont)
}

// ── Zustand store ────────────────────────────────────────────────
export const useDisplayStore = create<DisplayState>((set, get) => {
  const initial = loadFromStorage()
  // Apply on store creation
  setTimeout(() => applyDisplaySettings(initial), 0)

  return {
    ...initial,

    setFontSize: (v) => {
      set({ fontSize: v })
      const s = { ...get(), fontSize: v }
      saveToStorage(s)
      applyDisplaySettings(s)
    },

    setLineHeight: (v) => {
      set({ lineHeight: v })
      const s = { ...get(), lineHeight: v }
      saveToStorage(s)
      applyDisplaySettings(s)
    },

    setCodeFont: (v) => {
      set({ codeFont: v })
      const s = { ...get(), codeFont: v }
      saveToStorage(s)
      applyDisplaySettings(s)
    },

    reset: () => {
      set(DEFAULT)
      saveToStorage(DEFAULT)
      applyDisplaySettings(DEFAULT)
    },
  }
})
