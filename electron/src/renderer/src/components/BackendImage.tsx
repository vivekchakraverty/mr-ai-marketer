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
}

export default function BackendImage({ url, alt, style }: Props): React.JSX.Element {
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

  return <img src={src} alt={alt} style={style} />
}
