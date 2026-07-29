export type ToolIconVariant =
  | 'blog'
  | 'guest'
  | 'tutorial'
  | 'docu'
  | 'plan'
  | 'distribute'
  | 'social'
  | 'mastodon'
  | 'email'

interface Props {
  variant: ToolIconVariant
  size?: number
}

const TOOL_COLOR: Record<ToolIconVariant, string> = {
  blog: 'var(--tool-blog)',
  guest: 'var(--tool-guest)',
  tutorial: 'var(--tool-tutorial)',
  docu: 'var(--tool-docu)',
  plan: 'var(--tool-plan)',
  distribute: 'var(--tool-distribute)',
  social: 'var(--tool-social)',
  mastodon: 'var(--tool-mastodon)',
  email: 'var(--tool-email)'
}

// Organic 4-corner-percentage "blob" radii, one per tool — matches the mockup's hand-drawn
// badge shapes (no two are identical rounded rectangles).
const TOOL_BLOB: Record<ToolIconVariant, string> = {
  blog: '55% 45% 50% 50%',
  guest: '50% 50% 45% 55%',
  tutorial: '45% 55% 50% 50%',
  docu: '50% 50% 55% 45%',
  plan: '52% 48% 50% 50%',
  distribute: '48% 52% 45% 55%',
  social: '53% 47% 47% 53%',
  mastodon: '49% 51% 53% 47%',
  email: '47% 53% 52% 48%'
}

export default function ToolIcon({ variant, size = 38 }: Props): React.JSX.Element {
  const scale = size / 24

  const containerStyle = {
    width: size,
    height: size,
    borderRadius: TOOL_BLOB[variant],
    background: TOOL_COLOR[variant],
    border: '2.5px solid var(--border)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0
  } as const

  const glyphStyle = { position: 'relative', width: 24 * scale, height: 24 * scale } as const

  let inner: React.ReactNode
  switch (variant) {
    case 'blog':
      // Pencil: triangular nib, shaft line, tip, and a pink eraser dot.
      inner = (
        <div style={glyphStyle}>
          <div
            style={{
              position: 'absolute',
              top: 2 * scale,
              left: 2 * scale,
              width: 20 * scale,
              height: 11 * scale,
              background: 'var(--surface)',
              clipPath: 'polygon(0 0, 100% 0, 50% 100%)'
            }}
          />
          <div
            style={{ position: 'absolute', top: 12 * scale, left: 11 * scale, width: 2 * scale, height: 8 * scale, background: 'var(--surface)' }}
          />
          <div
            style={{
              position: 'absolute',
              top: 19 * scale,
              left: 7 * scale,
              width: 10 * scale,
              height: 3 * scale,
              background: 'var(--surface)',
              borderRadius: 2
            }}
          />
          <div
            style={{
              position: 'absolute',
              top: 1 * scale,
              left: 16 * scale,
              width: 2 * scale,
              height: 9 * scale,
              background: 'var(--surface)',
              transform: 'rotate(32deg)',
              transformOrigin: 'bottom'
            }}
          />
          <div
            style={{
              position: 'absolute',
              top: -1 * scale,
              left: 16 * scale,
              width: 7 * scale,
              height: 7 * scale,
              borderRadius: '50%',
              background: '#ff3d6e'
            }}
          />
        </div>
      )
      break
    case 'guest':
      // Mailbox: rounded-top body, a mail slot, and a short post below.
      inner = (
        <div style={glyphStyle}>
          <div
            style={{
              position: 'absolute',
              top: 3 * scale,
              left: 2 * scale,
              width: 20 * scale,
              height: 10 * scale,
              background: 'var(--surface)',
              borderRadius: `${10 * scale}px ${10 * scale}px 0 0`
            }}
          />
          <div
            style={{ position: 'absolute', top: 3 * scale, left: 11 * scale, width: 2 * scale, height: 10 * scale, background: TOOL_COLOR.guest }}
          />
          <div
            style={{
              position: 'absolute',
              top: 12 * scale,
              left: 11 * scale,
              width: 2 * scale,
              height: 10 * scale,
              background: 'var(--surface)',
              borderRadius: 1
            }}
          />
        </div>
      )
      break
    case 'tutorial':
      // A looping swoosh — a rotated pill with a colored stripe down the middle.
      inner = (
        <div
          style={{
            width: 12 * scale,
            height: 26 * scale,
            background: 'var(--surface)',
            borderRadius: '50%',
            position: 'relative',
            transform: 'rotate(28deg)'
          }}
        >
          <div
            style={{
              position: 'absolute',
              top: 3 * scale,
              left: 5 * scale,
              width: 2 * scale,
              height: 20 * scale,
              background: TOOL_COLOR.tutorial,
              borderRadius: 2
            }}
          />
        </div>
      )
      break
    case 'docu':
      // A hand-drawn sparkle/star.
      inner = (
        <div
          style={{
            width: 24 * scale,
            height: 24 * scale,
            background: 'var(--surface)',
            clipPath:
              'polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%)'
          }}
        />
      )
      break
    case 'social':
      // A speech bubble with a tail and three "typing" dots — a post being written.
      inner = (
        <div style={glyphStyle}>
          <div
            style={{
              position: 'absolute',
              top: 4 * scale,
              left: 2 * scale,
              width: 20 * scale,
              height: 14 * scale,
              background: 'var(--surface)',
              borderRadius: `${7 * scale}px ${7 * scale}px ${7 * scale}px ${2 * scale}px`
            }}
          />
          {/* tail, tucked under the bubble's bottom-left corner */}
          <div
            style={{
              position: 'absolute',
              top: 16 * scale,
              left: 3 * scale,
              width: 5 * scale,
              height: 5 * scale,
              background: 'var(--surface)',
              clipPath: 'polygon(0% 100%, 0% 0%, 100% 0%)'
            }}
          />
          {[6, 11, 16].map((left) => (
            <div
              key={left}
              style={{
                position: 'absolute',
                top: 10 * scale,
                left: left * scale,
                width: 2.4 * scale,
                height: 2.4 * scale,
                borderRadius: '50%',
                background: TOOL_COLOR.social
              }}
            />
          ))}
        </div>
      )
      break
    case 'mastodon':
      // A rounded "m" arch with a curling trunk — the fediverse elephant, built
      // from the same absolutely-positioned blocks as its siblings.
      inner = (
        <div style={glyphStyle}>
          {/* head/arch */}
          <div
            style={{
              position: 'absolute',
              top: 4 * scale,
              left: 3 * scale,
              width: 18 * scale,
              height: 13 * scale,
              background: 'var(--surface)',
              borderRadius: `${9 * scale}px ${9 * scale}px ${4 * scale}px ${4 * scale}px`
            }}
          />
          {/* two eyes, punched out in the badge colour */}
          {[7, 14].map((left) => (
            <div
              key={left}
              style={{
                position: 'absolute',
                top: 8 * scale,
                left: left * scale,
                width: 2.6 * scale,
                height: 2.6 * scale,
                borderRadius: '50%',
                background: TOOL_COLOR.mastodon
              }}
            />
          ))}
          {/* trunk, hanging from the arch and curling right */}
          <div
            style={{
              position: 'absolute',
              top: 14 * scale,
              left: 10 * scale,
              width: 4 * scale,
              height: 8 * scale,
              background: 'var(--surface)',
              borderRadius: `0 0 ${4 * scale}px ${4 * scale}px`
            }}
          />
          <div
            style={{
              position: 'absolute',
              top: 18 * scale,
              left: 13 * scale,
              width: 5 * scale,
              height: 4 * scale,
              background: 'var(--surface)',
              borderRadius: `0 ${4 * scale}px ${4 * scale}px 0`
            }}
          />
        </div>
      )
      break
    case 'email':
      // An envelope: a cream body with a downward-V flap cut in the badge color.
      inner = (
        <div style={glyphStyle}>
          <div
            style={{
              position: 'absolute',
              top: 6 * scale,
              left: 2 * scale,
              width: 20 * scale,
              height: 13 * scale,
              background: 'var(--surface)',
              borderRadius: 2 * scale
            }}
          />
          <div
            style={{
              position: 'absolute',
              top: 6 * scale,
              left: 2 * scale,
              width: 20 * scale,
              height: 8 * scale,
              background: TOOL_COLOR.email,
              clipPath: 'polygon(0 0, 100% 0, 50% 100%)'
            }}
          />
        </div>
      )
      break
    case 'plan':
      // A target/compass: a ring with a rotated diamond needle.
      inner = (
        <div
          style={{
            width: 22 * scale,
            height: 22 * scale,
            borderRadius: '50%',
            border: `${3 * scale}px solid var(--surface)`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}
        >
          <div
            style={{
              width: 5 * scale,
              height: 14 * scale,
              background: 'var(--surface)',
              clipPath: 'polygon(50% 0, 100% 50%, 50% 100%, 0 50%)',
              transform: 'rotate(35deg)'
            }}
          />
        </div>
      )
      break
    case 'distribute':
    default:
      // A broadcast arrow/chevron.
      inner = (
        <div
          style={{
            width: 24 * scale,
            height: 20 * scale,
            background: 'var(--surface)',
            clipPath: 'polygon(0 0, 100% 50%, 0 100%, 26% 50%)'
          }}
        />
      )
      break
  }

  return <div style={containerStyle}>{inner}</div>
}
