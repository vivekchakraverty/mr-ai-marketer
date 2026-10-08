import { useState } from 'react'
import { textInput } from '../styles/styleKit'

/**
 * Paste a YouTube link to send with the post.
 *
 * The three networks mean three different things by "embed", and this says which one you
 * are getting rather than letting the result be a surprise after posting:
 *
 *   Tumblr    a real player, inline in the post.
 *   Bluesky   an external card with video details. Its app can play supported providers
 *             such as YouTube inline; other clients may open the source link.
 *   Mastodon  whatever the server makes of the link. Mastodon has no embed field at all;
 *             instances build their own preview cards, and some are configured not to.
 *
 * The id is checked here only to catch a paste that is obviously not YouTube. Whether the
 * video exists is the backend's question, since answering it means asking YouTube.
 */

export function isYouTubeLink(raw: string): boolean {
  try {
    const url = new URL(raw.trim())
    if (!['http:', 'https:'].includes(url.protocol)) return false
    const host = url.hostname.toLowerCase()
    const id = host === 'youtu.be' || host === 'www.youtu.be'
      ? url.pathname.split('/')[1]
      : ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtube-nocookie.com', 'www.youtube-nocookie.com'].includes(host)
        ? url.pathname === '/watch'
          ? url.searchParams.get('v')
          : /^\/(shorts|embed|live|v)\//.test(url.pathname)
            ? url.pathname.split('/')[2]
            : null
        : null
    return /^[\w-]{11}$/.test(id ?? '')
  } catch {
    return false
  }
}

const NOTE: Record<string, string> = {
  tumblr: 'Posts as a playable video in the post.',
  bluesky: 'Posts with video details and a thumbnail; YouTube plays in the Bluesky app.',
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
  const looksValid = isYouTubeLink(trimmed)
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
            : network === 'bluesky'
              ? 'Optional: links already in the post text are detected automatically.'
              : 'Paste a link and it goes out with the post.'}
      </div>
    </div>
  )
}
