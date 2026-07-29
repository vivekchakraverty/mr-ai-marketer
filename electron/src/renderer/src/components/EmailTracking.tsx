import { useEffect, useState } from 'react'
import {
  getMailTrackingStats,
  listMailMessages,
  syncMailTracking,
  type MailMessage,
  type MailTrackingStats
} from '../api/client'
import { card, secondaryButtonSmall, select } from '../styles/styleKit'
import StatTile from './StatTile'

const sub: React.CSSProperties = { font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }

type SourceFilter = '' | 'composer' | 'leadgen'

const SOURCE_OPTIONS: { value: SourceFilter; label: string }[] = [
  { value: '', label: 'All sources' },
  { value: 'composer', label: 'Mail Composer' },
  { value: 'leadgen', label: 'Lead Gen Agent' }
]

const STATUS_COLOR: Record<string, string> = {
  sent: 'var(--tool-distribute)',
  pending: 'var(--ink-fainter)',
  failed: '#a34a3a',
  bounced: '#a34a3a'
}

function recipients(json: string): string {
  try {
    const parsed = JSON.parse(json) as string[]
    return parsed.join(', ')
  } catch {
    return json
  }
}

function pct(rate: number): string {
  return `${Math.round(rate * 100)}%`
}

/** The Analytics "Email" tab — opens/clicks/bounces for both SMTP send paths
 * (the Mail Composer in Distribute, and the Lead Gen Agent's outreach). Fetch
 * once + manual sync, mirroring OutreachCrm's own pattern for this screen. */
export default function EmailTracking(): React.JSX.Element {
  const [source, setSource] = useState<SourceFilter>('')
  const [messages, setMessages] = useState<MailMessage[]>([])
  const [stats, setStats] = useState<MailTrackingStats | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncNote, setSyncNote] = useState('')

  async function load(): Promise<void> {
    const [m, s] = await Promise.all([listMailMessages(source || undefined), getMailTrackingStats(source || undefined)])
    setMessages(m)
    setStats(s)
    setLoaded(true)
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source])

  async function handleSync(): Promise<void> {
    setSyncing(true)
    setSyncNote('')
    try {
      const result = await syncMailTracking()
      setSyncNote(result.ok ? `Synced ${result.synced} new event${result.synced === 1 ? '' : 's'} ✓` : 'Sync failed — the tracking service may be unreachable right now.')
      await load()
    } catch (err) {
      setSyncNote(err instanceof Error ? err.message : String(err))
    } finally {
      setSyncing(false)
    }
  }

  if (!loaded) return <></>

  if (messages.length === 0 && !source) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <div style={{ ...card, textAlign: 'center', padding: 48 }}>
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink-fainter-2)' }}>No emails sent yet</div>
          <div style={sub}>
            Send one from Distribute → Mail Composer, or let the Lead Gen Agent send outreach — tracking results will
            show up here.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value as SourceFilter)}
          style={{ ...select, width: 'auto' }}
        >
          {SOURCE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <div style={{ ...secondaryButtonSmall, opacity: syncing ? 0.6 : 1 }} onClick={syncing ? undefined : () => void handleSync()}>
          {syncing ? 'Syncing…' : 'Sync now'}
        </div>
        {syncNote && <span style={{ font: "700 12.5px 'Quicksand'", color: 'var(--ink-muted)' }}>{syncNote}</span>}
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <StatTile value={stats?.sent ?? 0} label="Sent" />
        <StatTile value={`${stats?.opened ?? 0} (${pct(stats?.openRate ?? 0)})`} label="Opened" />
        <StatTile value={`${stats?.clicked ?? 0} (${pct(stats?.clickRate ?? 0)})`} label="Clicked" />
        <StatTile value={`${stats?.bounced ?? 0} (${pct(stats?.bounceRate ?? 0)})`} label="Bounced" />
      </div>

      <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-faint)' }}>
        Opens are a best-effort signal — some mail apps preload images regardless of whether a person actually looked,
        and a slow-to-wake tracking service can miss others. Treat these as directional, not exact.
      </div>

      {messages.length === 0 ? (
        <div style={{ ...card, textAlign: 'center', padding: 48 }}>
          <div style={sub}>No emails from this source yet.</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {messages.map((m) => (
            <div
              key={m.id}
              style={{
                background: 'var(--surface)',
                border: '2px solid var(--border)',
                borderRadius: 14,
                padding: '11px 15px',
                display: 'flex',
                alignItems: 'center',
                gap: 14,
                flexWrap: 'wrap'
              }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: STATUS_COLOR[m.status] ?? 'var(--ink-fainter)',
                  flexShrink: 0
                }}
                title={m.status}
              />
              <div style={{ minWidth: 160, flex: '1 1 260px' }}>
                <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {m.subject || '(no subject)'}
                </div>
                <div style={{ ...sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {recipients(m.to_addrs)}
                </div>
              </div>
              <span style={sub}>{m.source === 'leadgen' ? 'Lead Gen Agent' : 'Mail Composer'}</span>
              <span style={sub}>{new Date(m.sent_at ?? m.created_at).toLocaleString()}</span>
              <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
                <span style={{ ...sub, color: m.opens ? 'var(--accent-deep)' : 'var(--ink-fainter)' }}>👁 {m.opens}</span>
                <span style={{ ...sub, color: m.clicks ? 'var(--accent-deep)' : 'var(--ink-fainter)' }}>🔗 {m.clicks}</span>
                {m.bounces > 0 && <span style={{ ...sub, color: '#a34a3a' }}>⚠ bounced</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
