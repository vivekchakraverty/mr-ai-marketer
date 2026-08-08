/**
 * The non-grid sheets: the Ads dashboard, the Settings lists, the Instructions
 * page, and the Influencer Outreach Tracker's summary header.
 */
import type { CSSProperties } from 'react'
import {
  COLLABORATION_STATUSES,
  DASHBOARD_PLATFORMS,
  RESPONSE_STATUSES,
  dashboardTotals,
  filterInfluencers,
  isoToSerial,
  monthLabel,
  monthlyRows,
  nf,
  num,
  pct,
  platformRows,
  ratio,
  serialToIso,
  statusCounts,
  totalFollowers,
  totalInfluencers,
  type Row,
  type TrackerDefaults,
  type Workbooks
} from './formulas'
import { SCALE_ROAS, colorScale } from './ui'

const PANEL: CSSProperties = {
  border: '2.5px solid var(--border)',
  borderRadius: 16,
  background: '#FFF8EA',
  padding: '16px 18px'
}

const PANEL_TITLE: CSSProperties = { font: "700 17px 'Kalam'", color: 'var(--ink)' }
const CAPTION: CSSProperties = { font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)' }
const FIELD_LABEL: CSSProperties = {
  font: "700 10.5px 'Quicksand'",
  letterSpacing: '1.2px',
  color: 'var(--ink-body)'
}
const FIELD: CSSProperties = {
  padding: '8px 11px',
  border: '2px solid var(--border)',
  borderRadius: 10,
  background: 'var(--surface)',
  font: "600 13px 'Quicksand'",
  color: 'var(--ink)'
}

// --- Dashboard -------------------------------------------------------------

export function DashboardPanel({
  workbooks,
  money,
  onFilter
}: {
  workbooks: Workbooks
  money: (value: unknown) => string
  onFilter: (patch: Partial<Workbooks['filters']>) => void
}): React.JSX.Element {
  const { filters, daily, lists } = workbooks
  const totals = dashboardTotals(daily, filters)

  const kpis: { label: string; value: string; formula: string }[] = [
    { label: 'TOTAL SPEND', value: money(totals.spend), formula: 'SUMIFS(Spend)' },
    { label: 'TOTAL REVENUE', value: money(totals.revenue), formula: 'SUMIFS(Revenue)' },
    { label: 'ROAS', value: ratio(totals.roas), formula: '=IFERROR(C8/A8,0)' },
    { label: 'PURCHASES', value: nf(totals.purchases), formula: 'SUMIFS(Purchases)' },
    { label: 'IMPRESSIONS', value: nf(totals.impressions), formula: 'SUMIFS(Impressions)' },
    { label: 'LINK CLICKS', value: nf(totals.clicks), formula: 'SUMIFS(Link Clicks)' },
    { label: 'CTR', value: pct(totals.ctr), formula: '=IFERROR(C11/A11,0)' },
    { label: 'LEADS', value: nf(totals.leads), formula: 'SUMIFS(Leads)' },
    { label: 'CPC', value: money(totals.cpc), formula: '=IFERROR(A8/C11,0)' },
    { label: 'CPL', value: money(totals.cpl), formula: '=IFERROR(A8/G11,0)' },
    { label: 'CPA', value: money(totals.cpa), formula: '=IFERROR(A8/G8,0)' },
    { label: 'CONVERSION RATE', value: pct(totals.convRate), formula: '=IFERROR(G8/C11,0)' }
  ]

  // The month rows follow the report's own year, so changing the date filter to
  // another year re-bases the roll-up instead of stranding it on 2026.
  const year = new Date(Date.UTC(1899, 11, 30) + filters.start * 86400000).getUTCFullYear()
  const months = monthlyRows(daily, filters, year)
  const monthMax = Math.max(1, ...months.map((m) => Math.max(m.spend, m.revenue)))

  const platforms = platformRows(daily, filters, DASHBOARD_PLATFORMS)
  const platformMax = Math.max(1, ...platforms.map((p) => p.revenue))
  const platformRoas = platforms.map((p) => p.roas)

  const platformOptions = lists.find((l) => l.key === 'platforms')?.items ?? []
  const clientOptions = ['All', ...(lists.find((l) => l.key === 'clients')?.items ?? [])]

  return (
    <div style={{ padding: 20 }}>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 14,
          alignItems: 'flex-end',
          padding: '16px 18px',
          border: '2px solid var(--border)',
          borderRadius: 16,
          background: '#DCEEFB',
          marginBottom: 18
        }}
      >
        <div style={{ font: "700 15px 'Kalam'", marginRight: 6, color: 'var(--ink)' }}>Report Filters</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={FIELD_LABEL}>START DATE</label>
          <input
            type="date"
            value={serialToIso(filters.start)}
            onChange={(e) => e.target.value && onFilter({ start: isoToSerial(e.target.value) })}
            style={FIELD}
          />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={FIELD_LABEL}>END DATE</label>
          <input
            type="date"
            value={serialToIso(filters.end)}
            onChange={(e) => e.target.value && onFilter({ end: isoToSerial(e.target.value) })}
            style={FIELD}
          />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={FIELD_LABEL}>PLATFORM</label>
          <select value={filters.platform} onChange={(e) => onFilter({ platform: e.target.value })} style={{ ...FIELD, minWidth: 140, cursor: 'pointer' }}>
            {platformOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={FIELD_LABEL}>CLIENT</label>
          <select value={filters.client} onChange={(e) => onFilter({ client: e.target.value })} style={{ ...FIELD, minWidth: 170, cursor: 'pointer' }}>
            {clientOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        <div style={{ font: "600 12.5px 'Quicksand'", color: '#3F4B57', flex: 1, minWidth: 240 }}>
          Change the filters above to update all KPIs and charts.
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 14, marginBottom: 20 }}>
        {kpis.map((kpi) => (
          <div
            key={kpi.label}
            style={{
              border: '2.5px solid var(--border)',
              borderRadius: 16,
              padding: '16px 18px',
              background: 'var(--surface)',
              boxShadow: 'var(--shadow-sm)'
            }}
          >
            <div style={{ font: "700 10.5px 'Quicksand'", letterSpacing: '1.6px', color: 'var(--ink-faint)' }}>{kpi.label}</div>
            <div style={{ font: "700 30px/1.15 'Kalam'", marginTop: 6, color: 'var(--ink)' }}>{kpi.value}</div>
            <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-fainter)', marginTop: 4 }}>{kpi.formula}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.35fr 1fr', gap: 16 }}>
        <div style={PANEL}>
          <div style={{ ...PANEL_TITLE, marginBottom: 12 }}>Monthly Performance</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
            {months.map((month) => (
              <div
                key={month.monthIndex}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '56px 1fr 96px 96px 60px 62px 74px',
                  gap: 9,
                  alignItems: 'center'
                }}
              >
                <div style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)' }}>{monthLabel(month.monthIndex)}</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  <div style={{ height: 9, background: '#EDE3D2', borderRadius: 6, overflow: 'hidden' }}>
                    <div style={{ height: '100%', background: 'var(--accent)', width: `${(month.spend / monthMax) * 100}%` }} />
                  </div>
                  <div style={{ height: 9, background: '#EDE3D2', borderRadius: 6, overflow: 'hidden' }}>
                    <div style={{ height: '100%', background: '#5FA9D8', width: `${(month.revenue / monthMax) * 100}%` }} />
                  </div>
                </div>
                <div style={{ font: "600 12px 'Quicksand'", textAlign: 'right', color: '#B8442A' }}>
                  {month.spend ? money(month.spend) : '—'}
                </div>
                <div style={{ font: "600 12px 'Quicksand'", textAlign: 'right', color: '#2E6F97' }}>
                  {month.revenue ? money(month.revenue) : '—'}
                </div>
                <div style={{ font: "600 12px 'Quicksand'", textAlign: 'right', color: 'var(--ink-body)' }}>{nf(month.leads)}</div>
                <div style={{ font: "600 12px 'Quicksand'", textAlign: 'right', color: 'var(--ink-body)' }}>{nf(month.purchases)}</div>
                <div style={{ font: "700 12px 'Quicksand'", textAlign: 'right', color: 'var(--ink)' }}>{ratio(month.roas)}</div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 16, marginTop: 14, font: "700 11.5px 'Quicksand'", color: 'var(--ink-muted)' }}>
            <span>
              <span style={{ display: 'inline-block', width: 11, height: 11, background: 'var(--accent)', borderRadius: 3, marginRight: 6 }} />
              Spend
            </span>
            <span>
              <span style={{ display: 'inline-block', width: 11, height: 11, background: '#5FA9D8', borderRadius: 3, marginRight: 6 }} />
              Revenue
            </span>
            <span style={{ marginLeft: 'auto' }}>Leads · Purchases · ROAS</span>
          </div>
        </div>

        <div style={PANEL}>
          <div style={{ ...PANEL_TITLE, marginBottom: 12 }}>Platform Performance</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {platforms.map((platform) => (
              <div key={platform.name}>
                <div style={{ display: 'flex', justifyContent: 'space-between', font: "700 12.5px 'Quicksand'", color: 'var(--ink)' }}>
                  <span>{platform.name}</span>
                  <span style={{ color: 'var(--ink-muted)' }}>
                    {money(platform.spend)} → {money(platform.revenue)}
                  </span>
                </div>
                <div style={{ height: 11, background: '#EDE3D2', borderRadius: 7, overflow: 'hidden', marginTop: 5 }}>
                  <div style={{ height: '100%', background: '#F0A24B', width: `${(platform.revenue / platformMax) * 100}%` }} />
                </div>
                {/* Dashboard K18:K23 carries a 3-colour scale on ROAS — kept here as a tinted chip. */}
                <div style={{ font: "600 11px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 3 }}>
                  ROAS{' '}
                  <span
                    style={{
                      background: colorScale(platform.roas, platformRoas, SCALE_ROAS),
                      borderRadius: 5,
                      padding: '1px 6px',
                      color: 'var(--ink-body)',
                      font: "700 11px 'Quicksand'"
                    }}
                  >
                    {ratio(platform.roas)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// --- Settings --------------------------------------------------------------

export function SettingsPanel({
  workbooks,
  onList,
  onDefault
}: {
  workbooks: Workbooks
  onList: (listKey: string, index: number, value: string) => void
  onDefault: <K extends keyof TrackerDefaults>(key: K, value: TrackerDefaults[K]) => void
}): React.JSX.Element {
  const { lists, defaults } = workbooks
  const valueStyle: CSSProperties = {
    font: "600 12.5px 'Quicksand'",
    color: 'var(--ink-body)',
    textAlign: 'right',
    background: 'transparent',
    border: 'none',
    padding: 0,
    minWidth: 0,
    flex: 1
  }

  return (
    <div style={{ padding: 20, display: 'grid', gridTemplateColumns: '1fr 380px', gap: 18, alignItems: 'start' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: 12 }}>
        {lists.map((list) => (
          <div key={list.key} style={{ border: '2px solid var(--border)', borderRadius: 14, background: '#FFF8EA', overflow: 'hidden' }}>
            <div style={{ padding: '9px 13px', background: 'var(--border)', color: '#FFF7E9', font: "700 12px 'Quicksand'" }}>
              {list.name}
            </div>
            <div style={{ padding: '6px 0' }}>
              {list.items.map((item, index) => (
                <input
                  key={index}
                  value={item}
                  onChange={(e) => onList(list.key, index, e.target.value)}
                  style={{
                    display: 'block',
                    width: '100%',
                    padding: '6px 13px',
                    font: "600 12.5px 'Quicksand'",
                    color: 'var(--ink-body)',
                    background: 'transparent',
                    border: 'none'
                  }}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div style={{ border: '2.5px solid var(--border)', borderRadius: 16, background: '#FBE8C0', padding: '16px 18px' }}>
        <div style={{ ...PANEL_TITLE, marginBottom: 10 }}>Tracker Defaults</div>
        {(
          [
            ['Default Start Date', 'defaultStartDate', 'date'],
            ['Default End Date', 'defaultEndDate', 'date'],
            ['Currency', 'currency', 'text'],
            ['Reporting Time Zone', 'timeZone', 'text'],
            ['Notes', 'notes', 'text']
          ] as [string, keyof TrackerDefaults, 'date' | 'text'][]
        ).map(([label, key, kind]) => (
          <div
            key={key}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              gap: 12,
              padding: '9px 0',
              borderBottom: '1px dashed rgba(43, 36, 32, .25)'
            }}
          >
            <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--ink)', flexShrink: 0 }}>{label}</div>
            {kind === 'date' ? (
              <input
                type="date"
                value={serialToIso(defaults[key] as number)}
                onChange={(e) => e.target.value && onDefault(key as 'defaultStartDate', isoToSerial(e.target.value))}
                style={{ ...valueStyle, cursor: 'pointer' }}
              />
            ) : (
              <input
                value={String(defaults[key] ?? '')}
                onChange={(e) => onDefault(key as 'currency', e.target.value)}
                style={valueStyle}
              />
            )}
          </div>
        ))}
        <div style={{ ...CAPTION, marginTop: 10 }}>
          Currency drives every money column on both trackers. Start and end dates seed the Dashboard&rsquo;s report filters.
        </div>
      </div>
    </div>
  )
}

// --- Instructions ----------------------------------------------------------

const STEPS: { n: string; what: string; where: string }[] = [
  { n: '1', what: 'Update your clients, platforms, owners, statuses, and dropdown values.', where: 'SETTINGS' },
  { n: '2', what: 'Add every campaign before recording daily ad results.', where: 'CAMPAIGNS' },
  { n: '3', what: 'Enter one row per date, campaign, ad set, and ad/creative.', where: 'DAILY PERFORMANCE' },
  { n: '4', what: 'Track creative hooks, formats, CTAs, and winner status.', where: 'CREATIVES' },
  { n: '5', what: 'Record individual leads, follow-ups, conversions, and revenue.', where: 'LEADS & SALES' },
  { n: '6', what: 'Set monthly planned spend and business targets.', where: 'BUDGET & TARGETS' },
  { n: '7', what: 'Use the date, platform, and client filters for reporting.', where: 'DASHBOARD' }
]

const KPI_DEFS: { name: string; meaning: string }[] = [
  { name: 'CPM', meaning: 'Spend ÷ Impressions × 1,000' },
  { name: 'CPC', meaning: 'Spend ÷ Link Clicks' },
  { name: 'CTR', meaning: 'Link Clicks ÷ Impressions' },
  { name: 'CPL', meaning: 'Spend ÷ Leads' },
  { name: 'CPA', meaning: 'Spend ÷ Purchases' },
  { name: 'Conversion Rate', meaning: 'Purchases ÷ Link Clicks' },
  { name: 'ROAS', meaning: 'Revenue ÷ Spend' },
  { name: 'Frequency', meaning: 'Impressions ÷ Reach' }
]

export function InstructionsPanel(): React.JSX.Element {
  const note = (bg: string, ink: string, title: string, body: string, where: string): React.JSX.Element => (
    <div style={{ border: '2px solid var(--border)', borderRadius: 14, background: bg, padding: '13px 15px' }}>
      <div style={{ font: "700 14px 'Kalam'", color: 'var(--ink)' }}>{title}</div>
      <div style={{ font: "600 12.5px/1.45 'Quicksand'", marginTop: 4, color: 'var(--ink-body)' }}>{body}</div>
      <div style={{ font: "700 11px 'Quicksand'", color: ink, marginTop: 5 }}>{where}</div>
    </div>
  )

  return (
    <div style={{ padding: 22 }}>
      <div style={{ font: "700 20px 'Kalam'", marginBottom: 12, color: 'var(--ink)' }}>Recommended Workflow</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
        {STEPS.map((step) => (
          <div
            key={step.n}
            style={{ display: 'flex', gap: 12, border: '2px solid var(--border)', borderRadius: 14, background: '#FFF8EA', padding: '13px 15px' }}
          >
            <div
              style={{
                flex: 'none',
                width: 30,
                height: 30,
                borderRadius: '50%',
                background: 'var(--accent)',
                color: 'var(--accent-ink)',
                border: '2px solid var(--border)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                font: "700 13px 'Kalam'"
              }}
            >
              {step.n}
            </div>
            <div>
              <div style={{ font: "600 13.5px/1.45 'Quicksand'", color: 'var(--ink-body)' }}>{step.what}</div>
              <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '1.1px', color: 'var(--ink-faint)', marginTop: 4 }}>
                {step.where}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 12, marginTop: 14 }}>
        {note('#F8D3CC', '#7A2418', 'Important', 'Do not type over formula columns — they are calculated automatically.', 'Campaigns · Daily Performance · Creatives · Budget & Targets')}
        {note('#DCEEFB', '#2E6F97', 'Google Sheets', 'Upload this workbook to Google Drive, open with Google Sheets, then save as a Google Sheet.', 'Google Drive')}
        {note('#DCF2E8', '#14562F', 'Web App Phase', 'This exact sheet structure can become the database for the Apps Script web application.', 'Future phase')}
      </div>

      <div style={{ font: "700 20px 'Kalam'", margin: '20px 0 12px', color: 'var(--ink)' }}>Core KPIs Included</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12 }}>
        {KPI_DEFS.map((kpi) => (
          <div key={kpi.name} style={{ border: '2px solid var(--border)', borderRadius: 14, background: '#FFF8EA', padding: '12px 15px' }}>
            <div style={{ font: "700 15px 'Kalam'", color: 'var(--ink)' }}>{kpi.name}</div>
            <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 3 }}>{kpi.meaning}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// --- Influencer Outreach Tracker summary ----------------------------------

const STATUS_COLORS: Record<string, string> = {
  Pending: '#F0B429',
  Accepted: '#4FA97A',
  Declined: '#E0603F',
  'In Progress': '#F0B429',
  Collaborating: '#4FA97A',
  'Not Collaborating': '#9C9184'
}

function StatusChart({ title, rows }: { title: string; rows: { name: string; count: number; share: number }[] }): React.JSX.Element {
  return (
    <div style={PANEL}>
      <div style={{ font: "700 16px 'Kalam'", color: 'var(--ink)' }}>{title}</div>
      <div style={{ ...CAPTION, marginBottom: 12 }}>Percentage summary for the selected platform.</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {rows.map((row) => (
          <div key={row.name}>
            <div style={{ display: 'flex', justifyContent: 'space-between', font: "700 12.5px 'Quicksand'", color: 'var(--ink)' }}>
              <span>{row.name}</span>
              <span>
                {(row.share * 100).toFixed(0)}% · {row.count}
              </span>
            </div>
            <div style={{ height: 12, background: '#EDE3D2', borderRadius: 7, overflow: 'hidden', marginTop: 4 }}>
              <div style={{ height: '100%', width: `${(row.share * 100).toFixed(1)}%`, background: STATUS_COLORS[row.name] }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function InfluencerSummary({
  workbooks,
  onPlatform
}: {
  workbooks: Workbooks
  onPlatform: (platform: string) => void
}): React.JSX.Element {
  const platform = workbooks.filters.influencerPlatform
  const filtered: Row[] = filterInfluencers(workbooks.influencers, platform)
  const followers = totalFollowers(filtered)
  // Sheet N2:Q18's bar chart — Follower Count by Influencer Name.
  const followerMax = Math.max(1, ...filtered.map((row) => num(row.E)))

  return (
    <div style={{ padding: '20px 20px 4px' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr 1fr', gap: 16 }}>
        <div
          style={{
            border: '2.5px solid var(--border)',
            borderRadius: 16,
            background: '#F3E8FF',
            padding: '16px 18px',
            display: 'flex',
            flexDirection: 'column',
            gap: 14
          }}
        >
          <div>
            <div style={{ font: "700 10.5px 'Quicksand'", letterSpacing: '1.4px', color: '#5B4A72' }}>
              SELECT SOCIAL MEDIA PLATFORM
            </div>
            <select
              value={platform}
              onChange={(e) => onPlatform(e.target.value)}
              style={{ ...FIELD, marginTop: 7, width: '100%', font: "700 14px 'Quicksand'", cursor: 'pointer' }}
            >
              {(workbooks.setup.includes(platform) ? workbooks.setup : [platform, ...workbooks.setup]).map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
            <div style={{ font: "600 11.5px 'Quicksand'", color: '#5B4A72', marginTop: 7 }}>
              Select the platform to showcase the details on this sheet.
            </div>
          </div>
          <div style={{ borderTop: '2px dashed rgba(43, 36, 32, .25)', paddingTop: 12 }}>
            <div style={{ font: "700 10.5px 'Quicksand'", letterSpacing: '1.4px', color: '#5B4A72' }}>TOTAL FOLLOWER COUNT</div>
            <div style={{ font: "700 32px 'Kalam'", color: 'var(--ink)' }}>{nf(followers)}</div>
            <div style={{ font: "600 10.5px 'Quicksand'", color: 'var(--ink-faint)' }}>=SUM(D21:D900)</div>
          </div>
          <div>
            <div style={{ font: "700 10.5px 'Quicksand'", letterSpacing: '1.4px', color: '#5B4A72' }}>TOTAL NO. OF INFLUENCERS</div>
            <div style={{ font: "700 32px 'Kalam'", color: 'var(--ink)' }}>{nf(totalInfluencers(filtered))}</div>
            <div style={{ font: "600 10.5px 'Quicksand'", color: 'var(--ink-faint)' }}>=COUNTA(C21:C900)</div>
          </div>
        </div>

        <StatusChart title="Count of Collaboration Status" rows={statusCounts(filtered, 'J', COLLABORATION_STATUSES)} />
        <StatusChart title="Count of Response Status" rows={statusCounts(filtered, 'H', RESPONSE_STATUSES)} />
      </div>

      <div style={{ ...PANEL, marginTop: 16 }}>
        <div style={{ font: "700 16px 'Kalam'", color: 'var(--ink)' }}>Influencer Follower Count Summary</div>
        <div style={{ ...CAPTION, marginBottom: 12 }}>Follower count by influencer, for the selected platform.</div>
        {filtered.length === 0 ? (
          <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)' }}>No influencers recorded on {platform} yet.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {filtered.map((row, index) => (
              <div key={`${String(row.B)}-${index}`}>
                <div style={{ display: 'flex', justifyContent: 'space-between', font: "700 12.5px 'Quicksand'", color: 'var(--ink)' }}>
                  <span>{String(row.B ?? '')}</span>
                  <span style={{ color: 'var(--ink-muted)' }}>{nf(row.E)}</span>
                </div>
                <div style={{ height: 12, background: '#EDE3D2', borderRadius: 7, overflow: 'hidden', marginTop: 4 }}>
                  <div style={{ height: '100%', width: `${(num(row.E) / followerMax) * 100}%`, background: '#8B6FD1' }} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div
        style={{
          marginTop: 16,
          padding: '11px 15px',
          border: '2px dashed rgba(43, 36, 32, .28)',
          borderRadius: 12,
          font: "600 12.5px 'Quicksand'",
          color: 'var(--ink-muted)'
        }}
      >
        This sheet is auto-formulated from the selected platform. Edit records in the <b>Influencer Information</b> tab.
      </div>

      <div style={{ marginTop: 12, textAlign: 'center', font: "600 11.5px 'Quicksand'", color: 'var(--ink-fainter)' }}>
        {workbooks.companyLine}
      </div>
    </div>
  )
}
