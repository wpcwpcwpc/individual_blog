/**
 * Markdown export utilities — pure functions shared by message bubble components.
 *
 * Design notes:
 *   - Pure-function module, no React coupling
 *   - Windows-strictest filename sanitization
 *   - Blob + a-tag download
 *   - D6: navigator.clipboard with execCommand fallback
 */

/** Maximum length of the sanitized filename prefix (excluding `-message-N.md` suffix). */
const MAX_FILENAME_LENGTH = 80

/**
 * Format a millisecond timestamp as `YYYYMMDD-HHmm` in local time.
 *
 * Used by message bubbles to derive a session label
 * (`{agent_name}-{formatSessionTimestamp(created_at)}`) for download filenames.
 * See design.md §D8.
 *
 * Returns `''` for falsy input so the caller's fallback path is exercised.
 */
export function formatSessionTimestamp(ts: number): string {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}` +
    `-${pad(d.getHours())}${pad(d.getMinutes())}`
  )
}

/**
 * Sanitize a string so it is safe to use as a filename across platforms.
 *
 * Rules:
 * - Replace Windows-illegal chars (`\ / : * ? " < > |`) with `-`
 * - Strip leading/trailing whitespace and `.` (Windows disallows trailing `.`)
 * - Truncate to {@link MAX_FILENAME_LENGTH} chars
 */
export function sanitizeFileName(name: string): string {
  if (!name) return ''
  // 1. Replace illegal chars
  const replaced = name.replace(/[\\/:*?"<>|]/g, '-')
  // 2. Strip leading/trailing whitespace and dots
  const trimmed = replaced.replace(/^[\s.]+|[\s.]+$/g, '')
  // 3. Truncate
  return trimmed.slice(0, MAX_FILENAME_LENGTH)
}

/**
 * Build the download filename for a single message export.
 *
 * Format: `{sanitizedSessionName}-message-{index}.md`
 *
 * Fallback: when `sessionName` is missing or sanitizes to empty,
 * use `session-{sessionId.slice(0, 8)}` as the prefix.
 */
export function buildMessageFileName(
  sessionName: string | undefined,
  sessionId: string,
  index: number
): string {
  const sanitized = sanitizeFileName(sessionName ?? '')
  const prefix = sanitized || `session-${(sessionId ?? '').slice(0, 8)}`
  return `${prefix}-message-${index}.md`
}

/**
 * Copy text to the system clipboard.
 *
 * Prefers the modern `navigator.clipboard.writeText` API. Falls back to a
 * hidden `<textarea>` + `document.execCommand('copy')` when the Clipboard
 * API is unavailable (e.g. non-secure contexts on legacy browsers).
 *
 * Throws if both paths fail — callers should catch and surface the error.
 */
export async function copyMarkdown(text: string): Promise<void> {
  // Path 1: modern Clipboard API (requires HTTPS or localhost)
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // Permission denied / non-secure context → fall through to execCommand
    }
  }

  // Path 2: execCommand fallback — works in http webviews
  if (typeof document === 'undefined') {
    throw new Error('Clipboard not available in this environment')
  }

  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.top = '0'
  ta.style.left = '0'
  ta.style.opacity = '0'
  ta.setAttribute('readonly', '')
  document.body.appendChild(ta)
  try {
    // Focus + select is required for execCommand('copy') to work in many webviews
    ta.focus()
    ta.select()
    ta.setSelectionRange(0, text.length)
    const ok = document.execCommand('copy')
    if (!ok) throw new Error('execCommand("copy") returned false')
  } finally {
    document.body.removeChild(ta)
  }
}

/**
 * Trigger a save of `text` as a `.md` file through a browser download.
 *
 * Creates a Blob + temporary `<a>` + click; the browser writes the file to its
 * download folder silently and does not expose the resulting path.
 *
 * Returns a result object so callers can render consistent feedback UI.
 */
export interface DownloadResult {
  /** Operation succeeded (browser download triggered). */
  ok: boolean
  /** Error message when ok === false. */
  error?: string
}

export async function downloadMarkdownFile(
  text: string,
  fileName: string
): Promise<DownloadResult> {
  if (typeof document === 'undefined' || typeof URL === 'undefined') {
    return { ok: false, error: 'Download not available in this environment' }
  }
  try {
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fileName
    // Some browsers require the anchor to be in the DOM for the click to work.
    document.body.appendChild(a)
    try {
      a.click()
    } finally {
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    }
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}
