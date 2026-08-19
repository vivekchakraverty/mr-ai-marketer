import { useEffect } from 'react'
import BackendImage from './BackendImage'
import { useAppStore } from '../state/store'
import { refreshLibrary } from '../state/actions'

/**
 * Pick one of your generated images to attach to a post you are about to send.
 *
 * Deliberately not an upload control. The images worth attaching are the ones the
 * companion-image section in the post creators produced, and those already file
 * themselves in the Library with the PNG beside them — so this reads that shelf rather
 * than adding a second place for pictures to live, or a file dialog whose result the
 * backend would then have to be taught to trust.
 *
 * Unlike the same strip inside SendToDistributionModal, a local `/outputs` image is the
 * *normal* case here: Engage posts through each network's own API from the backend, which
 * can read the file off disk. The distribution engine runs in a container that cannot
 * reach this machine, which is why that copy refuses what this one accepts.
 *
 * Alt text is offered rather than required. Every one of the three networks supports it
 * and it is the difference between a post a screen reader can follow and one it cannot,
 * but a composer that blocks on it would just teach people to type a full stop.
 */

interface Props {
  url: string
  alt: string
  onChange: (next: { url: string; alt: string }) => void
  /** Shown above the strip. Networks differ on how the image lands, and saying so here
   * beats a surprise after the post is out. */
  hint?: string
  disabled?: boolean
}

export interface PickedImage {
  url: string
  alt: string
}

/** The Library rows that are actually images, newest first, as { title, url }. */
export function useGeneratedImages(limit = 12): { title: string; url: string }[] {
  const library = useAppStore((st) => st.library)
  return library
    .filter((i) => /\.(png|jpe?g|webp|gif)$/i.test(i.output_path ?? ''))
    .slice(0, limit)
    .map((i) => {
      // output_path is a filesystem path on Windows; the backend serves it under
      // /outputs, so the tail from that segment on is the URL.
      const p = (i.output_path ?? '').split('\\').join('/')
      return { title: i.title, url: '/outputs/' + p.split('/outputs/').slice(1).join('/outputs/') }
    })
}

export default function AttachImagePicker({
  url,
  alt,
  onChange,
  hint,
  disabled = false
}: Props): React.JSX.Element | null {
  const images = useGeneratedImages()
  const library = useAppStore((st) => st.library)

  // The composer can be the first screen touched in a session, so the shelf may not have
  // been read yet. Only when it is empty — a refresh on every mount would refetch the
  // whole Library each time someone clicks into Engage.
  useEffect(() => {
    if (library.length === 0) void refreshLibrary()
  }, [library.length])

  if (images.length === 0) return null

  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ font: "700 11px 'Quicksand'", color: 'var(--ink-fainter)', marginBottom: 6 }}>
        Attach an image {hint ? <span style={{ fontWeight: 600 }}>· {hint}</span> : null}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {images.map((img) => {
          const picked = url === img.url
          return (
            <div
              key={img.url}
              title={img.title}
              onClick={
                disabled ? undefined : () => onChange({ url: picked ? '' : img.url, alt: picked ? '' : alt })
              }
              style={{
                width: 56,
                height: 56,
                borderRadius: 10,
                overflow: 'hidden',
                cursor: disabled ? 'default' : 'pointer',
                opacity: disabled ? 0.5 : 1,
                border: picked ? '2px solid var(--accent)' : '1px solid var(--border-soft)',
                boxShadow: picked ? '0 0 0 3px var(--accent-soft-bg)' : 'none'
              }}
            >
              <BackendImage
                url={img.url}
                alt={img.title}
                saveable={false}
                style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
              />
            </div>
          )
        })}
      </div>
      {url && (
        <input
          value={alt}
          disabled={disabled}
          onChange={(e) => onChange({ url, alt: e.target.value })}
          placeholder="Describe the image for people who can't see it (optional)"
          style={{
            width: '100%',
            marginTop: 8,
            padding: '7px 10px',
            borderRadius: 9,
            border: '1px solid var(--border-soft)',
            background: 'var(--surface)',
            color: 'var(--ink)',
            font: "600 12px 'Quicksand'"
          }}
        />
      )}
    </div>
  )
}
