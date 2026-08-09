import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PostMediaItem } from '../api/client'

/**
 * Media for a feed post — images, video, and link cards — shared by the Bluesky
 * and Mastodon panels in Engage.
 *
 * Both routers emit the same `PostMediaItem` shape, so this renders either
 * network without knowing which one it is looking at. Three behaviours are worth
 * knowing about:
 *
 * * **Sensitive media stays hidden until asked for.** A content warning is a
 *   request from the author, so `blurred` starts true and the viewer clears it.
 *   The blur is a real filter over a real thumbnail rather than a placeholder,
 *   so revealing costs no extra fetch.
 * * **Bluesky video is HLS.** Chromium plays MP4 from a plain `<video src>` but
 *   not an `.m3u8` playlist, so those get hls.js attached on demand. The library
 *   is imported lazily — a feed with no video never loads it.
 * * **Nothing autoplays.** Video renders as its poster frame with a play badge
 *   and only starts on a click.
 */

interface Props {
  media: PostMediaItem[]
  /** Author marked this post sensitive — media starts blurred behind a reveal. */
  sensitive?: boolean
  /** Caller already revealed the post (e.g. a content warning was expanded). */
  revealed?: boolean
}

const MAX_INLINE = 4

function isVisual(item: PostMediaItem): boolean {
  return item.kind === 'image' || item.kind === 'video' || item.kind === 'gifv'
}

// ---------------------------------------------------------------------------
// Video
// ---------------------------------------------------------------------------

function VideoPlayer({ item, autoPlay }: { item: PostMediaItem; autoPlay: boolean }): React.JSX.Element {
  const ref = useRef<HTMLVideoElement | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const el = ref.current
    if (!el) return
    let destroy: (() => void) | undefined

    if (!item.isHls) {
      el.src = item.url
      return
    }

    // Safari-family engines play HLS natively; Chromium does not. Electron is
    // Chromium, but the check costs nothing and keeps this component honest if
    // it is ever reused elsewhere.
    if (el.canPlayType('application/vnd.apple.mpegurl')) {
      el.src = item.url
      return
    }

    let cancelled = false
    void (async () => {
      try {
        // The `light` build drops subtitles, alt-audio and DRM — none of which
        // Bluesky's video uses — for roughly a third of the full build's size.
        const { default: Hls } = await import('hls.js/light')
        if (cancelled || !ref.current) return
        if (!Hls.isSupported()) {
          setError('This video format is not supported here.')
          return
        }
        const hls = new Hls({ enableWorker: true })
        hls.loadSource(item.url)
        hls.attachMedia(ref.current)
        hls.on(Hls.Events.ERROR, (_evt, data) => {
          // Only fatal errors are worth surfacing; hls.js recovers from the rest
          // on its own and reporting them would flag healthy playback as broken.
          if (data.fatal) setError('This video could not be played.')
        })
        destroy = () => hls.destroy()
      } catch {
        if (!cancelled) setError('The video player could not be loaded.')
      }
    })()

    return () => {
      cancelled = true
      destroy?.()
    }
  }, [item.url, item.isHls])

  if (error) {
    return (
      <div
        style={{
          font: "600 12px 'Quicksand'",
          color: 'var(--ink-faint)',
          padding: '10px 12px',
          border: '2px solid var(--border)',
          borderRadius: 12
        }}
      >
        {error}{' '}
        <span
          style={{ color: 'var(--accent-deep)', cursor: 'pointer', fontWeight: 700 }}
          onClick={() => void window.api.openExternal(item.url)}
        >
          Open externally ↗
        </span>
      </div>
    )
  }

  return (
    <video
      ref={ref}
      poster={item.previewUrl || undefined}
      controls
      autoPlay={autoPlay}
      // gifv is Mastodon's silent looping MP4 — it is a GIF in everything but
      // container, so it behaves like one.
      loop={item.kind === 'gifv'}
      muted={item.kind === 'gifv'}
      playsInline
      aria-label={item.description || 'Video'}
      style={{ width: '100%', maxHeight: 460, borderRadius: 12, background: '#000', display: 'block' }}
    />
  )
}

// ---------------------------------------------------------------------------
// Lightbox
// ---------------------------------------------------------------------------

function Lightbox({
  items,
  index,
  onClose,
  onIndex
}: {
  items: PostMediaItem[]
  index: number
  onClose: () => void
  onIndex: (next: number) => void
}): React.JSX.Element {
  const item = items[index]

  useEffect(() => {
    function onKey(event: KeyboardEvent): void {
      if (event.key === 'Escape') onClose()
      if (event.key === 'ArrowRight' && index < items.length - 1) onIndex(index + 1)
      if (event.key === 'ArrowLeft' && index > 0) onIndex(index - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [index, items.length, onClose, onIndex])

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={item.description || 'Media viewer'}
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(12,10,20,.88)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 28,
        gap: 12
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '100%', maxHeight: '82vh', display: 'flex', alignItems: 'center', gap: 14 }}
      >
        {items.length > 1 && (
          <NavArrow direction="prev" disabled={index === 0} onClick={() => onIndex(index - 1)} />
        )}
        {item.kind === 'image' ? (
          <img
            src={item.url || item.previewUrl}
            alt={item.description}
            style={{ maxWidth: '100%', maxHeight: '82vh', objectFit: 'contain', borderRadius: 12 }}
          />
        ) : (
          <div style={{ width: 'min(880px, 86vw)' }}>
            <VideoPlayer item={item} autoPlay />
          </div>
        )}
        {items.length > 1 && (
          <NavArrow direction="next" disabled={index === items.length - 1} onClick={() => onIndex(index + 1)} />
        )}
      </div>

      {/* Alt text is the whole reason someone wrote it — show it, don't bury it
          in a title attribute nobody hovers. */}
      {item.description && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            font: "600 12.5px/1.6 'Quicksand'",
            color: 'rgba(255,255,255,.86)',
            maxWidth: 720,
            textAlign: 'center',
            maxHeight: '12vh',
            overflowY: 'auto'
          }}
        >
          {item.description}
        </div>
      )}

      <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
        {items.length > 1 && (
          <span style={{ font: "700 11.5px 'Quicksand'", color: 'rgba(255,255,255,.6)' }}>
            {index + 1} / {items.length}
          </span>
        )}
        <span
          onClick={(e) => {
            e.stopPropagation()
            void window.api.openExternal(item.url || item.previewUrl)
          }}
          style={{ font: "700 11.5px 'Quicksand'", color: 'rgba(255,255,255,.72)', cursor: 'pointer' }}
        >
          Open externally ↗
        </span>
        <span style={{ font: "700 11.5px 'Quicksand'", color: 'rgba(255,255,255,.45)' }}>Esc to close</span>
      </div>
    </div>
  )
}

function NavArrow({
  direction,
  disabled,
  onClick
}: {
  direction: 'prev' | 'next'
  disabled: boolean
  onClick: () => void
}): React.JSX.Element {
  return (
    <span
      role="button"
      aria-label={direction === 'prev' ? 'Previous' : 'Next'}
      onClick={disabled ? undefined : onClick}
      style={{
        font: "700 22px 'Quicksand'",
        color: '#fff',
        opacity: disabled ? 0.22 : 0.8,
        cursor: disabled ? 'default' : 'pointer',
        userSelect: 'none',
        padding: '0 4px'
      }}
    >
      {direction === 'prev' ? '‹' : '›'}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Gallery
// ---------------------------------------------------------------------------

export default function PostMedia({ media, sensitive = false, revealed = false }: Props): React.JSX.Element | null {
  const [blurred, setBlurred] = useState(sensitive && !revealed)
  const [lightbox, setLightbox] = useState<number | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)

  // A caller revealing the post (expanding a content warning) reveals the media
  // with it; re-hiding the text hides the media again.
  useEffect(() => setBlurred(sensitive && !revealed), [sensitive, revealed])

  const visuals = useMemo(() => media.filter(isVisual), [media])
  const links = useMemo(() => media.filter((m) => m.kind === 'link'), [media])
  const others = useMemo(() => media.filter((m) => !isVisual(m) && m.kind !== 'link'), [media])

  const openLightbox = useCallback(
    (item: PostMediaItem) => {
      const idx = visuals.indexOf(item)
      if (idx >= 0) setLightbox(idx)
    },
    [visuals]
  )

  if (media.length === 0) return null

  const shown = visuals.slice(0, MAX_INLINE)
  const overflow = visuals.length - shown.length
  // One image gets room to breathe; a set becomes a tighter grid.
  const columns = shown.length === 1 ? 1 : 2

  return (
    <div style={{ marginTop: 9 }}>
      {shown.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: `repeat(${columns}, 1fr)`,
            gap: 6,
            maxWidth: shown.length === 1 ? 420 : 460
          }}
        >
          {shown.map((item, i) => {
            const isPlaying = playing === item.url && !blurred
            const ratio = item.aspectRatio && item.aspectRatio > 0 ? item.aspectRatio : shown.length === 1 ? 1.5 : 1.2

            if (isPlaying) {
              return (
                <div key={item.url || i} style={{ gridColumn: columns === 1 ? 'auto' : 'span 2' }}>
                  <VideoPlayer item={item} autoPlay />
                </div>
              )
            }

            return (
              <div
                key={item.url || i}
                role="button"
                tabIndex={0}
                aria-label={item.description || (item.kind === 'image' ? 'Image' : 'Video')}
                title={item.description || undefined}
                onClick={() => {
                  if (blurred) {
                    setBlurred(false)
                    return
                  }
                  if (item.kind === 'image') openLightbox(item)
                  else setPlaying(item.url)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    if (blurred) setBlurred(false)
                    else if (item.kind === 'image') openLightbox(item)
                    else setPlaying(item.url)
                  }
                }}
                style={{
                  position: 'relative',
                  aspectRatio: String(ratio),
                  borderRadius: 12,
                  border: '2px solid var(--border)',
                  overflow: 'hidden',
                  cursor: 'pointer',
                  background: 'var(--surface-paper)'
                }}
              >
                <img
                  src={item.previewUrl || item.url}
                  alt={item.description}
                  loading="lazy"
                  style={{
                    width: '100%',
                    height: '100%',
                    objectFit: 'cover',
                    display: 'block',
                    filter: blurred ? 'blur(18px)' : 'none',
                    transform: blurred ? 'scale(1.08)' : 'none'
                  }}
                />
                {!blurred && item.kind !== 'image' && <PlayBadge />}
                {!blurred && item.description && <AltBadge />}
                {blurred && <SensitiveOverlay />}
                {i === shown.length - 1 && overflow > 0 && !blurred && <OverflowBadge count={overflow} />}
              </div>
            )
          })}
        </div>
      )}

      {links.map((item, i) => (
        <LinkCard key={item.url || i} item={item} />
      ))}

      {/* Audio and anything a network invents later: labelled, and openable. */}
      {others.map((item, i) => (
        <span
          key={item.url || i}
          onClick={() => void window.api.openExternal(item.url || item.previewUrl)}
          style={{
            display: 'inline-block',
            marginTop: 7,
            marginRight: 6,
            padding: '6px 11px',
            border: '2px solid var(--border)',
            borderRadius: 10,
            font: "700 11.5px 'Quicksand'",
            color: 'var(--ink-muted)',
            cursor: 'pointer'
          }}
          title={item.description || item.kind}
        >
          {item.kind} ↗
        </span>
      ))}

      {lightbox !== null && visuals[lightbox] && (
        <Lightbox items={visuals} index={lightbox} onClose={() => setLightbox(null)} onIndex={setLightbox} />
      )}
    </div>
  )
}

function PlayBadge(): React.JSX.Element {
  return (
    <span
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        pointerEvents: 'none'
      }}
    >
      <span
        style={{
          width: 46,
          height: 46,
          borderRadius: '50%',
          background: 'rgba(16,14,24,.62)',
          border: '2px solid rgba(255,255,255,.82)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          font: '15px/1 sans-serif',
          color: '#fff',
          paddingLeft: 3
        }}
      >
        ▶
      </span>
    </span>
  )
}

function AltBadge(): React.JSX.Element {
  return (
    <span
      style={{
        position: 'absolute',
        left: 7,
        bottom: 7,
        padding: '2px 6px',
        borderRadius: 6,
        background: 'rgba(16,14,24,.66)',
        color: '#fff',
        font: "700 9.5px 'Quicksand'",
        letterSpacing: '.04em',
        pointerEvents: 'none'
      }}
    >
      ALT
    </span>
  )
}

function OverflowBadge({ count }: { count: number }): React.JSX.Element {
  return (
    <span
      style={{
        position: 'absolute',
        right: 7,
        top: 7,
        padding: '3px 8px',
        borderRadius: 8,
        background: 'rgba(16,14,24,.7)',
        color: '#fff',
        font: "700 10.5px 'Quicksand'",
        pointerEvents: 'none'
      }}
    >
      +{count}
    </span>
  )
}

function SensitiveOverlay(): React.JSX.Element {
  return (
    <span
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        font: "700 11.5px 'Quicksand'",
        color: '#fff',
        background: 'rgba(16,14,24,.34)',
        textAlign: 'center',
        padding: 8,
        pointerEvents: 'none'
      }}
    >
      Sensitive — click to show
    </span>
  )
}

function LinkCard({ item }: { item: PostMediaItem }): React.JSX.Element {
  return (
    <div
      onClick={() => void window.api.openExternal(item.url)}
      style={{
        display: 'flex',
        gap: 10,
        alignItems: 'stretch',
        marginTop: 8,
        border: '2px solid var(--border)',
        borderRadius: 12,
        overflow: 'hidden',
        cursor: 'pointer',
        maxWidth: 460,
        background: 'var(--surface-paper)'
      }}
      title={item.url}
    >
      {item.previewUrl && (
        <img
          src={item.previewUrl}
          alt=""
          loading="lazy"
          style={{ width: 92, objectFit: 'cover', flexShrink: 0, display: 'block' }}
        />
      )}
      <div style={{ padding: '9px 11px', minWidth: 0, flex: 1 }}>
        {item.domain && (
          <div style={{ font: "700 10px 'Quicksand'", color: 'var(--ink-fainter)', letterSpacing: '.05em' }}>
            {item.domain.toUpperCase()}
          </div>
        )}
        {item.title && (
          <div
            style={{
              font: "700 12.5px/1.4 'Quicksand'",
              color: 'var(--ink)',
              marginTop: 2,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden'
            }}
          >
            {item.title}
          </div>
        )}
        {item.description && (
          <div
            style={{
              font: "600 11.5px/1.45 'Quicksand'",
              color: 'var(--ink-faint)',
              marginTop: 3,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden'
            }}
          >
            {item.description}
          </div>
        )}
      </div>
    </div>
  )
}
