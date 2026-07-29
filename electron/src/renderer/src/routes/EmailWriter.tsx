import { useState } from 'react'
import { generateEmail, type GenerateEmailResponse } from '../api/client'
import { refreshLibrary } from '../state/actions'
import { useAppStore } from '../state/store'
import { label, primaryButton, textarea } from '../styles/styleKit'

const EXAMPLE_PLACEHOLDER =
  'e.g. Write a last-chance promotional email for an e-commerce sunglasses brand’s summer sale: ' +
  '20% off, ending tonight, urgent but friendly tone, clear call-to-action button.'

const BUCKET_COLOR: Record<GenerateEmailResponse['ctrBucket'], string> = {
  'below average': 'var(--ink-faint)',
  typical: 'var(--ink-muted)',
  'above average': 'var(--accent)',
  strong: '#2fa366'
}

export default function EmailWriter(): React.JSX.Element {
  const fields = useAppStore((s) => s.fields.email)
  const setEmailField = useAppStore((s) => s.setEmailField)
  const goCreate = useAppStore((s) => s.goCreate)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<GenerateEmailResponse | null>(null)
  const [copied, setCopied] = useState(false)

  async function handleGenerate(): Promise<void> {
    if (!fields.instruction.trim()) return
    setLoading(true)
    setError('')
    setResult(null)
    setCopied(false)
    try {
      const res = await generateEmail(fields.instruction.trim())
      setResult(res)
      void refreshLibrary()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  async function handleCopy(): Promise<void> {
    if (!result) return
    try {
      await navigator.clipboard.writeText(result.text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard access can fail silently on some platforms — not worth surfacing an error for.
    }
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '22px 34px 60px' }}>
      <div
        style={{ font: "700 13px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer', marginBottom: 14 }}
        onClick={goCreate}
      >
        ← Create
      </div>
      <div style={{ marginBottom: 20 }}>
        <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)' }}>Email Writer</div>
        <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
          A model trained on real marketing emails — describe what you need and it writes the whole thing,
          subject line included.
        </div>
      </div>

      <div
        style={{
          background: 'var(--surface)',
          border: '2.5px solid var(--border)',
          borderRadius: 20,
          padding: 18,
          boxShadow: 'var(--shadow-md)',
          display: 'flex',
          flexDirection: 'column',
          gap: 14
        }}
      >
        <div>
          <label style={label}>Brief</label>
          <textarea
            value={fields.instruction}
            onChange={(e) => setEmailField('instruction', e.target.value)}
            placeholder={EXAMPLE_PLACEHOLDER}
            rows={4}
            style={textarea}
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <div
            style={{ ...primaryButton, padding: '11px 26px', display: 'inline-block', opacity: loading ? 0.6 : 1 }}
            onClick={loading ? undefined : handleGenerate}
          >
            {loading ? 'Writing…' : 'Write it'}
          </div>
          <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)' }}>
            Runs on a free Hugging Face CPU Space, so it's slow — usually 1–3 minutes, longer the first time if
            the Space has gone to sleep.
          </div>
        </div>
        {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}
      </div>

      {result && (
        <div
          style={{
            marginTop: 18,
            background: 'var(--surface-tint)',
            border: '2.5px solid var(--border)',
            borderRadius: 20,
            boxShadow: 'var(--shadow-md)',
            padding: '24px 26px'
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 }}>
            <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--accent)' }}>
              Your email
            </div>
            <div
              style={{
                font: "700 12.5px 'Quicksand'",
                color: copied ? 'var(--accent)' : 'var(--ink-muted)',
                cursor: 'pointer'
              }}
              onClick={handleCopy}
            >
              {copied ? 'Copied ✓' : 'Copy'}
            </div>
          </div>
          <div
            style={{
              background: 'var(--surface)',
              border: '2px solid var(--border-tint-2)',
              borderRadius: 14,
              padding: 18,
              font: "600 14px/1.65 'Quicksand'",
              color: 'var(--ink-body)',
              whiteSpace: 'pre-wrap'
            }}
          >
            {result.text}
          </div>

          <div
            style={{
              marginTop: 14,
              background: 'var(--surface)',
              border: '2px dashed var(--border-tint-2)',
              borderRadius: 14,
              padding: '14px 18px',
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              flexWrap: 'wrap'
            }}
          >
            <div
              style={{
                width: 9,
                height: 9,
                borderRadius: '50%',
                background: BUCKET_COLOR[result.ctrBucket],
                flexShrink: 0
              }}
            />
            <div style={{ font: "700 14px 'Quicksand'", color: 'var(--ink)' }}>
              Predicted click-through: {(result.predictedClickRate * 100).toFixed(1)}%
              <span style={{ color: BUCKET_COLOR[result.ctrBucket] }}> · {result.ctrBucket}</span>
            </div>
            <div style={{ font: "600 11.5px/1.4 'Quicksand'", color: 'var(--ink-faint)', flexBasis: '100%' }}>
              An estimate only, not a guarantee — from a model trained on ~1,900 historical email campaigns.
              Real results depend on your audience, sender reputation, and plenty this model never sees.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
