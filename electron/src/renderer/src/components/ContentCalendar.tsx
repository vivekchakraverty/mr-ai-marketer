import { useEffect, useState } from 'react'
import { contentCalendarApi, type CalendarJob, type CalendarResult, type PersonaReportChoice } from '../api/contentCalendar'
import { useAppStore } from '../state/store'
import PostingTimePanel from './PostingTimePanel'
import { card, primaryButtonSmall, secondaryButtonSmall, sectionEyebrow, select, textInput } from '../styles/styleKit'
import './ContentCalendar.css'

export default function ContentCalendar(): React.JSX.Element {
  const requireHf = useAppStore((state) => state.requireHf)
  const [choices, setChoices] = useState<PersonaReportChoice[]>([])
  const [choicesLoading, setChoicesLoading] = useState(true)
  const [reportId, setReportId] = useState('')
  const [job, setJob] = useState<CalendarJob | null>(null)
  const [result, setResult] = useState<CalendarResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [mastodonInstance, setMastodonInstance] = useState('')
  const [mastodonServerInput, setMastodonServerInput] = useState('')
  const [instanceLoaded, setInstanceLoaded] = useState(false)

  useEffect(() => {
    let alive = true
    void window.api.settings.getAll().then((settings) => {
      if (alive) {
        const server = (settings.mastodonInstance || '').trim()
        setMastodonInstance(server)
        setMastodonServerInput(server)
      }
    }).catch(() => {
      // The calendar still works; the Mastodon timing card explains what is missing.
    }).finally(() => {
      if (alive) setInstanceLoaded(true)
    })
    return () => { alive = false }
  }, [result?.generated_at])

  useEffect(() => {
    let alive = true
    void contentCalendarApi.personas().then(({ reports }) => {
      if (!alive) return
      setChoices(reports)
      setReportId((previous) => previous || reports[0]?.id || '')
    }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (alive) setChoicesLoading(false) })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    let alive = true
    setJob(null); setResult(null); setError('')
    if (reportId) void contentCalendarApi.latest(reportId).then(({ job: saved, previous_complete: previous }) => {
      if (!alive) return
      setJob(saved); setResult(saved?.result || previous?.result || null)
      if (saved?.status === 'error') setError(saved.error)
    }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)) })
    return () => { alive = false }
  }, [reportId])

  useEffect(() => {
    if (!job || !['queued', 'generating'].includes(job.status)) return
    const timer = window.setInterval(() => {
      void contentCalendarApi.get(job.id).then((next) => {
        setJob(next)
        if (next.result) setResult(next.result)
        if (next.status === 'error') setError(next.error)
      }).catch((err) => setError(err instanceof Error ? err.message : String(err)))
    }, 1800)
    return () => window.clearInterval(timer)
  }, [job?.id, job?.status])

  async function generate(): Promise<void> {
    if (!reportId || !requireHf()) return
    setLoading(true); setError('')
    try { setJob(await contentCalendarApi.generate(reportId)) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setLoading(false) }
  }

  const busy = loading || job?.status === 'queued' || job?.status === 'generating'
  const selected = choices.find((choice) => choice.id === reportId)
  const timedPlatforms = (['bluesky', 'mastodon'] as const).filter((platform) =>
    result?.channels.some((channel) => channel.name.toLowerCase().includes(platform)))
  return <div className="content-calendar">
    <section style={card}>
      <div style={sectionEyebrow}>Audience-led publishing plan</div>
      <p>Build a four-week posting rhythm and broad topic plan from a completed Buyer Persona report and the Align analysis behind it. Bluesky and Mastodon plans also show measured posting-time guidance.</p>
      {choices.length ? <>
        <label htmlFor="calendar-persona-report">Buyer Persona report</label>
        <select id="calendar-persona-report" value={reportId} disabled={busy} onChange={(event) => setReportId(event.target.value)} style={select}>
          {choices.map((choice) => <option key={choice.id} value={choice.id}>
            {choice.label} · {choice.persona_count} {choice.persona_count === 1 ? 'persona' : 'personas'} · {new Date(choice.created_at).toLocaleDateString()}
          </option>)}
        </select>
        <p className="content-calendar-muted">{selected?.source_key
          ? `Audience source: ${selected.source_key.split(':')[0].replaceAll('_', ' ')} analysis saved with this persona report.`
          : 'This persona report used project information only.'}</p>
        <button type="button" style={primaryButtonSmall} disabled={busy} onClick={() => void generate()}>
          {busy ? 'Generating calendar…' : result ? 'Regenerate calendar' : 'Generate calendar'}
        </button>
      </> : <p className="content-calendar-note">{choicesLoading ? 'Loading saved Buyer Persona reports…' : 'Generate a Buyer Persona report first, then return here to build your calendar.'}</p>}
      {error && <p role="alert" className="content-calendar-error">{error}</p>}
    </section>

    {result && <>
      <section style={card}>
        <div style={sectionEyebrow}>Suggested posting frequency</div>
        <h2>Four-week content calendar</h2>
        <p>{result.summary}</p>
        {result.generation_note && <p className="content-calendar-muted">{result.generation_note}</p>}
        <div className="content-calendar-channels">
          {result.channels.map((channel) => <article key={channel.name}>
            <h3>{channel.name}</h3>
            <strong>{channel.posts_per_week} {channel.posts_per_week === 1 ? 'post' : 'posts'} per week</strong>
            <p>{channel.formats.join(' · ')}</p><p>{channel.reason}</p>
          </article>)}
        </div>
        <p className="content-calendar-muted">These frequencies are starting hypotheses. Review capacity and results before increasing volume.</p>
      </section>
      {timedPlatforms.length > 0 && <section style={card}>
        <div style={sectionEyebrow}>Measured timing guidance</div>
        <h2>Best posting times</h2>
        <p>Use these windows as timing suggestions for the four-week plan. They are shown in your system time zone and may change as the underlying measurements are refreshed.</p>
        {timedPlatforms.map((platform) =>
          <div className="content-calendar-timing" key={platform}>
            <h3>{platform === 'mastodon' ? 'Mastodon' : 'Bluesky'}</h3>
            {platform === 'mastodon' && <form className="content-calendar-instance" onSubmit={(event) => {
              event.preventDefault()
              setMastodonInstance(mastodonServerInput.trim())
            }}>
              <label htmlFor="calendar-mastodon-server">Mastodon server</label>
              <input id="calendar-mastodon-server" style={textInput} value={mastodonServerInput}
                onChange={(event) => setMastodonServerInput(event.target.value)} placeholder="mastodon.social" />
              <button type="submit" style={secondaryButtonSmall} disabled={!mastodonServerInput.trim()}>Use server</button>
            </form>}
            {platform === 'mastodon' && !mastodonInstance
              ? <p className="content-calendar-note">{instanceLoaded
                ? 'Enter the Mastodon server you post from to see its own measured posting times.'
                : 'Loading your Mastodon server…'}</p>
              : <PostingTimePanel platform={platform} instance={platform === 'mastodon' ? mastodonInstance : ''} showStatus />}
          </div>
        )}
      </section>}
      {[1, 2, 3, 4].map((week) => <section key={week} style={card}>
        <div style={sectionEyebrow}>Week {week}</div>
        <div className="content-calendar-topics">
          {result.topics.filter((topic) => topic.week === week).map((topic, index) => <article key={`${topic.theme}-${index}`}>
            <h3>{topic.theme}</h3><p>{topic.angle}</p>
            <div className="content-calendar-meta"><span>{topic.persona}</span><span>{topic.channel}</span><span>{topic.format}</span><span>{topic.goal}</span></div>
          </article>)}
        </div>
      </section>)}
      <section style={card}>
        <div style={sectionEyebrow}>Review the experiment</div>
        <p>{result.measurement}</p>
        <details className="content-calendar-research"><summary>Research used in this plan</summary>
          {result.research.map((item) => <div key={item.name}><h3>{item.name}</h3><p>{item.scope}</p>
            <ul>{(item.findings || []).map((finding) => <li key={finding}>{finding}</li>)}</ul></div>)}
        </details>
        <p className="content-calendar-muted">Generated {new Date(result.generated_at).toLocaleString()}{result.model ? ` with ${result.model}` : ''}.</p>
      </section>
    </>}
  </div>
}
