import { useEffect, useRef, useState } from 'react'
import { getNicheFirstFill, type NicheFirstFill } from '../api/client'
import { label, primaryButtonSmall, secondaryButtonSmall, textInput } from '../styles/styleKit'

/**
 * Manage the niches a post generator learns from: what exists, how well stocked each one
 * is, collect more, add a new one.
 *
 * Shared by the Bluesky and Mastodon composers rather than written twice. A niche is the
 * same object in both — the backend keeps one list and both tools read it — so the panel
 * that edits them should be one thing too. What differs is only what "collect" means
 * (a keyword search versus an instance's hashtag timelines) and whose corpus the counts
 * describe, and both of those arrive as props.
 *
 * The one thing this panel fetches for itself is first-fill progress. Adding a niche now
 * queues a backend fill across both tools, and the counts prop cannot express the
 * difference between "this niche is empty" and "this niche is being filled right now" —
 * without which a user watches 0 posts · 0 exemplars and presses Collect on work already
 * underway. Polling here rather than in each route keeps that one behaviour in one place,
 * the same reason the rest of the panel is shared.
 */

const FILL_POLL_MS = 3000

export interface NicheRow {
  name: string
  keywords: string[]
  posts: number
  exemplars: number
}

interface Props {
  niches: NicheRow[]
  /** Collect for one niche. The panel shows a busy state until this settles. */
  onCollect: (name: string) => Promise<void>
  /** Create a niche. Keywords arrive already split and trimmed. */
  onAdd: (name: string, keywords: string[]) => Promise<void>
  /** Explains what the counts are counting — they mean different things per tool. */
  countsHint?: string
  /** When set, collection is unavailable and this says why. */
  blockedReason?: string
  /** Called when a background first fill finishes, so the caller can re-read the counts. */
  onRefresh?: () => void | Promise<void>
}

/** One line of plain English for a fill, at whatever stage it has reached. */
function describeFill(fill: NicheFirstFill): string {
  if (fill.state === 'queued') return 'Queued — collecting shortly'
  if (fill.state === 'running') return 'Collecting posts and building exemplars…'
  if (fill.state === 'failed') return `First collection failed: ${fill.error ?? 'unknown error'}`

  const parts: string[] = []
  if (fill.bluesky?.skipped) parts.push(`Bluesky skipped — ${fill.bluesky.skipped}`)
  else if (fill.bluesky?.error) parts.push('Bluesky failed')
  else if (fill.bluesky) parts.push(`Bluesky ${fill.bluesky.posts ?? 0} posts`)

  if (fill.mastodon?.skipped) parts.push(`Mastodon skipped — ${fill.mastodon.skipped}`)
  else if (fill.mastodon?.instances) {
    const hosts = Object.entries(fill.mastodon.instances)
    const stored = hosts.reduce((total, [, got]) => total + (got.stored ?? 0), 0)
    parts.push(`Mastodon ${stored} posts from ${hosts.length === 1 ? hosts[0][0] : `${hosts.length} instances`}`)
  }

  return parts.length ? `Collected on add — ${parts.join(' · ')}` : 'Collected on add'
}

export default function NichePanel({
  niches,
  onCollect,
  onAdd,
  countsHint,
  blockedReason,
  onRefresh
}: Props): React.JSX.Element {
  const [newNiche, setNewNiche] = useState('')
  const [newKeywords, setNewKeywords] = useState('')
  const [busyNiche, setBusyNiche] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState('')
  const [fills, setFills] = useState<NicheFirstFill[]>([])
  // Bumped on add, to restart polling that stopped when nothing was outstanding.
  const [pollKey, setPollKey] = useState(0)
  const wasPending = useRef(false)
  const refreshRef = useRef(onRefresh)
  refreshRef.current = onRefresh

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    async function poll(): Promise<void> {
      let state: { pending: boolean; fills: NicheFirstFill[] }
      try {
        state = await getNicheFirstFill()
      } catch {
        // The backend may still be starting, or this build may predate the endpoint.
        // Stop rather than retry into a loop of failures the user cannot act on.
        return
      }
      if (cancelled) return
      setFills(state.fills)
      // A fill that just finished wrote posts and exemplars the caller is still
      // showing pre-fill counts for.
      if (wasPending.current && !state.pending) void refreshRef.current?.()
      wasPending.current = state.pending
      if (state.pending) timer = setTimeout(() => void poll(), FILL_POLL_MS)
    }

    void poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [pollKey])

  const fillFor = (name: string): NicheFirstFill | undefined => fills.find((f) => f.niche === name)

  async function handleCollect(name: string): Promise<void> {
    if (busyNiche) return
    setBusyNiche(name)
    setError('')
    try {
      await onCollect(name)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyNiche(null)
    }
  }

  async function handleAdd(): Promise<void> {
    // Comma or newline, because people paste lists from both.
    const keywords = newKeywords
      .split(/[\n,]/)
      .map((k) => k.trim())
      .filter(Boolean)
    if (!newNiche.trim() || !keywords.length || adding) return
    setAdding(true)
    setError('')
    try {
      await onAdd(newNiche.trim(), keywords)
      setNewNiche('')
      setNewKeywords('')
      // The backend queued a first fill for it; start watching for the result.
      setPollKey((k) => k + 1)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setAdding(false)
    }
  }

  return (
    <div
      style={{
        marginTop: 16,
        background: 'var(--surface-paper)',
        border: '2.5px solid var(--border-paper)',
        borderRadius: 20,
        padding: 18,
        boxShadow: 'var(--shadow-paper)'
      }}
    >
      <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)', marginBottom: 2 }}>Niches</div>
      <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 14 }}>
        A niche is a name plus the search terms that find it. Be specific — &ldquo;ai&rdquo; drags in the
        whole internet.
        {countsHint ? ` ${countsHint}` : ''}
      </div>

      {blockedReason && (
        <div
          style={{
            font: "600 12px/1.5 'Quicksand'",
            color: 'var(--ink-muted)',
            background: 'var(--tip-bg)',
            border: '2px dashed var(--border-soft)',
            borderRadius: 12,
            padding: '9px 12px',
            marginBottom: 12
          }}
        >
          {blockedReason}
        </div>
      )}

      {!niches.length && (
        <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)', padding: '10px 0' }}>
          No niches yet. Add one below and then collect.
        </div>
      )}

      {niches.map((n) => {
        const fill = fillFor(n.name)
        const filling = fill?.state === 'queued' || fill?.state === 'running'
        return (
          <div
            key={n.name}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '10px 0',
              borderTop: '2px dashed var(--border-soft)'
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ font: "700 14px 'Quicksand'", color: 'var(--ink)' }}>{n.name}</div>
              <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 2 }}>
                {n.keywords.join(' · ')}
              </div>
              {fill && (
                <div
                  style={{
                    font: "600 12px 'Quicksand'",
                    color: fill.state === 'failed' ? 'var(--danger-ink)' : 'var(--ink-muted)',
                    marginTop: 3
                  }}
                >
                  {describeFill(fill)}
                </div>
              )}
            </div>
            <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-muted)', whiteSpace: 'nowrap' }}>
              {n.posts} posts · {n.exemplars} exemplars
            </div>
            {/* Collecting again while the first fill is still running would crawl the same
                timelines twice for nothing, so the button waits it out. */}
            <div
              style={{
                ...secondaryButtonSmall,
                opacity: blockedReason || busyNiche || filling ? 0.5 : 1,
                cursor: blockedReason || filling ? 'default' : 'pointer'
              }}
              onClick={
                blockedReason || busyNiche || filling ? undefined : () => void handleCollect(n.name)
              }
            >
              {busyNiche === n.name ? 'Collecting…' : filling ? 'Filling…' : 'Collect now'}
            </div>
          </div>
        )
      })}

      <div style={{ borderTop: '2px dashed var(--border-soft)', paddingTop: 14, marginTop: 6 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr auto', gap: 10, alignItems: 'end' }}>
          <div>
            <label style={label}>New niche</label>
            <input
              value={newNiche}
              onChange={(e) => setNewNiche(e.target.value)}
              placeholder="rust gamedev"
              style={textInput}
            />
          </div>
          <div>
            <label style={label}>Keywords (comma or newline)</label>
            <input
              value={newKeywords}
              onChange={(e) => setNewKeywords(e.target.value)}
              placeholder="bevy engine, wgpu, rust gamedev"
              style={textInput}
            />
          </div>
          <div
            style={{ ...primaryButtonSmall, opacity: adding ? 0.6 : 1 }}
            onClick={adding ? undefined : () => void handleAdd()}
          >
            {adding ? 'Adding…' : 'Add'}
          </div>
        </div>
      </div>

      {error && (
        <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--danger-ink)', marginTop: 10 }}>{error}</div>
      )}
    </div>
  )
}
