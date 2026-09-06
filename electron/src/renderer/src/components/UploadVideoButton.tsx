import { useState } from 'react'
import { secondaryButtonSmall, textInput } from '../styles/styleKit'

/**
 * Choose a video — or, where the network can take one, an audio file — from this machine.
 *
 * Unlike the image picker next to it, this does not read the Library: nothing in this app
 * makes video or audio, so the only source is a file the person already has.
 *
 * The size limit is shown before the dialog opens, not after the upload fails. The three
 * networks disagree sharply — Bluesky 300MB and ten minutes, Mastodon whatever the instance
 * publishes, Tumblr in between — and a 400MB export is a long wait to be told no. The check
 * happens again in the backend with the real figure; this is the courtesy.
 *
 * AUDIO IS NOT OFFERED EVERYWHERE, and what happens to it differs by network. Mastodon takes
 * a sound file natively and renders a player. Bluesky has no audio embed at all, so the app
 * renders the waveform into a video and posts that — which is said here, before the pick, so
 * nobody has to work out afterwards why their Bluesky post is a square video. Tumblr's video
 * block takes neither, so that screen still offers video only.
 *
 * Picking copies the file into the app's own storage. That is what lets the backend read it
 * at all: attachments are only ever read from inside that directory, so a compose request
 * can never name an arbitrary path on the machine.
 */

const LIMIT_MB: Record<string, number> = { bluesky: 300, mastodon: 40, tumblr: 100 }

const NOTE: Record<string, string> = {
  bluesky: 'Bluesky takes up to 300MB and ten minutes.',
  mastodon: 'Most instances take up to 40MB — yours may differ.',
  tumblr: 'Tumblr takes up to 100MB.'
}

// What the picker offers, and what the backend will accept as sound rather than picture.
// Kept in step with audio_attach.ALLOWED_SUFFIXES.
const AUDIO_SUFFIXES = ['.mp3', '.m4a', '.aac', '.wav', '.flac', '.ogg', '.oga', '.opus']

const AUDIO_NOTE: Record<string, string> = {
  bluesky:
    'Bluesky cannot carry sound on its own, so this goes out as a waveform video of it — up to ten minutes.',
  mastodon: 'Mastodon posts audio as it is, with a player.'
}

function isAudio(name: string): boolean {
  const dot = name.lastIndexOf('.')
  return dot >= 0 && AUDIO_SUFFIXES.includes(name.slice(dot).toLowerCase())
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
  // Tumblr's NPF video block has no audio counterpart, so that screen keeps the old picker.
  const allowAudio = network !== 'tumblr'
  const pickedAudio = value ? isAudio(value.name) : false

  async function choose(): Promise<void> {
    setBusy(true)
    setProblem('')
    try {
      const picked = await window.api.chooseVideo(allowAudio)
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
        {allowAudio ? 'Upload a video or audio file' : 'Upload a video'}{' '}
        <span style={{ fontWeight: 600 }}>· optional</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap' }}>
        <div
          style={{ ...secondaryButtonSmall, opacity: disabled || busy ? 0.6 : 1 }}
          onClick={disabled || busy ? undefined : () => void choose()}
        >
          {busy ? 'Choosing…' : value ? 'Choose another' : allowAudio ? 'Upload file' : 'Upload video'}
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
          placeholder={
            pickedAudio
              ? "Describe the audio for people who can't hear it (optional)"
              : "Describe the video for people who can't see it (optional)"
          }
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
            : pickedAudio
              ? (AUDIO_NOTE[network] ?? NOTE[network])
              : NOTE[network]}
      </div>
    </div>
  )
}
