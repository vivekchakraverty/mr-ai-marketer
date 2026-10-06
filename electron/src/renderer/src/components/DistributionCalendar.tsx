import { useEffect, useMemo, useState } from 'react'
import {
  fetchCalendarEvents, fetchCalendarLocations, fetchCalendarPosts,
  type CalendarEvent, type DistributionCalendarEvents, type DistributionJob
} from '../api/client'
import { PLATFORM_SETUP_GUIDES } from '../state/platformSetupGuides'
import { calendarWindow, localDayKey, postsByDay } from './distributionCalendarDates'
import './DistributionCalendar.css'

const LOCATION_KEY = 'distribution-calendar-location'

function savedLocation(): { country: string; region: string } {
  try {
    const value = JSON.parse(localStorage.getItem(LOCATION_KEY) ?? '{}')
    return { country: typeof value.country === 'string' ? value.country : '', region: typeof value.region === 'string' ? value.region : 'all' }
  } catch { return { country: '', region: 'all' } }
}

const countryNames = new Intl.DisplayNames(['en'], { type: 'region' })
function countryName(code: string): string { return countryNames.of(code) ?? code }

function eventLocation(event: CalendarEvent): string {
  if (event.scope === 'international') return 'International'
  const name = event.country ? countryName(event.country) : ''
  return event.scope === 'regional' ? `${name} · ${event.regions.join(', ')}` : `${name} · National`
}

export default function DistributionCalendar(): React.JSX.Element {
  const [location, setLocation] = useState(savedLocation)
  const [today, setToday] = useState(() => localDayKey(new Date()))
  const [selected, setSelected] = useState(today)
  const [countries, setCountries] = useState<string[]>([])
  const [regions, setRegions] = useState<{ code: string; name: string }[]>([])
  const [data, setData] = useState<DistributionCalendarEvents | null>(null)
  const [posts, setPosts] = useState<DistributionJob[]>([])
  const [eventError, setEventError] = useState('')
  const [postError, setPostError] = useState('')
  const [locationError, setLocationError] = useState('')
  const [retry, setRetry] = useState(0)
  const window = useMemo(() => calendarWindow(today), [today])
  const groupedPosts = useMemo(() => postsByDay(posts), [posts])
  const groupedEvents = useMemo(() => {
    const grouped = new Map<string, CalendarEvent[]>()
    for (const event of data?.events ?? []) grouped.set(event.date, [...(grouped.get(event.date) ?? []), event])
    return grouped
  }, [data])

  useEffect(() => {
    try { localStorage.setItem(LOCATION_KEY, JSON.stringify(location)) } catch { /* A session-only choice still works. */ }
  }, [location])

  useEffect(() => {
    const update = (): void => setToday(localDayKey(new Date()))
    const timer = globalThis.setInterval(update, 30000)
    globalThis.addEventListener('focus', update)
    return () => { globalThis.clearInterval(timer); globalThis.removeEventListener('focus', update) }
  }, [])

  useEffect(() => {
    setSelected((current) => current < today || current > localDayKey(window.days[29]) ? today : current)
  }, [today, window])

  useEffect(() => {
    let active = true
    setRegions([])
    setLocationError('')
    fetchCalendarLocations(location.country).then((result) => {
      if (!active) return
      setCountries(result.countries.sort((a, b) => countryName(a).localeCompare(countryName(b))))
      setRegions(result.regions.sort((a, b) => a.name.localeCompare(b.name)))
    }).catch((error) => { if (active) setLocationError(error instanceof Error ? error.message : String(error)) })
    return () => { active = false }
  }, [location.country, retry])

  useEffect(() => {
    let active = true
    setData(null)
    setEventError('')
    fetchCalendarEvents(today, location.country, location.region).then((result) => {
      if (active) setData(result)
    }).catch((error) => { if (active) setEventError(error instanceof Error ? error.message : String(error)) })
    return () => { active = false }
  }, [today, location, retry])

  useEffect(() => {
    let active = true
    let sequence = 0
    setPosts([])
    const refresh = async (): Promise<void> => {
      const request = ++sequence
      try {
        const result = await fetchCalendarPosts(window.start.toISOString(), window.end.toISOString())
        if (active && request === sequence) { setPosts(result.jobs); setPostError('') }
      } catch (error) {
        if (active && request === sequence) setPostError(error instanceof Error ? error.message : String(error))
      }
    }
    void refresh()
    const timer = globalThis.setInterval(() => void refresh(), 15000)
    const changed = (): void => { void refresh() }
    globalThis.addEventListener('distribution-jobs-changed', changed)
    globalThis.addEventListener('focus', changed)
    return () => {
      active = false
      globalThis.clearInterval(timer)
      globalThis.removeEventListener('distribution-jobs-changed', changed)
      globalThis.removeEventListener('focus', changed)
    }
  }, [window, retry])

  const selectedEvents = groupedEvents.get(selected) ?? []
  const selectedPosts = groupedPosts.get(selected) ?? []
  const selectedDate = window.days.find((day) => localDayKey(day) === selected) ?? window.start
  const label = (date: Date): string => date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  const leading = (window.start.getDay() + 6) % 7

  return (
    <section className="distribution-calendar" aria-label="30-day events and scheduled posts calendar">
      <div className="distribution-calendar-heading">
        <div>
          <h2>Plan the next 30 days</h2>
          <p>{label(window.start)} – {label(window.days[29])} · Events and your scheduled posts</p>
        </div>
        <span className="distribution-calendar-scheduled">Green = scheduled posts</span>
      </div>
      <div className="distribution-calendar-controls">
        <label>Country
          <select aria-label="Country" value={location.country} onChange={(event) => setLocation({ country: event.target.value, region: 'all' })}>
            <option value="">Choose a country · international events only</option>
            <option value="worldwide">Worldwide · all supported countries and regions</option>
            {countries.map((code) => <option key={code} value={code}>{countryName(code)}</option>)}
          </select>
        </label>
        <label>State / region
          <select aria-label="State / region" value={location.region} disabled={!location.country || location.country === 'worldwide' || regions.length === 0}
            onChange={(event) => setLocation((current) => ({ ...current, region: event.target.value }))}>
            <option value="all">All states / regions</option>
            <option value="">National events only</option>
            {regions.map((region) => <option key={region.code} value={region.code}>{region.name}</option>)}
          </select>
        </label>
      </div>
      <p className="distribution-calendar-help">International observances are included with every location. Select a day to see all its events and scheduled posts. Dates use your local timezone.</p>
      {(eventError || postError || locationError) && <div className="distribution-calendar-error" role="alert">
        {locationError && <div>Could not load locations: {locationError}</div>}
        {eventError && <div>Could not load events: {eventError}</div>}
        {postError && <div>Scheduled-post markers may be out of date: {postError}</div>}
        <button type="button" onClick={() => setRetry((current) => current + 1)}>Try again</button>
      </div>}
      {!data && !eventError && <p role="status">Loading events…</p>}
      <div className="distribution-calendar-scroll">
        <div className="distribution-calendar-weekdays" aria-hidden="true">
          {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day) => <span key={day}>{day}</span>)}
        </div>
        <div className="distribution-calendar-grid">
          {Array.from({ length: leading }, (_, i) => <div key={`empty-${i}`} aria-hidden="true" />)}
          {window.days.map((date) => {
            const key = localDayKey(date)
            const events = groupedEvents.get(key) ?? []
            const count = groupedPosts.get(key)?.length ?? 0
            // Preview distinct names; the detail panel retains every country/region row.
            const names = [...new Set(events.map((event) => event.name))]
            return <button type="button" key={key} className={`distribution-calendar-day${selected === key ? ' selected' : ''}${count ? ' has-posts' : ''}`}
              aria-pressed={selected === key} aria-label={`${date.toLocaleDateString(undefined, { dateStyle: 'full' })}, ${events.length} events, ${count} scheduled posts`}
              onClick={() => setSelected(key)}>
              <span className="distribution-calendar-date">{label(date)} {key === today && <small>Today</small>}</span>
              {count > 0 && <span className="distribution-calendar-scheduled">{count} scheduled</span>}
              {names.slice(0, 2).map((name) => <span className="distribution-calendar-preview" key={name} title={name}>{name}</span>)}
              {names.length > 2 && <span className="distribution-calendar-more">+{names.length - 2} more events</span>}
              {data && names.length === 0 && <span className="distribution-calendar-empty">No listed events</span>}
            </button>
          })}
        </div>
      </div>
      <div className="distribution-calendar-details" aria-live="polite">
        <h3>{selectedDate.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}</h3>
        {selectedPosts.length > 0 && <div className="distribution-calendar-posts">
          <h4><span className="distribution-calendar-scheduled">{selectedPosts.length} scheduled {selectedPosts.length === 1 ? 'post' : 'posts'}</span></h4>
          <ul>{selectedPosts.map((post) => <li key={post.id}>
            <strong>{PLATFORM_SETUP_GUIDES[post.channel]?.label ?? post.channel}</strong>
            {' · '}{new Date(post.scheduled_at!).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
            {post.status === 'scheduled_cloud' && ' · Cloud'}
          </li>)}</ul>
        </div>}
        {selectedPosts.length === 0 && <p>No posts scheduled for this day.</p>}
        <h4>Events &amp; observances</h4>
        {selectedEvents.length > 0 && <ul className="distribution-calendar-events">{selectedEvents.map((event, i) => <li key={`${event.name}-${event.country}-${i}`}>
          <div><strong>{event.name}</strong> <span className={`distribution-calendar-scope ${event.scope}`}>{event.scope}</span></div>
          <div className="distribution-calendar-event-location">{eventLocation(event)}</div>
          <a href={event.source} target="_blank" rel="noreferrer">Source</a>
        </li>)}</ul>}
        {data && selectedEvents.length === 0 && <p>No events listed for this day and location.</p>}
      </div>
      {data && <p className="distribution-calendar-help">{data.coverage} Holiday data: v{data.dataset_version}.</p>}
    </section>
  )
}
