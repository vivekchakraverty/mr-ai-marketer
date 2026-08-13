import { useEffect, useState } from 'react'
import { primaryButtonSmall, secondaryButtonSmall, textInput } from '../styles/styleKit'

/**
 * Accounts worth following, found from the niche keywords the user already configured.
 *
 * Shared by the Bluesky and Mastodon sides of Engage. The two platforms find candidates
 * differently — post search and actor search on one, hashtag timelines and account search
 * on the other — but what a suggestion *is* does not differ, so the list is one component
 * and the finding stays in each router.
 *
 * Every row carries the reason it is there. A suggestion list that only says "you might
 * like this" is asking to be trusted; one that says "posts about rust gamedev, and says so
 * in their bio" can be checked, and a wrong suggestion is then obviously wrong rather than
 * mysteriously bad.
 */

export interface SuggestionRow {
  key: string
  handle: string
  displayName: string
  bio: string
  avatar: string | null
  followers: number
  reason: string
  bioMatch: boolean
}

interface Props {
  rows: SuggestionRow[]
  keywords: string[]
  note?: string
  loading: boolean
  /** Absent while the tool has no way to act — the button is hidden rather than dead. */
  onFollow?: (key: string) => Promise<void>
  /** Empty string means "use my saved niches"; anything else is a subject typed here. */
  onSearch: (query: string) => void
  /** Where the accounts came from, in the user's words: "on toot.garden", "on Bluesky". */
  scope: string
}

function initials(name: string): string {
  const trimmed = name.replace(/^@/, '').trim()
  return (trimmed[0] || '?').toUpperCase()
}

/** How many suggestions appear at once, and how many each "Load more" adds. */
const PAGE_SIZE = 15

export default function SuggestedFollows({
  rows,
  keywords,
  note,
  loading,
  onFollow,
  onSearch,
  scope
}: Props): React.JSX.Element {
  const [followed, setFollowed] = useState<Record<string, 'busy' | 'done'>>({})
  const [query, setQuery] = useState('')
  // Paged locally rather than by refetching a larger page. The caller already asks for a
  // deep pool, and every refetch would re-run several searches against Bluesky or someone
  // else's Mastodon server — slow for the reader and rude to the server, for a list that
  // was already computed and ranked.
  const [visible, setVisible] = useState(PAGE_SIZE)

  // A new search is a new list; keeping the old page depth would drop the reader partway
  // down results they have not seen.
  useEffect(() => {
    setVisible(PAGE_SIZE)
  }, [rows])

  async function follow(key: string): Promise<void> {
    if (!onFollow || followed[key]) return
    setFollowed((f) => ({ ...f, [key]: 'busy' }))
    try {
      await onFollow(key)
      setFollowed((f) => ({ ...f, [key]: 'done' }))
    } catch {
      // Leaving the row actionable is the right failure: the account is still there and
      // the user can try again. The error surfaces through the caller's own reporting.
      setFollowed((f) => {
        const next = { ...f }
        delete next[key]
        return next
      })
    }
  }

  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '2.5px solid var(--border)',
        borderRadius: 20,
        padding: 18,
        boxShadow: 'var(--shadow-md)',
        marginBottom: 14
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 4 }}>
        <div style={{ font: "700 15px 'Kalam'", color: 'var(--ink)' }}>Suggested follows</div>
        <div
          style={{ ...secondaryButtonSmall, marginLeft: 'auto', opacity: loading ? 0.6 : 1 }}
          onClick={loading ? undefined : () => onSearch(query)}
        >
          {loading ? 'Looking…' : query.trim() ? 'Search' : 'Refresh'}
        </div>
      </div>

      {/* A subject typed here replaces the saved niches for this search rather than adding
          to them — "who is writing about X" is a question being asked now, and blending it
          with a standing interest answers neither. Enter submits, because a search box that
          needs a button hunt is a search box people stop using. */}
      <div style={{ display: 'flex', gap: 8, margin: '10px 0 4px' }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !loading) onSearch(query)
          }}
          placeholder="Search a different subject — e.g. rust gamedev, bevy engine"
          style={{ ...textInput, flex: 1 }}
        />
        {query.trim() && (
          <div
            style={secondaryButtonSmall}
            onClick={() => {
              setQuery('')
              onSearch('')
            }}
          >
            Clear
          </div>
        )}
      </div>

      <div style={{ font: "600 12px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
        {keywords.length
          ? `People ${scope} posting about ${keywords.slice(0, 3).join(', ')}${
              keywords.length > 3 ? ` and ${keywords.length - 3} more` : ''
            }, or saying so in their bio.`
          : `People ${scope} matching your niche keywords.`}
      </div>

      {note && (
        <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-faint)' }}>{note}</div>
      )}

      {!note && !rows.length && !loading && (
        <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-faint)' }}>
          Nobody new turned up this time. Everyone matching is already followed, or the
          keywords are too narrow to find anyone.
        </div>
      )}

      {rows.slice(0, visible).map((r) => {
        const state = followed[r.key]
        return (
          <div
            key={r.key}
            style={{
              display: 'flex',
              gap: 11,
              alignItems: 'flex-start',
              padding: '11px 0',
              borderTop: '2px dashed var(--border-soft)'
            }}
          >
            {r.avatar ? (
              <img
                src={r.avatar}
                alt=""
                style={{ width: 38, height: 38, borderRadius: 10, border: '2px solid var(--border)', flexShrink: 0 }}
              />
            ) : (
              <div
                style={{
                  width: 38,
                  height: 38,
                  borderRadius: 10,
                  border: '2px solid var(--border)',
                  background: 'var(--avatar-bg)',
                  color: 'var(--avatar-ink)',
                  display: 'grid',
                  placeItems: 'center',
                  font: "700 15px 'Kalam'",
                  flexShrink: 0
                }}
              >
                {initials(r.displayName || r.handle)}
              </div>
            )}

            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>
                {r.displayName}{' '}
                <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>
                  @{r.handle}
                </span>
              </div>
              {r.bio && (
                <div
                  style={{
                    font: "600 12px/1.5 'Quicksand'",
                    color: 'var(--ink-body)',
                    marginTop: 2,
                    overflowWrap: 'anywhere'
                  }}
                >
                  {r.bio.slice(0, 150)}
                  {r.bio.length > 150 ? '…' : ''}
                </div>
              )}
              <div
                style={{
                  font: "600 11.5px 'Quicksand'",
                  color: r.bioMatch ? 'var(--accent-deep)' : 'var(--ink-faint)',
                  marginTop: 3
                }}
              >
                {r.reason}
                {r.followers > 0 ? ` · ${r.followers.toLocaleString()} followers` : ''}
              </div>
            </div>

            {onFollow && (
              <div
                style={{
                  ...(state === 'done' ? secondaryButtonSmall : primaryButtonSmall),
                  opacity: state === 'busy' ? 0.6 : 1,
                  flexShrink: 0
                }}
                onClick={state ? undefined : () => void follow(r.key)}
              >
                {state === 'done' ? 'Following ✓' : state === 'busy' ? 'Following…' : 'Follow'}
              </div>
            )}
          </div>
        )
      })}

      {visible < rows.length && (
        <div
          style={{
            ...secondaryButtonSmall,
            display: 'block',
            textAlign: 'center',
            marginTop: 12
          }}
          onClick={() => setVisible((v) => v + PAGE_SIZE)}
        >
          Load {Math.min(PAGE_SIZE, rows.length - visible)} more
          <span style={{ color: 'var(--ink-faint)', fontWeight: 600 }}>
            {' '}
            · {rows.length - visible} left
          </span>
        </div>
      )}
    </div>
  )
}
