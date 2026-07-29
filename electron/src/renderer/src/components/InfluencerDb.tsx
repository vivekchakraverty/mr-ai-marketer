import { useEffect, useRef, useState } from 'react'
import {
  exportInfluencers,
  getInfluencerFacets,
  searchInfluencers,
  type InfluencerFacets,
  type InfluencerQuery,
  type InfluencerSearchResponse
} from '../api/client'
import StatTile from './StatTile'
import Toggle from './Toggle'
import { card, chip, label, pill, primaryButtonSmall, secondaryButtonSmall, select, tag, textInput } from '../styles/styleKit'

const DEFAULT_QUERY: InfluencerQuery = {
  query: '',
  niches: [],
  followerMin: null,
  followerMax: null,
  postsMin: null,
  postsMax: null,
  verifiedOnly: false,
  excludePrivate: false,
  withContactOnly: false,
  // Roughly a third of the catalogue was never enriched, so it has no follower or
  // post counts at all. Those rows can't answer the questions this screen exists to
  // answer, so they start hidden — the toggle brings them back.
  withStatsOnly: true,
  sort: 'followers_desc',
  page: 1,
  pageSize: 50
}

/** The tiers marketers actually shop by, so the common case is one click rather
 * than two typed numbers. `null` on either end means "unbounded". */
const FOLLOWER_TIERS: { key: string; label: string; min: number | null; max: number | null }[] = [
  { key: 'any', label: 'Any size', min: null, max: null },
  { key: 'nano', label: 'Nano · <10K', min: null, max: 10_000 },
  { key: 'micro', label: 'Micro · 10K–100K', min: 10_000, max: 100_000 },
  { key: 'mid', label: 'Mid · 100K–500K', min: 100_000, max: 500_000 },
  { key: 'macro', label: 'Macro · 500K–1M', min: 500_000, max: 1_000_000 },
  { key: 'mega', label: 'Mega · 1M+', min: 1_000_000, max: null }
]

const POST_TIERS: { key: string; label: string; min: number | null }[] = [
  { key: 'any', label: 'Any', min: null },
  { key: '100', label: '100+', min: 100 },
  { key: '500', label: '500+', min: 500 },
  { key: '1000', label: '1,000+', min: 1000 }
]

const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: 'followers_desc', label: 'Most followers' },
  { value: 'followers_asc', label: 'Fewest followers' },
  { value: 'posts_desc', label: 'Most posts' },
  { value: 'posts_asc', label: 'Fewest posts' },
  { value: 'lastpost_desc', label: 'Most recently active' },
  { value: 'name_asc', label: 'Name (A–Z)' }
]

const PAGE_SIZES = [25, 50, 100, 200]

type BoolFilter = 'verifiedOnly' | 'excludePrivate' | 'withContactOnly' | 'withStatsOnly'

const TOGGLES: { key: BoolFilter; text: string }[] = [
  { key: 'verifiedOnly', text: 'Verified accounts only' },
  { key: 'excludePrivate', text: 'Hide private accounts' },
  { key: 'withContactOnly', text: 'Has email or phone' },
  { key: 'withStatsOnly', text: 'Only profiles with stats' }
]

function compact(n: number | null): string {
  if (n === null || n === undefined) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 100_000 ? 0 : 1)}K`
  return String(n)
}

function full(n: number): string {
  return n.toLocaleString('en-US')
}

const cellHead: React.CSSProperties = {
  font: "700 10.5px 'Quicksand'",
  letterSpacing: '.09em',
  textTransform: 'uppercase',
  color: 'var(--ink-faint)',
  textAlign: 'left',
  padding: '0 10px 10px',
  whiteSpace: 'nowrap'
}

const cell: React.CSSProperties = {
  font: "600 13px 'Quicksand'",
  color: 'var(--ink)',
  padding: '11px 10px',
  borderTop: '2px dashed var(--border-soft)',
  verticalAlign: 'top'
}

const numberInput: React.CSSProperties = { ...textInput, padding: '9px 11px', font: "600 13px 'Quicksand'" }

const rowLabel: React.CSSProperties = { font: "700 12.5px 'Quicksand'", color: 'var(--ink-muted)' }

/**
 * Influencer Database — a filterable view over the ~14K-profile Instagram
 * catalogue bundled with the app. All filtering happens server-side (see
 * backend/app/routers/influencer_db.py) so the renderer never holds the full set.
 */
export default function InfluencerDb(): React.JSX.Element {
  const [facets, setFacets] = useState<InfluencerFacets | null>(null)
  const [query, setQuery] = useState<InfluencerQuery>(DEFAULT_QUERY)
  const [searchText, setSearchText] = useState('')
  const [result, setResult] = useState<InfluencerSearchResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [exportNote, setExportNote] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  // Guards against a slow early response overwriting a newer one — the first
  // request also pays for the backend parsing the xlsx, so it can land last.
  const requestId = useRef(0)

  useEffect(() => {
    getInfluencerFacets()
      .then(setFacets)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  // Typing shouldn't fire a request per keystroke; every other filter should apply
  // the moment it's clicked, so only the text box is debounced.
  useEffect(() => {
    const timer = setTimeout(() => patch({ query: searchText }), 300)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchText])

  useEffect(() => {
    const id = ++requestId.current
    setLoading(true)
    searchInfluencers(query)
      .then((res) => {
        if (id !== requestId.current) return
        setResult(res)
        setError('')
      })
      .catch((err) => {
        if (id !== requestId.current) return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (id === requestId.current) setLoading(false)
      })
  }, [query])

  /** Any filter change resets to page 1 — page is only kept when it's what changed. */
  function patch(next: Partial<InfluencerQuery>): void {
    setQuery((q) => {
      const merged = { ...q, ...next, page: next.page ?? 1 }
      return JSON.stringify(merged) === JSON.stringify(q) ? q : merged
    })
  }

  function toggleNiche(value: string): void {
    patch({ niches: query.niches.includes(value) ? query.niches.filter((n) => n !== value) : [...query.niches, value] })
  }

  function reset(): void {
    setSearchText('')
    setQuery(DEFAULT_QUERY)
    setExportNote('')
  }

  async function handleExport(): Promise<void> {
    setExporting(true)
    setExportNote('')
    try {
      const res = await exportInfluencers(query)
      setExportNote(`Saved ${full(res.count)} profiles to ${res.filename}`)
      await window.api.openFile(res.path)
    } catch (err) {
      setExportNote(err instanceof Error ? err.message : String(err))
    } finally {
      setExporting(false)
    }
  }

  const activeTier =
    FOLLOWER_TIERS.find((t) => t.min === query.followerMin && t.max === query.followerMax)?.key ?? 'custom'
  const activePostTier = POST_TIERS.find((t) => t.min === query.postsMin)?.key ?? 'custom'
  const pageCount = result ? Math.max(1, Math.ceil(result.total / result.pageSize)) : 1
  const filtersActive = JSON.stringify({ ...query, page: 1, pageSize: 0 }) !== JSON.stringify({ ...DEFAULT_QUERY, pageSize: 0 })

  return (
    <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
      <div style={{ width: 330, flexShrink: 0, ...card, display: 'flex', flexDirection: 'column', gap: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <label style={{ ...label, marginBottom: 0 }}>Filters</label>
          {filtersActive && (
            <span style={{ font: "700 12px 'Quicksand'", color: 'var(--ink-faint)', cursor: 'pointer' }} onClick={reset}>
              Reset
            </span>
          )}
        </div>

        <div>
          <label style={label}>Search</label>
          <input
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder="Name, handle or bio keyword"
            style={textInput}
          />
        </div>

        <div>
          <label style={label}>Niche{query.niches.length > 0 ? ` · ${query.niches.length} selected` : ''}</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, maxHeight: 232, overflowY: 'auto' }}>
            {(facets?.niches ?? []).map((n) => (
              <div key={n.value} style={chip(query.niches.includes(n.value))} onClick={() => toggleNiche(n.value)}>
                {n.value}
                <span style={{ opacity: 0.65, marginLeft: 6 }}>{full(n.count)}</span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <label style={label}>Follower count</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
            {FOLLOWER_TIERS.map((t) => (
              <div
                key={t.key}
                style={chip(activeTier === t.key)}
                onClick={() => patch({ followerMin: t.min, followerMax: t.max })}
              >
                {t.label}
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 9, alignItems: 'center' }}>
            <input
              type="number"
              min={0}
              value={query.followerMin ?? ''}
              onChange={(e) => patch({ followerMin: e.target.value === '' ? null : Number(e.target.value) })}
              placeholder="Min"
              style={numberInput}
            />
            <span style={rowLabel}>to</span>
            <input
              type="number"
              min={0}
              value={query.followerMax ?? ''}
              onChange={(e) => patch({ followerMax: e.target.value === '' ? null : Number(e.target.value) })}
              placeholder="Max"
              style={numberInput}
            />
          </div>
        </div>

        <div>
          <label style={label}>Post count</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
            {POST_TIERS.map((t) => (
              <div key={t.key} style={chip(activePostTier === t.key && query.postsMax === null)} onClick={() => patch({ postsMin: t.min, postsMax: null })}>
                {t.label}
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 9, alignItems: 'center' }}>
            <input
              type="number"
              min={0}
              value={query.postsMin ?? ''}
              onChange={(e) => patch({ postsMin: e.target.value === '' ? null : Number(e.target.value) })}
              placeholder="Min"
              style={numberInput}
            />
            <span style={rowLabel}>to</span>
            <input
              type="number"
              min={0}
              value={query.postsMax ?? ''}
              onChange={(e) => patch({ postsMax: e.target.value === '' ? null : Number(e.target.value) })}
              placeholder="Max"
              style={numberInput}
            />
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 11, paddingTop: 2 }}>
          {TOGGLES.map((t) => (
            <div key={t.key} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
              <span style={rowLabel}>{t.text}</span>
              <Toggle on={query[t.key]} onToggle={() => patch({ [t.key]: !query[t.key] } as Partial<InfluencerQuery>)} />
            </div>
          ))}
        </div>

        <div>
          <label style={label}>Sort by</label>
          <select value={query.sort} onChange={(e) => patch({ sort: e.target.value })} style={select}>
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        {facets && (
          <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', lineHeight: 1.5 }}>
            {full(facets.total)} profiles in the database · {full(facets.withStats)} with follower and post data.
          </div>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <StatTile value={result ? full(result.total) : '—'} label="Matches" />
          <StatTile value={result ? compact(result.totalFollowers) : '—'} label="Combined reach" />
          <StatTile value={result ? compact(result.medianFollowers) : '—'} label="Median followers" />
          <div style={{ flex: 1 }} />
          <div
            style={{ ...secondaryButtonSmall, opacity: exporting || !result?.total ? 0.5 : 1 }}
            onClick={exporting || !result?.total ? undefined : () => void handleExport()}
          >
            {exporting ? 'Exporting…' : 'Export CSV'}
          </div>
        </div>

        {exportNote && <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--ink-muted)' }}>{exportNote}</div>}
        {error && <div style={{ ...card, font: "700 13px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}

        <div style={{ ...card, padding: '20px 22px' }}>
          {loading && !result && (
            <div style={{ padding: 40, textAlign: 'center', font: "700 14px 'Kalam'", color: 'var(--ink-fainter-2)' }}>
              Loading the influencer database…
            </div>
          )}

          {result && result.total === 0 && (
            <div style={{ padding: 40, textAlign: 'center' }}>
              <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink-fainter-2)' }}>No profiles match these filters</div>
              <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 4 }}>
                Try widening the follower range, or clearing a niche or two.
              </div>
            </div>
          )}

          {result && result.total > 0 && (
            <div style={{ overflowX: 'auto', opacity: loading ? 0.55 : 1 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={cellHead}>Influencer</th>
                    <th style={cellHead}>Niche</th>
                    <th style={{ ...cellHead, textAlign: 'right' }}>Followers</th>
                    <th style={{ ...cellHead, textAlign: 'right' }}>Posts</th>
                    <th style={{ ...cellHead, textAlign: 'right' }}>Following</th>
                    <th style={cellHead}>Last post</th>
                    <th style={cellHead}>Contact</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((r, i) => {
                    const key = r.handle || `${r.name}-${i}`
                    const open = expanded === key
                    return (
                      <tr key={key} onClick={() => setExpanded(open ? null : key)} style={{ cursor: r.bio ? 'pointer' : 'default' }}>
                        <td style={{ ...cell, minWidth: 220 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>{r.name || r.fullName || r.handle}</span>
                            {r.isVerified && <span title="Verified">✔</span>}
                            {r.isPrivate && <span style={{ ...pill, padding: '2px 8px', font: "700 10px 'Quicksand'" }}>private</span>}
                          </div>
                          {r.handle && (
                            <span
                              style={{ font: "600 12px 'Quicksand'", color: 'var(--accent-deep)', cursor: 'pointer' }}
                              onClick={(e) => {
                                e.stopPropagation()
                                void window.api.openExternal(r.profileUrl)
                              }}
                            >
                              @{r.handle} ↗
                            </span>
                          )}
                          {open && r.bio && (
                            <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 6, whiteSpace: 'pre-wrap' }}>
                              {r.bio}
                            </div>
                          )}
                        </td>
                        <td style={cell}>
                          <span style={tag}>{r.niche}</span>
                        </td>
                        <td style={{ ...cell, textAlign: 'right', font: "700 13px 'Quicksand'" }} title={r.followers === null ? '' : full(r.followers)}>
                          {compact(r.followers)}
                        </td>
                        <td style={{ ...cell, textAlign: 'right' }}>{compact(r.posts)}</td>
                        <td style={{ ...cell, textAlign: 'right', color: 'var(--ink-muted)' }}>{compact(r.following)}</td>
                        <td style={{ ...cell, color: 'var(--ink-muted)', whiteSpace: 'nowrap' }}>{r.lastPostDate || '—'}</td>
                        <td style={{ ...cell, color: 'var(--ink-muted)', maxWidth: 190, wordBreak: 'break-word' }}>
                          {[r.email, r.mobile].filter(Boolean).join(' · ') || '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {result && result.total > 0 && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                marginTop: 18,
                paddingTop: 16,
                borderTop: '2px dashed var(--border-soft)',
                flexWrap: 'wrap'
              }}
            >
              <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>
                Showing {full((result.page - 1) * result.pageSize + 1)}–
                {full(Math.min(result.page * result.pageSize, result.total))} of {full(result.total)}
              </span>
              <div style={{ flex: 1 }} />
              <select
                value={query.pageSize}
                onChange={(e) => patch({ pageSize: Number(e.target.value) })}
                style={{ ...select, width: 'auto', padding: '7px 26px 7px 11px', font: "700 12px 'Quicksand'" }}
              >
                {PAGE_SIZES.map((s) => (
                  <option key={s} value={s}>
                    {s} per page
                  </option>
                ))}
              </select>
              <div
                style={{ ...secondaryButtonSmall, opacity: result.page <= 1 ? 0.4 : 1 }}
                onClick={result.page <= 1 ? undefined : () => patch({ page: result.page - 1 })}
              >
                ← Prev
              </div>
              <span style={{ font: "700 12px 'Quicksand'", color: 'var(--ink-muted)' }}>
                {result.page} / {full(pageCount)}
              </span>
              <div
                style={{ ...primaryButtonSmall, opacity: result.page >= pageCount ? 0.4 : 1 }}
                onClick={result.page >= pageCount ? undefined : () => patch({ page: result.page + 1 })}
              >
                Next →
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
