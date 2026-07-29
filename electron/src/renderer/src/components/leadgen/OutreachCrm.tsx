import { useEffect, useState } from 'react'
import {
  getLeadgenDeal,
  getLeadgenStats,
  listLeadgenCampaigns,
  listLeadgenDeals,
  type LeadgenCampaign,
  type LeadgenDeal,
  type LeadgenDealDetail
} from '../../api/client'
import { card, secondaryButtonSmall, select } from '../../styles/styleKit'
import StatTile from '../StatTile'
import { PIPELINE_STAGES, stageMeta } from './pipeline'

const sub: React.CSSProperties = { font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }

/** The Outreach CRM — the read-only "observe" surface (Analytics). Pipeline board of deals by
 * state + a per-deal conversation thread. */
export default function OutreachCrm(): React.JSX.Element {
  const [campaigns, setCampaigns] = useState<LeadgenCampaign[]>([])
  const [campaignId, setCampaignId] = useState<string | null>(null)
  const [deals, setDeals] = useState<LeadgenDeal[]>([])
  const [stats, setStats] = useState<{ states: Record<string, number>; sentToday: number } | null>(null)
  const [openDealId, setOpenDealId] = useState<string | null>(null)

  useEffect(() => {
    listLeadgenCampaigns()
      .then((cs) => {
        setCampaigns(cs)
        if (cs.length) setCampaignId((prev) => prev ?? cs[0].id)
      })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!campaignId) return
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId])

  async function load(): Promise<void> {
    if (!campaignId) return
    const [d, s] = await Promise.all([listLeadgenDeals(campaignId), getLeadgenStats(campaignId)])
    setDeals(d)
    setStats(s)
  }

  if (campaigns.length === 0) {
    return (
      <div style={{ ...card, textAlign: 'center', padding: 48 }}>
        <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink-fainter-2)' }}>No campaigns yet</div>
        <div style={sub}>Start a campaign in Research / Strategy → Lead Gen Agent, and its pipeline will appear here.</div>
      </div>
    )
  }

  const total = deals.length
  const emailed = (stats?.states['EMAILED'] ?? 0) + (stats?.states['REPLIED'] ?? 0) + (stats?.states['COMPLETED'] ?? 0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <select value={campaignId ?? ''} onChange={(e) => setCampaignId(e.target.value)} style={{ ...select, width: 'auto' }}>
          {campaigns.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <div style={secondaryButtonSmall} onClick={() => void load()}>
          Refresh
        </div>
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <StatTile value={total} label="Leads" />
        <StatTile value={stats?.states['QUALIFIED'] ?? 0} label="Qualified" />
        <StatTile value={emailed} label="Emailed" />
        <StatTile value={stats?.states['REPLIED'] ?? 0} label="Replied" />
        <StatTile value={stats?.states['COMPLETED'] ?? 0} label="Won" />
        <StatTile value={`${stats?.sentToday ?? 0}`} label="Sent today" />
      </div>

      {/* Pipeline board */}
      <div style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 8 }}>
        {PIPELINE_STAGES.filter((s) => s.key !== 'QUALIFYING' && s.key !== 'READY_TO_FIND_EMAIL' && s.key !== 'FINDING_EMAIL').map(
          (stage) => {
            const inStage = deals.filter((d) => d.state === stage.key)
            return (
              <div key={stage.key} style={{ minWidth: 210, flex: '0 0 210px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ width: 10, height: 10, borderRadius: '50%', background: stage.color }} />
                  <span style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)' }}>{stage.label}</span>
                  <span style={sub}>{inStage.length}</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {inStage.map((d) => (
                    <div
                      key={d.id}
                      onClick={() => setOpenDealId(d.id)}
                      style={{
                        background: 'var(--surface)',
                        border: '2px solid var(--border)',
                        borderLeft: `5px solid ${stage.color}`,
                        borderRadius: 12,
                        padding: '9px 12px',
                        cursor: 'pointer'
                      }}
                    >
                      <div style={{ font: "700 13px 'Quicksand'", color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {d.company}
                      </div>
                      <div style={{ ...sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {d.email ?? d.domain ?? d.region ?? '—'}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )
          }
        )}
      </div>

      {openDealId && <LeadDetailDrawer dealId={openDealId} onClose={() => setOpenDealId(null)} />}
    </div>
  )
}

function LeadDetailDrawer({ dealId, onClose }: { dealId: string; onClose: () => void }): React.JSX.Element {
  const [detail, setDetail] = useState<LeadgenDealDetail | null>(null)
  const [error, setError] = useState('')

  function load(): void {
    setError('')
    setDetail(null)
    getLeadgenDeal(dealId)
      .then(setDetail)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }

  useEffect(load, [dealId])

  const lead = (detail?.lead ?? {}) as Record<string, unknown>
  const stage = detail ? stageMeta(detail.deal.state) : null

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(43,36,32,.45)', display: 'flex', justifyContent: 'flex-end', zIndex: 50 }} onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 460,
          maxWidth: '92vw',
          height: '100%',
          background: 'var(--bg)',
          borderLeft: '2.5px solid var(--border)',
          padding: 26,
          overflowY: 'auto'
        }}
      >
        {error ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-start' }}>
            <div style={{ font: "700 13px 'Quicksand'", color: '#a34a3a' }}>Couldn’t load this lead: {error}</div>
            <div style={secondaryButtonSmall} onClick={load}>
              Retry
            </div>
          </div>
        ) : !detail ? (
          <div style={sub}>Loading…</div>
        ) : (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)' }}>{detail.deal.company}</div>
                <div style={sub}>{detail.deal.email ?? detail.deal.domain ?? ''}</div>
              </div>
              <div style={{ ...secondaryButtonSmall }} onClick={onClose}>
                Close
              </div>
            </div>

            {stage && (
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, marginTop: 12, padding: '5px 12px', borderRadius: 999, border: '2px solid var(--border)' }}>
                <span style={{ width: 9, height: 9, borderRadius: '50%', background: stage.color }} />
                <span style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)' }}>{stage.label}</span>
                {detail.deal.outcome && <span style={sub}>· {detail.deal.outcome}</span>}
              </div>
            )}

            <div style={{ ...card, marginTop: 16 }}>
              <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--ink-faint)', marginBottom: 6 }}>
                Profile
              </div>
              <div style={{ font: "600 13px/1.55 'Quicksand'", color: 'var(--ink-body)', whiteSpace: 'pre-wrap' }}>
                {String(lead.profile_text ?? detail.deal.profile_text ?? '—')}
              </div>
              {typeof lead.source === 'string' && (
                <div style={{ ...sub, marginTop: 8 }}>
                  Source: {String(lead.source)}
                  {typeof lead.email_source === 'string' ? ` · email ${String(lead.email_source)}` : ''}
                </div>
              )}
            </div>

            <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--ink-faint)', margin: '18px 0 8px' }}>
              Conversation
            </div>
            {detail.thread.length === 0 && <div style={sub}>No messages yet.</div>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {detail.thread.map((m) => (
                <div
                  key={m.id}
                  style={{
                    alignSelf: m.direction === 'out' ? 'flex-end' : 'flex-start',
                    maxWidth: '85%',
                    background: m.direction === 'out' ? 'var(--accent)' : 'var(--surface)',
                    color: m.direction === 'out' ? 'var(--accent-ink)' : 'var(--ink-body)',
                    border: '2px solid var(--border)',
                    borderRadius: 14,
                    padding: '10px 13px'
                  }}
                >
                  <div style={{ font: "700 12px 'Quicksand'", marginBottom: 3 }}>{m.subject || (m.direction === 'out' ? 'Sent' : 'Reply')}</div>
                  <div style={{ font: "600 13px/1.5 'Quicksand'", whiteSpace: 'pre-wrap' }}>{m.body}</div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
