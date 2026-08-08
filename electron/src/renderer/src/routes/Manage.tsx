/**
 * Manage — Tracker Studio.
 *
 * A working rebuild of two spreadsheets: "Social Media Ads Tracker" (8 sheets)
 * and "Marketing Influencer Outreach Tracker" (3 sheets). Every column of every
 * sheet is present under its original letter, every formula is recalculated
 * live from the input cells (see components/tracker/formulas.ts), and the
 * dropdowns, conditional formats and charts carry over from the workbooks.
 *
 * Input cells persist to the backend; derived cells never do — they are
 * recomputed on each render so a figure can't drift from its inputs.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getTrackerWorkbooks, resetTrackerWorkbooks, saveTrackerWorkbooks } from '../api/client'
import TrackerGrid from '../components/tracker/ui'
import { DashboardPanel, InfluencerSummary, InstructionsPanel, SettingsPanel } from '../components/tracker/panels'
import {
  budgetSheet,
  campaignsSheet,
  creativesSheet,
  dailySheet,
  influencerInfoSheet,
  influencerTrackerSheet,
  leadsSheet,
  setupSheet,
  type SheetContext,
  type SheetGrid,
  type SheetKey
} from '../components/tracker/sheets'
import { makeMoney, type TrackerDefaults, type Workbooks } from '../components/tracker/formulas'
import { sectionEyebrow } from '../styles/styleKit'
import { exportTracker, type ExportSheet } from '../api/client'

type WorkbookId = 'ads' | 'infl'

interface SheetMeta {
  id: string
  name: string
  sub: string
}

const AD_SHEETS: SheetMeta[] = [
  { id: 'dashboard', name: 'Dashboard', sub: 'Filtered KPIs, monthly roll-up and platform split — all SUMIFS-driven.' },
  { id: 'daily', name: 'Daily Performance', sub: 'One row per date, campaign, ad set and ad. 8 derived KPI columns.' },
  { id: 'campaigns', name: 'Campaigns', sub: 'Campaign master list with spend pacing pulled from Daily Performance.' },
  { id: 'creatives', name: 'Creatives', sub: 'Creative testing — metrics rolled up by ad name, winner flagged at ROAS ≥ 3.' },
  { id: 'leads', name: 'Leads & Sales', sub: 'Individual leads, follow-ups, conversions and revenue.' },
  { id: 'budget', name: 'Budget & Targets', sub: 'Monthly planned spend vs actuals, with variance on every target.' },
  { id: 'settings', name: 'Settings', sub: 'Dropdown source lists and tracker defaults.' },
  { id: 'instructions', name: 'Instructions', sub: 'How to use the tracker.' }
]

const INFL_SHEETS: SheetMeta[] = [
  { id: 'itracker', name: 'Outreach Tracker', sub: 'Auto-filtered by platform — FILTER() over Influencer Information.' },
  { id: 'iinfo', name: 'Influencer Information', sub: 'Master record for every influencer you reach out to.' },
  { id: 'isetup', name: 'Setup', sub: 'Platform list feeding the tracker dropdown.' }
]

/** Which sheets are row collections, and which array each one edits. */
const ROW_SHEETS: Record<string, SheetKey> = {
  daily: 'daily',
  campaigns: 'campaigns',
  creatives: 'creatives',
  leads: 'leads',
  budget: 'budget',
  iinfo: 'influencers',
  isetup: 'setup'
}

const pillBase = {
  cursor: 'pointer',
  userSelect: 'none',
  padding: '10px 18px',
  border: '2px solid var(--border)',
  borderRadius: 22,
  font: "700 13px 'Quicksand'",
  boxShadow: 'var(--shadow-sm)'
} as const

export default function Manage(): React.JSX.Element {
  const [workbooks, setWorkbooks] = useState<Workbooks | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [workbook, setWorkbook] = useState<WorkbookId>('ads')
  const [sheet, setSheet] = useState('dashboard')
  const [showFormulas, setShowFormulas] = useState(false)
  const [exporting, setExporting] = useState<'' | 'csv' | 'xlsx'>('')
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pending = useRef<Workbooks | null>(null)

  useEffect(() => {
    let cancelled = false
    getTrackerWorkbooks()
      .then((data) => {
        if (!cancelled) setWorkbooks(data)
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const flush = useCallback(() => {
    const next = pending.current
    if (!next) return
    pending.current = null
    setSaving(true)
    saveTrackerWorkbooks(next)
      .catch((err: Error) => setError(err.message))
      .finally(() => setSaving(false))
  }, [])

  // Debounced so a burst of keystrokes is one write, with a flush on unmount so
  // navigating away mid-edit can't drop the last change.
  useEffect(
    () => () => {
      if (saveTimer.current) clearTimeout(saveTimer.current)
      flush()
    },
    [flush]
  )

  const update = useCallback(
    (mutate: (draft: Workbooks) => Workbooks) => {
      setWorkbooks((current) => {
        if (!current) return current
        const next = mutate(current)
        pending.current = next
        if (saveTimer.current) clearTimeout(saveTimer.current)
        saveTimer.current = setTimeout(flush, 600)
        return next
      })
    },
    [flush]
  )

  const setCell = useCallback(
    (target: SheetKey, index: number, column: string, value: string | number) => {
      update((draft) => {
        if (target === 'setup') {
          const setup = draft.setup.slice()
          setup[index] = String(value)
          return { ...draft, setup }
        }
        const rows = draft[target].slice()
        rows[index] = { ...rows[index], [column]: value }
        return { ...draft, [target]: rows }
      })
    },
    [update]
  )

  const setFilters = useCallback(
    (patch: Partial<Workbooks['filters']>) => update((draft) => ({ ...draft, filters: { ...draft.filters, ...patch } })),
    [update]
  )

  const setListItem = useCallback(
    (listKey: string, index: number, value: string) =>
      update((draft) => ({
        ...draft,
        lists: draft.lists.map((list) =>
          list.key === listKey ? { ...list, items: list.items.map((item, i) => (i === index ? value : item)) } : list
        )
      })),
    [update]
  )

  const setDefault = useCallback(
    <K extends keyof TrackerDefaults>(key: K, value: TrackerDefaults[K]) =>
      update((draft) => ({ ...draft, defaults: { ...draft.defaults, [key]: value } })),
    [update]
  )

  const addRow = useCallback(() => {
    const target = ROW_SHEETS[sheet]
    if (!target) return
    update((draft) => {
      if (target === 'setup') return { ...draft, setup: [...draft.setup, ''] }
      const rows = draft[target]
      // A new row carries the same columns as the sheet's existing ones, blank —
      // so it lines up under the same letters instead of being a ragged object.
      const blank = Object.fromEntries(Object.keys(rows[0] ?? {}).map((key) => [key, '']))
      return { ...draft, [target]: [...rows, blank] }
    })
  }, [sheet, update])

  const deleteRow = useCallback(
    (index: number) => {
      const target = ROW_SHEETS[sheet]
      if (!target) return
      update((draft) => {
        if (target === 'setup') return { ...draft, setup: draft.setup.filter((_, i) => i !== index) }
        return { ...draft, [target]: draft[target].filter((_, i) => i !== index) }
      })
    },
    [sheet, update]
  )

  const resetAll = useCallback(() => {
    setBusy(true)
    if (saveTimer.current) clearTimeout(saveTimer.current)
    pending.current = null
    resetTrackerWorkbooks()
      .then(setWorkbooks)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false))
  }, [])

  const money = useMemo(() => makeMoney(workbooks?.defaults.currency ?? 'PHP'), [workbooks?.defaults.currency])

  const sheets = workbook === 'ads' ? AD_SHEETS : INFL_SHEETS
  const active = sheets.find((s) => s.id === sheet) ?? sheets[0]

  const grid: SheetGrid | null = useMemo(() => {
    if (!workbooks) return null
    const ctx: SheetContext = { workbooks, money, setCell }
    switch (active.id) {
      case 'daily':
        return dailySheet(ctx, showFormulas)
      case 'campaigns':
        return campaignsSheet(ctx, showFormulas)
      case 'creatives':
        return creativesSheet(ctx, showFormulas)
      case 'leads':
        return leadsSheet(ctx)
      case 'budget':
        return budgetSheet(ctx, showFormulas)
      case 'iinfo':
        return influencerInfoSheet(ctx)
      case 'itracker':
        return influencerTrackerSheet(ctx, showFormulas)
      case 'isetup':
        return setupSheet(ctx)
      default:
        return null
    }
  }, [workbooks, money, setCell, active.id, showFormulas])

  /**
   * Turn the sheets into exportable tables.
   *
   * Built from the same SheetGrid the screen renders, not from `workbooks`, because the
   * stored workbook holds only input cells — every SUMIFS total, ROAS figure and status
   * roll-up is computed here. Exporting the stored state would hand the user a spreadsheet
   * with the interesting columns missing.
   */
  const exportSheets = useCallback((): ExportSheet[] => {
    if (!workbooks) return []
    const ctx: SheetContext = { workbooks, money, setCell }
    const builders: { name: string; grid: SheetGrid | null }[] = sheets.map((s) => {
      switch (s.id) {
        case 'daily': return { name: s.name, grid: dailySheet(ctx, false) }
        case 'campaigns': return { name: s.name, grid: campaignsSheet(ctx, false) }
        case 'creatives': return { name: s.name, grid: creativesSheet(ctx, false) }
        case 'leads': return { name: s.name, grid: leadsSheet(ctx) }
        case 'budget': return { name: s.name, grid: budgetSheet(ctx, false) }
        case 'iinfo': return { name: s.name, grid: influencerInfoSheet(ctx) }
        case 'itracker': return { name: s.name, grid: influencerTrackerSheet(ctx, false) }
        case 'isetup': return { name: s.name, grid: setupSheet(ctx) }
        default: return { name: s.name, grid: null }
      }
    })
    return builders
      .filter((b): b is { name: string; grid: SheetGrid } => b.grid !== null)
      .map(({ name, grid }) => ({
        name,
        columns: grid.cols.map((c) => c.head),
        rows: grid.rows.map((row) =>
          row.cells.map((cell) => {
            // One string per cell, whatever kind it is — an export has no notion of an
            // editable cell or a badge, only what it currently reads.
            if (cell.kind === 'edit') return cell.text
            if (cell.kind === 'select') return cell.value
            if (cell.kind === 'calc') return cell.text
            if (cell.kind === 'badge') return cell.value
            return cell.text
          })
        )
      }))
  }, [workbooks, money, setCell, sheets])

  const runExport = useCallback(
    async (format: 'csv' | 'xlsx') => {
      setExporting(format)
      try {
        const res = await exportTracker(format, workbook === 'ads' ? 'Ads-Tracker' : 'Influencer-Tracker', exportSheets())
        await window.api.openFile(res.path)
      } catch {
        // Surfaced by the shared error popup.
      } finally {
        setExporting('')
      }
    },
    [exportSheets, workbook]
  )

  if (error && !workbooks) {
    return (
      <div style={{ maxWidth: 1620, margin: '0 auto', padding: '30px 34px 70px' }}>
        <div style={sectionEyebrow}>Manage · Tracker Studio</div>
        <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)', marginTop: 8 }}>Couldn&rsquo;t load your trackers</div>
        <div style={{ font: "600 14px 'Quicksand'", color: 'var(--danger-ink)', marginTop: 6 }}>{error}</div>
      </div>
    )
  }

  if (!workbooks || !active) {
    return (
      <div style={{ maxWidth: 1620, margin: '0 auto', padding: '30px 34px 70px' }}>
        <div style={sectionEyebrow}>Manage · Tracker Studio</div>
        <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 10 }}>Opening your workbooks…</div>
      </div>
    )
  }

  const canAddRow = Boolean(ROW_SHEETS[active.id])
  const rowCountLabel =
    active.id === 'dashboard'
      ? '12 KPIs · 2 charts'
      : grid
        ? `${grid.rows.length} rows × ${grid.cols.length} cols`
        : active.id === 'settings'
          ? `${workbooks.lists.length} lists · 5 defaults`
          : '7 steps · 8 KPIs'

  return (
    <div style={{ maxWidth: 1620, margin: '0 auto', padding: '30px 34px 70px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 24, flexWrap: 'wrap', marginBottom: 18 }}>
        <div>
          <div style={sectionEyebrow}>Manage · Tracker Studio</div>
          <div style={{ font: "700 34px/1.15 'Kalam'", color: 'var(--ink)', marginTop: 6 }}>
            Your spreadsheets, <span style={{ color: 'var(--accent)' }}>minus the spreadsheet</span>.
          </div>
          <div style={{ font: "600 14px/1.5 'Quicksand'", color: 'var(--ink-muted)', maxWidth: 640, marginTop: 8 }}>
            Two live workbooks — every column, every formula, recalculated the moment you type. Nothing dropped, nothing rewritten.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-fainter)', minWidth: 52 }}>
            {saving ? 'Saving…' : 'Saved'}
          </span>
          <div
            onClick={() => setShowFormulas((v) => !v)}
            style={{
              ...pillBase,
              background: showFormulas ? 'var(--accent)' : 'var(--surface)',
              color: showFormulas ? 'var(--accent-ink)' : 'var(--ink)'
            }}
          >
            ƒ {showFormulas ? 'Showing formulas' : 'Show formulas'}
          </div>
          <div
            onClick={exporting ? undefined : () => void runExport('xlsx')}
            style={{ ...pillBase, background: 'var(--surface)', color: 'var(--ink)', opacity: exporting ? 0.6 : 1 }}
          >
            {exporting === 'xlsx' ? 'Exporting…' : 'Export .xlsx'}
          </div>
          <div
            onClick={exporting ? undefined : () => void runExport('csv')}
            style={{ ...pillBase, background: 'var(--surface)', color: 'var(--ink)', opacity: exporting ? 0.6 : 1 }}
          >
            {exporting === 'csv' ? 'Exporting…' : 'Export .csv'}
          </div>
          <div onClick={busy ? undefined : resetAll} style={{ ...pillBase, background: '#FBE8C0', color: 'var(--ink)', opacity: busy ? 0.6 : 1 }}>
            {busy ? 'Resetting…' : 'Reset data'}
          </div>
        </div>
      </div>

      {error ? (
        <div
          style={{
            marginBottom: 14,
            padding: '10px 14px',
            border: '2px solid var(--danger-border)',
            borderRadius: 12,
            background: '#FEE2E2',
            font: "600 12.5px 'Quicksand'",
            color: 'var(--danger-ink)'
          }}
        >
          {error}
        </div>
      ) : null}

      <div style={{ display: 'flex', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
        {(
          [
            { id: 'ads' as const, name: 'Social Media Ads Tracker', meta: '8 sheets', landing: 'dashboard' },
            { id: 'infl' as const, name: 'Influencer Outreach Tracker', meta: '3 sheets', landing: 'itracker' }
          ]
        ).map((wb) => {
          const on = workbook === wb.id
          return (
            <div
              key={wb.id}
              onClick={() => {
                setWorkbook(wb.id)
                setSheet(wb.landing)
              }}
              style={{
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '13px 22px',
                border: '2.5px solid var(--border)',
                borderRadius: 16,
                background: on ? 'var(--border)' : 'var(--surface)',
                color: on ? '#FFF7E9' : 'var(--ink)',
                boxShadow: on ? '3px 3px 0 var(--accent)' : '3px 3px 0 rgba(43, 36, 32, .15)'
              }}
            >
              <span style={{ font: "700 16px 'Kalam'" }}>{wb.name}</span>
              <span style={{ font: "600 12px 'Quicksand'", opacity: on ? 0.75 : 1, color: on ? undefined : 'var(--ink-faint)' }}>
                {wb.meta}
              </span>
            </div>
          )
        })}
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
        {sheets.map((tab) => {
          const on = tab.id === active.id
          return (
            <div
              key={tab.id}
              onClick={() => setSheet(tab.id)}
              style={{
                cursor: 'pointer',
                padding: '8px 16px',
                border: on ? '2px solid var(--border)' : '2px solid rgba(43, 36, 32, .25)',
                borderBottom: on ? '2px solid var(--surface)' : undefined,
                borderRadius: '11px 11px 0 0',
                background: on ? 'var(--surface)' : '#F2E9D8',
                color: on ? 'var(--ink)' : 'var(--ink-muted)',
                font: on ? "700 13.5px 'Quicksand'" : "600 13.5px 'Quicksand'",
                position: 'relative',
                top: on ? 2 : 0
              }}
            >
              {tab.name}
            </div>
          )
        })}
      </div>

      <div
        style={{
          border: '2.5px solid var(--border)',
          borderRadius: 20,
          background: 'var(--surface)',
          boxShadow: 'var(--shadow-md)',
          overflow: 'hidden'
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
            padding: '16px 20px',
            borderBottom: '2px solid var(--border)',
            background: '#FFF8EA',
            flexWrap: 'wrap'
          }}
        >
          <div>
            <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)' }}>{active.name}</div>
            <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 2 }}>{active.sub}</div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {canAddRow ? (
              <div
                onClick={addRow}
                style={{
                  cursor: 'pointer',
                  padding: '8px 16px',
                  border: '2px solid var(--border)',
                  borderRadius: 20,
                  background: '#A8DED2',
                  font: "700 12.5px 'Quicksand'",
                  color: 'var(--ink)',
                  boxShadow: 'var(--shadow-sm)'
                }}
              >
                + Add row
              </div>
            ) : null}
            <div
              style={{
                padding: '8px 14px',
                border: '2px dashed rgba(43, 36, 32, .3)',
                borderRadius: 20,
                font: "700 12px 'Quicksand'",
                color: 'var(--ink-muted)'
              }}
            >
              {rowCountLabel}
            </div>
          </div>
        </div>

        {active.id === 'dashboard' ? <DashboardPanel workbooks={workbooks} money={money} onFilter={setFilters} /> : null}
        {active.id === 'settings' ? <SettingsPanel workbooks={workbooks} onList={setListItem} onDefault={setDefault} /> : null}
        {active.id === 'instructions' ? <InstructionsPanel /> : null}
        {active.id === 'itracker' ? (
          <>
            <InfluencerSummary workbooks={workbooks} onPlatform={(platform) => setFilters({ influencerPlatform: platform })} />
            {grid ? <TrackerGrid cols={grid.cols} rows={grid.rows} showFormulas={showFormulas} /> : null}
          </>
        ) : null}
        {grid && active.id !== 'itracker' ? (
          <TrackerGrid cols={grid.cols} rows={grid.rows} showFormulas={showFormulas} onDeleteRow={canAddRow ? deleteRow : undefined} />
        ) : null}
      </div>
    </div>
  )
}
