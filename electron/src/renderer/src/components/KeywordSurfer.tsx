import { useCallback, useEffect, useRef, useState } from 'react'
import {
  cancelSurferRun,
  closeSurferBrowser,
  exportSurferRun,
  getSurferConfig,
  getSurferStatus,
  listSurferRuns,
  openSurferBrowser,
  startSurferRun,
  type SurferConfig,
  type SurferRun,
  type SurferRunSummary,
  type SurferStatus
} from '../api/client'
import {
  card,
  label,
  pill,
  primaryButton,
  primaryButtonSmall,
  secondaryButtonSmall,
  sectionEyebrow,
  select,
  textInput,
  textarea
} from '../styles/styleKit'

const cellHead: React.CSSProperties = {
  font: "700 10.5px 'Quicksand'",
  letterSpacing: '.09em',
  textTransform: 'uppercase',
  color: 'var(--ink-faint)',
  textAlign: 'left',
  padding: '0 10px 10px',
  whiteSpace: 'nowrap'
}

const cell: React.CSSProperties = {
  font: "600 13px 'Quicksand'",
  color: 'var(--ink)',
  padding: '10px',
  borderTop: '2px dashed var(--border-soft)',
  verticalAlign: 'top'
}

/** Statuses where the run is still moving and the screen should keep polling. */
const LIVE = ['queued', 'running', 'needs_attention']

const STATUS_TONE: Record<string, string> = {
  complete: 'var(--accent)',
  partial: 'var(--ink-muted)',
  no_data: 'var(--ink-faint)',
  extension_not_detected: '#a34a3a',
  navigation_error: '#a34a3a',
  google_challenge: '#a34a3a'
}

function formatVolume(value: number | null): string {
  // A blank cell means Surfer published nothing for that term. Writing 0 would read as
  // "nobody searches for this", which is a different and much stronger claim.
  if (value === null || value === undefined) return '—'
  return value.toLocaleString()
}

export default function KeywordSurfer(): React.JSX.Element {
  const [config, setConfig] = useState<SurferConfig | null>(null)
  const [status, setStatus] = useState<SurferStatus | null>(null)
  const [runs, setRuns] = useState<SurferRunSummary[]>([])
  const [keywords, setKeywords] = useState('')
  const [country, setCountry] = useState('us')
  const [delaySeconds, setDelaySeconds] = useState(7)
  const [maxSuggestions, setMaxSuggestions] = useState(25)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run: SurferRun | null = status?.run ?? null
  const live = Boolean(run && LIVE.includes(run.status))

  const refresh = useCallback(async () => {
    try {
      setStatus(await getSurferStatus())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    void getSurferConfig().then(setConfig).catch(() => undefined)
    void refresh()
    void listSurferRuns().then((r) => setRuns(r.runs)).catch(() => undefined)
  }, [refresh])

  // Poll only while something is actually happening. A collector sitting idle should not
  // wake the backend twice a second for the rest of the session.
  const liveRef = useRef(live)
  liveRef.current = live
  useEffect(() => {
    const timer = setInterval(() => {
      if (liveRef.current) void refresh()
    }, 1500)
    return () => clearInterval(timer)
  }, [refresh])

  // When a run finishes, pick up the history list once so the finished run appears there.
  const wasLive = useRef(false)
  useEffect(() => {
    if (wasLive.current && !live) {
      void listSurferRuns().then((r) => setRuns(r.runs)).catch(() => undefined)
    }
    wasLive.current = live
  }, [live])

  async function guarded(action: () => Promise<unknown>): Promise<void> {
    setBusy(true)
    setError('')
    try {
      await action()
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const keywordList = keywords
    .split('\n')
    .map((k) => k.trim())
    .filter(Boolean)

  const browser = status?.browser
  const progress = run ? `${run.completedCount} of ${run.keywordCount}` : ''

  return (
    <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
      <div style={{ width: 400, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 18 }}>
        {/* ---- the browser ------------------------------------------------ */}
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={sectionEyebrow}>Collector browser</div>
          <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', lineHeight: 1.5 }}>
            Keyword Surfer publishes its numbers inside a real results page, so this opens a
            window with the extension loaded. Leave it open while a run works through your
            keywords — if Google asks you to verify, do it in that window and the run picks
            up on its own.
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ ...pill, background: browser?.running ? 'var(--accent)' : undefined, color: browser?.running ? 'var(--accent-ink)' : undefined }}>
              {browser?.running ? 'Browser open' : 'Browser closed'}
            </span>
            <span style={pill}>
              {browser?.extensionInstalled ? 'Extension ready' : 'Extension downloads on first open'}
            </span>
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <div
              style={{ ...primaryButtonSmall, opacity: busy ? 0.6 : 1 }}
              onClick={busy ? undefined : () => void guarded(() => openSurferBrowser())}
            >
              {browser?.running ? 'Bring to front' : 'Open browser'}
            </div>
            {browser?.running && (
              <div style={secondaryButtonSmall} onClick={() => void guarded(() => closeSurferBrowser())}>
                Close browser
              </div>
            )}
          </div>

          {browser?.error && (
            <div style={{ font: "700 12px 'Quicksand'", color: '#a34a3a' }}>{browser.error}</div>
          )}
        </div>

        {/* ---- the run ---------------------------------------------------- */}
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={sectionEyebrow}>Keywords</div>

          <div>
            <label style={label}>One keyword per line</label>
            <textarea
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              placeholder={'leather laptop bag\nhandmade satchel\nfull grain leather backpack'}
              rows={8}
              style={textarea}
            />
            <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 4 }}>
              {keywordList.length} keyword{keywordList.length === 1 ? '' : 's'}
              {config ? ` · up to ${config.maxKeywordsPerRun}` : ''}
            </div>
          </div>

          <div>
            <label style={label}>Google region</label>
            <select value={country} onChange={(e) => setCountry(e.target.value)} style={select}>
              {(config?.countries ?? [{ code: 'us', name: 'United States', language: 'en' }]).map((c) => (
                <option key={c.code} value={c.code}>
                  {c.name}
                </option>
              ))}
            </select>
            <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 4 }}>
              Keyword Surfer has its own location selector too — set it once in the panel and
              it sticks.
            </div>
          </div>

          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <label style={label}>Seconds between searches</label>
              <input
                type="number"
                min={(config?.minDelayMs ?? 3000) / 1000}
                max={60}
                value={delaySeconds}
                onChange={(e) => setDelaySeconds(Number(e.target.value))}
                style={textInput}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={label}>Ideas per keyword</label>
              <input
                type="number"
                min={1}
                max={100}
                value={maxSuggestions}
                onChange={(e) => setMaxSuggestions(Number(e.target.value))}
                style={textInput}
              />
            </div>
          </div>
          <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: -6 }}>
            Searches fired back to back are what earns a verification prompt. The gap is the
            price of getting through a long list in one go.
          </div>

          {live ? (
            <div style={secondaryButtonSmall} onClick={() => void guarded(() => cancelSurferRun())}>
              Cancel run
            </div>
          ) : (
            <div
              style={{ ...primaryButton, opacity: busy || keywordList.length === 0 ? 0.6 : 1 }}
              onClick={
                busy || keywordList.length === 0
                  ? undefined
                  : () =>
                      void guarded(() =>
                        startSurferRun({
                          keywords: keywordList,
                          country,
                          delayMs: Math.round(delaySeconds * 1000),
                          maxSuggestions
                        })
                      )
              }
            >
              Collect keyword data
            </div>
          )}

          {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}
        </div>
      </div>

      {/* ---- results ------------------------------------------------------ */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 18 }}>
        {run && (
          <div style={{ ...card, padding: '26px 30px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
              <div>
                <div style={sectionEyebrow}>Run</div>
                <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)', marginTop: 4 }}>
                  {run.message}
                </div>
              </div>
              {run.completedCount > 0 && (
                <div
                  style={secondaryButtonSmall}
                  onClick={() =>
                    void exportSurferRun(run.id)
                      .then((f) => window.api.openFile(f.path))
                      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
                  }
                >
                  Save as CSV
                </div>
              )}
            </div>

            {run.status === 'needs_attention' && (
              <div
                style={{
                  marginTop: 14,
                  padding: '12px 14px',
                  borderRadius: 12,
                  border: '2px dashed #a34a3a',
                  font: "700 12.5px 'Quicksand'",
                  color: '#a34a3a'
                }}
              >
                Google is asking for a manual check. Switch to the collector browser window,
                complete it, and this run continues by itself.
              </div>
            )}

            <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 10 }}>
              {progress} keywords · {run.settings.country.name}
            </div>

            <div
              style={{
                height: 8,
                borderRadius: 99,
                background: 'var(--surface)',
                border: '2px solid var(--border)',
                marginTop: 10,
                overflow: 'hidden'
              }}
            >
              <div
                style={{
                  height: '100%',
                  width: `${run.keywordCount ? (run.completedCount / run.keywordCount) * 100 : 0}%`,
                  background: 'var(--accent)',
                  transition: 'width .3s'
                }}
              />
            </div>
          </div>
        )}

        {run?.results?.map((result) => (
          <div key={`${result.query}-${result.collectedAt}`} style={{ ...card, padding: '22px 26px' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
              <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)' }}>{result.query}</div>
              <span style={{ ...pill, color: STATUS_TONE[result.status] ?? 'var(--ink-muted)' }}>
                {result.status.replace(/_/g, ' ')}
              </span>
              {result.countryLabel && <span style={pill}>{result.countryLabel}</span>}
            </div>

            <div style={{ display: 'flex', gap: 26, marginTop: 12 }}>
              <div>
                <div style={sectionEyebrow}>Volume</div>
                <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)' }}>{formatVolume(result.volume)}</div>
              </div>
              <div>
                <div style={sectionEyebrow}>CPC</div>
                <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)' }}>
                  {result.cpcDisplay ?? (result.cpc !== null && result.cpc !== undefined ? result.cpc : '—')}
                </div>
              </div>
              <div>
                <div style={sectionEyebrow}>Ideas</div>
                <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)' }}>{result.suggestions?.length ?? 0}</div>
              </div>
            </div>

            {result.status !== 'complete' && (
              <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 10 }}>
                {result.message}
              </div>
            )}

            {result.suggestions && result.suggestions.length > 0 && (
              <div style={{ marginTop: 16, overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      <th style={cellHead}>Keyword idea</th>
                      <th style={{ ...cellHead, textAlign: 'right' }}>Volume</th>
                      <th style={{ ...cellHead, textAlign: 'right' }}>CPC</th>
                      <th style={{ ...cellHead, textAlign: 'right' }}>Similarity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.suggestions.map((s) => (
                      <tr key={s.keyword}>
                        <td style={cell}>{s.keyword}</td>
                        <td style={{ ...cell, textAlign: 'right' }}>{formatVolume(s.volume)}</td>
                        <td style={{ ...cell, textAlign: 'right' }}>{s.cpcDisplay ?? (s.cpc ?? '—')}</td>
                        <td style={{ ...cell, textAlign: 'right' }}>
                          {s.similarity !== null && s.similarity !== undefined ? `${s.similarity}%` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}

        {!run && (
          <div style={{ ...card, padding: '30px 34px' }}>
            <div style={sectionEyebrow}>Keyword Surfer</div>
            <div style={{ font: "700 22px/1.25 'Kalam'", color: 'var(--ink)', marginTop: 6 }}>
              Real search volumes, read off the page you could read yourself.
            </div>
            <div style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 10, lineHeight: 1.6 }}>
              Open the collector browser, paste your keywords, and every search runs in that
              window while the volumes, CPC and related ideas are collected here. Nothing is
              hidden from Google and nothing bypasses it — which is exactly why it works where
              a silent scrape does not.
            </div>
            {runs.length > 0 && (
              <div style={{ marginTop: 20 }}>
                <div style={sectionEyebrow}>Earlier runs</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
                  {runs.slice(0, 8).map((r) => (
                    <div
                      key={r.id}
                      style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)' }}
                    >
                      {r.createdAt.slice(0, 10)} · {r.completedCount}/{r.keywordCount} keywords ·{' '}
                      {r.status}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
