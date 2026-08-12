import { useCallback, useEffect, useRef, useState } from 'react'
import { secondaryButtonSmall } from '../styles/styleKit'

/**
 * A scrap shelf on the right edge: somewhere to park text and images while moving
 * between tools, with cut/copy/paste against the real system clipboard.
 *
 * It persists to localStorage rather than the Library or the backend, deliberately. The
 * Library is for finished work you meant to keep; this is a pocket, and its contents are
 * expected to be thrown away. Keeping it out of SQLite also means it survives a backend
 * that has not started yet, which is exactly when you are most likely to be shuffling
 * text around.
 *
 * Images are the reason most of the code below exists. localStorage is a ~5MB string
 * store, and one pasted screenshot as a PNG data URL can be 3MB of it — so images are
 * downscaled and re-encoded on the way in, and the whole shelf is held under a budget by
 * evicting oldest-first. Without that, two screenshots would fill the quota and every
 * later write would throw.
 */

const STORAGE_KEY = 'mraim.scrapDrawer.v1'
// Comfortably under the ~5MB localStorage quota, leaving room for whatever else the
// renderer keeps there. Measured against the serialised JSON, not the raw bytes.
const BUDGET_BYTES = 3_500_000
const MAX_IMAGE_EDGE = 1000
const IMAGE_QUALITY = 0.82

interface Scrap {
  id: string
  kind: 'text' | 'image'
  /** Plain text, or a data: URL for an image. */
  content: string
  createdAt: number
}

function load(): Scrap[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    // Shape-checked, not just parsed. `JSON.parse` succeeds on "null" and on any object,
    // and either would sail past a try/catch here and then throw at `items.map` during
    // render — in a component mounted outside the route's error boundary, which takes the
    // whole window blank rather than showing a message.
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (s): s is Scrap =>
        Boolean(s) && typeof s === 'object' && typeof (s as Scrap).content === 'string'
    )
  } catch {
    return []
  }
}

/** Persist, dropping the oldest items until it fits. Returns what was actually stored, so
 *  the UI shows the truth rather than items that silently failed to save. */
function save(items: Scrap[]): Scrap[] {
  let kept = [...items]
  for (;;) {
    const payload = JSON.stringify(kept)
    if (payload.length <= BUDGET_BYTES || kept.length <= 1) {
      try {
        localStorage.setItem(STORAGE_KEY, payload)
        return kept
      } catch {
        // Quota rejected it even under our own budget — another origin key grew. Drop the
        // oldest and try again rather than losing the newest thing the user just added.
        if (kept.length <= 1) return kept
        kept = kept.slice(0, -1)
        continue
      }
    }
    kept = kept.slice(0, -1)
  }
}

/** Shrink and re-encode a pasted image. A screenshot arrives as multi-megabyte PNG; at
 *  1000px and JPEG q82 the same thing is usually under 200KB and still perfectly readable
 *  as a reference. Transparency is lost, which is an acceptable trade for a scratch pad. */
function downscale(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(img.width, img.height))
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(img.width * scale)
      canvas.height = Math.round(img.height * scale)
      const ctx = canvas.getContext('2d')
      if (!ctx) return reject(new Error('no 2d context'))
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      resolve(canvas.toDataURL('image/jpeg', IMAGE_QUALITY))
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('could not read that image'))
    }
    img.src = url
  })
}

async function dataUrlToBlob(dataUrl: string): Promise<Blob> {
  return await (await fetch(dataUrl)).blob()
}

export default function ScrapDrawer(): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<Scrap[]>(() => load())
  const [flash, setFlash] = useState('')
  const panelRef = useRef<HTMLDivElement | null>(null)

  const note = useCallback((message: string) => {
    setFlash(message)
    setTimeout(() => setFlash(''), 1800)
  }, [])

  const commit = useCallback((next: Scrap[]) => setItems(save(next)), [])

  const add = useCallback(
    (kind: Scrap['kind'], content: string) => {
      if (!content) return
      // Newest first: the thing just added should not be below a screenful of older scraps.
      commit([{ id: crypto.randomUUID(), kind, content, createdAt: Date.now() }, ...load()])
      note(kind === 'image' ? 'Image added' : 'Text added')
    },
    [commit, note]
  )

  // Paste straight into the drawer. The paste event carries image bytes directly, which
  // navigator.clipboard.read() cannot always get at without a permission prompt — so this
  // is the primary path and the button below is the fallback.
  useEffect(() => {
    if (!open) return
    async function onPaste(event: ClipboardEvent): Promise<void> {
      const target = event.target as HTMLElement | null
      // Don't hijack a paste the user aimed at a real input somewhere on the page.
      if (target && ['INPUT', 'TEXTAREA'].includes(target.tagName)) return
      if (!panelRef.current?.contains(document.activeElement) && document.activeElement !== document.body) return
      const files = Array.from(event.clipboardData?.items ?? [])
      for (const item of files) {
        if (item.type.startsWith('image/')) {
          const blob = item.getAsFile()
          if (blob) {
            event.preventDefault()
            try {
              add('image', await downscale(blob))
            } catch {
              note("Couldn't read that image")
            }
            return
          }
        }
      }
      const text = event.clipboardData?.getData('text/plain')?.trim()
      if (text) {
        event.preventDefault()
        add('text', text)
      }
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [open, add, note])

  async function pasteFromButton(): Promise<void> {
    try {
      const entries = await navigator.clipboard.read()
      for (const entry of entries) {
        const imageType = entry.types.find((t) => t.startsWith('image/'))
        if (imageType) {
          add('image', await downscale(await entry.getType(imageType)))
          return
        }
      }
      const text = (await navigator.clipboard.readText()).trim()
      if (text) add('text', text)
      else note('Clipboard is empty')
    } catch {
      note('Could not read the clipboard')
    }
  }

  async function copy(item: Scrap): Promise<void> {
    try {
      if (item.kind === 'text') {
        await navigator.clipboard.writeText(item.content)
      } else {
        const blob = await dataUrlToBlob(item.content)
        await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })])
      }
      note('Copied')
    } catch {
      note('Could not copy')
    }
  }

  async function cut(item: Scrap): Promise<void> {
    // Remove only after the clipboard write succeeds — cutting into a failed write would
    // destroy the only copy.
    try {
      if (item.kind === 'text') {
        await navigator.clipboard.writeText(item.content)
      } else {
        const blob = await dataUrlToBlob(item.content)
        await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })])
      }
    } catch {
      note('Could not copy — nothing removed')
      return
    }
    commit(load().filter((i) => i.id !== item.id))
    note('Cut')
  }

  function remove(item: Scrap): void {
    commit(load().filter((i) => i.id !== item.id))
  }

  const used = JSON.stringify(items).length

  return (
    <>
      {/* Collapsed tab. Always present so the shelf is discoverable without a menu. */}
      <div
        onClick={() => setOpen((v) => !v)}
        title={open ? 'Close the shelf' : 'Open the shelf'}
        style={{
          position: 'fixed',
          right: open ? 320 : 0,
          top: '46%',
          zIndex: 60,
          background: 'var(--surface)',
          border: '2px solid var(--border)',
          borderRight: open ? '2px solid var(--border)' : 'none',
          borderRadius: '12px 0 0 12px',
          padding: '14px 7px',
          cursor: 'pointer',
          boxShadow: 'var(--shadow-sm)',
          transition: 'right .18s ease',
          font: "700 11px 'Quicksand'",
          color: 'var(--ink-muted)',
          writingMode: 'vertical-rl',
          letterSpacing: '.08em'
        }}
      >
        {open ? '›› SHELF' : `SHELF${items.length ? ` · ${items.length}` : ''}`}
      </div>

      <div
        ref={panelRef}
        tabIndex={-1}
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          bottom: 0,
          width: 320,
          zIndex: 59,
          background: 'var(--surface-paper)',
          borderLeft: '2px solid var(--border)',
          boxShadow: open ? 'var(--shadow-md)' : 'none',
          transform: open ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform .18s ease',
          display: 'flex',
          flexDirection: 'column',
          outline: 'none'
        }}
      >
        <div style={{ padding: '14px 14px 10px', borderBottom: '2px dashed var(--border-soft)' }}>
          <div style={{ font: "700 15px 'Kalam'", color: 'var(--ink)' }}>Shelf</div>
          <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 2 }}>
            Park text and images here while you move between tools. Ctrl+V with the shelf open,
            or use Paste.
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <div style={secondaryButtonSmall} onClick={() => void pasteFromButton()}>
              Paste
            </div>
            {items.length > 0 && (
              <div style={secondaryButtonSmall} onClick={() => commit([])}>
                Clear all
              </div>
            )}
            {flash && (
              <span style={{ font: "700 11px 'Quicksand'", color: 'var(--accent-deep)', alignSelf: 'center' }}>
                {flash}
              </span>
            )}
          </div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '10px 12px' }}>
          {items.length === 0 && (
            <div style={{ font: "600 12px/1.6 'Quicksand'", color: 'var(--ink-fainter)', padding: '8px 2px' }}>
              Nothing here yet. Copy something, then press Ctrl+V with this open.
            </div>
          )}
          {items.map((item) => (
            <div
              key={item.id}
              style={{
                background: 'var(--surface)',
                border: '2px solid var(--border)',
                borderRadius: 12,
                padding: 9,
                marginBottom: 9
              }}
            >
              {item.kind === 'image' ? (
                <img
                  src={item.content}
                  alt=""
                  style={{ width: '100%', borderRadius: 8, display: 'block', maxHeight: 190, objectFit: 'contain' }}
                />
              ) : (
                <div
                  style={{
                    font: "600 12px/1.5 'Quicksand'",
                    color: 'var(--ink-muted)',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    maxHeight: 120,
                    overflow: 'hidden'
                  }}
                >
                  {item.content}
                </div>
              )}
              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <div style={{ ...secondaryButtonSmall, padding: '4px 9px' }} onClick={() => void copy(item)}>
                  Copy
                </div>
                <div style={{ ...secondaryButtonSmall, padding: '4px 9px' }} onClick={() => void cut(item)}>
                  Cut
                </div>
                <div
                  style={{ ...secondaryButtonSmall, padding: '4px 9px', marginLeft: 'auto' }}
                  onClick={() => remove(item)}
                >
                  Delete
                </div>
              </div>
            </div>
          ))}
        </div>

        <div
          style={{
            padding: '8px 14px',
            borderTop: '2px dashed var(--border-soft)',
            font: "600 10.5px 'Quicksand'",
            color: 'var(--ink-fainter)'
          }}
        >
          {items.length} item{items.length === 1 ? '' : 's'} · {(used / 1024).toFixed(0)} KB of{' '}
          {(BUDGET_BYTES / 1_048_576).toFixed(1)} MB · oldest drop out when full
        </div>
      </div>
    </>
  )
}
