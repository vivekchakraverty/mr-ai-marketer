import { useState } from 'react'
import { textInput } from '../styles/styleKit'

/**
 * Paste a YouTube link to send with the post.
 *
 * The three networks mean three different things by "embed", and this says which one you
 * are getting rather than letting the result be a surprise after posting:
 *
 *   Tumblr    a real player, inline in the post.
 *   Bluesky   a link card with the thumbnail — there is no inline player in the protocol.
 *   Mastodon  whatever the server makes of the link. Mastodon has no embed field at all;
 *             instances build their own preview cards, and some are configured not to.
 *
 * The id is checked here only to catch a paste that is obviously not YouTube. Whether the
 * video exists is the backend's question, since answering it means asking YouTube.
 */

const YOUTUBE = /(?:youtu\.be\/|youtube(?:-nocookie)?\.com\/(?:watch\?(?:.*&)?v=|shorts\/|embed\/|live\/|v\/))([\w-]{11})/

const NOTE: Record<string, string> = {
  tumblr: 'Posts as a playable video in the post.',
  bluesky: 'Posts as a link card with the thumbnail — Bluesky has no inline player.',
  mastodon: "Posts as a link; your server builds the preview card, and some don't."
}

interface Props {
  network: 'bluesky' | 'mastodon' | 'tumblr'
  value: string
  onChange: (url: string) => void
  disabled?: boolean
}

export default function VideoEmbedInput({
  network,
  value,
  onChange,
  disabled = false
}: Props): React.JSX.Element {
  const [touched, setTouched] = useState(false)
  const trimmed = value.trim()
  const looksValid = YOUTUBE.test(trimmed)
  const complain = touched && trimmed.length > 0 && !looksValid

  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ font: "700 11px 'Quicksand'", color: 'var(--ink-fainter)', marginBottom: 6 }}>
        YouTube video <span style={{ fontWeight: 600 }}>· optional</span>
      </div>
      <input
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => setTouched(true)}
        placeholder="Paste a YouTube link"
        style={{
          ...textInput,
          borderColor: complain ? 'var(--danger-ink)' : undefined
        }}
      />
      <div
        style={{
          font: "600 11.5px/1.5 'Quicksand'",
          color: complain ? 'var(--danger-ink)' : 'var(--ink-faint)',
          marginTop: 5
        }}
      >
        {complain
          ? "That doesn't look like a YouTube link — paste the full address of the video."
          : trimmed
            ? NOTE[network]
            : 'Paste a link and it goes out with the post.'}
      </div>
    </div>
  )
}
