/**
 * Shared cell primitives for Tracker Studio's grids.
 *
 * The colour rules here are not decoration — each one is a conditional format
 * carried over from the source workbooks, with the stop colours copied from the
 * sheets themselves so a cell that reads red in Excel reads red here.
 */
import { useEffect, useRef, type CSSProperties } from 'react'

/**
 * Status pill colours, from the Tracker Studio design.
 *
 * The design left the Lead Stage vocabulary (New / Contacted / Proposal / Won)
 * unmapped even though it mapped Qualified and Lost, which are shared with Lead
 * Status — so those four are filled in here from the same palette rather than
 * all falling through to neutral grey, which would have shown "Won" in the same
 * colour as "Closed".
 */
export const BADGE_COLORS: Record<string, [string, string]> = {
  // Campaign Status
  Active: ['#CDEBD8', '#14562F'],
  Paused: ['#FDF0C8', '#7A5B00'],
  Planning: ['#DCEEFB', '#2E6F97'],
  Completed: ['#EDE7DF', '#5A5248'],
  Cancelled: ['#F8D3CC', '#7A2418'],
  // Creatives — Winner?
  YES: ['#CDEBD8', '#14562F'],
  WATCH: ['#FDF0C8', '#7A5B00'],
  NO: ['#F8D3CC', '#7A2418'],
  // Lead Status / Lead Stage
  Converted: ['#CDEBD8', '#14562F'],
  Open: ['#FDF0C8', '#7A5B00'],
  Qualified: ['#DCEEFB', '#2E6F97'],
  Lost: ['#F8D3CC', '#7A2418'],
  Closed: ['#EDE7DF', '#5A5248'],
  New: ['#DCEEFB', '#2E6F97'],
  Contacted: ['#FDF0C8', '#7A5B00'],
  Proposal: ['#FDF0C8', '#7A5B00'],
  Won: ['#CDEBD8', '#14562F'],
  // Influencer response / collaboration status
  Pending: ['#FDF0C8', '#7A5B00'],
  Accepted: ['#CDEBD8', '#14562F'],
  Declined: ['#F8D3CC', '#7A2418'],
  'In Progress': ['#FDF0C8', '#7A5B00'],
  Collaborating: ['#CDEBD8', '#14562F'],
  'Not Collaborating': ['#EDE7DF', '#5A5248']
}

const BADGE_FALLBACK: [string, string] = ['#EDE7DF', '#5A5248']

/**
 * Three-colour-scale stops, verbatim from the workbooks' rules. Note
 * SCALE_BUDGET_USED runs the other way on purpose: on Campaigns "Budget Used %"
 * a *high* number is the warning, so green sits at the bottom of that scale.
 */
export const SCALE_ROAS = ['#FECACA', '#FEF3C7', '#DCFCE7']
export const SCALE_ROAS_DAILY = ['#FEE2E2', '#FEF3C7', '#DCFCE7']
export const SCALE_BUDGET_USED = ['#DCFCE7', '#FEF3C7', '#FECACA']

/** Budget & Targets variance columns, `H4<0` → red fill, red ink. */
export const NEGATIVE_TINT = '#FEE2E2'
export const NEGATIVE_INK = '#B91C1C'

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '')
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}

function mix(a: string, b: string, k: number): string {
  const [r1, g1, b1] = hexToRgb(a)
  const [r2, g2, b2] = hexToRgb(b)
  const at = (x: number, y: number): string =>
    Math.round(x + (y - x) * k)
      .toString(16)
      .padStart(2, '0')
  return `#${at(r1, r2)}${at(g1, g2)}${at(b1, b2)}`
}

/** PERCENTILE with linear interpolation, matching Excel's own. */
function percentile(sorted: number[], p: number): number {
  if (sorted.length === 1) return sorted[0]
  const idx = p * (sorted.length - 1)
  const lo = Math.floor(idx)
  const hi = Math.ceil(idx)
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo)
}

/**
 * Excel's 3-colour scale. Every rule in these workbooks is anchored
 * min / percentile-50 / max, so the midpoint is the column's *median* — not the
 * arithmetic middle of the range, which would shift the whole gradient whenever
 * one outlier row appeared.
 */
export function colorScale(value: number, values: number[], stops: string[]): string | undefined {
  if (!values.length) return undefined
  const sorted = [...values].sort((a, b) => a - b)
  const min = sorted[0]
  const max = sorted[sorted.length - 1]
  if (min === max) return stops[1]
  const mid = percentile(sorted, 0.5)
  const clamp = (k: number): number => Math.max(0, Math.min(1, k))
  if (value <= mid) return mix(stops[0], stops[1], mid === min ? 1 : clamp((value - min) / (mid - min)))
  return mix(stops[1], stops[2], max === mid ? 1 : clamp((value - mid) / (max - mid)))
}

// --- Cell model ------------------------------------------------------------

export interface ColumnDef {
  /** The spreadsheet column letter, shown under the heading. */
  letter: string
  head: string
  tag: 'INPUT' | 'ƒ' | 'LIST'
}

export type Cell =
  | { kind: 'edit'; text: string; onCommit: (text: string) => void }
  | { kind: 'select'; value: string; options: string[]; onChange: (value: string) => void }
  | { kind: 'calc'; text: string; formula: string; tint?: string; ink?: string }
  | { kind: 'badge'; value: string }
  | { kind: 'plain'; text: string }

const CELL_PAD = '9px 13px'

/**
 * A contentEditable cell that stays in step with its formatted value.
 *
 * The DOM text is written imperatively rather than passed as children: React
 * will not touch a node whose virtual text is unchanged, so a cell edited to
 * something that normalises back to the same value ("1,850" → 1850 → "₱1,850")
 * would otherwise keep whatever was typed. Re-syncing on blur and on every
 * value change means the cell always shows the canonical, formatted figure.
 */
function EditableCell({ text, onCommit }: { text: string; onCommit: (text: string) => void }): React.JSX.Element {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (el && document.activeElement !== el && el.innerText !== text) el.innerText = text
  }, [text])

  return (
    <div
      ref={ref}
      contentEditable
      suppressContentEditableWarning
      onBlur={(e) => {
        const typed = e.currentTarget.innerText.trim()
        // Restore the incoming value first; if the commit produces a different
        // one the effect above overwrites it on the next render.
        e.currentTarget.innerText = text
        if (typed !== text) onCommit(typed)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          e.currentTarget.blur()
        } else if (e.key === 'Escape') {
          e.currentTarget.innerText = text
          e.currentTarget.blur()
        }
      }}
      style={{
        padding: CELL_PAD,
        font: "600 12.5px 'Quicksand'",
        color: 'var(--ink-body)',
        minWidth: 64,
        cursor: 'text'
      }}
    />
  )
}

function badgeStyle(value: string): CSSProperties {
  const [bg, fg] = BADGE_COLORS[value] ?? BADGE_FALLBACK
  return {
    display: 'inline-block',
    padding: '4px 11px',
    borderRadius: 20,
    font: "700 11.5px 'Quicksand'",
    border: '1.5px solid rgba(43, 36, 32, .35)',
    // backgroundColor, not the `background` shorthand: SelectCell reuses this and
    // the shorthand would wipe out the dropdown arrow tokens.css paints via
    // background-image (and React warns about mixing the two on rerender).
    backgroundColor: bg,
    color: fg
  }
}

/**
 * A list-validated column. The workbook's data validation is a dropdown, so this
 * stays a real <select> — styled as the design's status pill — rather than a
 * read-only badge, which would have dropped the ability to change a status.
 */
function SelectCell({
  value,
  options,
  onChange
}: {
  value: string
  options: string[]
  onChange: (value: string) => void
}): React.JSX.Element {
  // A value already in the data but missing from Settings still has to be
  // selectable, or opening the dropdown would silently rewrite the cell.
  const list = options.includes(value) || value === '' ? options : [value, ...options]
  return (
    <div style={{ padding: '8px 13px' }}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ ...badgeStyle(value), appearance: 'none', cursor: 'pointer', paddingRight: 22, backgroundPosition: 'right 7px center' }}
      >
        {value === '' ? <option value="" /> : null}
        {list.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </div>
  )
}

export function TrackerCell({ cell, showFormulas }: { cell: Cell; showFormulas: boolean }): React.JSX.Element {
  if (cell.kind === 'edit') return <EditableCell text={cell.text} onCommit={cell.onCommit} />
  if (cell.kind === 'select') return <SelectCell value={cell.value} options={cell.options} onChange={cell.onChange} />
  if (cell.kind === 'badge') {
    if (!cell.value) return <div style={{ padding: CELL_PAD }} />
    return (
      <div style={{ padding: '8px 13px' }}>
        <span style={badgeStyle(cell.value)}>{cell.value}</span>
      </div>
    )
  }
  if (cell.kind === 'plain') {
    return <div style={{ padding: CELL_PAD, font: "600 12.5px 'Quicksand'", color: 'var(--ink-body)' }}>{cell.text}</div>
  }
  return (
    <div
      title={cell.formula}
      style={{
        padding: CELL_PAD,
        font: "700 12.5px 'Quicksand'",
        background: cell.tint ?? '#F6F1E2',
        color: cell.ink ?? 'var(--ink-body)',
        textAlign: showFormulas ? 'left' : 'right',
        whiteSpace: showFormulas ? 'nowrap' : undefined
      }}
    >
      {showFormulas ? cell.formula : cell.text}
    </div>
  )
}

export interface GridRow {
  cells: Cell[]
}

/**
 * The scrolling sheet grid: sticky dark header, one table cell per column.
 *
 * `onDeleteRow` adds a trailing remove control. The spreadsheets have no such
 * column — it exists because this version keeps your data between sessions, so
 * a row added by mistake needs a way out that isn't "reset the whole workbook".
 */
export default function TrackerGrid({
  cols,
  rows,
  showFormulas,
  onDeleteRow
}: {
  cols: ColumnDef[]
  rows: GridRow[]
  showFormulas: boolean
  onDeleteRow?: (index: number) => void
}): React.JSX.Element {
  return (
    <div style={{ overflow: 'auto', maxHeight: 620 }}>
      <div style={{ display: 'table', minWidth: '100%', borderCollapse: 'collapse' }}>
        <div style={{ display: 'table-row' }}>
          {cols.map((col) => (
            <div
              key={col.letter}
              style={{
                display: 'table-cell',
                position: 'sticky',
                top: 0,
                zIndex: 5,
                background: 'var(--border)',
                color: '#FFF7E9',
                padding: '11px 13px',
                font: "700 11.5px 'Quicksand'",
                letterSpacing: '.4px',
                whiteSpace: 'nowrap',
                borderRight: '1px solid rgba(255,247,233,.18)'
              }}
            >
              <div>{col.head}</div>
              <div style={{ font: "600 9.5px 'Quicksand'", opacity: 0.55, letterSpacing: '1px' }}>
                {col.letter} · {col.tag}
              </div>
            </div>
          ))}
          {onDeleteRow ? (
            <div
              style={{
                display: 'table-cell',
                position: 'sticky',
                top: 0,
                zIndex: 5,
                background: 'var(--border)',
                padding: '11px 10px'
              }}
            />
          ) : null}
        </div>
        {rows.map((row, rowIndex) => (
          <div key={rowIndex} style={{ display: 'table-row' }}>
            {row.cells.map((cell, cellIndex) => (
              <div
                key={cols[cellIndex]?.letter ?? cellIndex}
                style={{
                  display: 'table-cell',
                  borderBottom: '1px solid #EAE0CE',
                  borderRight: '1px solid #F2EADB',
                  verticalAlign: 'middle',
                  whiteSpace: 'nowrap'
                }}
              >
                <TrackerCell cell={cell} showFormulas={showFormulas} />
              </div>
            ))}
            {onDeleteRow ? (
              <div
                style={{
                  display: 'table-cell',
                  borderBottom: '1px solid #EAE0CE',
                  verticalAlign: 'middle',
                  textAlign: 'center',
                  padding: '0 8px'
                }}
              >
                <button
                  type="button"
                  title="Remove this row"
                  aria-label="Remove this row"
                  onClick={() => onDeleteRow(rowIndex)}
                  style={{ font: "700 14px 'Quicksand'", color: 'var(--ink-fainter)', padding: '2px 6px', lineHeight: 1 }}
                >
                  ×
                </button>
              </div>
            ) : null}
          </div>
        ))}
        {rows.length === 0 ? (
          <div style={{ display: 'table-row' }}>
            <div
              style={{
                display: 'table-cell',
                padding: '22px 14px',
                font: "600 13px 'Quicksand'",
                color: 'var(--ink-faint)'
              }}
            >
              No rows yet — use “+ Add row” to start this sheet.
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
