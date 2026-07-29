import { useEffect, useState } from 'react'
import {
  addBlueskyAnalyticsAccount,
  discoverBlueskyAnalyticsAccounts,
  getBlueskyAnalyticsCohort,
  getBlueskyAnalyticsDashboard,
  getBlueskyAnalyticsStatus,
  removeBlueskyAnalyticsAccount,
  syncBlueskyAnalytics,
  type BlueskyAnalyticsAccount,
  type BlueskyAnalyticsDashboard,
  type BlueskyAnalyticsDiscovery,
  type BlueskyAnalyticsStatus
} from '../api/client'
import { useAppStore } from '../state/store'
import { card, primaryButtonSmall, secondaryButtonSmall, textInput } from '../styles/styleKit'
import StatTile from './StatTile'

const sub: React.CSSProperties = { font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-faint)' }
const label: React.CSSProperties = { font: "700 11px 'Quicksand'", color: 'var(--accent-deep)', textTransform: 'uppercase', letterSpacing: '.08em' }

function compactNumber(value: number): string {
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

function rate(value: number): string {
  return `${value.toFixed(value < 1 ? 2 : 1)}%`
}

function timeLabel(value: string | null): string {
  if (!value) return 'Not synced yet'
  return new Date(value).toLocaleString()
}

function accountKey(account: { did: string }): string {
  return account.did
}

export default function BlueskyAnalytics(): React.JSX.Element {
  const goSettings = useAppStore((state) => state.goSettings)
  const [status, setStatus] = useState<BlueskyAnalyticsStatus | null>(null)
  const [accounts, setAccounts] = useState<BlueskyAnalyticsAccount[]>([])
  const [discovered, setDiscovered] = useState<BlueskyAnalyticsDiscovery[]>([])
  const [dashboard, setDashboard] = useState<BlueskyAnalyticsDashboard | null>(null)
  const [niche, setNiche] = useState('')
  const [followerMin, setFollowerMin] = useState('0')
  const [followerMax, setFollowerMax] = useState('100000')
  const [handle, setHandle] = useState('')
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState('')
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  const minFollowers = Math.max(0, Number(followerMin) || 0)
  const maxFollowers = Math.max(minFollowers, Number(followerMax) || 0)

  async function loadDashboard(): Promise<void> {
    setDashboard(await getBlueskyAnalyticsDashboard(niche.trim(), minFollowers, maxFollowers))
  }

  async function load(): Promise<void> {
    setLoading(true)
    setError('')
    try {
      const [nextStatus, cohort] = await Promise.all([getBlueskyAnalyticsStatus(), getBlueskyAnalyticsCohort()])
      setStatus(nextStatus)
      setAccounts(cohort.accounts)
      if (nextStatus.configured) await loadDashboard()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function discover(): Promise<void> {
    if (!niche.trim()) return
    setWorking('discover')
    setError('')
    setNote('')
    try {
      const result = await discoverBlueskyAnalyticsAccounts(niche.trim(), minFollowers, maxFollowers)
      setDiscovered(result.accounts.filter((candidate) => !accounts.some((account) => account.did === candidate.did)))
      setNote(`${result.accounts.length} comparable account${result.accounts.length === 1 ? '' : 's'} found`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setWorking('')
    }
  }

  async function addAccount(actor: string, source: 'selected' | 'discovered'): Promise<void> {
    if (!niche.trim() || !actor.trim()) return
    setWorking(`add:${actor}`)
    setError('')
    try {
      const account = await addBlueskyAnalyticsAccount(actor.trim(), niche.trim(), source)
      setAccounts((current) => [account, ...current.filter((item) => item.did !== account.did)])
      setDiscovered((current) => current.filter((item) => item.did !== account.did))
      setHandle('')
      setNote(`Added @${account.handle} to the cohort`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setWorking('')
    }
  }

  async function removeAccount(account: BlueskyAnalyticsAccount): Promise<void> {
    setWorking(`remove:${account.did}`)
    setError('')
    try {
      await removeBlueskyAnalyticsAccount(account.did)
      setAccounts((current) => current.filter((item) => item.did !== account.did))
      setNote(`Removed @${account.handle} from the cohort`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setWorking('')
    }
  }

  async function sync(): Promise<void> {
    if (!niche.trim()) return
    setWorking('sync')
    setError('')
    setNote('')
    try {
      const result = await syncBlueskyAnalytics(niche.trim())
      setNote(`Synced ${result.posts} posts from ${result.accounts} account${result.accounts === 1 ? '' : 's'}`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setWorking('')
    }
  }

  async function applyFilters(): Promise<void> {
    setWorking('filters')
    setError('')
    try {
      await loadDashboard()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setWorking('')
    }
  }

  if (loading) return <div style={sub}>Loading Bluesky analytics…</div>

  if (status && !status.configured) {
    return (
      <div style={{ ...card, textAlign: 'center', padding: 48 }}>
        <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>Connect Bluesky first</div>
        <div style={{ ...sub, margin: '6px auto 16px', maxWidth: 430 }}>Use the same handle and app password from Social Post Generator settings.</div>
        <div style={secondaryButtonSmall} onClick={goSettings}>Open Settings</div>
      </div>
    )
  }

  const summary = dashboard?.summary
  const maxRate = Math.max(...(dashboard?.posts.map((post) => post.engagementRate) ?? [1]), 1)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 220 }}>
            <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>Comparable Bluesky cohort</div>
            <div style={sub}>@{status?.handle} · last sync {timeLabel(status?.lastSyncedAt ?? null)}</div>
          </div>
          <div style={{ ...secondaryButtonSmall, opacity: working === 'sync' ? 0.55 : 1 }} onClick={working ? undefined : () => void sync()}>
            {working === 'sync' ? 'Syncing…' : 'Sync now'}
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(200px, 2fr) repeat(2, minmax(120px, 1fr)) auto', gap: 10, alignItems: 'end' }}>
          <label>
            <span style={label}>Niche</span>
            <input value={niche} onChange={(event) => setNiche(event.target.value)} placeholder="e.g. AI tools" style={textInput} />
          </label>
          <label>
            <span style={label}>Min followers</span>
            <input type="number" min={0} value={followerMin} onChange={(event) => setFollowerMin(event.target.value)} style={textInput} />
          </label>
          <label>
            <span style={label}>Max followers</span>
            <input type="number" min={0} value={followerMax} onChange={(event) => setFollowerMax(event.target.value)} style={textInput} />
          </label>
          <div style={{ ...primaryButtonSmall, opacity: working === 'discover' || !niche.trim() ? 0.55 : 1 }} onClick={working || !niche.trim() ? undefined : () => void discover()}>
            {working === 'discover' ? 'Finding…' : 'Find accounts'}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'end', flexWrap: 'wrap' }}>
          <label style={{ flex: '1 1 260px' }}>
            <span style={label}>Add a handle</span>
            <input value={handle} onChange={(event) => setHandle(event.target.value)} placeholder="@creator.bsky.social" style={textInput} />
          </label>
          <div style={{ ...secondaryButtonSmall, opacity: working.startsWith('add:') || !handle.trim() || !niche.trim() ? 0.55 : 1 }} onClick={working || !handle.trim() || !niche.trim() ? undefined : () => void addAccount(handle, 'selected')}>
            Add account
          </div>
          <div style={{ ...secondaryButtonSmall, opacity: working === 'filters' ? 0.55 : 1 }} onClick={working ? undefined : () => void applyFilters()}>
            Apply filters
          </div>
        </div>
        {note && <div style={{ font: "700 12px 'Quicksand'", color: 'var(--accent-deep)' }}>{note}</div>}
        {error && <div style={{ font: "700 12px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}
      </div>

      {discovered.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={label}>Discovered accounts</div>
          {discovered.map((candidate) => (
            <div key={candidate.did} style={{ ...card, padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: 220 }}>
                <div style={{ font: "700 14px 'Quicksand'", color: 'var(--ink)' }}>{candidate.displayName} <span style={sub}>@{candidate.handle}</span></div>
                <div style={sub}>{compactNumber(candidate.followers)} followers · {candidate.matchedPosts} matching posts</div>
                <div style={{ ...sub, marginTop: 3 }}>{candidate.sampleText || 'No text preview available.'}</div>
              </div>
              <div style={{ ...secondaryButtonSmall, opacity: working === `add:${candidate.handle}` ? 0.55 : 1 }} onClick={working ? undefined : () => void addAccount(candidate.handle, 'discovered')}>Add</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <div style={label}>Selected cohort</div>
        <span style={sub}>{accounts.length} account{accounts.length === 1 ? '' : 's'}</span>
      </div>
      {accounts.length > 0 ? (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {accounts.map((account) => (
            <div key={accountKey(account)} style={{ ...card, padding: '10px 13px', display: 'flex', alignItems: 'center', gap: 10, flex: '1 1 220px' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ font: "700 13px 'Quicksand'", color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{account.displayName}</div>
                <div style={sub}>@{account.handle} · {compactNumber(account.followers)} followers · {account.niche}</div>
              </div>
              <div style={{ ...secondaryButtonSmall, padding: '6px 10px', opacity: working === `remove:${account.did}` ? 0.55 : 1 }} onClick={working ? undefined : () => void removeAccount(account)}>Remove</div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ ...card, padding: 24, textAlign: 'center', ...sub }}>Add accounts or discover comparable creators to build the benchmark cohort.</div>
      )}

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <StatTile value={summary?.minePosts ?? 0} label="Your posts" />
        <StatTile value={summary ? rate(summary.mineMedianRate) : '—'} label="Your median rate" />
        <StatTile value={summary ? rate(summary.cohortMedianRate) : '—'} label="Cohort median rate" />
        <StatTile value={summary?.cohortPosts ?? 0} label="Posts compared" />
      </div>

      {dashboard?.posts.length ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={label}>Post performance</div>
          {dashboard.posts.map((post) => (
            <div key={`${post.uri}-${post.capturedAt}`} style={{ ...card, padding: '13px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ font: "700 13px 'Quicksand'", color: 'var(--ink)' }}>{post.isOwn ? 'Your post' : `@${post.handle}`}</span>
                <span style={sub}>{post.ageHours < 24 ? `${post.ageHours.toFixed(1)}h old` : `${Math.floor(post.ageHours / 24)}d old`} · {compactNumber(post.followers)} followers · {post.comparisonWindow === '24h' ? '24h benchmark' : 'latest snapshot'}</span>
                <a href={post.webUrl} onClick={(event) => { event.preventDefault(); void window.api.openExternal(post.webUrl) }} style={{ ...sub, marginLeft: 'auto', color: 'var(--accent-deep)' }}>Open post</a>
              </div>
              <div style={{ font: "600 13px/1.45 'Quicksand'", color: 'var(--ink-muted)' }}>{post.text || 'Media post'}</div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', ...sub }}>
                <span>{post.engagement} engagements</span>
                <span>{post.likes} likes</span>
                <span>{post.reposts} reposts</span>
                <span>{post.replies} replies</span>
                <span>{post.quotes} quotes</span>
                <strong style={{ color: post.isOwn ? 'var(--accent-deep)' : 'var(--ink-muted)' }}>{rate(post.engagementRate)} · {post.percentile.toFixed(0)}th percentile</strong>
              </div>
              <div style={{ height: 7, background: 'var(--accent-soft-bg)', borderRadius: 6, overflow: 'hidden' }}>
                <div style={{ width: `${Math.min(100, (post.engagementRate / maxRate) * 100)}%`, height: '100%', background: post.isOwn ? 'var(--accent)' : 'var(--tool-social)' }} />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ ...card, padding: 28, textAlign: 'center' }}>
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink-fainter-2)' }}>No snapshots yet</div>
          <div style={sub}>Choose a niche, add comparable accounts, then sync Bluesky.</div>
        </div>
      )}

      <div style={sub}>Rates use public likes, reposts, replies and quotes divided by followers captured during sync. Bluesky does not provide public impressions or reach.</div>
    </div>
  )
}
