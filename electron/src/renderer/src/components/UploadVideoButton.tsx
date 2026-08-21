import { useState } from 'react'
import { secondaryButtonSmall, textInput } from '../styles/styleKit'

/**
 * Choose a video from this machine to post.
 *
 * Unlike the image picker next to it, this does not read the Library: nothing in this app
 * makes video, so the only source is a file the person already has.
 *
 * The size limit is shown before the dialog opens, not after the upload fails. The three
 * networks disagree sharply — Bluesky 50MB and about three minutes, Mastodon whatever the
 * instance publishes, Tumblr the most generous — and a 90MB export is a long wait to be told
 * no. The check happens again in the backend with the real figure; this is the courtesy.
 *
 * Picking copies the file into the app's own storage. That is what lets the backend read it
 * at all: attachments are only ever read from inside that directory, so a compose request
 * can never name an arbitrary path on the machine.
 */

const LIMIT_MB: Record<string, number> = { bluesky: 50, mastodon: 40, tumblr: 100 }

const NOTE: Record<string, string> = {
  bluesky: 'Bluesky takes up to 50MB and about three minutes.',
  mastodon: 'Most instances take up to 40MB — yours may differ.',
  tumblr: 'Tumblr is the most generous of the three.'
}

export interface ChosenVideo {
  url: string
  name: string
  bytes: number
}

interface Props {
  network: 'bluesky' | 'mastodon' | 'tumblr'
  value: ChosenVideo | null
  onChange: (video: ChosenVideo | null) => void
  alt: string
  onAltChange: (alt: string) => void
  disabled?: boolean
}

function sizeLabel(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(1)}MB`
    : `${Math.max(1, Math.round(bytes / 1024))}KB`
}

export default function UploadVideoButton({
  network,
  value,
  onChange,
  alt,
  onAltChange,
  disabled = false
}: Props): React.JSX.Element {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')

  const limit = LIMIT_MB[network] ?? 40
  const tooBig = value ? value.bytes > limit * 1024 * 1024 : false

  async function choose(): Promise<void> {
    setBusy(true)
    setProblem('')
    try {
      const picked = await window.api.chooseVideo()
      if (picked) onChange(picked)
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ font: "700 11px 'Quicksand'", color: 'var(--ink-fainter)', marginBottom: 6 }}>
        Upload a video <span style={{ fontWeight: 600 }}>· optional</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap' }}>
        <div
          style={{ ...secondaryButtonSmall, opacity: disabled || busy ? 0.6 : 1 }}
          onClick={disabled || busy ? undefined : () => void choose()}
        >
          {busy ? 'Choosing…' : value ? 'Choose another' : 'Upload video'}
        </div>
        {value && (
          <>
            <span
              style={{
                font: "600 12px 'Quicksand'",
                color: tooBig ? 'var(--danger-ink)' : 'var(--ink-muted)'
              }}
            >
              {value.name} · {sizeLabel(value.bytes)}
            </span>
            <span
              style={{ font: "700 11.5px 'Quicksand'", color: 'var(--accent-deep)', cursor: 'pointer' }}
              onClick={disabled ? undefined : () => onChange(null)}
            >
              Remove
            </span>
          </>
        )}
      </div>

      {value && (
        <input
          value={alt}
          disabled={disabled}
          onChange={(e) => onAltChange(e.target.value)}
          placeholder="Describe the video for people who can't see it (optional)"
          style={{ ...textInput, marginTop: 8 }}
        />
      )}

      <div
        style={{
          font: "600 11.5px/1.5 'Quicksand'",
          color: tooBig || problem ? 'var(--danger-ink)' : 'var(--ink-faint)',
          marginTop: 5
        }}
      >
        {problem
          ? problem
          : tooBig
            ? `That is ${sizeLabel(value!.bytes)} and ${network} allows about ${limit}MB — it will be refused.`
            : NOTE[network]}
      </div>
    </div>
  )
}
