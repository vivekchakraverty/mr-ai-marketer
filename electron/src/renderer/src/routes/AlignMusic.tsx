import { useEffect, useRef, useState } from 'react'
import { fetchMusicAudience, type MusicAudienceReport } from '../api/client'
import { alignSavedReportsApi, type SavedAlignSummary } from '../api/alignSavedReports'
import { card, label, primaryButton, primaryButtonSmall, sectionEyebrow, textInput } from '../styles/styleKit'
import type { EssentiaSongResult, EssentiaWorkerReply } from './essentiaTypes'
import { RESEARCH_REVIEWED, suggestMusicDestinations, type MusicAudienceContext, type MusicDestination, type MusicGoal, type ReleaseStage } from './musicDestinations'

const MAX_AUDIO_BYTES = 100 * 1024 * 1024
const MAX_DURATION_SECONDS = 15 * 60
const SAMPLE_RATE = 44_100
const ALLOWED_AUDIO = /\.(mp3|wav|ogg|oga|flac|m4a|aac|aif|aiff)$/i

function clock(seconds: number): string {
  const rounded = Math.round(seconds)
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}`
}

function metric(value: string, name: string, detail: string): React.JSX.Element {
  return <div style={{ border: '1.5px solid var(--border)', borderRadius: 12, padding: 13 }}>
    <div style={sectionEyebrow}>{name}</div>
    <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)', marginTop: 4 }}>{value}</div>
    <div style={{ font: "600 10.5px/1.5 'Quicksand'", color: 'var(--ink-muted)' }}>{detail}</div>
  </div>
}

function DestinationCard({ item, rank }: { item: MusicDestination; rank: number }): React.JSX.Element {
  return <article style={{ border: '1.5px solid var(--border)', borderRadius: 12, padding: 14, background: 'var(--surface)' }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
      <div><span style={sectionEyebrow}>{rank}. {item.kind}</span><h4 style={{ font: "700 16px 'Kalam'", color: 'var(--ink)', margin: '3px 0 0' }}>{item.name}</h4></div>
      <a href={item.visitUrl} target="_blank" rel="noopener noreferrer" style={{ font: "700 11px 'Quicksand'", color: 'var(--accent-deep)', whiteSpace: 'nowrap' }}>Explore ↗</a>
    </div>
    <p style={{ font: "600 11.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', margin: '8px 0' }}>{item.reason}</p>
    <div style={{ font: "700 10.5px 'Quicksand'", color: 'var(--ink)' }}>How to test it</div>
    <ol style={{ margin: '5px 0 0', paddingLeft: 18, font: "600 10.5px/1.55 'Quicksand'", color: 'var(--ink-muted)' }}>{item.nextSteps.map((step, index) => <li key={`${item.id}-${index}`}>{step}</li>)}</ol>
    <p style={{ font: "600 10px/1.5 'Quicksand'", color: 'var(--ink-faint)', margin: '8px 0 4px' }}>{item.boundary}</p>
    <a href={item.sourceUrl} target="_blank" rel="noopener noreferrer" style={{ font: "700 10px 'Quicksand'", color: 'var(--accent-deep)' }}>Source: {item.sourceName} ↗</a>
    {item.secondSource && <a href={item.secondSource.url} target="_blank" rel="noopener noreferrer" style={{ font: "700 10px 'Quicksand'", color: 'var(--accent-deep)', marginLeft: 12 }}>{item.secondSource.name} ↗</a>}
  </article>
}

function MusicAudiencePanel({ references, report, busy, error, onExplore }: {
  references: string
  report: MusicAudienceReport | null
  busy: boolean
  error: string
  onExplore: () => void
}): React.JSX.Element {
  return <div style={{ borderTop: '1.5px solid var(--border)', marginTop: 21, paddingTop: 17 }}>
    <div style={sectionEyebrow}>Listener research</div>
    <h3 style={{ font: "700 20px 'Kalam'", color: 'var(--ink)', margin: '4px 0 5px' }}>Who might enjoy this sound?</h3>
    <p style={{ font: "600 11.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', margin: '0 0 10px' }}>Enter one to three genuinely comparable artists above, separated by commas. We match those names to MusicBrainz and use ListenBrainz data to find nearby artist audiences. Essentia's measurements do not establish that these artists sound alike.</p>
    <p style={{ font: "600 11px/1.55 'Quicksand'", color: 'var(--ink-muted)', margin: '0 0 10px' }}>Need soundalikes? <a href="https://cosine.club/" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-deep)', fontWeight: 700 }}>Try cosine.club ↗</a> with a public track link, listen to the matches, then enter the best references here. Its free catalog focuses on electronic and underground music; submitting a link there sends audio to that service.</p>
    <button type="button" disabled={!references.trim() || busy} onClick={onExplore} style={{ ...primaryButtonSmall, opacity: !references.trim() || busy ? .6 : 1 }}>{busy ? 'Checking listener data…' : 'Explore listener audiences'}</button>
    {!references.trim() && <span style={{ marginLeft: 10, font: "600 10.5px 'Quicksand'", color: 'var(--ink-faint)' }}>Add reference artists above to start.</span>}
    {error && <p role="alert" style={{ font: "700 11px 'Quicksand'", color: 'var(--danger-ink)' }}>{error}</p>}
    {report && <div style={{ marginTop: 13 }}>
      {report.reference_artists.length > 0 && <>
        <div style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)', marginBottom: 7 }}>Reference artist audiences</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 8 }}>
          {report.reference_artists.map((artist) => <div key={artist.mbid} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 11 }}>
            <a href={artist.musicbrainz_url} target="_blank" rel="noopener noreferrer" style={{ font: "700 12px 'Quicksand'", color: 'var(--accent-deep)' }}>{artist.name} ↗</a>
            <div style={{ font: "600 10.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 5 }}>{artist.unique_listeners === null ? 'Listener count unavailable' : `${artist.unique_listeners.toLocaleString()} ListenBrainz listeners`}</div>
          </div>)}
        </div>
      </>}
      {report.adjacent_artists.length > 0 && <>
        <div style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)', margin: '15px 0 7px' }}>Nearby artist audiences to investigate</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>{report.adjacent_artists.map((artist) => <a key={artist.mbid} href={artist.musicbrainz_url} target="_blank" rel="noopener noreferrer" style={{ border: '1px solid var(--border)', borderRadius: 9, padding: '7px 10px', font: "700 10.5px 'Quicksand'", color: 'var(--accent-deep)' }}>{artist.name} ↗ <span style={{ color: 'var(--ink-faint)' }}>via {artist.reference_artists.join(', ')}</span></a>)}</div>
      </>}
      {report.unmatched.length > 0 && <p style={{ font: "600 10.5px/1.5 'Quicksand'", color: 'var(--ink-muted)' }}>No exact MusicBrainz match for: {report.unmatched.join(', ')}. Check the spelling or use a more specific artist name.</p>}
      {report.warnings.map((warning) => <p key={warning} style={{ font: "600 10.5px/1.5 'Quicksand'", color: 'var(--ink-muted)' }}>{warning}</p>)}
      <p style={{ font: "600 10.5px/1.55 'Quicksand'", color: 'var(--ink-faint)', marginBottom: 0 }}>These are research leads based on the artists you supplied and ListenBrainz activity. Counts describe those artists' existing listeners, not projected listeners for your song. No individual preference or demographic is predicted. <a href="https://listenbrainz.readthedocs.io/en/latest/users/api/popularity.html" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-deep)' }}>Listener count source ↗</a> · <a href="https://listenbrainz.readthedocs.io/en/latest/users/api/core.html" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-deep)' }}>Related artist source ↗</a></p>
    </div>}
  </div>
}

export default function AlignMusic(): React.JSX.Element {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [result, setResult] = useState<EssentiaSongResult | null>(null)
  const [audienceReport, setAudienceReport] = useState<MusicAudienceReport | null>(null)
  const [audienceBusy, setAudienceBusy] = useState(false)
  const [audienceError, setAudienceError] = useState('')
  const [audience, setAudience] = useState<MusicAudienceContext>({
    style: '', referenceArtists: '', location: '', goal: 'feedback', stage: 'draft'
  })
  const [savedReports, setSavedReports] = useState<SavedAlignSummary[]>([])
  const [savedTitle, setSavedTitle] = useState('')
  const [saveError, setSaveError] = useState('')
  const [savedAt, setSavedAt] = useState('')
  const savedIdRef = useRef('')
  const saveVersionRef = useRef(0)
  const saveQueueRef = useRef<Promise<void>>(Promise.resolve())
  const workerRef = useRef<Worker | null>(null)
  const audienceRunRef = useRef(0)
  const destinations = result ? suggestMusicDestinations(result, audience) : []

  useEffect(() => { void alignSavedReportsApi.list('music').then(({ reports }) => setSavedReports(reports)).catch(() => undefined) }, [])

  useEffect(() => {
    if (!result) return
    const version = saveVersionRef.current
    const timer = window.setTimeout(() => {
      saveQueueRef.current = saveQueueRef.current.catch(() => undefined).then(async () => {
        if (version !== saveVersionRef.current) return
        try {
          const saved = await alignSavedReportsApi.save('music', savedTitle || file?.name || 'Song', {
            analysis: result, audience_context: audience, audience_report: audienceReport,
            destinations: suggestMusicDestinations(result, audience)
          }, savedIdRef.current)
          if (version !== saveVersionRef.current) return
          savedIdRef.current = saved.id
          setSavedAt(saved.updated_at)
          setSaveError('')
          const { reports } = await alignSavedReportsApi.list('music')
          if (version === saveVersionRef.current) setSavedReports(reports)
        } catch (cause) {
          if (version === saveVersionRef.current) setSaveError(cause instanceof Error ? cause.message : String(cause))
        }
      })
    }, 450)
    return () => window.clearTimeout(timer)
  }, [result, audience, audienceReport, savedTitle, file])

  async function openSaved(id: string): Promise<void> {
    try {
      const saved = await alignSavedReportsApi.get<{
        analysis: EssentiaSongResult; audience_context: MusicAudienceContext; audience_report: MusicAudienceReport | null
      }>(id)
      saveVersionRef.current++
      savedIdRef.current = saved.id
      setFile(null)
      const input = document.getElementById('align-music-file') as HTMLInputElement | null
      if (input) input.value = ''
      setSavedTitle(saved.title)
      setResult(saved.document.analysis)
      setAudience(saved.document.audience_context)
      setAudienceReport(saved.document.audience_report)
      setSavedAt(saved.updated_at)
      setSaveError('')
      setError('')
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  function updateAudience<K extends keyof MusicAudienceContext>(key: K, value: MusicAudienceContext[K]): void {
    if (key === 'referenceArtists') {
      audienceRunRef.current++
      setAudienceReport(null)
      setAudienceError('')
      setAudienceBusy(false)
    }
    setAudience((current) => ({ ...current, [key]: value }))
  }

  async function exploreAudience(): Promise<void> {
    if (!result || !audience.referenceArtists.trim() || audienceBusy) return
    const run = ++audienceRunRef.current
    setAudienceBusy(true)
    setAudienceError('')
    setAudienceReport(null)
    try {
      const report = await fetchMusicAudience(audience.referenceArtists.trim())
      if (run === audienceRunRef.current) {
        setAudienceReport(report)
        const version = saveVersionRef.current
        try {
          const saved = await alignSavedReportsApi.save('music', savedTitle || file?.name || 'Song', {
            analysis: result, audience_context: audience, audience_report: report,
            destinations: suggestMusicDestinations(result, audience)
          }, savedIdRef.current)
          if (version === saveVersionRef.current) {
            savedIdRef.current = saved.id
            setSavedAt(saved.updated_at)
            setSaveError('')
            const { reports } = await alignSavedReportsApi.list('music')
            setSavedReports(reports)
          }
        } catch (cause) {
          if (version === saveVersionRef.current) setSaveError(cause instanceof Error ? cause.message : String(cause))
        }
      }
    } catch (cause) {
      if (run === audienceRunRef.current) setAudienceError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (run === audienceRunRef.current) setAudienceBusy(false)
    }
  }

  useEffect(() => () => workerRef.current?.terminate(), [])

  async function analyze(): Promise<void> {
    if (!file || busy) return
    saveVersionRef.current++
    savedIdRef.current = ''
    setSavedTitle(file.name)
    setSavedAt('')
    setSaveError('')
    setError('')
    setResult(null)
    audienceRunRef.current++
    setAudienceReport(null)
    setAudienceError('')
    setAudienceBusy(false)
    if (!ALLOWED_AUDIO.test(file.name)) {
      setError('Choose an MP3, WAV, OGG, FLAC, M4A, AAC, or AIFF file.')
      return
    }
    if (file.size > MAX_AUDIO_BYTES) {
      setError('This file is over 100 MB. Export a smaller copy and try again.')
      return
    }
    setBusy(true)
    let context: AudioContext | null = null
    let worker: Worker | null = null
    try {
      setStatus('Decoding audio on this device…')
      context = new AudioContext({ sampleRate: SAMPLE_RATE })
      const decoded = await context.decodeAudioData(await file.arrayBuffer())
      if (decoded.duration > MAX_DURATION_SECONDS) throw new Error('This song is over the 15 minute analysis limit.')
      if (decoded.duration < 1) throw new Error('The audio is too short to analyze.')
      const samples = new Float32Array(decoded.length)
      for (let channel = 0; channel < decoded.numberOfChannels; channel++) {
        const input = decoded.getChannelData(channel)
        for (let index = 0; index < samples.length; index++) samples[index] += input[index] / decoded.numberOfChannels
      }
      await context.close()
      context = null

      setStatus('Loading bundled Essentia…')
      worker = new Worker(new URL('./essentia.worker.ts', import.meta.url), { type: 'module' })
      workerRef.current = worker
      const analysis = new Promise<EssentiaSongResult>((resolve, reject) => {
        worker!.onmessage = (event: MessageEvent<EssentiaWorkerReply>) => {
          const reply = event.data
          if (reply.kind === 'progress') setStatus(reply.message)
          if (reply.kind === 'result') resolve(reply.result)
          if (reply.kind === 'error') reject(new Error(reply.message))
        }
        worker!.onerror = () => reject(new Error('The bundled Essentia worker could not start.'))
      })
      worker.postMessage({ samples, sampleRate: decoded.sampleRate }, [samples.buffer])
      const analyzed = await analysis
      try {
        const saved = await alignSavedReportsApi.save('music', file.name, {
          analysis: analyzed, audience_context: audience, audience_report: null,
          destinations: suggestMusicDestinations(analyzed, audience)
        })
        savedIdRef.current = saved.id
        setSavedAt(saved.updated_at)
        const { reports } = await alignSavedReportsApi.list('music')
        setSavedReports(reports)
      } catch (cause) {
        setSaveError(cause instanceof Error ? cause.message : String(cause))
      }
      setResult(analyzed)
      setStatus('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setStatus('')
    } finally {
      worker?.terminate()
      workerRef.current = null
      if (context) await context.close()
      setBusy(false)
    }
  }

  return <>
    {savedReports.length > 0 && <section style={{ ...card, marginBottom: 16 }}><strong>Saved music analyses</strong><div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 9 }}>{savedReports.map((item) => <button type="button" key={item.id} onClick={() => void openSaved(item.id)} style={{ ...primaryButtonSmall, background: item.id === savedIdRef.current ? 'var(--accent)' : 'var(--surface)', color: item.id === savedIdRef.current ? 'var(--accent-ink)' : 'var(--ink)' }}>{item.title}</button>)}</div></section>}
    <section style={{ ...card, marginBottom: 16 }}>
      <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)', marginBottom: 5 }}>Analyze a song with Essentia</div>
      <p style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', margin: '0 0 15px' }}>
        Essentia runs inside this app, even offline. Choose a song to measure its level, spectral balance, tempo, and estimated key. The audio stays on your device.
      </p>
      <label><span style={label}>Song audio (up to 100 MB and 15 minutes)</span><input id="align-music-file" type="file" accept=".mp3,.wav,.ogg,.oga,.flac,.m4a,.aac,.aif,.aiff,audio/*" onChange={(event) => { saveVersionRef.current++; savedIdRef.current = ''; setSavedAt(''); setFile(event.target.files?.[0] ?? null); setResult(null); setAudienceReport(null); setAudienceBusy(false); audienceRunRef.current++ }} style={{ ...textInput, boxSizing: 'border-box', padding: '8px 10px' }} /></label>
      <div style={{ borderTop: '1px solid var(--border)', marginTop: 17, paddingTop: 14 }}>
        <div style={{ font: "700 15px 'Kalam'", color: 'var(--ink)' }}>Where might it find listeners?</div>
        <p style={{ font: "600 11px/1.55 'Quicksand'", color: 'var(--ink-muted)', margin: '3px 0 11px' }}>These details are optional and stay in this window. They make the platform and community suggestions specific without asking Essentia to guess genre or audience.</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(205px, 1fr))', gap: 10 }}>
          <label><span style={label}>Style or subgenre</span><input value={audience.style} onChange={(event) => updateAudience('style', event.target.value)} maxLength={120} placeholder="e.g. dream pop, liquid drum and bass" style={textInput} /></label>
          <label><span style={label}>Reference artists (optional)</span><input value={audience.referenceArtists} onChange={(event) => updateAudience('referenceArtists', event.target.value)} maxLength={120} placeholder="Artists you genuinely sound near" style={textInput} /></label>
          <label><span style={label}>City or scene (optional)</span><input value={audience.location} onChange={(event) => updateAudience('location', event.target.value)} maxLength={100} placeholder="e.g. Kolkata" style={textInput} /></label>
          <label><span style={label}>Main goal</span><select value={audience.goal} onChange={(event) => updateAudience('goal', event.target.value as MusicGoal)} style={textInput}><option value="feedback">Honest feedback</option><option value="listeners">Find listeners</option><option value="sales">Direct fan support</option><option value="playlists">Playlist consideration</option></select></label>
          <label><span style={label}>Release stage</span><select value={audience.stage} onChange={(event) => updateAudience('stage', event.target.value as ReleaseStage)} style={textInput}><option value="draft">Work in progress</option><option value="unreleased">Finished, unreleased</option><option value="released">Already released</option></select></label>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 14 }}>
        <button type="button" disabled={!file || busy} onClick={() => void analyze()} style={{ ...primaryButton, opacity: !file || busy ? .6 : 1 }}>{busy ? 'Analyzing…' : 'Analyze song'}</button>
        {status && <span role="status" style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-muted)' }}>{status}</span>}
      </div>
      {error && <div role="alert" style={{ marginTop: 12, font: "700 12px 'Quicksand'", color: 'var(--danger-ink)' }}>{error}</div>}
      <p style={{ font: "600 10.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', margin: '12px 0 0' }}>Essentia 0.1.3 is bundled under AGPL-3.0. Song audio stays on your device. Derived measurements, your context, and listener research are saved locally for future strategy work. Audience research contacts MusicBrainz and ListenBrainz only when you request it, sending the reference artist names you entered.</p>
      {savedAt && <p role="status" style={{ font: "700 10.5px 'Quicksand'", color: 'var(--ink-muted)' }}>Derived report saved {new Date(savedAt).toLocaleString()}.</p>}
      {saveError && <p role="alert" style={{ color: 'var(--danger-ink)' }}>Could not save analysis: {saveError}</p>}
    </section>

    {result && <section style={{ ...card, padding: 22 }}>
      <div style={sectionEyebrow}>Local Essentia analysis</div>
      <h2 style={{ font: "700 22px 'Kalam'", color: 'var(--ink)', margin: '5px 0 12px' }}>{savedTitle || file?.name || 'Song'}</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 9 }}>
        {metric(clock(result.durationSeconds), 'Duration', `Decoded at ${result.sampleRate.toLocaleString()} Hz`)}
        {metric(result.tempoBpm === null ? 'Unknown' : `${result.tempoBpm} BPM`, 'Tempo estimate', `${result.excerptCount} excerpt${result.excerptCount === 1 ? '' : 's'} measured`)}
        {metric(result.key ?? 'Uncertain', 'Estimated key', result.keyStrength === null ? 'No agreement across excerpts' : `Essentia strength ${result.keyStrength.toFixed(2)}`)}
      </div>
      <h3 style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', margin: '19px 0 8px' }}>Sound across the song</h3>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(115px, 1fr))', gap: 7 }}>
        {result.sections.map((section) => <div key={section.startSeconds} style={{ background: 'var(--accent-soft-bg)', borderRadius: 9, padding: 9 }}>
          <div style={{ font: "700 10.5px 'Quicksand'", color: 'var(--ink)' }}>{clock(section.startSeconds)}–{clock(section.endSeconds)}</div>
          <div style={{ font: "600 10px/1.55 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>Level {section.levelDbfs.toFixed(1)} dBFS<br />Peak {section.peakDbfs.toFixed(1)} dBFS<br />Centroid {section.spectralCentroidHz} Hz</div>
        </div>)}
      </div>
      <p style={{ font: "600 10.5px/1.6 'Quicksand'", color: 'var(--ink-faint)' }}>Essentia RMS covers each entire section. Spectral centroid uses one second at each section’s midpoint; it describes frequency balance, not instruments or genre.</p>
      <ul style={{ margin: '8px 0 0', paddingLeft: 18, font: "600 11px/1.65 'Quicksand'", color: 'var(--ink-muted)' }}>{result.notes.map((note) => <li key={note}>{note}</li>)}</ul>
      <div style={{ font: "600 10px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 10 }}>Essentia engine {result.essentiaVersion} · all processing local</div>
      <MusicAudiencePanel references={audience.referenceArtists} report={audienceReport} busy={audienceBusy} error={audienceError} onExplore={() => void exploreAudience()} />
      <div style={{ borderTop: '1.5px solid var(--border)', marginTop: 21, paddingTop: 17 }}>
        <div style={sectionEyebrow}>Audience routes</div>
        <h3 style={{ font: "700 20px 'Kalam'", color: 'var(--ink)', margin: '4px 0 5px' }}>Places worth testing</h3>
        <p style={{ font: "600 11.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', margin: '0 0 6px' }}>Ranked for your stated goal and release stage. These are documented routes for publishing, feedback, or scene research—not predictions of streams, acceptance, or listener taste.</p>
        <p style={{ font: "600 10.5px/1.55 'Quicksand'", color: 'var(--ink-faint)', margin: '0 0 13px' }}>Essentia measured tempo, an estimated key, and level across the song. It did not identify genre, instruments, lyrical language, similar artists, or communities. Source research reviewed {RESEARCH_REVIEWED}; open current rules before posting.</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
          {destinations.map((item, index) => <DestinationCard key={item.id} item={item} rank={index + 1} />)}
        </div>
      </div>
      <button type="button" onClick={() => setResult(null)} style={{ ...primaryButtonSmall, marginTop: 15 }}>Close analysis</button>
    </section>}
  </>
}
