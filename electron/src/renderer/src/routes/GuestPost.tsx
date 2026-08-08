import { useState } from 'react'
import { analyzeGuestPost, searchGuestPosts, type AnalyzeGuestResponse, type GuestSite } from '../api/client'
import { refreshLibrary } from '../state/actions'
import { useAppStore } from '../state/store'
import ScreenBackdrop from '../components/ScreenBackdrop'
import { label, primaryButtonSmall, secondaryButtonSmall, select, textInput } from '../styles/styleKit'
import SaveButton from '../components/SaveButton'

const TIER_LABELS: Record<string, string> = {
  rss: 'RSS feed',
  'wp-json': 'WordPress API',
  sitemap: 'sitemap',
  'keyword-listing': 'blog listing page',
  'llm-html': 'AI page read',
  none: 'nothing found'
}

const AUTHORITY_OPTIONS = [
  { label: 'Any DA', value: 0 },
  { label: 'DA 2+', value: 2 },
  { label: 'DA 3+', value: 3 },
  { label: 'DA 4+', value: 4 },
  { label: 'DA 5+', value: 5 }
]
const RESULT_OPTIONS = [10, 25, 50]

export default function GuestPost(): React.JSX.Element {
  const fields = useAppStore((s) => s.fields.guest)
  const setGuestField = useAppStore((s) => s.setGuestField)
  const requireHf = useAppStore((s) => s.requireHf)
  const goCreate = useAppStore((s) => s.goCreate)

  const [sites, setSites] = useState<GuestSite[] | null>(null)
  // The search saves itself to the Library; hold the id so the Save control can say so.
  const [libraryId, setLibraryId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [analyzed, setAnalyzed] = useState<{ domain: string; data: AnalyzeGuestResponse } | null>(null)
  const [analyzing, setAnalyzing] = useState<string | null>(null)

  async function handleSearch(): Promise<void> {
    if (!fields.topic.trim()) return
    setLoading(true)
    setError('')
    setAnalyzed(null)
    try {
      const res = await searchGuestPosts(fields)
      setSites(res.sites)
      setLibraryId(res.libraryId)
      void refreshLibrary()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  async function handleAnalyze(domain: string): Promise<void> {
    if (!requireHf()) return
    setAnalyzing(domain)
    try {
      const data = await analyzeGuestPost(domain, fields.topic)
      setAnalyzed({ domain, data })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setAnalyzing(null)
    }
  }

  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '22px 34px 60px' }}>
      <ScreenBackdrop video="guest" />
      <div style={{ font: "700 13px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer', marginBottom: 14 }} onClick={goCreate}>
        ← Create
      </div>
      <div style={{ marginBottom: 20 }}>
        <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)' }}>Guest Post Suggester</div>
        <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
          Strangers on the internet willing to publish your genius — found live, ranked by authority. Analyze any
          site to pull its submission details.
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
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 240 }}>
            <label style={label}>Topic / niche</label>
            <input
              value={fields.topic}
              onChange={(e) => setGuestField('topic', e.target.value)}
              placeholder="e.g. fintech, developer tools, wellness"
              style={{ ...textInput, padding: '12px 14px', font: "600 15px 'Quicksand'" }}
            />
          </div>
          <div style={{ width: 130 }}>
            <label style={label}>Min authority</label>
            <select
              value={fields.minAuthority}
              onChange={(e) => setGuestField('minAuthority', Number(e.target.value))}
              style={select}
            >
              {AUTHORITY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div style={{ width: 130 }}>
            <label style={label}>Results</label>
            <select value={fields.maxResults} onChange={(e) => setGuestField('maxResults', Number(e.target.value))} style={select}>
              {RESULT_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {o} results
                </option>
              ))}
            </select>
          </div>
          <div style={{ ...primaryButtonSmall, padding: '12px 22px', whiteSpace: 'nowrap', opacity: loading ? 0.6 : 1 }} onClick={loading ? undefined : handleSearch}>
            {loading ? 'Searching…' : 'Find sites'}
          </div>
        </div>
        <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)' }}>
          Search is free and local. Analyzing a site calls your connected Hugging Face account.
        </div>
        {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}
      </div>

      {sites && (
        <div style={{ marginTop: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
            <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>
              Sites for “{fields.topic}” · ranked by authority
            </div>
            <SaveButton
              libraryId={libraryId}
              tool="Guest"
              title={`“${fields.topic}” guest-post targets`}
              subtitle="Guest research"
              content={sites.map((s) => `${s.domain} · PR ${s.page_rank} · ${s.guest_posts_url}`).join('\n')}
            />
          </div>
          <div style={{ background: 'var(--surface)', border: '2.5px solid var(--border)', borderRadius: 20, boxShadow: 'var(--shadow-md)', overflow: 'hidden' }}>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '2.2fr .7fr 1.6fr .9fr',
                gap: 12,
                padding: '13px 22px',
                borderBottom: '2px solid var(--border-tint-2)',
                font: "700 10.5px 'Quicksand'",
                letterSpacing: '.1em',
                textTransform: 'uppercase',
                color: 'var(--ink-faint)'
              }}
            >
              <span>Website</span>
              <span>DA</span>
              <span>Niche</span>
              <span></span>
            </div>
            {sites.length === 0 && (
              <div style={{ padding: '20px 22px', font: "600 13px 'Quicksand'", color: 'var(--ink-faint)' }}>
                No sites matched that authority threshold.
              </div>
            )}
            {sites.map((site, i) => (
              <div
                key={site.domain}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '2.2fr .7fr 1.6fr .9fr',
                  gap: 12,
                  padding: '15px 22px',
                  borderBottom: i === sites.length - 1 ? 'none' : '2px dashed var(--border-soft)',
                  alignItems: 'center'
                }}
              >
                <div>
                  <div style={{ font: "700 15px 'Kalam'", color: 'var(--ink)' }}>{site.domain}</div>
                  <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>{site.title}</div>
                </div>
                <span style={{ font: "700 14px 'Quicksand'", color: 'var(--accent)' }}>{site.page_rank.toFixed(1)}</span>
                <span style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-body)' }}>{site.niche}</span>
                <div
                  style={
                    analyzed?.domain === site.domain
                      ? { ...secondaryButtonSmall, textAlign: 'center', padding: '7px 0' }
                      : {
                          background: 'var(--accent)',
                          color: 'var(--accent-ink)',
                          border: '2px solid var(--border)',
                          borderRadius: 999,
                          padding: '7px 0',
                          textAlign: 'center',
                          font: "700 12px 'Quicksand'",
                          cursor: 'pointer'
                        }
                  }
                  onClick={() => handleAnalyze(site.domain)}
                >
                  {analyzing === site.domain ? 'Analyzing…' : 'Analyze'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {analyzed && (
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
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--accent)' }}>
                Analysis · {analyzed.domain}
              </div>
              <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)', marginTop: 4 }}>Content gap analysis</div>
            </div>
            <div style={{ font: "700 13px 'Quicksand'", color: 'var(--ink-muted)', cursor: 'pointer' }} onClick={() => setAnalyzed(null)}>
              Close ×
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14, marginTop: 18 }}>
            {[
              { label: 'Contact', value: analyzed.data.contactUrl },
              { label: 'Guest post page', value: analyzed.data.guestPostsUrl },
              { label: 'Existing posts found', value: `${analyzed.data.titleCount} · via ${TIER_LABELS[analyzed.data.tierUsed] ?? analyzed.data.tierUsed}` }
            ].map((f) => (
              <div key={f.label} style={{ background: 'var(--surface)', border: '2px solid var(--border-tint-2)', borderRadius: 14, padding: 16, overflow: 'hidden' }}>
                <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.08em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
                  {f.label}
                </div>
                <div style={{ font: "700 14px 'Quicksand'", color: 'var(--ink)', marginTop: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {f.value}
                </div>
              </div>
            ))}
          </div>

          {analyzed.data.sampleTitles.length > 0 && (
            <div style={{ marginTop: 18 }}>
              <div style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)', marginBottom: 8 }}>Their recent posts</div>
              <ul style={{ margin: 0, paddingLeft: 20, font: "600 13.5px/1.7 'Quicksand'", color: 'var(--ink-body)' }}>
                {analyzed.data.sampleTitles.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </div>
          )}

          {analyzed.data.suggestions.length > 0 ? (
            <div style={{ marginTop: 16 }}>
              <div style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)', marginBottom: 8 }}>Pitch ideas that fill the gap</div>
              <ul style={{ margin: 0, paddingLeft: 20, font: "600 13.5px/1.7 'Quicksand'", color: 'var(--ink-body)' }}>
                {analyzed.data.suggestions.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </div>
          ) : (
            <div style={{ font: "600 13.5px/1.7 'Quicksand'", color: 'var(--ink-body)', marginTop: 16 }}>
              Couldn't find this site's existing posts automatically — check their guest-post page directly for
              submission guidelines.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
