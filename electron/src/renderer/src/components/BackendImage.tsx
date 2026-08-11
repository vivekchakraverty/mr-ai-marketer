import { useEffect, useState } from 'react'
import { fetchObjectUrl } from '../api/client'

/**
 * An `<img>` for a file the backend serves under /outputs.
 *
 * Every tool that shows a generated image — Brand Studio, Blog Writer, DocuMaker, Social
 * Post, TutorialMaker — pointed an `<img>` straight at `${backendUrl}${url}`. An `<img>`
 * cannot send the `x-mraim-token` header the backend requires, so all of them 401'd and
 * showed the browser's broken-image box with the alt text next to it.
 *
 * This fetches the bytes with the token attached and hands the element a blob: URL. The
 * alternative — exempting /outputs from the token check — is one line but opens the same
 * mount that serves the influencer and tracker CSV exports, whose filenames are timestamps
 * rather than UUIDs.
 */

interface Props {
  /** Backend-relative path, e.g. "/outputs/brand/<uuid>/logo-mark-concept.png". */
  url: string
  alt: string
  style?: React.CSSProperties
  /** Set false for decorative images where a save affordance would just be clutter. */
  saveable?: boolean
}

/** "Logo Mark Concept" + /…/logo-mark-concept.png -> "Logo Mark Concept.png".
 *
 * The alt text is what the user sees the image called on screen, so it is the name they
 * expect in the save dialog — but the extension has to come from the actual file, since
 * the generators emit png here and could emit something else later. */
function suggestedName(url: string, alt: string): string {
  const file = url.split('/').pop() || 'image.png'
  const ext = file.includes('.') ? file.slice(file.lastIndexOf('.')) : '.png'
  const base = (alt || file).replace(/[\\/:*?"<>|]/g, '-').trim() || 'image'
  return base.toLowerCase().endsWith(ext.toLowerCase()) ? base : base + ext
}

export default function BackendImage({ url, alt, style, saveable = true }: Props): React.JSX.Element {
  const [src, setSrc] = useState('')
  const [failed, setFailed] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let revoked = false
    let objectUrl = ''
    setSrc('')
    setFailed(false)

    void (async () => {
      try {
        const next = await fetchObjectUrl(url)
        // The effect can be torn down while the fetch is in flight — on a re-render, or on
        // StrictMode's double-invoke. Revoking immediately keeps the blob from leaking when
        // nothing will ever render it.
        if (revoked) {
          URL.revokeObjectURL(next)
          return
        }
        objectUrl = next
        setSrc(next)
      } catch {
        if (!revoked) setFailed(true)
      }
    })()

    return () => {
      revoked = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [url])

  if (failed) {
    return (
      <div
        style={{
          ...style,
          display: 'grid',
          placeItems: 'center',
          minHeight: 90,
          border: '2px dashed var(--border)',
          borderRadius: 12,
          font: "600 11.5px 'Quicksand'",
          color: 'var(--ink-muted)',
          textAlign: 'center',
          padding: 8
        }}
      >
        Couldn&rsquo;t load this image
      </div>
    )
  }

  // Holding the space before the bytes arrive stops the surrounding layout jumping as each
  // image lands, which is visible when three of them load at once.
  if (!src) {
    return (
      <div
        style={{
          ...style,
          minHeight: 90,
          border: '2px solid var(--border)',
          borderRadius: 12,
          background: 'var(--accent-soft-bg)'
        }}
      />
    )
  }

  async function handleSave(): Promise<void> {
    try {
      // Re-reading the blob rather than keeping a second copy in state: the bytes are
      // already held by the URL, and a saved image is a rare action next to rendering one.
      const bytes = new Uint8Array(await (await fetch(src)).arrayBuffer())
      if (await window.api.saveBytes(suggestedName(url, alt), bytes)) {
        setSaved(true)
        setTimeout(() => setSaved(false), 1800)
      }
    } catch {
      // A cancelled dialog is the common case and is not a failure; a genuine write error
      // is rare and the OS has already told the user about it.
    }
  }

  if (!saveable) return <img src={src} alt={alt} style={style} />

  // The button sits over the image and appears on hover, so a wall of generated images
  // stays a wall of images rather than a wall of controls.
  return (
    <span
      style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}
      onMouseEnter={(e) => {
        const el = e.currentTarget.querySelector('[data-save]') as HTMLElement | null
        if (el) el.style.opacity = '1'
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget.querySelector('[data-save]') as HTMLElement | null
        if (el) el.style.opacity = '0'
      }}
    >
      <img src={src} alt={alt} style={style} />
      <span
        data-save
        onClick={(e) => {
          e.stopPropagation()
          void handleSave()
        }}
        title="Save this image"
        style={{
          position: 'absolute',
          top: 8,
          right: 8,
          opacity: 0,
          transition: 'opacity .13s',
          cursor: 'pointer',
          background: 'var(--surface)',
          border: '2px solid var(--border)',
          borderRadius: 999,
          padding: '4px 11px',
          font: "700 11.5px 'Quicksand'",
          color: saved ? 'var(--accent-deep)' : 'var(--ink-muted)',
          boxShadow: 'var(--shadow-sm)',
          userSelect: 'none'
        }}
      >
        {saved ? 'Saved' : 'Save'}
      </span>
    </span>
  )
}
