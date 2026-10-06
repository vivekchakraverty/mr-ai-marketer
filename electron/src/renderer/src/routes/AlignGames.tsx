import { useEffect, useState } from 'react'
import {
  analyzeGameplay, deleteGameAnalysis, fetchGameAnalysis, fetchGameAnalysisStatus, listGameAnalyses, refreshGameComparables,
  type ComparableGame, type GameAnalysisJob, type GameAnalysisStatus, type GamePlatform
} from '../api/client'
import { card, primaryButton, primaryButtonSmall, sectionEyebrow } from '../styles/styleKit'

const stages: Record<string, string> = {
  queued: 'Queued', preprocessing_video: 'Inspecting video', analyzing_gameplay: 'Observing gameplay chunks',
  merging_analysis: 'Merging timestamped evidence', querying_igdb: 'Searching IGDB',
  ranking_comparables: 'Ranking comparable games', modeling_audience: 'Modeling player motivations',
  generating_recommendations: 'Estimating platform fit', completed: 'Completed', failed: 'Failed'
}
const percent = (value: number): string => `${Math.round(value * 100)}%`
const clock = (seconds: number): string => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
const heading = { font: "700 20px 'Kalam'", color: 'var(--ink)', margin: '0 0 10px' }
const muted = { color: 'var(--ink-muted)', font: "600 12px/1.6 'Quicksand'" }

function GameCard({ game }: { game: ComparableGame }): React.JSX.Element {
  return <details style={{ border: '1px solid var(--border)', borderRadius: 12, padding: 12, background: 'var(--surface)' }}>
    <summary style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 12 }}>
      {game.cover_url && <img src={game.cover_url} alt="" loading="lazy" style={{ width: 42, height: 58, objectFit: 'cover', borderRadius: 5 }} />}
      <span style={{ flex: 1 }}><strong>{game.name}</strong><br /><small>{game.genres.join(' · ') || 'Genre unavailable'}</small></span>
      <span style={{ textAlign: 'right' }}><strong>{game.similarity}% similar</strong><br /><small>IGDB {game.rating == null ? 'unrated' : `${Math.round(game.rating)}/100 · ${game.rating_count ?? 'unknown'} ratings`}</small></span>
    </summary>
    <div style={{ ...muted, marginTop: 12 }}>
      <div><strong>Why it matches:</strong> {game.why.join(', ')}.</div>
      <div><strong>Key difference:</strong> {game.difference}</div>
      <div><strong>Platforms in IGDB:</strong> {game.platforms.join(', ') || 'Unavailable'}</div>
      <div>Similarity confidence: {percent(game.similarity_confidence)}. IGDB rating is displayed separately and does not affect similarity.</div>
      {game.igdb_url && <a href={game.igdb_url} target="_blank" rel="noreferrer">View IGDB record ↗</a>}
    </div>
  </details>
}

function RecommendationList({ title, rows }: { title: string; rows: GamePlatform[] }): React.JSX.Element {
  return <div>
    <h3 style={{ font: "700 16px 'Kalam'", color: 'var(--ink)', margin: '16px 0 8px' }}>{title}</h3>
    <div style={{ display: 'grid', gap: 8 }}>
      {rows.map((row) => <details key={row.name} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 12px' }}>
        <summary style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <strong>{row.name}</strong><span>{row.category} · {row.score}/100</span>
        </summary>
        <div style={{ ...muted, paddingTop: 8 }}>
          {row.reasons.map((reason) => <div key={reason}>• {reason}</div>)}
          <div>Confidence: {percent(row.confidence)}{row.average_similarity != null ? ` · Average comparable similarity: ${row.average_similarity}%` : ''}{row.input_suitability != null ? ` · Input suitability estimate: ${row.input_suitability}/100` : ''}</div>
          {row.publishing_feasibility && <div>Publishing feasibility: {row.publishing_feasibility}</div>}
        </div>
      </details>)}
    </div>
  </div>
}

export default function AlignGames(): React.JSX.Element {
  const [file, setFile] = useState<File | null>(null)
  const [samplingFps, setSamplingFps] = useState(1)
  const [jobId, setJobId] = useState(() => window.localStorage.getItem('alignGamesAnalysisId') || '')
  const [job, setJob] = useState<GameAnalysisJob | null>(null)
  const [recent, setRecent] = useState<GameAnalysisJob[]>([])
  const [uploading, setUploading] = useState(false)
  const [refreshingComparables, setRefreshingComparables] = useState(false)
  const [error, setError] = useState('')
  const [provider, setProvider] = useState<GameAnalysisStatus | null>(null)

  useEffect(() => { void listGameAnalyses().then((value) => setRecent(value.analyses)).catch(() => undefined) }, [])
  useEffect(() => { void fetchGameAnalysisStatus().then(setProvider).catch(() => undefined) }, [])
  useEffect(() => {
    if (!jobId) return
    let stopped = false
    let timer = 0
    const poll = async (): Promise<void> => {
      try {
        const result = await fetchGameAnalysis(jobId)
        if (stopped) return
        setJob(result)
        if (result.status !== 'completed' && result.status !== 'failed') timer = window.setTimeout(() => { void poll() }, 2000)
        else void listGameAnalyses().then((value) => setRecent(value.analyses)).catch(() => undefined)
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : String(err))
      }
    }
    void poll()
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [jobId])

  async function upload(): Promise<void> {
    if (!file || uploading) return
    setUploading(true); setError('')
    try {
      const result = await analyzeGameplay(file, samplingFps)
      setJobId(result.analysis_id)
      window.localStorage.setItem('alignGamesAnalysisId', result.analysis_id)
      setFile(null)
      const input = document.getElementById('align-game-file') as HTMLInputElement | null
      if (input) input.value = ''
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setUploading(false) }
  }

  async function remove(): Promise<void> {
    if (!job) return
    try {
      await deleteGameAnalysis(job.id)
      setJob(null); setJobId(''); window.localStorage.removeItem('alignGamesAnalysisId')
      setRecent((items) => items.filter((item) => item.id !== job.id))
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
  }

  async function refreshComparables(): Promise<void> {
    if (!job || refreshingComparables) return
    const selectedId = job.id
    setRefreshingComparables(true); setError('')
    try {
      const refreshed = await refreshGameComparables(selectedId)
      setJob((current) => current?.id === selectedId ? refreshed : current)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setRefreshingComparables(false) }
  }

  const result = job?.report
  const profile = result?.profile
  return <div style={{ maxWidth: 970 }}>
    <div style={sectionEyebrow}>Game Audience Analyzer</div>
    <h2 style={{ color: 'var(--ink)', marginBottom: 4 }}>Who might enjoy this game?</h2>
    <p style={{ ...muted, fontSize: 14, marginTop: 0 }}>Upload gameplay from a released game, prototype, mod, or game jam project. The game title is optional because the analysis starts with what is visible.</p>
    {provider && <div role="status" style={{ ...card, padding: 14, marginBottom: 14, color: provider.modal_credentials_configured ? 'var(--ink-muted)' : '#a34a3a' }}>
      <strong>{provider.modal_credentials_configured ? 'Modal token detected' : 'GPU setup needed'}</strong>
      <div style={{ fontSize: 12, marginTop: 4 }}>{provider.message}</div>
      <div style={{ fontSize: 12, marginTop: 4 }}>{provider.igdb_configured ? 'IGDB credentials are configured.' : 'IGDB credentials are missing. Gameplay observation can still run, but comparable games will be unavailable.'}</div>
    </div>}
    <section style={{ ...card, padding: 18, marginBottom: 16 }}>
      <label htmlFor="align-game-file"><strong>Gameplay video</strong></label><br />
      <input id="align-game-file" type="file" accept=".mp4,.mov,.webm,.mkv,.m4v,video/*" onChange={(event) => setFile(event.target.files?.[0] ?? null)} style={{ margin: '10px 0' }} />
      <div style={{ ...muted, marginBottom: 10 }}>MP4, MOV, WebM, MKV or M4V · up to 500 MB and 10 minutes. Uploaded footage is deleted after analysis by default. Sampled frames go to your Modal GPU workspace; derived reports stay in the local app database.</div>
      <label style={{ ...muted, display: 'block', marginBottom: 12 }}>Sampling rate: <select value={samplingFps} onChange={(event) => setSamplingFps(Number(event.target.value))} style={{ marginLeft: 6 }}><option value={1}>1 frame/sec</option><option value={2}>2 frames/sec (fast gameplay)</option></select></label>
      <button type="button" disabled={!file || uploading || provider?.modal_credentials_configured === false} style={{ ...primaryButton, opacity: !file || uploading || provider?.modal_credentials_configured === false ? .6 : 1 }} onClick={() => { void upload() }}>{uploading ? 'Uploading…' : 'Analyze gameplay'}</button>
      {error && <div role="alert" style={{ color: '#a34a3a', marginTop: 10 }}>{error}</div>}
    </section>
    {recent.length > 0 && <section style={{ ...card, padding: 16, marginBottom: 16 }}><strong>Recent analyses</strong><div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>{recent.map((item) => <button type="button" key={item.id} onClick={() => { setJobId(item.id); window.localStorage.setItem('alignGamesAnalysisId', item.id) }} style={{ ...primaryButtonSmall, background: item.id === jobId ? 'var(--accent)' : 'var(--surface)', color: item.id === jobId ? '#fff' : 'var(--ink)' }}>{item.filename} · {stages[item.status] || item.status}</button>)}</div></section>}
    {job && <section style={{ ...card, padding: 18, marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}><strong>{job.filename}</strong><button type="button" onClick={() => { void remove() }} style={primaryButtonSmall}>Delete analysis</button></div>
      <div role="status" style={{ ...muted, marginTop: 5 }}>{stages[job.status] || job.status}{job.status !== 'completed' && job.status !== 'failed' ? '…' : ''}{job.status === 'analyzing_gameplay' ? ' The first run may take several minutes while Modal sets up the model.' : ''}</div>
      {job.status === 'completed' && <button type="button" disabled={refreshingComparables || !provider?.igdb_configured}
        onClick={() => { void refreshComparables() }} style={{ ...primaryButtonSmall, marginTop: 10, opacity: provider?.igdb_configured ? 1 : .6 }}>
        {refreshingComparables ? 'Finding comparable games…' : 'Refresh comparable games'}
      </button>}
      {job.metadata && <div style={{ ...muted, marginTop: 5 }}>{clock(job.metadata.duration)} · {job.metadata.width}×{job.metadata.height} · {job.metadata.fps} FPS · {job.metadata.codec} · {job.metadata.has_audio ? 'Audio present' : 'No audio'}</div>}
      {job.error && <div role="alert" style={{ color: '#a34a3a', marginTop: 8 }}>{job.error}{job.error.startsWith('Gameplay analysis failed.') && <div style={{ fontSize: 12, marginTop: 4 }}>This run was saved before step-specific errors were available. Reupload the video after checking the GPU setup above.</div>}</div>}
    </section>}
    {result && profile && <>
      <section style={{ ...card, padding: 20, marginBottom: 16 }}><h3 style={heading}>What this game appears to be like</h3>{result.description.split(/\n\n+/).filter(Boolean).map((paragraph, index) => <p key={index} style={{ color: 'var(--ink)', lineHeight: 1.7 }}>{paragraph}</p>)}<div style={muted}>Gameplay interpretation · overall observation confidence {percent(result.overall_confidence)}</div></section>
      <section style={{ ...card, padding: 20, marginBottom: 16 }}>
        <h3 style={heading}>Gameplay DNA and core loop</h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: 14 }}>{[...profile.genres, ...profile.subgenres, ...profile.themes].map((tag) => <span key={tag} style={{ borderRadius: 999, padding: '5px 9px', background: 'var(--accent-soft-bg)', color: 'var(--ink)' }}>{tag}</span>)}</div>
        <div style={{ fontWeight: 700, color: 'var(--ink)', marginBottom: 8 }}>{profile.core_loop.length ? profile.core_loop.join(' → ') : 'Core loop is not clear from the footage.'}</div>
        <div style={muted}>Pace: {profile.pace} · Complexity: {profile.complexity}</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 8, marginTop: 14 }}>{Object.entries(profile.dimensions).map(([key, value]) => <div key={key} style={{ border: '1px solid var(--border)', padding: 8, borderRadius: 8 }}><div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}><span>{key.replaceAll('_', ' ')}</span><strong>{value.score}/100</strong></div><div style={{ background: 'var(--border)', borderRadius: 4, height: 5, marginTop: 5 }}><div style={{ width: `${value.score}%`, background: 'var(--accent)', height: 5, borderRadius: 4 }} /></div><small style={muted}>Confidence {percent(value.confidence)} · evidence near {value.evidence_timestamps.map(clock).join(', ')}</small></div>)}</div>
      </section>
      <section style={{ ...card, padding: 20, marginBottom: 16 }}><h3 style={heading}>Who is most likely to enjoy it?</h3><p style={{ ...muted, fontSize: 13 }}>{result.audience.summary || 'Audience fit is uncertain from this footage.'}</p><div style={{ display: 'grid', gap: 8 }}>{result.audience.archetypes.map((item) => <details key={item.archetype} style={{ border: '1px solid var(--border)', borderRadius: 9, padding: 10 }}><summary style={{ cursor: 'pointer' }}><strong>{item.archetype}</strong> · {item.affinity}/100 predicted affinity</summary><div style={{ ...muted, marginTop: 7 }}>{item.reason}<br />Evidence near {item.evidence_timestamps.map(clock).join(', ')} · confidence {percent(item.confidence)}</div></details>)}</div><p style={muted}>{result.audience.basis}</p></section>
      <section style={{ ...card, padding: 20, marginBottom: 16 }}><h3 style={heading}>Closest games</h3>{result.igdb_note && <p style={{ ...muted, color: '#a34a3a' }}>{result.igdb_note}</p>}{result.comparables.length ? <><div style={{ overflowX: 'auto', marginBottom: 14 }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}><thead><tr><th style={{ textAlign: 'left' }}>Comparable game</th><th>Similarity</th><th>IGDB rating</th><th>Rating count</th></tr></thead><tbody>{result.comparables.map((game) => <tr key={game.igdb_id} style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: 7 }}>{game.name}</td><td style={{ textAlign: 'center' }}>{game.similarity}%</td><td style={{ textAlign: 'center' }}>{game.rating == null ? '—' : Math.round(game.rating)}</td><td style={{ textAlign: 'center' }}>{game.rating_count == null ? '—' : new Intl.NumberFormat().format(game.rating_count)}</td></tr>)}</tbody></table></div><div style={{ display: 'grid', gap: 8 }}>{result.comparables.map((game) => <GameCard key={game.igdb_id} game={game} />)}</div></> : <div style={muted}>No defensible IGDB comparables were found. The gameplay analysis still stands independently.</div>}</section>
      <section style={{ ...card, padding: 20, marginBottom: 16 }}><h3 style={heading}>Where could this game find its audience?</h3><p style={muted}>{result.platforms.caveat}</p><RecommendationList title="Distribution / hardware" rows={result.platforms.hardware} /><RecommendationList title="Storefronts" rows={result.platforms.distribution} /><RecommendationList title="Discovery / community" rows={result.platforms.discovery} /></section>
      <section style={{ ...card, padding: 20, marginBottom: 16 }}><h3 style={heading}>Why does the AI think this?</h3><p style={muted}>{result.source_note}</p>{profile.claims.map((claim) => <details key={claim.claim} style={{ borderBottom: '1px solid var(--border)', padding: '8px 0' }}><summary style={{ cursor: 'pointer' }}>{claim.claim} · {claim.category.replaceAll('_', ' ')} · {percent(claim.confidence)}</summary><ul>{claim.evidence.map((item) => <li key={`${item.timestamp}-${item.observation}`}>{clock(item.timestamp)} — {item.observation} ({percent(item.confidence)})</li>)}</ul></details>)}{profile.claims.length === 0 && <p style={muted}>No inference had enough timestamped evidence to display as a grounded claim.</p>}<h4>Gameplay event timeline</h4><div style={{ maxHeight: 280, overflowY: 'auto', ...muted }}>{profile.events.map((event, index) => <div key={index}>{clock(event.start)}–{clock(event.end)} · {event.type}: {event.description} ({percent(event.confidence)})</div>)}</div>{profile.uncertainties.length > 0 && <p style={muted}>Uncertain: {profile.uncertainties.join('; ')}</p>}</section>
    </>}
  </div>
}
