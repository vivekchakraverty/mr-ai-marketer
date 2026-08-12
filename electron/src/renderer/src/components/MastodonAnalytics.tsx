import { useEffect, useState } from 'react'
import { getMastodonAnalytics, type MastodonAnalyticsResponse } from '../api/client'
import { useAppStore } from '../state/store'
import StatTile from './StatTile'
import { label, primaryButtonSmall, textInput } from '../styles/styleKit'

/**
 * How the posts on your Mastodon account have actually done.
 *
 * Read live from the instance rather than from what this app recorded. The app only knows
 * a post exists if you came back and pasted its link, which is a step almost nobody
 * performs — building this screen on it would leave it permanently empty for most people.
 * Your account already knows every post you made and carries the counts on each one.
 *
 * There is no cohort comparison here, unlike the Bluesky tab. That one works because
 * Bluesky has a searchable firehose to draw comparable accounts from; Mastodon has no
 * equivalent, and a "versus similar accounts" figure assembled from whatever a hashtag
 * timeline returned would look authoritative and mean very little.
 */

function ago(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const mins = Math.floor((Date.now() - then) / 60000)
  if (mins < 60) return `${Math.max(mins, 0)}m`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h`
  return `${Math.floor(hrs / 24)}d`
}

export default function MastodonAnalytics(): React.JSX.Element {
  const savedInstance = useAppStore((s) => s.fields.mastodon.instance)
  const [instance, setInstance] = useState(savedInstance)
  const [data, setData] = useState<MastodonAnalyticsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function load(): Promise<void> {
    if (!instance.trim()) return
    setLoading(true)
    setError('')
    try {
      setData(await getMastodonAnalytics(instance.trim()))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (savedInstance.trim()) void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedInstance])

  const t = data?.totals ?? {}

  return (
    <div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'end', marginBottom: 18, maxWidth: 560 }}>
        <div style={{ flex: 1 }}>
          <label style={label}>Instance</label>
          <input
            value={instance}
            onChange={(e) => setInstance(e.target.value)}
            placeholder="hachyderm.io"
            style={textInput}
          />
        </div>
        <div
          style={{ ...primaryButtonSmall, opacity: loading || !instance.trim() ? 0.6 : 1 }}
          onClick={loading || !instance.trim() ? undefined : () => void load()}
        >
          {loading ? 'Reading…' : 'Refresh'}
        </div>
      </div>

      {error && (
        <div style={{ font: "700 12.5px/1.6 'Quicksand'", color: 'var(--danger-ink)', marginBottom: 14 }}>
          {error}
        </div>
      )}

      {data && (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
            <StatTile value={t.posts ?? 0} label="posts" />
            <StatTile value={t.followers ?? 0} label="followers" />
            <StatTile value={t.likes ?? 0} label="favourites" />
            <StatTile value={t.reposts ?? 0} label="boosts" />
            <StatTile value={t.replies ?? 0} label="replies" />
            <StatTile
              value={`${((t.avgEngagementRate ?? 0) * 100).toFixed(2)}%`}
              label="avg engagement"
            />
          </div>

          {data.account && (
            <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginBottom: 12 }}>
              @{data.account} · newest {data.posts.length} posts · engagement is
              favourites + boosts + replies against your follower count
              {t.fromApp ? ` · ${t.fromApp} linked to a draft written here` : ''}
            </div>
          )}

          {!data.posts.length && (
            <div style={{ font: "600 13px/1.6 'Quicksand'", color: 'var(--ink-muted)' }}>
              No posts on this account yet. Write one in Create → Mastodon Post Creator.
            </div>
          )}

          {data.posts.map((p) => (
            <div
              key={p.postUri}
              style={{
                display: 'flex',
                gap: 14,
                alignItems: 'flex-start',
                padding: '12px 0',
                borderTop: '2px dashed var(--border-soft)'
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    font: "600 13.5px/1.6 'Quicksand'",
                    color: 'var(--ink-body)',
                    overflowWrap: 'anywhere'
                  }}
                >
                  {p.text.slice(0, 220)}
                  {p.text.length > 220 ? '…' : ''}
                </div>
                <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 4 }}>
                  {ago(p.publishedAt)} ago
                  {p.fromApp ? ' · written here' : ''}
                  {p.webUrl ? (
                    <>
                      {' · '}
                      <span
                        style={{ color: 'var(--accent-deep)', cursor: 'pointer' }}
                        onClick={() => void window.api.openExternal(p.webUrl)}
                      >
                        open ↗
                      </span>
                    </>
                  ) : null}
                </div>
              </div>
              <div
                style={{
                  font: "600 12px 'Quicksand'",
                  color: 'var(--ink-muted)',
                  whiteSpace: 'nowrap',
                  textAlign: 'right'
                }}
              >
                <div>
                  ♥ {p.likes} · ↻ {p.reposts} · 💬 {p.replies}
                </div>
                <div style={{ color: 'var(--ink-faint)', marginTop: 2 }}>
                  {(p.engagementRate * 100).toFixed(2)}%
                </div>
              </div>
            </div>
          ))}
        </>
      )}

      {!data && !loading && !error && (
        <div style={{ font: "600 13px/1.6 'Quicksand'", color: 'var(--ink-muted)' }}>
          Set your instance above, then Refresh. Reading your own posts needs the access token
          from Settings — the same one the Mastodon composer uses.
        </div>
      )}
    </div>
  )
}
