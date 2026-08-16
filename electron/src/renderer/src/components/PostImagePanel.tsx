import { useState } from 'react'
import type { ImagePromptSuggestion, SocialGeneratedImage } from '../api/client'
import BackendImage from '../components/BackendImage'
import { label, primaryButtonSmall, secondaryButtonSmall, textarea } from '../styles/styleKit'

/**
 * The companion-image section, shared by all three post composers.
 *
 * Its shape is the point: **suggest, review, then draw.** A small language model
 * proposes the art direction, the proposal lands in an editable box, and nothing is
 * rendered until the author presses Generate on text they have actually read. The
 * backend enforces the same rule — /images refuses a request that carries no prompt —
 * so this is the interface to a guarantee rather than a convention.
 *
 * Why it works that way: the Bluesky composer used to build the prompt internally and
 * draw in one click, which meant the only way to change the picture was to change the
 * post. Image models fail in specific, correctable ways — "wider shot", "lose the
 * desk", "warmer light" — and a prompt nobody can see is a prompt nobody can fix.
 *
 * Editing the suggestion clears any image already rendered from the previous wording,
 * so what is on screen is always what the current prompt produced.
 */

interface Props {
  /** The finished post the image should accompany. Empty disables the section. */
  postText: string
  /** Asks the backend for a suggestion. Never rejects for a model failure — it falls back. */
  onSuggest: () => Promise<ImagePromptSuggestion>
  /** Draws the approved prompt. */
  onGenerate: (prompt: string) => Promise<SocialGeneratedImage>
}

export default function PostImagePanel({ postText, onSuggest, onGenerate }: Props): React.JSX.Element {
  const [prompt, setPrompt] = useState('')
  const [suggestion, setSuggestion] = useState<ImagePromptSuggestion | null>(null)
  const [image, setImage] = useState<SocialGeneratedImage | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [drawing, setDrawing] = useState(false)
  const [error, setError] = useState('')

  const ready = Boolean(postText.trim())
  const approved = Boolean(prompt.trim())

  async function handleSuggest(): Promise<void> {
    setSuggesting(true)
    setError('')
    try {
      const next = await onSuggest()
      setSuggestion(next)
      setPrompt(next.prompt)
      // A new suggestion describes a different picture than the one on screen.
      setImage(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSuggesting(false)
    }
  }

  async function handleGenerate(): Promise<void> {
    if (!approved) return
    setDrawing(true)
    setError('')
    try {
      setImage(await onGenerate(prompt.trim()))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setDrawing(false)
    }
  }

  return (
    <div
      style={{
        marginTop: 16,
        background: 'var(--surface-paper)',
        border: '2.5px solid var(--border-paper)',
        borderRadius: 20,
        padding: 18,
        boxShadow: 'var(--shadow-paper)'
      }}
    >
      <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)', marginBottom: 2 }}>
        Companion image
      </div>
      <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 14 }}>
        A small model reads your post and proposes the picture. Read it, change anything you
        like, then generate — nothing is drawn until you do.
      </div>

      {!ready ? (
        <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)', padding: '6px 0' }}>
          Write the post first, then come back for its image.
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12 }}>
            <div
              style={{ ...secondaryButtonSmall, opacity: suggesting ? 0.6 : 1 }}
              onClick={suggesting ? undefined : () => void handleSuggest()}
            >
              {suggesting ? 'Thinking…' : suggestion ? 'Suggest another' : 'Suggest a prompt'}
            </div>
            {suggestion && (
              <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
                {suggestion.width}×{suggestion.height}
                {suggestion.source === 'template' ? ' · standard direction' : ' · written for this post'}
              </div>
            )}
          </div>

          {suggestion?.note && (
            <div
              style={{
                font: "600 12px/1.5 'Quicksand'",
                color: 'var(--ink-muted)',
                background: 'var(--tip-bg)',
                border: '2px dashed var(--border-soft)',
                borderRadius: 12,
                padding: '9px 12px',
                marginBottom: 12
              }}
            >
              {suggestion.note}
            </div>
          )}

          {suggestion && (
            <>
              <label style={label}>Image prompt — edit freely</label>
              <textarea
                value={prompt}
                onChange={(e) => {
                  setPrompt(e.target.value)
                  // The picture on screen no longer matches the words on screen.
                  setImage(null)
                }}
                style={{ ...textarea, minHeight: 132 }}
              />

              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10 }}>
                <div
                  style={{ ...primaryButtonSmall, opacity: drawing || !approved ? 0.55 : 1 }}
                  onClick={drawing || !approved ? undefined : () => void handleGenerate()}
                >
                  {drawing ? 'Generating…' : image ? 'Generate again' : 'Generate image'}
                </div>
                {!approved && (
                  <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
                    An empty prompt draws nothing.
                  </div>
                )}
              </div>
            </>
          )}

          {image && (
            <div style={{ marginTop: 14 }}>
              <BackendImage
                url={image.url}
                alt="Generated companion image"
                style={{
                  width: '100%',
                  maxHeight: 640,
                  objectFit: 'contain',
                  background: 'var(--surface)',
                  border: '2px solid var(--border)',
                  borderRadius: 8,
                  display: 'block'
                }}
              />
              <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 6 }}>
                {image.width}×{image.height} · drawn from the prompt above
              </div>
            </div>
          )}
        </>
      )}

      {error && (
        <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--danger-ink)', marginTop: 12 }}>
          {error}
        </div>
      )}
    </div>
  )
}
