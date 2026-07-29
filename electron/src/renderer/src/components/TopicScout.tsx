import { useEffect, useMemo, useState } from 'react'
import {
  discoverTopics,
  getTopicScoutOptions,
  type ScoutedTopic,
  type TopicScoutOptions,
  type TopicScoutResponse
} from '../api/client'
import { refreshLibrary } from '../state/actions'
import { card, chip, label, primaryButton, sectionEyebrow, select, textInput } from '../styles/styleKit'

/**
 * Topic Scout — the discovery half of TrendScout, wearing TrendScope's extra
 * sources and its sentiment layer.
 *
 * The design bet is that a ranked list nobody trusts is worthless, so every number
 * here is expandable into the measurement that produced it. Momentum and tone are
 * shown as two separate readings on purpose: a topic can be accelerating and
 * negative at the same time, and collapsing that into one number hides the story.
 */

const WINDOWS = [7, 14, 30, 60, 90, 180]

const TIER_STYLE: Record<string, { bg: string; ink: string }> = {
  'High momentum': { bg: '#d9f8ec', ink: '#116056' },
  Emerging: { bg: '#dff3fb', ink: '#1d6785' },
  Watch: { bg: '#fff0cc', ink: '#8a5b00' },
  Monitor: { bg: '#eef0f2', ink: '#5e6a72' },
  'Evidence only': { bg: '#f4ecff', ink: '#6b4fae' }
}

const TONE_STYLE: Record<string, { bg: string; ink: string }> = {
  positive: { bg: '#dff6e9', ink: '#2fa366' },
  negative: { bg: '#ffe1e1', ink: '#b8453c' },
  mixed: { bg: '#fff0cc', ink: '#8a5b00' },
  neutral: { bg: '#eef0f2', ink: '#5e6a72' },
  unknown: { bg: '#eef0f2', ink: '#5e6a72' }
}

function pillStyle(palette: { bg: string; ink: string }): React.CSSProperties {
  return {
    display: 'inline-block',
    padding: '3px 9px',
    borderRadius: 999,
    font: "700 11px 'Quicksand'",
    background: palette.bg,
    color: palette.ink
  }
}

function formatValue(value: number): string {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  if (Math.abs(value) >= 10) return value.toLocaleString(undefined, { maximumFractionDigits: 0 })
  return String(Number(value.toFixed(3)))
}

function Metric({ name, value }: { name: string; value: string }): React.JSX.Element {
  return (
    <div style={{ ...card, padding: '13px 16px', flex: 1, minWidth: 0 }}>
      <div style={{ font: "700 10.5px 'Quicksand'", letterSpacing: '.09em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
        {name}
      </div>
      <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)', marginTop: 2 }}>{value}</div>
    </div>
  )
}

function FamilyBars({
  scores,
  labels
}: {
  scores: Record<string, number>
  labels: Record<string, string>
}): React.JSX.Element {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginTop: 8 }}>
      {Object.entries(scores).map(([key, value]) => (
        <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 138, flexShrink: 0, font: "600 11.5px 'Quicksand'", color: 'var(--ink-muted)' }}>
            {labels[key] ?? key}
          </div>
          <div style={{ flex: 1, height: 8, borderRadius: 999, background: 'var(--surface)', border: '1.5px solid var(--border-soft)' }}>
            <div
              style={{
                width: `${Math.max(0, Math.min(100, value))}%`,
                height: '100%',
                borderRadius: 999,
                background: 'var(--accent-deep)'
              }}
            />
          </div>
          <div style={{ width: 30, textAlign: 'right', font: "700 11px 'Quicksand'", color: 'var(--ink-muted)' }}>
            {value.toFixed(0)}
          </div>
        </div>
      ))}
    </div>
  )
}

function TopicCard({
  topic,
  rank,
  familyLabels
}: {
  topic: ScoutedTopic
  rank: number
  familyLabels: Record<string, string>
}): React.JSX.Element {
  const tier = TIER_STYLE[topic.tier] ?? TIER_STYLE.Monitor
  const tone = TONE_STYLE[topic.sentiment.label] ?? TONE_STYLE.unknown

  return (
    <div style={{ ...card, padding: '15px 18px', marginBottom: 11 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ font: "700 10.5px 'Quicksand'", letterSpacing: '.08em', color: 'var(--accent-deep)' }}>
            #{String(rank).padStart(2, '0')}
          </div>
          <div style={{ font: "700 19px 'Kalam'", color: 'var(--ink)', margin: '2px 0 7px' }}>{topic.label}</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <span style={pillStyle(tier)}>{topic.tier}</span>
            <span style={pillStyle({ bg: '#eef0f2', ink: '#5e6a72' })}>{topic.confidence}% confidence</span>
            <span style={pillStyle({ bg: '#eef0f2', ink: '#5e6a72' })}>{topic.measuredFamilies}/5 families</span>
            <span style={pillStyle(tone)} title={`Tone measured by ${topic.sentiment.engine}`}>
              {topic.sentiment.label} {topic.sentiment.polarity >= 0 ? '+' : ''}
              {topic.sentiment.polarity.toFixed(2)}
            </span>
          </div>
        </div>
        <div style={{ font: "700 30px 'Kalam'", color: 'var(--accent-deep)', flexShrink: 0 }}>
          {topic.score.toFixed(0)}
        </div>
      </div>

      <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginTop: 9 }}>{topic.angle}</div>

      <details style={{ marginTop: 9 }}>
        <summary style={{ font: "700 11.5px 'Quicksand'", color: 'var(--ink-muted)', cursor: 'pointer' }}>
          Why am I seeing this?
        </summary>

        <div style={{ marginTop: 10 }}>
          <div style={{ ...sectionEyebrow, fontSize: 10.5 }}>Signal balance</div>
          <FamilyBars scores={topic.familyScores} labels={familyLabels} />

          <div style={{ ...sectionEyebrow, fontSize: 10.5, marginTop: 14 }}>Measured change vs the previous window</div>
          {topic.measurements.length > 0 ? (
            <div style={{ marginTop: 6 }}>
              {topic.measurements.map((m, i) => (
                <div
                  key={i}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 10,
                    padding: '6px 0',
                    borderBottom: '1.5px dashed var(--border-soft)'
                  }}
                  title={m.note}
                >
                  <div style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)', minWidth: 0 }}>
                    {m.source}
                    {m.contextOnly && (
                      <span style={{ font: "600 10.5px 'Quicksand'", color: 'var(--ink-faint)' }}> · context only</span>
                    )}
                  </div>
                  <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-muted)', flexShrink: 0 }}>
                    {formatValue(m.current)} vs {formatValue(m.baseline)} {m.unit} ·{' '}
                    <span style={{ color: (m.changePct ?? 0) >= 0 ? 'var(--accent-deep)' : '#b8453c' }}>
                      {m.changePct === null
                        ? m.current > 0
                          ? 'new vs zero baseline'
                          : 'no activity'
                        : `${m.changePct >= 0 ? '+' : ''}${m.changePct.toFixed(1)}%`}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 6 }}>
              No historical metric returned a usable comparison for this candidate — it is ranked on discovery
              evidence alone.
            </div>
          )}

          <div style={{ ...sectionEyebrow, fontSize: 10.5, marginTop: 14 }}>
            Recent evidence · {topic.sentiment.analyzed} headlines read for tone (
            {topic.sentiment.positive} positive / {topic.sentiment.neutral} neutral / {topic.sentiment.negative}{' '}
            negative)
          </div>
          <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 5 }}>
            {topic.evidence.map((item, i) => {
              const itemTone = TONE_STYLE[item.sentimentLabel] ?? TONE_STYLE.unknown
              return (
                <div key={i} style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)' }}>
                  {item.sentimentLabel && (
                    <span style={{ ...pillStyle(itemTone), marginRight: 6, fontSize: 9.5, padding: '2px 6px' }}>
                      {item.sentimentLabel}
                    </span>
                  )}
                  {item.url ? (
                    <a href={item.url} target="_blank" rel="noreferrer" style={{ color: 'var(--ink)' }}>
                      {item.title}
                    </a>
                  ) : (
                    <span style={{ color: 'var(--ink)' }}>{item.title}</span>
                  )}
                  <span style={{ color: 'var(--ink-faint)' }}>
                    {' '}
                    — {item.source}, {item.published.slice(0, 10)}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      </details>
    </div>
  )
}

export default function TopicScout(): React.JSX.Element {
  const [options, setOptions] = useState<TopicScoutOptions | null>(null)
  const [niche, setNiche] = useState('content marketing for small tech companies')
  const [group, setGroup] = useState('')
  const [subNiche, setSubNiche] = useState('')
  const [days, setDays] = useState(30)
  const [maxTopics, setMaxTopics] = useState(10)
  const [sources, setSources] = useState<string[]>([])
  const [signalSources, setSignalSources] = useState<string[]>([])

  const [result, setResult] = useState<TopicScoutResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    getTopicScoutOptions()
      .then((opts) => {
        setOptions(opts)
        setGroup((current) => current || Object.keys(opts.groups)[0] || '')
        setSources(opts.defaultSources)
        setSignalSources(opts.defaultSignalSources)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  const subNiches = useMemo(() => (options && group ? options.groups[group] ?? [] : []), [options, group])

  function toggle(list: string[], setList: (next: string[]) => void, value: string): void {
    setList(list.includes(value) ? list.filter((item) => item !== value) : [...list, value])
  }

  async function handleRun(): Promise<void> {
    if (niche.trim().length < 3 || !group) return
    setLoading(true)
    setError('')
    try {
      const res = await discoverTopics({ niche, group, subNiche, days, maxTopics, sources, signalSources })
      setResult(res)
      void refreshLibrary()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const topics = result?.topics ?? []
  const topScore = topics.length ? Math.max(...topics.map((t) => t.score)) : 0
  const strong = topics.filter((t) => t.tier === 'High momentum' || t.tier === 'Emerging').length
  const bestCorroboration = topics.length ? Math.max(...topics.map((t) => t.measuredFamilies)) : 0

  return (
    <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
      <div style={{ width: 400, flexShrink: 0, ...card, display: 'flex', flexDirection: 'column', gap: 15 }}>
        <div style={sectionEyebrow}>What are you scouting?</div>

        <div>
          <label style={label}>Your niche</label>
          <input
            value={niche}
            onChange={(e) => setNiche(e.target.value)}
            placeholder="e.g. AI agents for B2B marketing teams"
            style={textInput}
          />
        </div>

        <div>
          <label style={label}>Closest market group</label>
          <select
            value={group}
            onChange={(e) => {
              setGroup(e.target.value)
              setSubNiche('')
            }}
            style={select}
          >
            {options &&
              Object.keys(options.groups).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
          </select>
          <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 4 }}>
            Sets how the five evidence families are weighted. Consumer niches lean on attention and
            conversation; deep-tech niches lean on adoption and research.
          </div>
        </div>

        <div>
          <label style={label}>Optional focus</label>
          <select value={subNiche} onChange={(e) => setSubNiche(e.target.value)} style={select}>
            <option value="">Use my niche as entered</option>
            {subNiches.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', gap: 10 }}>
          <div style={{ flex: 1 }}>
            <label style={label}>Signal window</label>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={select}>
              {WINDOWS.map((value) => (
                <option key={value} value={value}>
                  {value} days
                </option>
              ))}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label style={label}>Suggestions</label>
            <select value={maxTopics} onChange={(e) => setMaxTopics(Number(e.target.value))} style={select}>
              {[5, 8, 10, 15, 20].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label style={label}>Discovery sources</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
            {options?.sources.map((name) => (
              <div key={name} style={chip(sources.includes(name))} onClick={() => toggle(sources, setSources, name)}>
                {name}
              </div>
            ))}
          </div>
          <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 6 }}>
            These nominate candidate stories. TikTok, Amazon and Twitter/X are off by default — they need
            either extra packages or a session, and say so in the source-health panel when they can&apos;t run.
          </div>
        </div>

        <div>
          <label style={label}>Measurable baselines</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
            {options?.signalSources.map((name) => (
              <div
                key={name}
                style={chip(signalSources.includes(name))}
                onClick={() => toggle(signalSources, setSignalSources, name)}
              >
                {name}
              </div>
            ))}
          </div>
          <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 6 }}>
            Each compares your window against the equal window before it. Feeds find candidates; only these
            decide the ranking. FRED is shown as context and never counted as momentum.
          </div>
        </div>

        <div style={{ ...primaryButton, opacity: loading || !group ? 0.6 : 1 }} onClick={loading || !group ? undefined : handleRun}>
          {loading ? 'Reading the evidence… this takes a minute' : 'Find emerging topics'}
        </div>

        {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {!result && !loading && (
          <div style={{ ...card, textAlign: 'center', padding: '52px 30px' }}>
            <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)', marginBottom: 6 }}>
              Nothing scouted yet
            </div>
            <div style={{ font: "600 14px/1.7 'Quicksand'", color: 'var(--ink-muted)' }}>
              Describe your niche and hit the button. News, community, code, research, search and video
              signals get clustered into topics, each one measured against the window before it — and each
              one comes with its receipts.
            </div>
          </div>
        )}

        {result && (
          <>
            <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
              <Metric name="Top score" value={`${topScore.toFixed(0)}/100`} />
              <Metric name="Emerging or strong" value={String(strong)} />
              <Metric name="Best corroboration" value={`${bestCorroboration}/5`} />
              <Metric name="Window" value={`${days} days`} />
            </div>

            {result.sentimentNote && (
              <div
                style={{
                  ...card,
                  padding: '11px 15px',
                  marginBottom: 12,
                  font: "600 12.5px/1.6 'Quicksand'",
                  color: 'var(--ink-muted)'
                }}
              >
                {result.sentimentNote}
              </div>
            )}

            {topics.length === 0 && (
              <div style={{ ...card, padding: '30px', font: "600 13.5px/1.7 'Quicksand'", color: 'var(--ink-muted)' }}>
                Not enough repeated evidence was found to cluster into topics. Try a broader niche, a 60–90
                day window, or more discovery sources.
              </div>
            )}

            {topics.map((topic, index) => (
              <TopicCard key={topic.query} topic={topic} rank={index + 1} familyLabels={result.familyLabels} />
            ))}

            {result.sourceHealth.length > 0 && (
              <details style={{ ...card, padding: '12px 16px', marginTop: 4 }}>
                <summary style={{ font: "700 12px 'Quicksand'", color: 'var(--ink-muted)', cursor: 'pointer' }}>
                  Source health · {result.sourceHealth.length} check(s) skipped
                </summary>
                <div style={{ marginTop: 8 }}>
                  <div style={{ font: "600 12px/1.6 'Quicksand'", color: 'var(--ink-faint)', marginBottom: 6 }}>
                    The ranking completed with the sources that responded.
                  </div>
                  {result.sourceHealth.map((line, i) => (
                    <div key={i} style={{ font: "600 11.5px/1.6 'Quicksand'", color: 'var(--ink-muted)' }}>
                      • {line}
                    </div>
                  ))}
                </div>
              </details>
            )}

            <div style={{ font: "600 11.5px/1.6 'Quicksand'", color: 'var(--ink-faint)', marginTop: 12 }}>
              Scores are directional indicators for editorial discovery, not forecasts or financial advice.
              Tone describes how the coverage reads, not whether the topic is worth pursuing. Saved to your
              Library.
            </div>
          </>
        )}
      </div>
    </div>
  )
}
