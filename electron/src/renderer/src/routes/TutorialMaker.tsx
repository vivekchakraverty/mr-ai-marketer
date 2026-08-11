import { useState } from 'react'
import { generateTutorial, type GenerateTutorialResponse } from '../api/client'
import BackendImage from '../components/BackendImage'
import { refreshLibrary } from '../state/actions'
import { useAppStore } from '../state/store'
import ScreenBackdrop from '../components/ScreenBackdrop'
import { label, paperCard, primaryButtonSmall, primaryButton, secondaryButtonSmall, select, textInput, textarea } from '../styles/styleKit'
import SaveButton from '../components/SaveButton'

const SHOT_OPTIONS = [4, 6, 8, 10]

export default function TutorialMaker(): React.JSX.Element {
  const fields = useAppStore((s) => s.fields.tutorial)
  const setTutorialField = useAppStore((s) => s.setTutorialField)
  const requireHf = useAppStore((s) => s.requireHf)
  const goCreate = useAppStore((s) => s.goCreate)

  const [result, setResult] = useState<GenerateTutorialResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleGenerate(): Promise<void> {
    if (!requireHf()) return
    if (!fields.topic.trim()) return
    setLoading(true)
    setError('')
    try {
      const res = await generateTutorial(fields)
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
      <ScreenBackdrop video="tutorial" />
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
            <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)' }}>Tutorial Maker</div>
            <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 2 }}>
              Give a topic — we find the best video and turn it into a written tutorial.
            </div>
          </div>

          <div>
            <label style={label}>Tutorial topic</label>
            <input
              value={fields.topic}
              onChange={(e) => setTutorialField('topic', e.target.value)}
              placeholder="e.g. Connect your store to the API"
              style={textInput}
            />
          </div>
          <div>
            <label style={label}>Primary keyword</label>
            <input
              value={fields.primaryKeyword}
              onChange={(e) => setTutorialField('primaryKeyword', e.target.value)}
              placeholder="Optional"
              style={textInput}
            />
          </div>
          <div>
            <label style={label}>Secondary keyword</label>
            <input
              value={fields.secondaryKeyword}
              onChange={(e) => setTutorialField('secondaryKeyword', e.target.value)}
              placeholder="Optional"
              style={textInput}
            />
          </div>
          <div>
            <label style={label}>Content brief</label>
            <textarea
              value={fields.contentBrief}
              onChange={(e) => setTutorialField('contentBrief', e.target.value)}
              placeholder="Anything the tutorial must cover."
              rows={3}
              style={textarea}
            />
          </div>
          <div>
            <label style={label}>Max screenshots</label>
            <select value={fields.maxScreenshots} onChange={(e) => setTutorialField('maxScreenshots', Number(e.target.value))} style={select}>
              {SHOT_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {o} screenshots
                </option>
              ))}
            </select>
          </div>

          <div style={{ ...primaryButton, opacity: loading ? 0.6 : 1 }} onClick={loading ? undefined : handleGenerate}>
            {loading ? 'Generating…' : 'Generate tutorial'}
          </div>
          {loading && (
            <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)' }}>
              Finding a video, transcribing, writing, and capturing screenshots — this can take a few minutes.
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
                  tool="Tutorial"
                  title={result.title}
                  subtitle="Tutorial"
                  content={result.intro}
                />
                <div
                  style={result.docxPath ? secondaryButtonSmall : { ...secondaryButtonSmall, opacity: 0.5, cursor: 'default' }}
                  onClick={() => result.docxPath && window.api.openFile(result.docxPath)}
                >
                  Export .docx
                </div>
                {result.sourceUrl && (
                  <div style={primaryButtonSmall} onClick={() => window.open(result.sourceUrl as string, '_blank')}>
                    View source video →
                  </div>
                )}
              </div>
              <div style={{ ...paperCard, padding: '38px 44px' }}>
                <div style={{ font: "700 11.5px 'Quicksand'", letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--ink-placeholder)' }}>
                  Tutorial
                </div>
                <h1 style={{ font: "700 30px/1.2 'Kalam'", color: 'var(--ink)', margin: '10px 0 0' }}>{result.title}</h1>
                {result.answer && (
                  <div style={{ background: 'var(--tip-bg)', border: '2px solid var(--border)', borderRadius: 14, padding: '14px 16px', margin: '16px 0 0' }}>
                    <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--tip-ink)' }}>{result.answer}</div>
                  </div>
                )}
                {result.sentimentNote && (
                  <div
                    style={{
                      border: '2px dashed var(--border)',
                      borderRadius: 14,
                      padding: '11px 14px',
                      margin: '14px 0 0',
                      font: "600 12.5px/1.6 'Quicksand'",
                      color: 'var(--ink-muted)'
                    }}
                  >
                    {result.sentimentNote}
                  </div>
                )}
                <p style={{ font: "600 15px/1.7 'Quicksand'", color: 'var(--ink-body)', margin: '14px 0 22px' }}>{result.intro}</p>
                {result.steps.map((step, i) => (
                  <div key={step.heading} style={{ display: 'flex', gap: 16, marginBottom: 20 }}>
                    <div
                      style={{
                        width: 34,
                        height: 34,
                        borderRadius: '50%',
                        background: 'var(--accent)',
                        border: '2px solid var(--border)',
                        color: 'var(--accent-ink)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        font: "700 15px 'Kalam'",
                        flexShrink: 0
                      }}
                    >
                      {i + 1}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>{step.heading}</div>
                      <div style={{ font: "600 14px/1.65 'Quicksand'", color: 'var(--ink-body)', marginTop: 4 }}>{step.body}</div>
                      {step.imageUrl && (
                        <figure style={{ margin: '12px 0 0' }}>
                          <BackendImage
                            url={step.imageUrl}
                            alt={step.caption ?? step.heading}
                            style={{ maxWidth: '100%', borderRadius: 12, border: '2px solid var(--border-paper)', display: 'block' }}
                          />
                          {step.caption && (
                            <figcaption style={{ font: "600 12px/1.4 'Quicksand'", color: 'var(--ink-faint)', marginTop: 6 }}>
                              {step.caption}
                            </figcaption>
                          )}
                        </figure>
                      )}
                    </div>
                  </div>
                ))}
                {result.faqs.length > 0 && (
                  <div style={{ marginTop: 24, paddingTop: 20, borderTop: '2px dashed var(--border-soft)' }}>
                    <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)', marginBottom: 10 }}>FAQ</div>
                    {result.faqs.map((f) => (
                      <div key={f.q} style={{ marginBottom: 12 }}>
                        <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>{f.q}</div>
                        <div style={{ font: "600 13.5px/1.6 'Quicksand'", color: 'var(--ink-body)', marginTop: 3 }}>{f.a}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
