import { useEffect, useState } from 'react'
import { listSocialNiches, suggestSocialTopics, type TopicsResponse } from '../api/client'
import { useAppStore } from '../state/store'
import { card, label, primaryButtonSmall, sectionEyebrow, select } from '../styles/styleKit'

/**
 * Post Topic Finder — lives in Research / Strategy because deciding what is worth
 * saying is research, not writing. Picking a suggestion hands it to the Social
 * Post composer over in Create, which is where the writing happens.
 */
export default function TopicFinder(): React.JSX.Element {
  const setSocialField = useAppStore((s) => s.setSocialField)
  const openTool = useAppStore((s) => s.openTool)

  const [niches, setNiches] = useState<string[]>([])
  const [niche, setNiche] = useState('')
  const [topics, setTopics] = useState<TopicsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    listSocialNiches()
      .then((rows) => {
        const active = rows.filter((r) => r.active).map((r) => r.name)
        setNiches(active)
        setNiche((cur) => cur || active[0] || '')
      })
      .catch(() => setNiches([]))
  }, [])

  async function handleFind(): Promise<void> {
    if (!niche) return
    setLoading(true)
    setError('')
    try {
      setTopics(await suggestSocialTopics(niche))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  function useTopic(topic: string): void {
    setSocialField('userInput', topic)
    setSocialField('niche', niche)
    openTool('social')
  }

  return (
    <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
      <div style={{ width: 400, flexShrink: 0, ...card, display: 'flex', flexDirection: 'column', gap: 17 }}>
        <div style={sectionEyebrow}>Which corner of the internet?</div>

        <div>
          <label style={label}>Niche</label>
          {niches.length > 0 ? (
            <select value={niche} onChange={(e) => setNiche(e.target.value)} style={select}>
              {niches.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          ) : (
            <div style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-muted)' }}>
              No niches yet. Add one in Create → Social Post Generator → Manage niches, and
              collect some posts first.
            </div>
          )}
        </div>

        <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)' }}>
          Reads what your niche actually talked about in the last 48 hours, then checks Google
          Trends, Google News, Wikipedia and Hacker News — using your niche&apos;s own keywords,
          not whatever is globally on fire.
        </div>

        <div
          style={{ ...primaryButtonSmall, opacity: loading || !niche ? 0.6 : 1, alignSelf: 'flex-start' }}
          onClick={loading || !niche ? undefined : handleFind}
        >
          {loading ? 'Reading the room…' : 'Find me something to say'}
        </div>

        {error && (
          <div style={{ font: "700 13px 'Quicksand'", color: 'var(--danger-ink)' }}>{error}</div>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {!topics && !loading && (
          <div
            style={{
              ...card,
              font: "600 14px/1.7 'Quicksand'",
              color: 'var(--ink-muted)',
              textAlign: 'center',
              padding: '52px 30px'
            }}
          >
            <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)', marginBottom: 6 }}>
              Nothing suggested yet
            </div>
            Pick a niche and hit the button. Every idea comes with its receipts.
          </div>
        )}

        {topics && (
          <div
            style={{
              background: 'var(--tip-bg)',
              border: '2.5px solid var(--border)',
              borderRadius: 20,
              padding: 18,
              boxShadow: 'var(--shadow-md)'
            }}
          >
            <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>
              Things worth posting about in {niche}
            </div>
            <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', margin: '4px 0 12px' }}>
              Grounded in {topics.corpusPosts} recent posts plus live trend signals for your
              keywords. Click one to take it to the composer.
            </div>

            {topics.note && (
              <div style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-muted)' }}>{topics.note}</div>
            )}

            {topics.suggestions.map((s, i) => (
              <div
                key={i}
                onClick={() => useTopic(s.topic)}
                style={{
                  background: 'var(--surface)',
                  border: '2px solid var(--border)',
                  borderRadius: 14,
                  padding: '11px 14px',
                  marginBottom: 9,
                  cursor: 'pointer'
                }}
              >
                <div style={{ font: "700 14px 'Quicksand'", color: 'var(--ink)' }}>{s.topic}</div>
                {s.whyNow && (
                  <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 3 }}>
                    {s.whyNow}
                  </div>
                )}
                {s.sources.length > 0 && (
                  <div style={{ font: "700 10.5px 'Quicksand'", color: 'var(--accent-deep)', marginTop: 5 }}>
                    {s.sources.join(' · ').replace(/_/g, ' ')}
                  </div>
                )}
              </div>
            ))}

            {topics.signals.length > 0 && (
              <details style={{ marginTop: 4 }}>
                <summary style={{ font: "700 12px 'Quicksand'", color: 'var(--ink-muted)', cursor: 'pointer' }}>
                  The evidence behind these ({topics.signals.length} signals)
                </summary>
                <div style={{ marginTop: 8 }}>
                  {topics.signals.map((sig, i) => (
                    <div key={i} style={{ font: "600 12px/1.6 'Quicksand'", color: 'var(--ink-muted)' }}>
                      • {sig}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
