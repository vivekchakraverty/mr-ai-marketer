import { useState } from 'react'
import { backendUrl, generateBlog, type GenerateBlogResponse } from '../api/client'
import { refreshLibrary } from '../state/actions'
import { useAppStore } from '../state/store'
import { BLOG_GOAL_OPTIONS } from '../state/types'
import SegmentedControl from '../components/SegmentedControl'
import MarkdownPanel from '../components/MarkdownPanel'
import SendToDistributionModal from '../components/SendToDistributionModal'
import { label, paperCard, pill, primaryButton, primaryButtonSmall, secondaryButtonSmall, textInput, textarea } from '../styles/styleKit'

export default function BlogWriter(): React.JSX.Element {
  const fields = useAppStore((s) => s.fields.blog)
  const setBlogField = useAppStore((s) => s.setBlogField)
  const requireHf = useAppStore((s) => s.requireHf)
  const goCreate = useAppStore((s) => s.goCreate)

  const [result, setResult] = useState<GenerateBlogResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showSend, setShowSend] = useState(false)

  async function handleGenerate(): Promise<void> {
    if (!requireHf()) return
    if (!fields.topic.trim()) {
      setError('Topic is required.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const res = await generateBlog(fields)
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
            <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)' }}>Blog Writer</div>
            <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 2 }}>Fill the brief, then generate.</div>
          </div>

          <div>
            <label style={label}>Topic</label>
            <input value={fields.topic} onChange={(e) => setBlogField('topic', e.target.value)} placeholder="What's the article about?" style={textInput} />
          </div>
          <div>
            <label style={label}>Primary keyword</label>
            <input
              value={fields.primaryKeyword}
              onChange={(e) => setBlogField('primaryKeyword', e.target.value)}
              placeholder="e.g. saas pricing strategy"
              style={textInput}
            />
          </div>
          <div>
            <label style={label}>Secondary keyword</label>
            <input
              value={fields.secondaryKeyword}
              onChange={(e) => setBlogField('secondaryKeyword', e.target.value)}
              placeholder="Optional"
              style={textInput}
            />
          </div>
          <div>
            <label style={label}>Brief</label>
            <textarea
              value={fields.brief}
              onChange={(e) => setBlogField('brief', e.target.value)}
              placeholder="Anything the article must cover."
              rows={3}
              style={textarea}
            />
          </div>
          <div>
            <label style={label}>Content goal</label>
            <SegmentedControl options={BLOG_GOAL_OPTIONS} value={fields.contentGoal} onChange={(v) => setBlogField('contentGoal', v)} />
          </div>
          <div>
            <label style={label}>Target word count</label>
            <input
              type="number"
              min={300}
              max={5000}
              step={100}
              value={fields.targetWordCount}
              onChange={(e) => setBlogField('targetWordCount', Number(e.target.value))}
              style={textInput}
            />
          </div>

          <div style={{ ...primaryButton, opacity: loading ? 0.6 : 1 }} onClick={loading ? undefined : handleGenerate}>
            {loading ? 'Generating…' : 'Generate article'}
          </div>
          {loading && (
            <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)' }}>
              Researching the web, writing, and generating illustrations — this can take a few minutes.
            </div>
          )}
          {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}
        </div>

        <div style={{ flex: 1 }}>
          {result && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
                <div style={{ display: 'flex', gap: 8 }}>
                  <span style={pill}>{fields.targetWordCount.toLocaleString()} words (target)</span>
                  <span style={pill}>{fields.contentGoal}</span>
                </div>
                <div style={{ display: 'flex', gap: 10 }}>
                  <div
                    style={result.docxPath ? secondaryButtonSmall : { ...secondaryButtonSmall, opacity: 0.5, cursor: 'default' }}
                    onClick={() => result.docxPath && window.api.openFile(result.docxPath)}
                  >
                    Export .docx
                  </div>
                  <div style={primaryButtonSmall} onClick={() => setShowSend(true)}>
                    Send to Distribution →
                  </div>
                </div>
              </div>
              <div style={{ ...paperCard, padding: '38px 44px' }}>
                <div style={{ font: "700 11.5px 'Quicksand'", letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--ink-placeholder)' }}>
                  Draft · Blog article
                </div>
                <h1 style={{ font: "700 32px/1.25 'Kalam'", color: 'var(--ink)', margin: '12px 0 0' }}>{result.title}</h1>
                <MarkdownPanel markdown={result.markdown} />
                {result.images.length > 0 && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12, marginTop: 24 }}>
                    {result.images.map((img, i) => (
                      <figure key={img.url} style={{ margin: 0 }}>
                        <img
                          src={`${backendUrl}${img.url}`}
                          alt={img.caption ?? `Illustration ${i + 1}`}
                          style={{ width: '100%', borderRadius: 12, border: '2px solid var(--border-paper)', display: 'block' }}
                        />
                        {img.caption && (
                          <figcaption style={{ font: "600 12px/1.4 'Quicksand'", color: 'var(--ink-faint)', marginTop: 6 }}>
                            {img.caption}
                          </figcaption>
                        )}
                      </figure>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
      {showSend && result && (
        <SendToDistributionModal
          libraryItemId={result.libraryId}
          title={result.title}
          defaultText={result.markdown}
          onClose={() => setShowSend(false)}
        />
      )}
    </div>
  )
}
