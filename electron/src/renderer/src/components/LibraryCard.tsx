import BackendImage from './BackendImage'
import type { LibraryItem } from '../state/types'

interface Props {
  item: LibraryItem
  onOpen: () => void
}

/** The /outputs URL for a card whose file is a picture, else ''.
 *
 * Generated images sit on the same shelf as documents, and a card that shows only a
 * title cannot tell you which of four mood boards it is. The Reader already renders the
 * picture on open; this is the same test, so the card and what it opens agree. */
function thumbnailUrl(item: LibraryItem): string {
  const outputs = item.output_path?.replace(/\\/g, '/') ?? ''
  if (!/\.(png|jpe?g|webp|gif)$/i.test(outputs)) return ''
  return '/outputs/' + outputs.split('/outputs/').slice(1).join('/outputs/')
}

// Keyed by string, not by the LibraryItem['tool'] union, and looked up through the
// helpers below. The union is not the truth: the save endpoint accepts any `tool` a caller
// sends and defaults to "Note", and the app itself already writes "Hashtags" and "Engage",
// neither of which is in the union or these maps. Indexing them directly meant one such row
// made `tag.bg` a read of undefined — which took down the entire Library screen, not just
// its own card, because a render that throws unmounts the tree.
const TAPE_COLOR: Record<string, string> = {
  Plan: 'rgba(255, 203, 77, .6)',
  Blog: 'rgba(111, 203, 192, .55)',
  Guest: 'rgba(255, 154, 124, .5)',
  Tutorial: 'rgba(167, 139, 250, .45)',
  Docs: 'rgba(255, 203, 77, .6)',
  Social: 'rgba(247, 154, 200, .5)',
  Email: 'rgba(246, 169, 122, .5)',
  Brand: 'rgba(129, 178, 154, .55)',
  Topics: 'rgba(143, 211, 244, .55)'
}

const TAG_STYLE: Record<string, { bg: string; ink: string }> = {
  Plan: { bg: '#ffe3d9', ink: '#b0553b' },
  Blog: { bg: '#dff6e9', ink: '#2fa366' },
  Guest: { bg: '#ffe9dc', ink: '#d8703f' },
  Tutorial: { bg: '#eee5fe', ink: '#7c5fd8' },
  Docs: { bg: '#fff3d2', ink: '#b08f00' },
  Social: { bg: '#ffe1f0', ink: '#c04f8d' },
  Email: { bg: '#ffe9d6', ink: '#c9752f' },
  Brand: { bg: '#dcefe4', ink: '#3f7d63' },
  Topics: { bg: '#d9eefb', ink: '#2c6f93' }
}

/** Neutral styling for a tool nobody assigned a colour to. Better a plain card than none. */
const FALLBACK_TAPE = 'rgba(176, 164, 136, .45)'
const FALLBACK_TAG = { bg: '#f0e8da', ink: '#6b5f4e' }

// A different tilt per card so the washi-tape accent reads as hand-placed, not templated.
const TAPE_TILTS = [-4, 3, -2, 4, -5, 2]

function formatDate(iso: string): string {
  const d = new Date(iso)
  const diffMs = Date.now() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return 'Just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  const diffDay = Math.floor(diffHr / 24)
  if (diffDay === 1) return '1 day ago'
  if (diffDay < 7) return `${diffDay} days ago`
  return d.toLocaleDateString()
}

export default function LibraryCard({ item, onOpen }: Props): React.JSX.Element {
  const tilt = TAPE_TILTS[item.id.charCodeAt(0) % TAPE_TILTS.length]
  const tag = TAG_STYLE[item.tool] ?? FALLBACK_TAG
  const thumbnail = thumbnailUrl(item)

  return (
    <div
      style={{
        position: 'relative',
        background: 'var(--surface)',
        border: '2.5px solid var(--border)',
        borderRadius: 20,
        padding: 20,
        boxShadow: 'var(--shadow-sm)',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        minHeight: 150,
        cursor: 'pointer'
      }}
      onClick={onOpen}
    >
      <div
        style={{
          position: 'absolute',
          top: -9,
          left: 22,
          width: 44,
          height: 16,
          background: TAPE_COLOR[item.tool] ?? FALLBACK_TAPE,
          border: '1.5px solid var(--border)',
          borderRadius: 3,
          transform: `rotate(${tilt}deg)`
        }}
      />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span
          style={{
            padding: '3px 10px',
            borderRadius: 999,
            font: "700 10.5px 'Quicksand'",
            textTransform: 'uppercase',
            background: tag.bg,
            color: tag.ink
          }}
        >
          {item.tool}
        </span>
      </div>
      {thumbnail && (
        <div
          style={{
            borderRadius: 12,
            overflow: 'hidden',
            border: '1.5px solid var(--border-soft)',
            aspectRatio: '16 / 9',
            background: 'var(--surface-tint)'
          }}
        >
          {/* saveable={false}: the card is a click target that opens the Reader, and a
              save affordance sitting on it would swallow that click. */}
          <BackendImage
            url={thumbnail}
            alt={item.title}
            saveable={false}
            style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
          />
        </div>
      )}
      <div style={{ font: "700 17px/1.3 'Kalam'", color: 'var(--ink)', flex: 1 }}>{item.title}</div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ font: "600 11px 'Quicksand'", color: 'var(--ink-faint)' }}>{item.subtitle}</span>
        <span style={{ font: "600 11px 'Quicksand'", color: 'var(--ink-faint)' }}>{formatDate(item.created_at)}</span>
      </div>
    </div>
  )
}
