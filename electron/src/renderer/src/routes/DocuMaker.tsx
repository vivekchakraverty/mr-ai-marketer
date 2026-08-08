import { useRef, useState } from 'react'
import { backendUrl, generateDocu, type GenerateDocuResponse } from '../api/client'
import { refreshLibrary } from '../state/actions'
import { useAppStore } from '../state/store'
import ScreenBackdrop from '../components/ScreenBackdrop'
import { label, paperCard, primaryButton, secondaryButtonSmall, textInput } from '../styles/styleKit'
import SaveButton from '../components/SaveButton'

export default function DocuMaker(): React.JSX.Element {
  const fields = useAppStore((s) => s.fields.docu)
  const setDocuField = useAppStore((s) => s.setDocuField)
  const requireHf = useAppStore((s) => s.requireHf)
  const goCreate = useAppStore((s) => s.goCreate)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [video, setVideo] = useState<File | null>(null)
  const [result, setResult] = useState<GenerateDocuResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleGenerate(): Promise<void> {
    if (!requireHf()) return
    if (!video) {
      setError('Upload a screen recording first — DocuMaker writes docs from what happens in the video.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const res = await generateDocu(fields, video)
      setResult(res)
      void refreshLibrary()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '22px 34px 60px' }}>
      <ScreenBackdrop video="docu" />
      <div style={{ font: "700 13px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer', marginBottom: 14 }} onClick={goCreate}>
        ← Create
      </div>
      <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
        <div
          style={{
            width: 330,
            flexShrink: 0,
            background: 'var(--surface-tint)',
            border: '2.5px solid var(--border)',
            borderRadius: 22,
            padding: 24,
            boxShadow: 'var(--shadow-md)',
            display: 'flex',
            flexDirection: 'column',
            gap: 16
          }}
        >
          <div>
            <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)' }}>DocuMaker</div>
            <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 2 }}>
              Upload a screen recording — we transcribe it, write the guide, and match screenshots to each step.
            </div>
          </div>

          <div>
            <label style={label}>Screen recording</label>
            <input
              ref={fileInputRef}
              type="file"
              accept="video/*"
              style={{ display: 'none' }}
              onChange={(e) => setVideo(e.target.files?.[0] ?? null)}
            />
            <div
              style={{
                border: '2px dashed var(--border)',
                borderRadius: 14,
                padding: '16px 14px',
                textAlign: 'center',
                font: "700 13px 'Quicksand'",
                color: video ? 'var(--ink)' : 'var(--ink-placeholder)',
                cursor: 'pointer',
                background: 'var(--surface)'
              }}
              onClick={() => fileInputRef.current?.click()}
            >
              {video ? video.name : 'Click to choose a video file…'}
            </div>
          </div>

          <div>
            <label style={label}>Product / feature (optional)</label>
            <input value={fields.product} onChange={(e) => setDocuField('product', e.target.value)} placeholder="e.g. Payments API" style={textInput} />
          </div>

          <div style={{ ...primaryButton, opacity: loading ? 0.6 : 1 }} onClick={loading ? undefined : handleGenerate}>
            {loading ? 'Generating…' : 'Generate docs'}
          </div>
          {loading && (
            <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)' }}>
              Transcribing, writing the guide, and matching screenshots — this can take a few minutes, longer for
              longer recordings.
            </div>
          )}
          {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}
        </div>

        <div style={{ flex: 1 }}>
          {result && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, alignItems: 'center' }}>
                <SaveButton
                  libraryId={result.libraryId}
                  tool="Docs"
                  title={result.title}
                  subtitle="Documentation"
                  content={result.intro}
                />
                <div
                  style={result.docxPath ? secondaryButtonSmall : { ...secondaryButtonSmall, opacity: 0.5, cursor: 'default' }}
                  onClick={() => result.docxPath && window.api.openFile(result.docxPath)}
                >
                  Export .docx
                </div>
              </div>
              <div style={{ ...paperCard, padding: '34px 40px' }}>
                <div style={{ font: "700 11.5px 'Quicksand'", letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--ink-placeholder)' }}>
                  Documentation
                </div>
                <h1 style={{ font: "700 30px 'Kalam'", color: 'var(--ink)', margin: '8px 0 0' }}>{result.title}</h1>
                <p style={{ font: "600 15px/1.7 'Quicksand'", color: 'var(--ink-body)', margin: '14px 0 0' }}>{result.intro}</p>
                {result.prerequisites.length > 0 && (
                  <>
                    <h2 style={{ font: "700 20px 'Kalam'", color: 'var(--ink)', margin: '26px 0 8px' }}>Prerequisites</h2>
                    <ul style={{ margin: 0, paddingLeft: 20, font: "600 15px/1.7 'Quicksand'", color: 'var(--ink-body)' }}>
                      {result.prerequisites.map((p) => (
                        <li key={p}>{p}</li>
                      ))}
                    </ul>
                  </>
                )}
                {result.steps.map((step, i) => (
                  <div key={step.heading}>
                    <h2 style={{ font: "700 20px 'Kalam'", color: 'var(--ink)', margin: '26px 0 0' }}>
                      Step {i + 1}: {step.heading}
                    </h2>
                    <p style={{ font: "600 15px/1.7 'Quicksand'", color: 'var(--ink-body)', margin: '8px 0 0' }}>{step.text}</p>
                    {step.imageUrl && (
                      <figure style={{ margin: '12px 0 0', textAlign: 'center' }}>
                        <img
                          src={`${backendUrl}${step.imageUrl}`}
                          alt={step.caption ?? step.heading}
                          style={{ maxWidth: '100%', borderRadius: 12, border: '2px solid var(--border-paper)', display: 'inline-block' }}
                        />
                        {step.caption && (
                          <figcaption style={{ font: "600 12px/1.4 'Quicksand'", color: 'var(--ink-faint)', marginTop: 6 }}>
                            {step.caption}
                          </figcaption>
                        )}
                      </figure>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
