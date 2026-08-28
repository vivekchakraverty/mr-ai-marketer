import { useEffect, useState } from 'react'
import { fetchObjectUrl } from '../api/client'

/**
 * A `<video>` for a clip the backend serves under /outputs.
 *
 * The same problem BackendImage solves, for the other kind of attachment: a `<video src>`
 * cannot send the `x-mraim-token` header the backend requires, so pointing one straight at
 * `${backendUrl}${url}` gets a 401 and an empty player. The bytes are fetched with the token
 * attached and handed over as a blob: URL instead.
 *
 * Why a blob rather than streaming. The alternative is exempting /outputs from the token
 * check so the element can fetch it itself, and that mount also serves the influencer and
 * tracker CSV exports — the reason BackendImage took this route, and the reasoning has not
 * changed. The cost is real and worth naming: the whole file lands in memory, so a 50MB clip
 * is 50MB held for as long as the player is mounted. That is tolerable here only because
 * nothing renders this until a person opens the card it lives in, and it is released the
 * moment they close it again.
 *
 * Nothing autoplays and nothing preloads beyond metadata. A send-history list can hold many
 * of these, and a screen that starts playing on its own is worse than one that waits.
 */

interface Props {
  /** Backend-relative path, e.g. "/outputs/uploads/<uuid>/clip.mp4". */
  url: string
  /** The clip's alt text, when the post carried one. Used as the accessible name. */
  alt?: string
  style?: React.CSSProperties
}

export default function BackendVideo({ url, alt = '', style }: Props): React.JSX.Element {
  const [src, setSrc] = useState('')
  const [failed, setFailed] = useState(false)

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

  // A clip that cannot be loaded is usually one that has since been deleted off disk, and in
  // a send history that is worth knowing rather than hiding. The path comes back on screen
  // here — not as a fallback rendering, but because it is the only remaining answer to
  // "which file was this?".
  if (failed) {
    return (
      <div
        style={{
          font: "600 12px/1.6 'Quicksand'",
          color: 'var(--ink-muted)',
          border: '2px dashed var(--border)',
          borderRadius: 12,
          padding: '9px 12px'
        }}
      >
        This video is no longer on disk.
        <div style={{ color: 'var(--ink-faint)', wordBreak: 'break-all', marginTop: 3 }}>{url}</div>
      </div>
    )
  }

  // Holding the space before the bytes arrive stops the card jumping as the clip lands.
  if (!src) {
    return (
      <div
        style={{
          ...style,
          minHeight: 120,
          border: '2px solid var(--border)',
          borderRadius: 12,
          background: 'var(--accent-soft-bg)'
        }}
      />
    )
  }

  return (
    <video
      src={src}
      controls
      preload="metadata"
      playsInline
      aria-label={alt || 'Attached video'}
      style={{
        maxWidth: 320,
        width: '100%',
        borderRadius: 12,
        border: '2px solid var(--border)',
        background: '#000',
        display: 'block',
        ...style
      }}
    />
  )
}
