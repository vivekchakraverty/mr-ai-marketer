/**
 * Tracker Studio's calculation engine — the two source workbooks' formulas,
 * ported one for one.
 *
 * Every function here corresponds to a specific cell formula in either "Social
 * Media Ads Tracker" or "Marketing Influencer Outreach Tracker", and each is
 * annotated with the formula it implements. Nothing is stored: derived values
 * are recomputed from the input cells on every render, the way a spreadsheet
 * recalculates, so a figure can never drift from the numbers behind it.
 *
 * Dates are Excel serial numbers (days since 1899-12-30) throughout, because
 * that is what the workbook's own date arithmetic operates on — `MAX(H4-
 * TODAY(),0)` and the `>=month start` / `<next month` comparisons stay exact
 * integer maths instead of becoming timezone-sensitive Date subtraction.
 */

/** One spreadsheet row, keyed by column letter exactly as the sheet addresses it. */
export type Row = Record<string, string | number>

export interface ListDef {
  key: string
  name: string
  items: string[]
}

export interface TrackerDefaults {
  defaultStartDate: number
  defaultEndDate: number
  currency: string
  timeZone: string
  notes: string
}

export interface TrackerFilters {
  start: number
  end: number
  platform: string
  client: string
  influencerPlatform: string
}

export interface Workbooks {
  daily: Row[]
  campaigns: Row[]
  creatives: Row[]
  leads: Row[]
  budget: Row[]
  influencers: Row[]
  lists: ListDef[]
  defaults: TrackerDefaults
  filters: TrackerFilters
  setup: string[]
  companyLine: string
}

// --- Dates -----------------------------------------------------------------

const EPOCH = Date.UTC(1899, 11, 30)
const DAY = 86400000
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function serialToDate(serial: number): Date {
  return new Date(EPOCH + Number(serial) * DAY)
}

/** Blank and the sheet's literal "-" placeholder pass straight through. */
export function formatDate(value: string | number): string {
  if (value === '' || value === null || value === undefined) return ''
  if (typeof value === 'string' && !/^\d+(\.\d+)?$/.test(value.trim())) return value
  const n = Number(value)
  if (Number.isNaN(n)) return String(value)
  const d = serialToDate(n)
  return `${String(d.getUTCDate()).padStart(2, '0')} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

/** Budget & Targets column A renders `mmm yyyy`, not a full date. */
export function formatMonth(value: string | number): string {
  const n = Number(value)
  if (!value || Number.isNaN(n)) return String(value ?? '')
  const d = serialToDate(n)
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

export function monthLabel(monthIndex: number): string {
  return MONTHS[monthIndex]
}

/** Serial -> `yyyy-mm-dd`, for binding to <input type="date">. */
export function serialToIso(serial: number): string {
  if (serial === null || serial === undefined || Number.isNaN(Number(serial))) return ''
  return serialToDate(Number(serial)).toISOString().slice(0, 10)
}

export function isoToSerial(iso: string): number {
  return Math.round((Date.parse(`${iso}T00:00:00Z`) - EPOCH) / DAY)
}

/**
 * Whatever someone typed into a date cell -> a serial. Accepts a parseable date
 * string ("12 Jul 2026", "2026-07-12") or a raw serial; anything else is handed
 * back untouched so a typo stays visible rather than silently becoming 1899.
 */
export function parseDateish(text: string): string | number {
  const parsed = Date.parse(text)
  if (!Number.isNaN(parsed)) return Math.round((parsed - EPOCH) / DAY)
  const n = Number(text)
  return Number.isNaN(n) ? text : n
}

/**
 * Excel's TODAY() — the machine's *local* calendar date, which is why this
 * reads local parts rather than dividing Date.now(). Near midnight in a
 * non-UTC timezone the two disagree by a day, and "Days Remaining" would be
 * off by one.
 */
export function todaySerial(): number {
  const now = new Date()
  return Math.round((Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - EPOCH) / DAY)
}

/** Serial of the 1st of the month containing `serial`. */
export function monthStartSerial(serial: number): number {
  const d = serialToDate(serial)
  return Math.round((Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) - EPOCH) / DAY)
}

/** `DATE(YEAR(A4),MONTH(A4)+1,1)` — the exclusive upper bound of A4's month. */
export function nextMonthSerial(serial: number): number {
  const d = serialToDate(serial)
  return Math.round((Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1) - EPOCH) / DAY)
}

export function monthStartOf(year: number, monthIndex: number): number {
  return Math.round((Date.UTC(year, monthIndex, 1) - EPOCH) / DAY)
}

// --- Numbers ---------------------------------------------------------------

export function num(value: unknown): number {
  const n = Number(value)
  return Number.isNaN(n) ? 0 : n
}

/** The `IFERROR(a/b, 0)` that wraps every ratio in both workbooks. */
export function div(a: unknown, b: unknown): number {
  return num(b) === 0 ? 0 : num(a) / num(b)
}

/** `#,##0` / `#,##0.00`. */
export function nf(value: unknown, decimals = 0): string {
  return Number(num(value)).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  })
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  PHP: '₱',
  USD: '$',
  EUR: '€',
  GBP: '£',
  INR: '₹',
  JPY: '¥',
  AUD: 'A$',
  CAD: 'C$',
  SGD: 'S$'
}

/**
 * The workbook's `₱#,##0.00`, driven by Settings -> Tracker Defaults -> Currency
 * rather than hardcoded, since that cell is user-editable. An unrecognised code
 * prefixes the code itself ("CHF 1,850") instead of guessing a glyph. Whole
 * amounts drop the ".00" — the sheets carry a lot of round figures and the
 * trailing zeros only add noise at this density.
 */
export function makeMoney(currency: string): (value: unknown) => string {
  const symbol = CURRENCY_SYMBOLS[currency?.toUpperCase()] ?? (currency ? `${currency} ` : '')
  return (value: unknown) => {
    const rounded = Math.round(num(value) * 100) / 100
    return symbol + nf(rounded, rounded % 1 === 0 ? 0 : 2)
  }
}

/** `0.00%` — the workbook stores the ratio, the format multiplies by 100. */
export function pct(value: unknown): string {
  return `${(num(value) * 100).toFixed(2)}%`
}

/** `0.00x` — ROAS and Frequency. */
export function ratio(value: unknown): string {
  return nf(value, 2)
}

// --- Daily Performance roll-ups -------------------------------------------

export interface DailyFilter {
  /** Inclusive lower bound, `">="&start`. */
  from?: number
  /** Inclusive upper bound, `"<="&end`. */
  to?: number
  /** Exclusive upper bound, `"<"&DATE(...)` — used by the month windows. */
  lt?: number
  platform?: string
  client?: string
  campaign?: string
  ad?: string
}

/**
 * The SUMIFS/SUMIF engine behind every roll-up in the Ads workbook.
 *
 * `platform`/`client` of "All" reproduce the workbook's
 * `IF($F$4="All","<>",$F$4)`: the criteria becomes "<>", which in SUMIFS means
 * *non-blank* rather than "everything". A row with no platform is therefore
 * excluded even under "All" — matching the sheet instead of quietly summing
 * rows the spreadsheet leaves out.
 */
export function sumDaily(daily: Row[], field: string, filter: DailyFilter): number {
  let total = 0
  for (const row of daily) {
    const date = num(row.A)
    if (filter.from !== undefined && date < filter.from) continue
    if (filter.to !== undefined && date > filter.to) continue
    if (filter.lt !== undefined && date >= filter.lt) continue

    if (filter.platform !== undefined) {
      const platform = String(row.C ?? '')
      if (filter.platform === 'All' ? platform === '' : platform !== filter.platform) continue
    }
    if (filter.client !== undefined) {
      const client = String(row.B ?? '')
      if (filter.client === 'All' ? client === '' : client !== filter.client) continue
    }
    if (filter.campaign !== undefined && String(row.D ?? '') !== filter.campaign) continue
    if (filter.ad !== undefined && String(row.G ?? '') !== filter.ad) continue

    total += num(row[field])
  }
  return total
}

// --- Derived columns, per sheet -------------------------------------------

export interface DailyDerived {
  cpm: number
  cpc: number
  ctr: number
  cpl: number
  cpa: number
  convRate: number
  roas: number
  frequency: number
}

/** Daily Performance O..V. */
export function dailyDerived(row: Row): DailyDerived {
  return {
    cpm: div(row.H, row.I) * 1000, // =IFERROR(H4/I4*1000,0)
    cpc: div(row.H, row.K), //        =IFERROR(H4/K4,0)
    ctr: div(row.K, row.I), //        =IFERROR(K4/I4,0)
    cpl: div(row.H, row.L), //        =IFERROR(H4/L4,0)
    cpa: div(row.H, row.M), //        =IFERROR(H4/M4,0)
    convRate: div(row.M, row.K), //   =IFERROR(M4/K4,0)
    roas: div(row.N, row.H), //       =IFERROR(N4/H4,0)
    frequency: div(row.I, row.J) //   =IFERROR(I4/J4,0)
  }
}

export interface CampaignDerived {
  actualSpend: number
  remainingBudget: number
  budgetUsed: number
  daysRemaining: number
  actualRevenue: number
  roas: number
}

/** Campaigns Q..V. */
export function campaignDerived(daily: Row[], row: Row): CampaignDerived {
  const campaign = String(row.A ?? '')
  // =SUMIF('Daily Performance'!$D$4:$D$503,A4,'Daily Performance'!$H$4:$H$503)
  const actualSpend = sumDaily(daily, 'H', { campaign })
  // =SUMIF('Daily Performance'!$D$4:$D$503,A4,'Daily Performance'!$N$4:$N$503)
  const actualRevenue = sumDaily(daily, 'N', { campaign })
  return {
    actualSpend,
    remainingBudget: Math.max(num(row.J) - actualSpend, 0), // =MAX(J4-Q4,0)
    budgetUsed: div(actualSpend, row.J), //                    =IFERROR(Q4/J4,0)
    daysRemaining: Math.max(num(row.H) - todaySerial(), 0), // =MAX(H4-TODAY(),0)
    actualRevenue,
    roas: div(actualRevenue, actualSpend) //                   =IFERROR(U4/Q4,0)
  }
}

export interface CreativeDerived {
  spend: number
  impressions: number
  clicks: number
  leads: number
  purchases: number
  revenue: number
  ctr: number
  cpl: number
  roas: number
  winner: string
}

/**
 * Creatives L..U. The six SUMIFs match on *ad name* (Daily Performance column
 * G), not campaign ID — a creative's numbers are the rows that ran it.
 */
export function creativeDerived(daily: Row[], row: Row): CreativeDerived {
  const ad = String(row.E ?? '')
  const spend = sumDaily(daily, 'H', { ad })
  const impressions = sumDaily(daily, 'I', { ad })
  const clicks = sumDaily(daily, 'K', { ad })
  const leads = sumDaily(daily, 'L', { ad })
  const purchases = sumDaily(daily, 'M', { ad })
  const revenue = sumDaily(daily, 'N', { ad })
  const roas = div(revenue, spend) // =IFERROR(Q4/L4,0)
  return {
    spend,
    impressions,
    clicks,
    leads,
    purchases,
    revenue,
    ctr: div(clicks, impressions), // =IFERROR(N4/M4,0)
    cpl: div(spend, leads), //        =IFERROR(L4/O4,0)
    roas,
    // =IF(T4>=3,"YES",IF(T4>=2,"WATCH","NO"))
    winner: roas >= 3 ? 'YES' : roas >= 2 ? 'WATCH' : 'NO'
  }
}

export interface BudgetDerived {
  actualSpend: number
  spendVariance: number
  actualLeads: number
  leadVariance: number
  actualPurchases: number
  purchaseVariance: number
  actualRevenue: number
  revenueVariance: number
  roas: number
}

/**
 * Budget & Targets G..O. Each row's actuals are that client's Daily
 * Performance rows inside that row's month.
 *
 * Note the variance directions are not uniform, and are kept as the sheet has
 * them: spend variance is planned − actual (underspend reads positive), while
 * lead/purchase/revenue variance is actual − target (overdelivery reads
 * positive).
 */
export function budgetDerived(daily: Row[], row: Row): BudgetDerived {
  const month = num(row.A)
  const window: DailyFilter = {
    from: month,
    lt: nextMonthSerial(month),
    client: String(row.B ?? '')
  }
  const actualSpend = sumDaily(daily, 'H', window)
  const actualLeads = sumDaily(daily, 'L', window)
  const actualPurchases = sumDaily(daily, 'M', window)
  const actualRevenue = sumDaily(daily, 'N', window)
  return {
    actualSpend,
    spendVariance: num(row.C) - actualSpend, //        =C4-G4
    actualLeads,
    leadVariance: actualLeads - num(row.D), //         =I4-D4
    actualPurchases,
    purchaseVariance: actualPurchases - num(row.E), // =K4-E4
    actualRevenue,
    revenueVariance: actualRevenue - num(row.F), //    =M4-F4
    roas: div(actualRevenue, actualSpend) //           =IFERROR(M4/G4,0)
  }
}

// --- Dashboard -------------------------------------------------------------

export interface DashboardTotals {
  spend: number
  revenue: number
  roas: number
  purchases: number
  impressions: number
  clicks: number
  ctr: number
  leads: number
  cpc: number
  cpl: number
  cpa: number
  convRate: number
}

/** Dashboard A7:H14 — the twelve KPI tiles, all under the report filters. */
export function dashboardTotals(daily: Row[], filters: TrackerFilters): DashboardTotals {
  const window: DailyFilter = {
    from: filters.start,
    to: filters.end,
    platform: filters.platform,
    client: filters.client
  }
  const spend = sumDaily(daily, 'H', window)
  const revenue = sumDaily(daily, 'N', window)
  const purchases = sumDaily(daily, 'M', window)
  const impressions = sumDaily(daily, 'I', window)
  const clicks = sumDaily(daily, 'K', window)
  const leads = sumDaily(daily, 'L', window)
  return {
    spend,
    revenue,
    roas: div(revenue, spend), //     =IFERROR(C8/A8,0)
    purchases,
    impressions,
    clicks,
    ctr: div(clicks, impressions), // =IFERROR(C11/A11,0)
    leads,
    cpc: div(spend, clicks), //       =IFERROR(A8/C11,0)
    cpl: div(spend, leads), //        =IFERROR(A8/G11,0)
    cpa: div(spend, purchases), //    =IFERROR(A8/G8,0)
    convRate: div(purchases, clicks) //=IFERROR(G8/C11,0)
  }
}

export interface MonthlyRow {
  monthIndex: number
  spend: number
  revenue: number
  leads: number
  purchases: number
  roas: number
}

/**
 * Dashboard A18:F29 — twelve month rows. These deliberately ignore the report's
 * start/end dates (each row is its own month window) but *do* honour the
 * platform and client filters, exactly as the sheet's SUMIFS do.
 */
export function monthlyRows(daily: Row[], filters: TrackerFilters, year: number): MonthlyRow[] {
  const rows: MonthlyRow[] = []
  for (let monthIndex = 0; monthIndex < 12; monthIndex++) {
    const start = monthStartOf(year, monthIndex)
    const window: DailyFilter = {
      from: start,
      lt: monthStartOf(year, monthIndex + 1),
      platform: filters.platform,
      client: filters.client
    }
    const spend = sumDaily(daily, 'H', window)
    const revenue = sumDaily(daily, 'N', window)
    rows.push({
      monthIndex,
      spend,
      revenue,
      leads: sumDaily(daily, 'L', window),
      purchases: sumDaily(daily, 'M', window),
      roas: div(revenue, spend) // =IFERROR(C18/B18,0)
    })
  }
  return rows
}

export interface PlatformRow {
  name: string
  spend: number
  revenue: number
  roas: number
}

/**
 * Dashboard H18:K23 — one row per platform. Uses the date range and the client
 * filter, but each row supplies its own platform rather than reading the
 * platform filter.
 */
export function platformRows(daily: Row[], filters: TrackerFilters, platforms: string[]): PlatformRow[] {
  return platforms.map((name) => {
    const window: DailyFilter = {
      from: filters.start,
      to: filters.end,
      platform: name,
      client: filters.client
    }
    const spend = sumDaily(daily, 'H', window)
    const revenue = sumDaily(daily, 'N', window)
    return { name, spend, revenue, roas: div(revenue, spend) } // =IFERROR(J18/I18,0)
  })
}

/** The six platforms hardwired into Dashboard H18:H23. */
export const DASHBOARD_PLATFORMS = ['Facebook', 'Instagram', 'TikTok', 'Google Ads', 'YouTube', 'LinkedIn']

// --- Influencer Outreach Tracker ------------------------------------------

/**
 * The sheet's `FILTER('Influencer Information'!B5:B1007, $D$5:$D1007=$F$4)` —
 * every column on the tracker is that same filter over a different source
 * column, so one row filter reproduces the whole sheet.
 */
export function filterInfluencers(influencers: Row[], platform: string): Row[] {
  return influencers.filter((row) => String(row.D ?? '') === platform)
}

/** `=SUM(D21:D900)` over the filtered rows (source column E, Follower Count). */
export function totalFollowers(filtered: Row[]): number {
  return filtered.reduce((total, row) => total + num(row.E), 0)
}

/** `=COUNTA(C21:C900)` — filtered rows carrying contact information. */
export function totalInfluencers(filtered: Row[]): number {
  return filtered.filter((row) => String(row.C ?? '') !== '').length
}

export interface StatusSlice {
  name: string
  count: number
  share: number
}

/**
 * The sheet's two pivot charts: "Count of Response Status" (column H) and
 * "Count of Collaboration Status" (column J), over the platform-filtered rows.
 */
export function statusCounts(filtered: Row[], column: string, order: string[]): StatusSlice[] {
  return order.map((name) => {
    const count = filtered.filter((row) => String(row[column] ?? '') === name).length
    return { name, count, share: filtered.length ? count / filtered.length : 0 }
  })
}

export const RESPONSE_STATUSES = ['Pending', 'Accepted', 'Declined']
export const COLLABORATION_STATUSES = ['In Progress', 'Collaborating', 'Not Collaborating']
