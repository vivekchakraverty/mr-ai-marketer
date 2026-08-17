import { useEffect, useState } from 'react'
import { fetchPostingTime, measureInstance, type PostingTimeRecommendation } from '../api/client'
import { secondaryButtonSmall } from '../styles/styleKit'

/**
 * In-composer "when to post" panel for Bluesky.
 *
 * Everything here is measured against the re-hydrated Bluesky corpus — see
 * app/routers/posting_time.py for how the curve was derived and why it is a
 * global curve rather than a per-niche one.
 *
 * The presentation is deliberately restrained. The real effect is a ~7
 * percentile-point swing between the best and worst hour, which is a tiebreaker
 * between two otherwise equal slots and nothing more. A big confident "POST AT
 * 8AM" badge would misrepresent the evidence, so the effect size, the sample and
 * the caveats are all one click away rather than buried, and the bars are drawn
 * on a scale that shows the curve honestly instead of exaggerating it.
 */

interface Props {
  /**
   * Which platform's curve to ask for. Each is measured on its own data — a
   * Bluesky curve says nothing about the fediverse — so an unmeasured platform
   * gets an explanation rather than somebody else's numbers.
   */
  platform: string
  /**
   * Mastodon only: the server being posted to, which the user names in the
   * composer. Timing is measured per instance, so without it there is no
   * question to answer.
   */
  instance?: string
  /** Optional hook for a caller that can actually schedule — Distribute's modal. */
  onPickSlot?: (iso: string) => void
}

/** Platforms the backend collects a curve for. Anything else hides the panel. */
const MEASURED = new Set(['bluesky', 'mastodon'])

function hourLabel(h: number): string {
  const suffix = h < 12 ? 'a' : 'p'
  return `${h % 12 || 12}${suffix}`
}

function hourLabelLong(h: number): string {
  return `${h % 12 || 12}${h < 12 ? 'am' : 'pm'}`
}

/** JS weeks start on Sunday; the measured curve starts on Monday. */
function utcWeekday(d: Date): number {
  return (d.getUTCDay() + 6) % 7
}

/**
 * The UTC hour that a given local hour of today actually falls in.
 *
 * Built from a real Date rather than an offset arithmetic, so a half-hour zone
 * lands in the correct UTC bucket (08:00 in India is 02:30 UTC — hour 2, not the
 * hour 3 that rounding +5:30 up to +6 would have produced) and a DST shift is
 * whatever the system says it is on that date.
 */
function localHourToUtc(localHour: number, ref: Date): number {
  const d = new Date(ref)
  d.setHours(localHour, 0, 0, 0)
  return d.getUTCHours()
}

interface LocalWindow {
  startHour: number
  label: string
  score: number
  lift: number
}

interface LocalSlot {
  date: Date
  label: string
  score: number
  lift: number
}

/** Non-overlapping best blocks, so the second suggestion is a real alternative. */
function rankWindows(
  localScores: number[],
  width: number,
  baseline: number
): { best: LocalWindow[]; worst: LocalWindow | null } {
  const blocks: LocalWindow[] = []
  for (let start = 0; start < 24; start++) {
    let sum = 0
    for (let i = 0; i < width; i++) sum += localScores[(start + i) % 24]
    const score = sum / width
    blocks.push({
      startHour: start,
      label: `${hourLabelLong(start)}–${hourLabelLong((start + width) % 24)}`,
      score,
      lift: score - baseline
    })
  }
  blocks.sort((a, b) => b.score - a.score)

  const best: LocalWindow[] = []
  const claimed = new Set<number>()
  for (const b of blocks) {
    const hours = Array.from({ length: width }, (_, i) => (b.startHour + i) % 24)
    if (hours.some((h) => claimed.has(h))) continue
    if (b.score <= baseline) continue
    hours.forEach((h) => claimed.add(h))
    best.push(b)
    if (best.length === 3) break
  }
  return { best, worst: blocks[blocks.length - 1] ?? null }
}

/**
 * The highest-scoring whole hours in the coming week, in the system's own clock.
 *
 * Each candidate is a real Date stepped forward an hour at a time, so its UTC hour
 * and UTC weekday — the two things the curve is indexed by — are read off the same
 * instant the user would actually be posting at, DST included.
 */
function nextSlots(
  hourlyUtc: number[],
  dailyUtc: number[],
  baseline: number,
  count: number
): LocalSlot[] {
  const now = new Date()
  const start = new Date(now)
  start.setMinutes(0, 0, 0)
  start.setHours(start.getHours() + 1)

  const scored: LocalSlot[] = []
  for (let i = 0; i < 24 * 7; i++) {
    const at = new Date(start)
    at.setHours(start.getHours() + i)
    const score =
      baseline + (hourlyUtc[at.getUTCHours()] - baseline) + (dailyUtc[utcWeekday(at)] - baseline)
    scored.push({
      date: at,
      // Labelled by the reader's own day and hour, scored by UTC.
      label: `${DAY_SHORT[at.getDay()]} ${hourLabelLong(at.getHours())}`,
      score,
      lift: score - baseline
    })
  }
  return scored
    .sort((a, b) => b.score - a.score)
    .slice(0, count)
    .sort((a, b) => a.date.getTime() - b.date.getTime())
}

const DAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

export default function PostingTimePanel({
  platform,
  instance = '',
  onPickSlot
}: Props): React.JSX.Element | null {
  const [data, setData] = useState<PostingTimeRecommendation | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState('')
  const [measuring, setMeasuring] = useState(false)
  const [measureNote, setMeasureNote] = useState('')

  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    if (!MEASURED.has(platform)) return
    let cancelled = false
    setData(null)
    setError('')
    fetchPostingTime(platform, instance)
      .then((res) => !cancelled && setData(res))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)))
    return () => {
      cancelled = true
    }
  }, [platform, instance, reloadKey])

  /**
   * Read this server's last month and see whether it can support a curve.
   *
   * User-initiated only: it pages the instance's public timeline and takes tens
   * of seconds, which is not something to do behind their back on every render.
   */
  async function handleMeasure(): Promise<void> {
    setMeasuring(true)
    setMeasureNote('')
    try {
      const res = await measureInstance(instance)
      // Only worth echoing when it says something the refreshed panel won't. On a
      // sample that was simply too small, the reason below already quotes the same
      // counts, and printing them twice reads like two separate findings.
      setMeasureNote(res.enough || res.scoredPosts === 0 ? res.detail : '')
      // Re-ask the endpoint either way: on success it now has a curve to serve,
      // and on failure the reason it gives is more specific than it was.
      setReloadKey((k) => k + 1)
    } catch (err) {
      setMeasureNote(err instanceof Error ? err.message : String(err))
    } finally {
      setMeasuring(false)
    }
  }

  // Nothing was measured for X or LinkedIn, so showing a curve beside one of
  // those drafts would assert something about a network never in the corpus.
  if (!MEASURED.has(platform)) return null
  if (error) return null
  if (!data) return null

  // A platform with no trustworthy curve says so, rather than borrowing another
  // platform's numbers or quietly rendering a flat line as if it were a finding.
  if (!data.available) {
    return (
      <div
        style={{
          marginTop: 18,
          background: 'var(--surface)',
          border: '2.5px dashed var(--border)',
          borderRadius: 20,
          padding: 18
        }}
      >
        <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>When to post</div>
        <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
          {data.unavailableReason ?? 'No timing data for this platform yet.'}
        </div>
        {measureNote && (
          <div style={{ font: "700 12px/1.55 'Quicksand'", color: 'var(--ink-body)', marginTop: 8 }}>
            {measureNote}
          </div>
        )}
        {platform === 'mastodon' && instance && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
            <div
              style={{ ...secondaryButtonSmall, opacity: measuring ? 0.6 : 1 }}
              onClick={measuring ? undefined : handleMeasure}
            >
              {measuring ? `Reading ${instance}…` : `Check ${instance}`}
            </div>
            <span style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
              Reads the server's last month — takes a moment.
            </span>
          </div>
        )}
      </div>
    )
  }

  // --- map the UTC curve onto the system's own clock -----------------------
  const now = new Date()
  const hourlyUtc = Array.from({ length: 24 }, (_, h) => data.hours.find((x) => x.hourUtc === h)?.score ?? data.baseline)
  const dailyUtc = Array.from({ length: 7 }, (_, d) => data.days.find((x) => x.weekday === d)?.score ?? data.baseline)

  const hours = Array.from({ length: 24 }, (_, localHour) => {
    const hourUtc = localHourToUtc(localHour, now)
    const source = data.hours.find((x) => x.hourUtc === hourUtc)
    return {
      hourLocal: localHour,
      hourUtc,
      score: source?.score ?? data.baseline,
      lift: (source?.score ?? data.baseline) - data.baseline,
      volumeShare: source?.volumeShare ?? 0
    }
  })

  const localScores = hours.map((h) => h.score)
  const { best: windows, worst } = rankWindows(localScores, data.windowHours, data.baseline)
  // 1 means the hourly curve held up; anything larger means the sample only
  // supported wider blocks and the copy below must not imply otherwise.
  const resolutionHours = data.sample?.resolutionHours ?? 1
  const best = windows[0]
  const slots = nextSlots(hourlyUtc, dailyUtc, data.baseline, 5)

  const maxAbs = Math.max(...hours.map((h) => Math.abs(h.lift))) || 1
  const nowHour = now.getHours()
  const zoneName =
    Intl.DateTimeFormat().resolvedOptions().timeZone || `UTC${-now.getTimezoneOffset() / 60}`

  return (
    <div
      style={{
        marginTop: 18,
        background: 'var(--surface)',
        border: '2.5px solid var(--border)',
        borderRadius: 20,
        padding: 18,
        boxShadow: 'var(--shadow-md)'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 4 }}>
        <div style={{ flex: 1 }}>
          <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>When to post</div>
          <div style={{ font: "600 12.5px/1.55 'Quicksand'", color: 'var(--ink-muted)', marginTop: 3 }}>
            {best ? (
              <>
                Your best window is <b>{best.label}</b> in {zoneName}. The quiet hours do better — engagement runs
                opposite to how many people are posting.
                {/* When the sample only supported wider blocks, say so rather than let a
                    window imply the data could distinguish the hours inside it. */}
                {resolutionHours > 1 && (
                  <>
                    {' '}
                    This server&rsquo;s data resolves to <b>{resolutionHours}-hour blocks</b>, so treat
                    anything finer than that as noise.
                  </>
                )}
              </>
            ) : (
              <>No hour beats an average slot by enough to call it.</>
            )}
          </div>
        </div>
        <div
          style={{
            font: "700 10.5px 'Quicksand'",
            letterSpacing: '.1em',
            textTransform: 'uppercase',
            color: 'var(--ink-faint)',
            border: '2px solid var(--border)',
            borderRadius: 999,
            padding: '4px 10px',
            whiteSpace: 'nowrap',
            flexShrink: 0
          }}
        >
          small effect
        </div>
      </div>

      {/* --- the curve, in the reader's own clock ------------------------- */}
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 76, marginTop: 14 }}>
        {hours.map((h) => {
          const norm = h.lift / maxAbs
          const height = Math.max(3, Math.abs(norm) * 32)
          const isNow = h.hourLocal === nowHour
          return (
            <div
              key={h.hourLocal}
              title={`${hourLabel(h.hourLocal)} local (${h.hourUtc}:00 UTC) — ${
                h.lift >= 0 ? 'above' : 'below'
              } an average slot by ${Math.abs(h.lift * 100).toFixed(1)} points · ${(h.volumeShare * 100).toFixed(
                1
              )}% of all posts go out in this hour`}
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
                cursor: 'default'
              }}
            >
              {/* above the midline */}
              <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', width: '100%' }}>
                {norm > 0 && (
                  <div
                    style={{
                      width: '100%',
                      height,
                      background: 'var(--accent)',
                      border: '1.5px solid var(--border)',
                      borderBottom: 'none',
                      borderRadius: '5px 5px 0 0'
                    }}
                  />
                )}
              </div>
              <div style={{ width: '100%', height: 2, background: 'var(--border)' }} />
              {/* below the midline */}
              <div style={{ flex: 1, width: '100%' }}>
                {norm < 0 && (
                  <div
                    style={{
                      width: '100%',
                      height,
                      background: 'var(--surface-tint)',
                      border: '1.5px solid var(--border)',
                      borderTop: 'none',
                      borderRadius: '0 0 5px 5px'
                    }}
                  />
                )}
              </div>
              <div
                style={{
                  font: `${isNow ? 800 : 600} 8.5px 'Quicksand'`,
                  color: isNow ? 'var(--accent-deep)' : 'var(--ink-faint)',
                  marginTop: 3
                }}
              >
                {h.hourLocal % 3 === 0 ? hourLabel(h.hourLocal) : ''}
              </div>
            </div>
          )
        })}
      </div>
      <div style={{ font: "600 11px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 2 }}>
        Above the line beats an average slot for the same account. Times are your system's
        {worst ? ` · worst is ${worst.label}` : ''}.
      </div>

      {/* --- concrete upcoming slots -------------------------------------- */}
      {slots.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div
            style={{
              font: "700 11px 'Quicksand'",
              letterSpacing: '.12em',
              textTransform: 'uppercase',
              color: 'var(--ink-faint)',
              marginBottom: 7
            }}
          >
            Next good slots
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
            {slots.map((slot) => (
              <div
                key={slot.date.toISOString()}
                title={`${slot.date.toLocaleString()} — ${
                  onPickSlot ? 'schedule this post for this slot' : 'click to copy'
                }`}
                onClick={() => {
                  if (onPickSlot) {
                    onPickSlot(slot.date.toISOString())
                    return
                  }
                  void navigator.clipboard.writeText(slot.date.toLocaleString())
                  setCopied(slot.date.toISOString())
                  setTimeout(() => setCopied(''), 1500)
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 7,
                  padding: '7px 12px',
                  borderRadius: 999,
                  border: '2px solid var(--border)',
                  background: 'var(--surface-tint)',
                  font: "700 12.5px 'Quicksand'",
                  color: 'var(--ink)',
                  cursor: 'pointer'
                }}
              >
                {copied === slot.date.toISOString() ? 'Copied ✓' : slot.label}
                <span style={{ font: "700 10.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
                  +{(slot.lift * 100).toFixed(1)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* --- day of week --------------------------------------------------- */}
      <div style={{ display: 'flex', gap: 5, marginTop: 14 }}>
        {data.days.map((d) => (
          <div
            key={d.weekday}
            title={`${d.name} — ${d.lift >= 0 ? '+' : ''}${(d.lift * 100).toFixed(1)} points against an average slot`}
            style={{
              flex: 1,
              textAlign: 'center',
              padding: '6px 0',
              borderRadius: 10,
              border: '2px solid var(--border)',
              background: d.lift > 0 ? 'var(--accent-soft-bg)' : 'var(--surface)',
              font: "700 11.5px 'Quicksand'",
              color: d.lift > 0 ? 'var(--ink)' : 'var(--ink-faint)'
            }}
          >
            {d.name.slice(0, 3)}
          </div>
        ))}
      </div>

      {/* --- the honest small print ---------------------------------------- */}
      <div style={{ marginTop: 12 }}>
        <div style={{ ...secondaryButtonSmall, display: 'inline-block' }} onClick={() => setOpen((v) => !v)}>
          {open ? 'Hide the fine print' : 'How reliable is this?'}
        </div>
      </div>
      {open && (
        <div
          style={{
            marginTop: 10,
            background: 'var(--surface-tint)',
            border: '2px solid var(--border)',
            borderRadius: 14,
            padding: '12px 15px'
          }}
        >
          <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-body)' }}>
            {data.effect.summary} Measured over {(data.sample.scoredPosts ?? 0).toLocaleString()} posts from{' '}
            {(data.sample.scoredAuthors ?? 0).toLocaleString()} accounts on {platform}, each scored against that
            same account's other posts so follower count and account quality can't skew the hour.
            {data.sample.windowStart && (
              <>
                {' '}
                Window: {data.sample.windowStart} to {data.sample.windowEnd}.
              </>
            )}
            {typeof data.sample.reliability === 'number' && (
              <>
                {' '}
                It reproduces on a random half of its own data at r={data.sample.reliability.toFixed(2)}.
              </>
            )}
          </div>
          <ul
            style={{
              margin: '10px 0 0',
              paddingLeft: 18,
              font: "600 12px/1.65 'Quicksand'",
              color: 'var(--ink-muted)'
            }}
          >
            {data.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
          <div style={{ font: "600 11.5px/1.55 'Quicksand'", color: 'var(--ink-faint)', marginTop: 10 }}>
            This is one curve for all of {platform}, not one per niche — per-niche curves were tested and did not
            reproduce on their own data at the sample sizes available, so shipping them would have been noise
            dressed as advice.
          </div>
        </div>
      )}
    </div>
  )
}
