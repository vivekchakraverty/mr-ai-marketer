import { useEffect, useState } from 'react'
import { listBrandVoices, type BrandVoice } from '../api/client'
import { label, select } from '../styles/styleKit'

/**
 * Optional "write this as one of my brands" picker.
 *
 * Brand Studio saves a voice card with every document it assembles — tone dimensions, voice
 * traits, guardrails, never-sound-like. Choosing one here folds that card into the request
 * so the generated text sounds like the brand instead of like nothing in particular.
 *
 * The whole control hides itself when there are no brand documents yet. A dropdown whose
 * only entry is "None" is a question the user cannot answer and an advertisement for a
 * feature they have not set up; when they assemble their first document it appears on its
 * own.
 */

interface Props {
  value: string
  onChange: (id: string) => void
  /** Shown under the control when a brand is chosen. Tools word this differently. */
  hint?: string
}

export default function BrandVoiceSelect({ value, onChange, hint }: Props): React.JSX.Element | null {
  const [voices, setVoices] = useState<BrandVoice[] | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const list = await listBrandVoices()
        if (!cancelled) setVoices(list)
      } catch {
        // An optional enhancement must not put an error on a screen that is otherwise
        // working. Treated as "no brands", which hides the control.
        if (!cancelled) setVoices([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  if (!voices || voices.length === 0) return null

  // A stale id — the document was deleted since this form was filled in — would otherwise
  // leave the select showing the first option while sending something else.
  const known = voices.some((v) => v.id === value)

  return (
    <div>
      <label style={label}>Brand voice (optional)</label>
      <select style={select} value={known ? value : ''} onChange={(e) => onChange(e.target.value)}>
        <option value="">Don&rsquo;t use a brand voice</option>
        {voices.map((v) => (
          <option key={v.id} value={v.id}>
            {v.title}
            {v.createdAt ? ` · ${new Date(v.createdAt).toLocaleDateString()}` : ''}
          </option>
        ))}
      </select>
      {known && value && (
        <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
          {hint ?? "Written in this brand's tone, following its guardrails."}
        </div>
      )}
    </div>
  )
}
