/**
 * Column layouts and cell construction for every sheet in both workbooks.
 *
 * Each builder mirrors one sheet: the same columns, in the same order, under
 * the same letters, with input cells editable and every derived column read
 * only and annotated with the formula it came from (hover a calculated cell, or
 * hit "Show formulas", to see it).
 */
import {
  budgetDerived,
  campaignDerived,
  creativeDerived,
  dailyDerived,
  filterInfluencers,
  formatDate,
  formatMonth,
  nf,
  num,
  parseDateish,
  pct,
  ratio,
  type ListDef,
  type Row,
  type Workbooks
} from './formulas'
import {
  colorScale,
  NEGATIVE_INK,
  NEGATIVE_TINT,
  SCALE_BUDGET_USED,
  SCALE_ROAS,
  SCALE_ROAS_DAILY,
  type Cell,
  type ColumnDef,
  type GridRow
} from './ui'

export type SheetKey = 'daily' | 'campaigns' | 'creatives' | 'leads' | 'budget' | 'influencers' | 'setup'

/**
 * Input columns holding dates, and those holding numbers, per sheet — the sheet
 * knows the type of each cell, so a typed value is coerced the way Excel would
 * coerce it rather than being stored as whatever string was entered.
 */
const DATE_COLUMNS: Record<string, string[]> = {
  daily: ['A'],
  campaigns: ['G', 'H'],
  creatives: ['J'],
  leads: ['B', 'J', 'K'],
  budget: ['A'],
  influencers: ['G', 'I', 'L', 'M']
}

const NUMBER_COLUMNS: Record<string, string[]> = {
  daily: ['H', 'I', 'J', 'K', 'L', 'M', 'N'],
  campaigns: ['J', 'K'],
  leads: ['L'],
  budget: ['C', 'D', 'E', 'F'],
  influencers: ['E']
}

/**
 * Coerce what was typed into a cell to that column's type.
 *
 * "-" survives untouched in date columns: the influencer sheet uses it as its
 * own "not applicable" marker (Jane Blogger declined, so there is no follow-up
 * date), and turning that into a real date would invent a fact.
 */
export function commitValue(sheet: SheetKey, column: string, text: string): string | number {
  if (DATE_COLUMNS[sheet]?.includes(column)) {
    return text === '' || text === '-' ? text : parseDateish(text)
  }
  if (NUMBER_COLUMNS[sheet]?.includes(column)) {
    return text === '' ? 0 : num(text.replace(/[^0-9.-]/g, ''))
  }
  return text
}

/**
 * List-validated columns: the union of the columns the workbook actually puts a
 * data-validation dropdown on, and the columns the Tracker Studio design marks
 * "LIST". Keeping the workbook's set preserves dropdowns the design rendered as
 * plain text; keeping the design's set covers Creatives → Status, which the
 * workbook left unvalidated but whose values come from the Campaign Status list
 * all the same. An existing value outside its list is always kept selectable,
 * so nothing already in the data gets rewritten by opening a dropdown.
 */
const LIST_COLUMNS: Record<string, Record<string, string>> = {
  daily: { B: 'clients', C: 'platforms' },
  campaigns: { C: 'platforms', F: 'objectives', L: 'campaignStatus', M: 'owners' },
  creatives: { B: 'clients', C: 'platforms', F: 'creativeFormat', H: 'cta', K: 'campaignStatus' },
  leads: { D: 'platforms', H: 'leadStage', I: 'owners', M: 'leadStatus' },
  budget: { B: 'clients' },
  influencers: { D: '@setup', H: '@response', J: '@collaboration' }
}

/** Influencer Information's two inline validation lists, verbatim from the sheet. */
const RESPONSE_OPTIONS = ['Pending', 'Declined', 'Accepted']
const COLLABORATION_OPTIONS = ['In Progress', 'Not Collaborating', 'Collaborating']

export interface SheetContext {
  workbooks: Workbooks
  money: (value: unknown) => string
  /** Replace one input cell; the caller persists and re-renders. */
  setCell: (sheet: SheetKey, index: number, column: string, value: string | number) => void
}

export interface SheetGrid {
  cols: ColumnDef[]
  rows: GridRow[]
}

function listItems(lists: ListDef[], key: string): string[] {
  return lists.find((list) => list.key === key)?.items ?? []
}

function optionsFor(ctx: SheetContext, sheet: SheetKey, column: string): string[] | null {
  const key = LIST_COLUMNS[sheet]?.[column]
  if (!key) return null
  if (key === '@setup') return ctx.workbooks.setup
  if (key === '@response') return RESPONSE_OPTIONS
  if (key === '@collaboration') return COLLABORATION_OPTIONS
  return listItems(ctx.workbooks.lists, key)
}

/** Build the column headers, tagging each as an input, a dropdown or a formula. */
function columns(sheet: SheetKey, ctx: SheetContext, defs: [string, string, 'INPUT' | 'ƒ'][]): ColumnDef[] {
  return defs.map(([letter, head, tag]) => ({
    letter,
    head,
    tag: tag === 'INPUT' && optionsFor(ctx, sheet, letter) ? 'LIST' : tag
  }))
}

/** An editable (or list-validated) input cell for `column` of `row`. */
function input(ctx: SheetContext, sheet: SheetKey, index: number, row: Row, column: string, display?: string): Cell {
  const raw = row[column]
  const options = optionsFor(ctx, sheet, column)
  if (options) {
    return {
      kind: 'select',
      value: String(raw ?? ''),
      options,
      onChange: (value) => ctx.setCell(sheet, index, column, value)
    }
  }
  return {
    kind: 'edit',
    text: display ?? String(raw ?? ''),
    onCommit: (text) => ctx.setCell(sheet, index, column, commitValue(sheet, column, text))
  }
}

const calc = (text: string, formula: string, tint?: string, ink?: string): Cell => ({
  kind: 'calc',
  text,
  formula,
  tint,
  ink
})

/** Budget & Targets variance columns turn red when negative (`=H4<0`). */
const varianceTint = (value: number): [string | undefined, string | undefined] =>
  value < 0 ? [NEGATIVE_TINT, NEGATIVE_INK] : [undefined, undefined]

// --- Daily Performance -----------------------------------------------------

export function dailySheet(ctx: SheetContext, showFormulas: boolean): SheetGrid {
  const { money } = ctx
  const rows = ctx.workbooks.daily
  const roasValues = rows.map((row) => dailyDerived(row).roas)

  return {
    cols: columns('daily', ctx, [
      ['A', 'Date', 'INPUT'], ['B', 'Client', 'INPUT'], ['C', 'Platform', 'INPUT'], ['D', 'Campaign ID', 'INPUT'],
      ['E', 'Campaign', 'INPUT'], ['F', 'Ad Set', 'INPUT'], ['G', 'Ad Name', 'INPUT'], ['H', 'Spend', 'INPUT'],
      ['I', 'Impressions', 'INPUT'], ['J', 'Reach', 'INPUT'], ['K', 'Link Clicks', 'INPUT'], ['L', 'Leads', 'INPUT'],
      ['M', 'Purchases', 'INPUT'], ['N', 'Revenue', 'INPUT'], ['O', 'CPM', 'ƒ'], ['P', 'CPC', 'ƒ'], ['Q', 'CTR', 'ƒ'],
      ['R', 'CPL', 'ƒ'], ['S', 'CPA', 'ƒ'], ['T', 'Conv. Rate', 'ƒ'], ['U', 'ROAS', 'ƒ'], ['V', 'Frequency', 'ƒ'],
      ['W', 'Notes', 'INPUT']
    ]),
    rows: rows.map((row, i) => {
      const n = i + 4 // the sheet's own row number, for the formula annotations
      const d = dailyDerived(row)
      const E = (column: string, display?: string): Cell => input(ctx, 'daily', i, row, column, display)
      return {
        cells: [
          E('A', formatDate(row.A)), E('B'), E('C'), E('D'), E('E'), E('F'), E('G'),
          E('H', row.H === '' ? '' : money(row.H)), E('I', nf(row.I)), E('J', nf(row.J)), E('K', nf(row.K)),
          E('L', nf(row.L)), E('M', nf(row.M)), E('N', row.N === '' ? '' : money(row.N)),
          calc(money(d.cpm), `=IFERROR(H${n}/I${n}*1000,0)`),
          calc(money(d.cpc), `=IFERROR(H${n}/K${n},0)`),
          calc(pct(d.ctr), `=IFERROR(K${n}/I${n},0)`),
          calc(money(d.cpl), `=IFERROR(H${n}/L${n},0)`),
          calc(money(d.cpa), `=IFERROR(H${n}/M${n},0)`),
          calc(pct(d.convRate), `=IFERROR(M${n}/K${n},0)`),
          calc(ratio(d.roas), `=IFERROR(N${n}/H${n},0)`, showFormulas ? undefined : colorScale(d.roas, roasValues, SCALE_ROAS_DAILY)),
          calc(ratio(d.frequency), `=IFERROR(I${n}/J${n},0)`),
          E('W')
        ]
      }
    })
  }
}

// --- Campaigns -------------------------------------------------------------

export function campaignsSheet(ctx: SheetContext, showFormulas: boolean): SheetGrid {
  const { money } = ctx
  const daily = ctx.workbooks.daily
  const rows = ctx.workbooks.campaigns
  const derived = rows.map((row) => campaignDerived(daily, row))
  const usedValues = derived.map((d) => d.budgetUsed)
  const roasValues = derived.map((d) => d.roas)

  return {
    cols: columns('campaigns', ctx, [
      ['A', 'Campaign ID', 'INPUT'], ['B', 'Client', 'INPUT'], ['C', 'Platform', 'INPUT'], ['D', 'Ad Account', 'INPUT'],
      ['E', 'Campaign Name', 'INPUT'], ['F', 'Objective', 'INPUT'], ['G', 'Start Date', 'INPUT'], ['H', 'End Date', 'INPUT'],
      ['I', 'Budget Type', 'INPUT'], ['J', 'Planned Budget', 'INPUT'], ['K', 'Daily Budget', 'INPUT'], ['L', 'Status', 'INPUT'],
      ['M', 'Owner', 'INPUT'], ['N', 'Target Audience', 'INPUT'], ['O', 'Landing Page', 'INPUT'], ['P', 'UTM Code', 'INPUT'],
      ['Q', 'Actual Spend', 'ƒ'], ['R', 'Remaining Budget', 'ƒ'], ['S', 'Budget Used %', 'ƒ'], ['T', 'Days Remaining', 'ƒ'],
      ['U', 'Actual Revenue', 'ƒ'], ['V', 'ROAS', 'ƒ']
    ]),
    rows: rows.map((row, i) => {
      const n = i + 4
      const d = derived[i]
      const E = (column: string, display?: string): Cell => input(ctx, 'campaigns', i, row, column, display)
      const sumif = (source: string): string =>
        `=SUMIF('Daily Performance'!$D$4:$D$503,A${n},'Daily Performance'!$${source}$4:$${source}$503)`
      return {
        cells: [
          E('A'), E('B'), E('C'), E('D'), E('E'), E('F'), E('G', formatDate(row.G)), E('H', formatDate(row.H)), E('I'),
          E('J', row.J === '' ? '' : money(row.J)), E('K', row.K === '' ? '' : money(row.K)), E('L'), E('M'), E('N'),
          E('O'), E('P'),
          calc(money(d.actualSpend), sumif('H')),
          calc(money(d.remainingBudget), `=MAX(J${n}-Q${n},0)`),
          calc(pct(d.budgetUsed), `=IFERROR(Q${n}/J${n},0)`, showFormulas ? undefined : colorScale(d.budgetUsed, usedValues, SCALE_BUDGET_USED)),
          calc(nf(d.daysRemaining), `=MAX(H${n}-TODAY(),0)`),
          calc(money(d.actualRevenue), sumif('N')),
          calc(ratio(d.roas), `=IFERROR(U${n}/Q${n},0)`, showFormulas ? undefined : colorScale(d.roas, roasValues, SCALE_ROAS))
        ]
      }
    })
  }
}

// --- Creatives -------------------------------------------------------------

export function creativesSheet(ctx: SheetContext, showFormulas: boolean): SheetGrid {
  const { money } = ctx
  const daily = ctx.workbooks.daily
  const rows = ctx.workbooks.creatives
  const derived = rows.map((row) => creativeDerived(daily, row))
  const roasValues = derived.map((d) => d.roas)

  return {
    cols: columns('creatives', ctx, [
      ['A', 'Creative ID', 'INPUT'], ['B', 'Client', 'INPUT'], ['C', 'Platform', 'INPUT'], ['D', 'Campaign ID', 'INPUT'],
      ['E', 'Creative / Ad Name', 'INPUT'], ['F', 'Format', 'INPUT'], ['G', 'Hook / Angle', 'INPUT'], ['H', 'CTA', 'INPUT'],
      ['I', 'Asset URL', 'INPUT'], ['J', 'Launch Date', 'INPUT'], ['K', 'Status', 'INPUT'], ['L', 'Spend', 'ƒ'],
      ['M', 'Impressions', 'ƒ'], ['N', 'Clicks', 'ƒ'], ['O', 'Leads', 'ƒ'], ['P', 'Purchases', 'ƒ'], ['Q', 'Revenue', 'ƒ'],
      ['R', 'CTR', 'ƒ'], ['S', 'CPL', 'ƒ'], ['T', 'ROAS', 'ƒ'], ['U', 'Winner?', 'ƒ'], ['V', 'Notes', 'INPUT']
    ]),
    rows: rows.map((row, i) => {
      const n = i + 4
      const d = derived[i]
      const E = (column: string, display?: string): Cell => input(ctx, 'creatives', i, row, column, display)
      // Creatives roll up by *ad name* — Daily Performance column G.
      const sumif = (source: string): string =>
        `=SUMIF('Daily Performance'!$G$4:$G$503,E${n},'Daily Performance'!$${source}$4:$${source}$503)`
      const winnerFormula = `=IF(T${n}>=3,"YES",IF(T${n}>=2,"WATCH","NO"))`
      return {
        cells: [
          E('A'), E('B'), E('C'), E('D'), E('E'), E('F'), E('G'), E('H'), E('I'), E('J', formatDate(row.J)), E('K'),
          calc(money(d.spend), sumif('H')),
          calc(nf(d.impressions), sumif('I')),
          calc(nf(d.clicks), sumif('K')),
          calc(nf(d.leads), sumif('L')),
          calc(nf(d.purchases), sumif('M')),
          calc(money(d.revenue), sumif('N')),
          calc(pct(d.ctr), `=IFERROR(N${n}/M${n},0)`),
          calc(money(d.cpl), `=IFERROR(L${n}/O${n},0)`),
          calc(ratio(d.roas), `=IFERROR(Q${n}/L${n},0)`, showFormulas ? undefined : colorScale(d.roas, roasValues, SCALE_ROAS)),
          showFormulas ? calc('', winnerFormula) : { kind: 'badge', value: d.winner },
          E('V')
        ]
      }
    })
  }
}

// --- Leads & Sales ---------------------------------------------------------

export function leadsSheet(ctx: SheetContext): SheetGrid {
  const { money } = ctx
  const rows = ctx.workbooks.leads
  return {
    cols: columns('leads', ctx, [
      ['A', 'Lead ID', 'INPUT'], ['B', 'Lead Date', 'INPUT'], ['C', 'Client', 'INPUT'], ['D', 'Platform', 'INPUT'],
      ['E', 'Campaign ID', 'INPUT'], ['F', 'Lead / Customer Name', 'INPUT'], ['G', 'Contact Details', 'INPUT'],
      ['H', 'Stage', 'INPUT'], ['I', 'Assigned To', 'INPUT'], ['J', 'Follow-up Date', 'INPUT'], ['K', 'Sale Date', 'INPUT'],
      ['L', 'Revenue', 'INPUT'], ['M', 'Status', 'INPUT'], ['N', 'Notes', 'INPUT']
    ]),
    rows: rows.map((row, i) => {
      const E = (column: string, display?: string): Cell => input(ctx, 'leads', i, row, column, display)
      return {
        cells: [
          E('A'), E('B', formatDate(row.B)), E('C'), E('D'), E('E'), E('F'), E('G'), E('H'), E('I'),
          E('J', formatDate(row.J)), E('K', row.K === '' ? '' : formatDate(row.K)),
          E('L', row.L === '' ? '' : money(row.L)), E('M'), E('N')
        ]
      }
    })
  }
}

// --- Budget & Targets ------------------------------------------------------

export function budgetSheet(ctx: SheetContext, showFormulas: boolean): SheetGrid {
  const { money } = ctx
  const daily = ctx.workbooks.daily
  const rows = ctx.workbooks.budget
  const derived = rows.map((row) => budgetDerived(daily, row))
  const roasValues = derived.map((d) => d.roas)

  return {
    cols: columns('budget', ctx, [
      ['A', 'Month', 'INPUT'], ['B', 'Client', 'INPUT'], ['C', 'Planned Spend', 'INPUT'], ['D', 'Lead Target', 'INPUT'],
      ['E', 'Purchase Target', 'INPUT'], ['F', 'Revenue Target', 'INPUT'], ['G', 'Actual Spend', 'ƒ'],
      ['H', 'Spend Variance', 'ƒ'], ['I', 'Actual Leads', 'ƒ'], ['J', 'Lead Variance', 'ƒ'], ['K', 'Actual Purchases', 'ƒ'],
      ['L', 'Purchase Variance', 'ƒ'], ['M', 'Actual Revenue', 'ƒ'], ['N', 'Revenue Variance', 'ƒ'], ['O', 'ROAS', 'ƒ']
    ]),
    rows: rows.map((row, i) => {
      const n = i + 4
      const d = derived[i]
      const E = (column: string, display?: string): Cell => input(ctx, 'budget', i, row, column, display)
      const sumifs = (source: string): string =>
        `=SUMIFS('Daily Performance'!$${source}$4:$${source}$503,'Daily Performance'!$B$4:$B$503,B${n},` +
        `'Daily Performance'!$A$4:$A$503,">="&A${n},'Daily Performance'!$A$4:$A$503,"<"&DATE(YEAR(A${n}),MONTH(A${n})+1,1))`
      const [spendTint, spendInk] = varianceTint(d.spendVariance)
      const [leadTint, leadInk] = varianceTint(d.leadVariance)
      const [purchaseTint, purchaseInk] = varianceTint(d.purchaseVariance)
      const [revenueTint, revenueInk] = varianceTint(d.revenueVariance)
      return {
        cells: [
          E('A', formatMonth(row.A)), E('B'), E('C', money(row.C)), E('D', nf(row.D)), E('E', nf(row.E)), E('F', money(row.F)),
          calc(money(d.actualSpend), sumifs('H')),
          calc(money(d.spendVariance), `=C${n}-G${n}`, showFormulas ? undefined : spendTint, showFormulas ? undefined : spendInk),
          calc(nf(d.actualLeads), sumifs('L')),
          calc(nf(d.leadVariance), `=I${n}-D${n}`, showFormulas ? undefined : leadTint, showFormulas ? undefined : leadInk),
          calc(nf(d.actualPurchases), sumifs('M')),
          calc(nf(d.purchaseVariance), `=K${n}-E${n}`, showFormulas ? undefined : purchaseTint, showFormulas ? undefined : purchaseInk),
          calc(money(d.actualRevenue), sumifs('N')),
          calc(money(d.revenueVariance), `=M${n}-F${n}`, showFormulas ? undefined : revenueTint, showFormulas ? undefined : revenueInk),
          calc(ratio(d.roas), `=IFERROR(M${n}/G${n},0)`, showFormulas ? undefined : colorScale(d.roas, roasValues, SCALE_ROAS))
        ]
      }
    })
  }
}

// --- Influencer Information ------------------------------------------------

export function influencerInfoSheet(ctx: SheetContext): SheetGrid {
  const rows = ctx.workbooks.influencers
  return {
    cols: columns('influencers', ctx, [
      ['B', 'Influencer Name', 'INPUT'], ['C', 'Contact Information', 'INPUT'], ['D', 'Social Media Platform', 'INPUT'],
      ['E', 'Follower Count', 'INPUT'], ['F', 'Collaboration Type', 'INPUT'], ['G', 'Outreach Date', 'INPUT'],
      ['H', 'Response Status', 'INPUT'], ['I', 'Follow-Up Date', 'INPUT'], ['J', 'Collaboration Status', 'INPUT'],
      ['K', 'Collaboration Details', 'INPUT'], ['L', 'Content Delivery Deadline', 'INPUT'],
      ['M', 'Content Approval Date', 'INPUT'], ['N', 'Payment Details', 'INPUT'], ['O', 'Post Analytics Tracking', 'INPUT']
    ]),
    rows: rows.map((row, i) => {
      const E = (column: string, display?: string): Cell => input(ctx, 'influencers', i, row, column, display)
      return {
        cells: [
          E('B'), E('C'), E('D'), E('E', nf(row.E)), E('F'), E('G', formatDate(row.G)), E('H'),
          E('I', row.I === '-' ? '-' : formatDate(row.I)), E('J'), E('K'), E('L', formatDate(row.L)),
          E('M', formatDate(row.M)), E('N'), E('O')
        ]
      }
    })
  }
}

// --- Marketing Influencer Outreach Tracker ---------------------------------

/**
 * The tracker sheet is entirely formula-driven: every column is the same
 * FILTER() over Influencer Information, keyed on the platform picked in F4.
 * Nothing here is editable — records are edited on the Influencer Information
 * tab, exactly as in the workbook.
 *
 * Note the column letters skip F, J and N: those are the sheet's spacer
 * columns, and the layout is kept as-is so a cell reference still lands where
 * the spreadsheet puts it.
 */
export function influencerTrackerSheet(ctx: SheetContext, showFormulas: boolean): SheetGrid {
  const filtered = filterInfluencers(ctx.workbooks.influencers, ctx.workbooks.filters.influencerPlatform)
  const filterFormula = (source: string): string =>
    `=IFERROR(FILTER('Influencer Information'!${source}5:${source}1007,` +
    `'Influencer Information'!$D$5:$D1007=$F$4),"no data")`

  return {
    cols: [
      ['B', 'Influencer Name'], ['C', 'Contact Information'], ['D', 'Follower Count'], ['E', 'Collaboration Type'],
      ['G', 'Outreach Date'], ['H', 'Response Status'], ['I', 'Follow-Up Date'], ['K', 'Collaboration Status'],
      ['L', 'Collaboration Details'], ['M', 'Content Delivery Deadline'], ['O', 'Content Approval Date'],
      ['P', 'Payment Details'], ['Q', 'Post Analytics Tracking']
    ].map(([letter, head]) => ({ letter, head, tag: 'ƒ' as const })),
    rows: filtered.map((row) => ({
      cells: [
        calc(String(row.B ?? ''), filterFormula('B')),
        calc(String(row.C ?? ''), filterFormula('C')),
        calc(nf(row.E), filterFormula('E')),
        calc(String(row.F ?? ''), filterFormula('F')),
        calc(formatDate(row.G), filterFormula('G')),
        showFormulas ? calc('', filterFormula('H')) : { kind: 'badge', value: String(row.H ?? '') },
        calc(row.I === '-' ? '-' : formatDate(row.I), filterFormula('I')),
        showFormulas ? calc('', filterFormula('J')) : { kind: 'badge', value: String(row.J ?? '') },
        calc(String(row.K ?? ''), filterFormula('K')),
        calc(formatDate(row.L), filterFormula('L')),
        calc(formatDate(row.M), filterFormula('M')),
        calc(String(row.N ?? ''), filterFormula('N')),
        calc(String(row.O ?? ''), filterFormula('O'))
      ]
    }))
  }
}

// --- Setup -----------------------------------------------------------------

export function setupSheet(ctx: SheetContext): SheetGrid {
  return {
    cols: [{ letter: 'B', head: 'Social Media Platform', tag: 'INPUT' }],
    rows: ctx.workbooks.setup.map((value, i) => ({
      cells: [{ kind: 'edit', text: value, onCommit: (text) => ctx.setCell('setup', i, 'B', text) }]
    }))
  }
}
