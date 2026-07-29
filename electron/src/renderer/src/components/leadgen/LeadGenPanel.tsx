import { useEffect, useState } from 'react'
import {
  approveSendLeadgenDraft,
  createLeadgenCampaign,
  deleteLeadgenCampaign,
  discardLeadgenDraft,
  editLeadgenDraft,
  getLeadgenStats,
  getLeadgenStatus,
  listLeadgenCampaigns,
  listLeadgenDrafts,
  patchLeadgenCampaign,
  runLeadgenCampaignOnce,
  type LeadgenCampaign,
  type LeadgenDraft,
  type LeadgenStatus
} from '../../api/client'
import { useAppStore } from '../../state/store'
import {
  card,
  label,
  primaryButton,
  primaryButtonSmall,
  secondaryButtonSmall,
  sectionEyebrow,
  textInput,
  textarea,
  toggleKnob,
  toggleTrack
} from '../../styles/styleKit'

const hint: React.CSSProperties = { font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)' }

// Shown, one at a time, while the agent is actually churning through a Run-now pass.
const WORKING_MESSAGES = [
  '🗺️ Scouring the map for businesses…',
  '🕵️ Casing the joint (politely)…',
  '🧠 Learning your taste in leads…',
  '📇 Hunting down email addresses…',
  '✅ Poking Reacher to verify an address…',
  '✍️ Ghostwriting an irresistible opener…',
  '☕ Bribing the model with fresh tokens…'
]

function totalDeals(states: Record<string, number>): number {
  return Object.values(states).reduce((a, b) => a + b, 0)
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }): React.JSX.Element {
  return (
    <div style={toggleTrack(on)} onClick={onClick}>
      <div style={toggleKnob(on)} />
    </div>
  )
}

export default function LeadGenPanel(): React.JSX.Element {
  const setLeadgenEngineReady = useAppStore((s) => s.setLeadgenEngineReady)
  const openLeadgenGate = useAppStore((s) => s.openLeadgenGate)
  const leadgenEngineReady = useAppStore((s) => s.leadgenEngineReady)

  const [status, setStatus] = useState<LeadgenStatus | null>(null)
  const [campaigns, setCampaigns] = useState<LeadgenCampaign[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [stats, setStats] = useState<{ states: Record<string, number>; sentToday: number } | null>(null)
  const [drafts, setDrafts] = useState<LeadgenDraft[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [running, setRunning] = useState(false) // a Run-now pass is in flight
  const [workMsg, setWorkMsg] = useState(0)
  const [runResult, setRunResult] = useState('') // jokey summary after a pass
  const [flash, setFlash] = useState('') // brief "+N leads" note from background polling

  // New-campaign form
  const [name, setName] = useState('')
  const [product, setProduct] = useState('')
  const [objective, setObjective] = useState('')
  const [country, setCountry] = useState('')
  const [dailyCap, setDailyCap] = useState(20)
  const [autoSend, setAutoSend] = useState(false)
  const [useBluesky, setUseBluesky] = useState(false)

  async function refresh(): Promise<void> {
    try {
      const [st, cs] = await Promise.all([getLeadgenStatus(), listLeadgenCampaigns()])
      setStatus(st)
      setCampaigns(cs)
      if (!selectedId && cs.length) setSelectedId(cs[0].id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => {
    void refresh()
    // Check whether the self-hosted services are up (Docker/WSL layer).
    window.api.leadgen
      .detectStatus()
      .then((s) => setLeadgenEngineReady(s.dockerInstalled && s.dockerRunning && s.leadgenRunning))
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!selectedId) return
    void refreshSelected()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  // Cycle the jokey working message while a pass is running.
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setWorkMsg((i) => (i + 1) % WORKING_MESSAGES.length), 2200)
    return () => clearInterval(id)
  }, [running])

  // Poll while a campaign is selected, so the background daemon's progress shows up live —
  // and flash a fun note when new leads roll in on their own.
  useEffect(() => {
    if (!selectedId) return
    const id = setInterval(() => void refreshSelected(), 6000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  async function refreshSelected(): Promise<void> {
    if (!selectedId) return
    try {
      const [s, d] = await Promise.all([getLeadgenStats(selectedId), listLeadgenDrafts(selectedId)])
      setStats((prev) => {
        if (prev && !running) {
          const gained = totalDeals(s.states) - totalDeals(prev.states)
          if (gained > 0) {
            setFlash(`🎉 ${gained} new lead${gained === 1 ? '' : 's'} just rolled in!`)
            setTimeout(() => setFlash(''), 6000)
          }
        }
        return s
      })
      setDrafts(d)
    } catch {
      /* transient */
    }
  }

  async function handleCreate(): Promise<void> {
    if (!product.trim()) {
      setError('Describe your product or service first.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const c = await createLeadgenCampaign({
        name,
        productDescription: product,
        objective,
        country,
        dailyCap,
        autoSend,
        useBluesky
      })
      setName('')
      setProduct('')
      setObjective('')
      setCountry('')
      setAutoSend(false)
      setUseBluesky(false)
      await refresh()
      setSelectedId(c.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function toggleActive(c: LeadgenCampaign): Promise<void> {
    if (!c.active && !leadgenEngineReady) {
      openLeadgenGate()
    }
    await patchLeadgenCampaign(c.id, { active: !c.active })
    await refresh()
  }

  async function toggleAutoSend(c: LeadgenCampaign): Promise<void> {
    await patchLeadgenCampaign(c.id, { autoSend: !c.autoSend })
    await refresh()
  }

  async function toggleBluesky(c: LeadgenCampaign): Promise<void> {
    await patchLeadgenCampaign(c.id, { useBluesky: !c.useBluesky })
    await refresh()
  }

  async function runNow(c: LeadgenCampaign): Promise<void> {
    if (!leadgenEngineReady) {
      openLeadgenGate()
      return
    }
    setSelectedId(c.id)
    setError('')
    setRunResult('')
    setRunning(true)
    setWorkMsg(0)
    const before = stats ? totalDeals(stats.states) : 0
    try {
      const res = await runLeadgenCampaignOnce(c.id, 12)
      await refreshSelected()
      await refresh()
      setRunResult(summarizeRun(res.steps, res.states, before, res.diagnostic))
      setTimeout(() => setRunResult(''), 15000)
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err)
      setError(`💥 The agent tripped over something: ${detail}`)
    } finally {
      setRunning(false)
    }
  }

  function summarizeRun(steps: number, states: Record<string, number>, before: number, diagnostic?: string | null): string {
    if (steps === 0) {
      if (diagnostic) {
        return `🔎 Nothing happened this pass — ${diagnostic}`
      }
      return '🤷 Came back empty-handed this pass — but everything checks out, so the well may just be dry for this niche. Try a broader "what you’re selling", or give it another go.'
    }
    const gained = Math.max(0, totalDeals(states) - before)
    const drafted = states['DRAFTED'] ?? 0
    const qualified = states['QUALIFIED'] ?? 0
    const parts: string[] = [`✨ Did ${steps} thing${steps === 1 ? '' : 's'}.`]
    if (gained > 0) parts.push(`Sniffed out ${gained} new lead${gained === 1 ? '' : 's'}.`)
    if (drafted > 0) parts.push(`Wrote ${drafted} opener${drafted === 1 ? '' : 's'} — they’re in the review queue 👇`)
    else if (qualified > 0) parts.push(`${qualified} qualified and lining up for email-hunting.`)
    else if (gained === 0) parts.push('Mostly thinking hard about who’s worth chasing.')
    return parts.join(' ')
  }

  async function removeCampaign(c: LeadgenCampaign): Promise<void> {
    await deleteLeadgenCampaign(c.id)
    if (selectedId === c.id) setSelectedId(null)
    await refresh()
  }

  const selected = campaigns.find((c) => c.id === selectedId) ?? null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {status && (!status.llmConfigured || !status.smtpConfigured) && (
        <div style={{ ...card, borderColor: 'var(--accent)', display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ font: "700 13px 'Quicksand'", color: 'var(--ink)' }}>Finish setup in Settings</div>
          <div style={hint}>
            {!status.llmConfigured && 'Add your Hugging Face token (reasoning calls bill to it). '}
            {!status.smtpConfigured && 'Connect a sending mailbox (SMTP) so the agent can email. '}
            Discovery + verification also need the local lead engine — the campaign’s Start button sets it up.
          </div>
        </div>
      )}

      {/* New campaign */}
      <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={sectionEyebrow}>New campaign</div>
        <div>
          <label style={label}>Campaign name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Austin dental clinics" style={textInput} />
        </div>
        <div>
          <label style={label}>What you’re selling (be specific)</label>
          <textarea
            value={product}
            onChange={(e) => setProduct(e.target.value)}
            rows={3}
            placeholder="e.g. A booking widget that fills last-minute appointment gaps for independent dental clinics."
            style={textarea}
          />
        </div>
        <div style={{ display: 'flex', gap: 12 }}>
          <div style={{ flex: 2 }}>
            <label style={label}>Objective</label>
            <input value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="book a 15-min demo" style={textInput} />
          </div>
          <div style={{ flex: 1 }}>
            <label style={label}>Country</label>
            <input value={country} onChange={(e) => setCountry(e.target.value)} placeholder="US" style={textInput} />
          </div>
          <div style={{ width: 120 }}>
            <label style={label}>Daily cap</label>
            <input type="number" min={1} value={dailyCap} onChange={(e) => setDailyCap(Number(e.target.value))} style={textInput} />
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Toggle on={useBluesky} onClick={() => setUseBluesky((v) => !v)} />
          <div>
            <div style={{ font: "700 13px 'Quicksand'", color: 'var(--ink)' }}>Also search Bluesky 🦋</div>
            <div style={hint}>
              Finds people publicly posting about your topic — best for audiences that live on social (indie devs,
              creators, hobbyists) rather than in business directories. Reuses your Bluesky login from Settings.
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Toggle on={autoSend} onClick={() => setAutoSend((v) => !v)} />
            <div>
              <div style={{ font: "700 13px 'Quicksand'", color: 'var(--ink)' }}>Auto-send</div>
              <div style={hint}>Off = every email waits in the review queue for your approval (recommended).</div>
            </div>
          </div>
          <div style={{ ...primaryButton, opacity: busy ? 0.6 : 1 }} onClick={busy ? undefined : handleCreate}>
            Create campaign
          </div>
        </div>
        {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}
      </div>

      {/* Campaign list + controls */}
      {campaigns.length > 0 && (
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={sectionEyebrow}>Campaigns</div>
          {campaigns.map((c) => (
            <div
              key={c.id}
              onClick={() => setSelectedId(c.id)}
              style={{
                border: `2px solid ${c.id === selectedId ? 'var(--accent)' : 'var(--border)'}`,
                borderRadius: 14,
                padding: '12px 16px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ font: "700 15px 'Quicksand'", color: 'var(--ink)' }}>{c.name}</div>
                <div style={{ ...hint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.productDescription}</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
                <span style={{ ...hint, display: 'flex', alignItems: 'center', gap: 6 }} title="Also search Bluesky for people posting about your topic">
                  🦋 <Toggle on={c.useBluesky} onClick={() => void toggleBluesky(c)} />
                </span>
                <span style={{ ...hint, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Auto-send <Toggle on={c.autoSend} onClick={() => void toggleAutoSend(c)} />
                </span>
                <div
                  style={{ ...secondaryButtonSmall, opacity: running ? 0.6 : 1 }}
                  onClick={running ? undefined : () => void runNow(c)}
                >
                  {running ? 'Working…' : 'Run now'}
                </div>
                <div
                  style={{ ...primaryButtonSmall, background: c.active ? 'var(--surface)' : 'var(--accent)', color: c.active ? 'var(--ink-muted)' : 'var(--accent-ink)', border: '2px solid var(--border)' }}
                  onClick={() => void toggleActive(c)}
                >
                  {c.active ? 'Pause' : 'Start'}
                </div>
                <div style={{ ...hint, cursor: 'pointer' }} title="Delete campaign" onClick={() => void removeCampaign(c)}>
                  ✕
                </div>
              </div>
            </div>
          ))}
          {running && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                background: 'var(--accent-soft-bg, var(--surface))',
                border: '2px dashed var(--accent)',
                borderRadius: 14,
                padding: '11px 15px'
              }}
            >
              <span style={{ width: 12, height: 12, borderRadius: '50%', background: 'var(--accent)', animation: 'bob 1s ease-in-out infinite', flexShrink: 0 }} />
              <span style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>{WORKING_MESSAGES[workMsg]}</span>
            </div>
          )}

          {!running && runResult && (
            <div
              style={{
                font: "700 13px/1.5 'Quicksand'",
                color: 'var(--ink)',
                background: 'var(--accent-soft-bg, var(--surface))',
                border: '2px solid var(--accent)',
                borderRadius: 14,
                padding: '11px 15px'
              }}
            >
              {runResult}
            </div>
          )}

          {!running && !runResult && flash && (
            <div style={{ font: "700 13px 'Quicksand'", color: 'var(--accent-deep, var(--accent))' }}>{flash}</div>
          )}

          {selected && stats && (
            <div style={hint}>
              Pipeline: {Object.entries(stats.states).map(([k, v]) => `${k} ${v}`).join(' · ') || 'nothing yet — press Run now'} · sent
              today {stats.sentToday}/{selected.dailyCap}
            </div>
          )}
        </div>
      )}

      {/* Review queue */}
      <ReviewQueue drafts={drafts} onChanged={refreshSelected} />
    </div>
  )
}

function ReviewQueue({ drafts, onChanged }: { drafts: LeadgenDraft[]; onChanged: () => void }): React.JSX.Element {
  return (
    <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={sectionEyebrow}>Review queue — approve before anything sends</div>
      {drafts.length === 0 && <div style={hint}>No drafts waiting. As the agent qualifies leads, openers land here for your review.</div>}
      {drafts.map((d) => (
        <DraftCard key={d.id} draft={d} onChanged={onChanged} />
      ))}
    </div>
  )
}

function DraftCard({ draft, onChanged }: { draft: LeadgenDraft; onChanged: () => void }): React.JSX.Element {
  const [subject, setSubject] = useState(draft.subject)
  const [body, setBody] = useState(draft.body)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function send(): Promise<void> {
    setBusy(true)
    setErr('')
    try {
      if (subject !== draft.subject || body !== draft.body) {
        await editLeadgenDraft(draft.id, { subject, body })
      }
      await approveSendLeadgenDraft(draft.id)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function discard(): Promise<void> {
    await discardLeadgenDraft(draft.id)
    onChanged()
  }

  return (
    <div style={{ border: '2px solid var(--border)', borderRadius: 14, padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}>
        <div style={{ font: "700 14px 'Quicksand'", color: 'var(--ink)' }}>
          {draft.company} <span style={{ ...hint, fontWeight: 600 }}>· {draft.email ?? 'no address'}</span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ ...hint, textTransform: 'capitalize' }}>{draft.kind.replace('_', '-')}</span>
          {draft.predicted_click_rate != null && (
            <span
              style={{
                font: "700 11px 'Quicksand'",
                padding: '3px 9px',
                borderRadius: 999,
                background: 'var(--surface)',
                border: '2px solid var(--border)',
                color: 'var(--ink-muted)'
              }}
              title="Estimated click-rate from the CTR model — an estimate, not a guarantee."
            >
              CTR ≈ {(draft.predicted_click_rate * 100).toFixed(1)}% ({draft.ctr_bucket})
            </span>
          )}
        </div>
      </div>
      <input value={subject} onChange={(e) => setSubject(e.target.value)} style={{ ...textInput, fontWeight: 700 }} />
      <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={5} style={textarea} />
      {err && <div style={{ font: "700 12px 'Quicksand'", color: '#a34a3a' }}>{err}</div>}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <div style={secondaryButtonSmall} onClick={busy ? undefined : discard}>
          Discard
        </div>
        <div style={{ ...primaryButtonSmall, opacity: busy ? 0.6 : 1 }} onClick={busy ? undefined : send}>
          {busy ? 'Sending…' : 'Approve & send'}
        </div>
      </div>
    </div>
  )
}
