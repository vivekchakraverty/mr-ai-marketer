import { useEffect, useState } from 'react'
import { useErrors } from '../state/errors'

/**
 * The error popup.
 *
 * Exists mostly for the Copy button. When something fails in a desktop app there is nowhere
 * for the user to go — no devtools they'd think to open, no server log to paste — so the one
 * genuinely useful thing is a block of text they can hand to someone. What gets copied is
 * therefore more than the sentence on screen: the source, the timestamp and the detail go
 * with it, because "it says generation failed" is not a bug report.
 */
function formatForClipboard(e: { message: string; source: string; detail: string; at: Date }): string {
  const lines = [
    'Mr. AI Marketer — error report',
    `When:   ${e.at.toISOString()}`,
    `Where:  ${e.source}`,
    `Error:  ${e.message}`
  ]
  if (e.detail) lines.push('', 'Detail:', e.detail)
  return lines.join('\n')
}

export default function ErrorPopup(): React.JSX.Element | null {
  const current = useErrors((s) => s.current)
  const suppressed = useErrors((s) => s.suppressed)
  const dismiss = useErrors((s) => s.dismiss)

  const [copied, setCopied] = useState(false)
  const [showDetail, setShowDetail] = useState(false)

  // Reset per error, so a second failure doesn't open pre-marked "Copied" or pre-expanded.
  useEffect(() => {
    setCopied(false)
    setShowDetail(false)
  }, [current?.id])

  useEffect(() => {
    if (!current) return
    function onKey(e: KeyboardEvent): void {
      if (e.key === 'Escape') dismiss()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [current, dismiss])

  if (!current) return null

  return (
    <div
      onClick={dismiss}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(43, 36, 32, 0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        // Above the gate modals, which are zIndex 50 — an error raised while one of those is
        // open has to be readable, or the app looks frozen.
        zIndex: 90
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 520,
          maxWidth: 'calc(100vw - 48px)',
          background: 'var(--surface)',
          border: '2.5px solid var(--border)',
          borderRadius: 22,
          boxShadow: 'var(--shadow-sm)',
          padding: '22px 24px 20px'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span
            style={{
              width: 26,
              height: 26,
              borderRadius: '50%',
              flexShrink: 0,
              background: 'var(--danger-ink)',
              color: 'var(--surface)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              font: "700 15px 'Quicksand'"
            }}
          >
            !
          </span>
          <div style={{ font: "700 19px 'Kalam'", color: 'var(--ink)' }}>That didn&apos;t work</div>
        </div>

        <div
          style={{
            font: "600 13.5px/1.65 'Quicksand'",
            color: 'var(--ink-body)',
            background: 'var(--surface-tint)',
            border: '2px solid var(--border)',
            borderRadius: 14,
            padding: '11px 13px',
            maxHeight: 190,
            overflowY: 'auto',
            overflowWrap: 'anywhere',
            whiteSpace: 'pre-wrap'
          }}
        >
          {current.message}
        </div>

        <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 7 }}>
          {current.source} · {current.at.toLocaleTimeString()}
          {suppressed > 0 && ` · ${suppressed} other error${suppressed === 1 ? '' : 's'} while this was open`}
        </div>

        {current.detail && (
          <>
            <div
              onClick={() => setShowDetail((v) => !v)}
              style={{
                font: "700 12px 'Quicksand'",
                color: 'var(--accent-deep)',
                cursor: 'pointer',
                marginTop: 10
              }}
            >
              {showDetail ? 'Hide technical detail' : 'Show technical detail'}
            </div>
            {showDetail && (
              <pre
                style={{
                  font: "500 11.5px/1.5 ui-monospace, monospace",
                  color: 'var(--ink-muted)',
                  background: 'var(--surface-tint)',
                  border: '2px solid var(--border)',
                  borderRadius: 12,
                  padding: '10px 12px',
                  marginTop: 8,
                  maxHeight: 200,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  overflowWrap: 'anywhere'
                }}
              >
                {current.detail}
              </pre>
            )}
          </>
        )}

        <div style={{ display: 'flex', gap: 10, marginTop: 16, justifyContent: 'flex-end' }}>
          <div
            onClick={() => {
              void navigator.clipboard
                .writeText(formatForClipboard(current))
                .then(() => setCopied(true))
                .catch(() => setCopied(false))
            }}
            style={{
              padding: '9px 18px',
              borderRadius: 999,
              border: '2.5px solid var(--border)',
              background: copied ? 'var(--tool-distribute)' : 'var(--surface)',
              color: copied ? 'var(--accent-ink)' : 'var(--ink-muted)',
              font: "700 13px 'Quicksand'",
              cursor: 'pointer'
            }}
          >
            {copied ? 'Copied ✓' : 'Copy error'}
          </div>
          <div
            onClick={dismiss}
            style={{
              padding: '9px 22px',
              borderRadius: 999,
              border: '2.5px solid var(--border)',
              background: 'var(--accent)',
              color: 'var(--accent-ink)',
              font: "700 13px 'Quicksand'",
              cursor: 'pointer',
              boxShadow: 'var(--shadow-sm)'
            }}
          >
            Close
          </div>
        </div>
      </div>
    </div>
  )
}
