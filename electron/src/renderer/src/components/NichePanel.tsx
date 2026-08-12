import { useState } from 'react'
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
 */

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
}

export default function NichePanel({
  niches,
  onCollect,
  onAdd,
  countsHint,
  blockedReason
}: Props): React.JSX.Element {
  const [newNiche, setNewNiche] = useState('')
  const [newKeywords, setNewKeywords] = useState('')
  const [busyNiche, setBusyNiche] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState('')

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

      {niches.map((n) => (
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
          </div>
          <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-muted)', whiteSpace: 'nowrap' }}>
            {n.posts} posts · {n.exemplars} exemplars
          </div>
          <div
            style={{
              ...secondaryButtonSmall,
              opacity: blockedReason || busyNiche ? 0.5 : 1,
              cursor: blockedReason ? 'default' : 'pointer'
            }}
            onClick={blockedReason || busyNiche ? undefined : () => void handleCollect(n.name)}
          >
            {busyNiche === n.name ? 'Collecting…' : 'Collect now'}
          </div>
        </div>
      ))}

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
