import { useEffect, useState } from 'react'
import {
  analyzeViralFootprint,
  fetchViralFootprint,
  fetchPublicVideoSource,
  type ViralFootprintJob,
  type ViralFootprintMatch,
  type PublicVideoSourceStatus
} from '../api/client'
import { card, primaryButton, sectionEyebrow } from '../styles/styleKit'
import { startVideoPolling, videoAnalysisState, videoSourceState, videoViralityScore } from './videoFootprintState'

const number = (value: number | null | undefined): string => value == null ? '—' : new Intl.NumberFormat().format(value)
const stage: Record<string, string> = {
  queued: 'Queued', extracting_media: 'Reading video', fingerprinting: 'Creating fingerprints',
  finding_candidates: 'Searching public videos', comparing: 'Comparing footage',
  collecting_metrics: 'Collecting post metrics', calculating_virality: 'Calculating content virality'
}

function MatchCard({ match, comparison = false }: { match: ViralFootprintMatch; comparison?: boolean }): React.JSX.Element {
  const [open, setOpen] = useState(false)
  return <div style={{ ...card, padding: 16, marginBottom: 10 }}>
    <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} style={{ border: 0, background: 'none', color: 'var(--ink)', width: '100%', textAlign: 'left', cursor: 'pointer', display: 'grid', gridTemplateColumns: '1fr auto', gap: 12 }}>
      <span><strong>{match.platform}</strong> · {match.title}<br /><small>{comparison ? `${match.visual_similarity}% visual similarity` : `${match.confidence} · ${match.similarity}% copy similarity`}</small></span>
      <span style={{ textAlign: 'right' }}><strong>{number(match.views)}</strong> views{!comparison && <><br /><small>{match.post_virality_score == null ? 'Post score unavailable' : `Post score ${number(match.post_virality_score)} / 100`}</small></>}</span>
    </button>
    {open && <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12, fontSize: 13, lineHeight: 1.8 }}>
      <div>Visual: {match.visual_similarity}% · Transcript: {match.transcript_similarity == null ? 'unavailable' : `${match.transcript_similarity}%`} · Duration: {match.duration_similarity}% {match.exact ? '· Byte-identical' : ''}</div>
      <div>Creator: {match.creator || 'Unknown'} · Posted: {match.posted_at ? new Date(match.posted_at).toLocaleDateString() : 'Unknown'}</div>
      {match.duration_seconds != null && <div>Length: {match.duration_seconds.toFixed(1)} seconds</div>}
      <div>Likes: {number(match.likes)} · Comments: {number(match.comments)} · Shares: {number(match.shares)} · Outlier: {match.outlier_multiplier == null ? 'unavailable' : `${match.outlier_multiplier}×`}</div>
      {match.post_url && /^https?:\/\//i.test(match.post_url) && <a href={match.post_url} target="_blank" rel="noreferrer">Open source post ↗</a>}
    </div>}
  </div>
}

export default function AlignVideos(): React.JSX.Element {
  const [source, setSource] = useState<PublicVideoSourceStatus | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [searchTerms, setSearchTerms] = useState('')
  const [referenceUrl, setReferenceUrl] = useState('')
  const [job, setJob] = useState<ViralFootprintJob | null>(null)
  const [jobId, setJobId] = useState(() => window.localStorage.getItem('alignVideosPublicAnalysisId') || '')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { void fetchPublicVideoSource().then(setSource).catch((err) => setError(String(err))) }, [])
  useEffect(() => {
    if (!jobId) return
    return startVideoPolling({
      read: () => fetchViralFootprint(jobId), publish: setJob,
      onError: (err) => setError(err instanceof Error ? err.message : String(err)),
      schedule: (callback) => {
        const timer = window.setTimeout(callback, 2000)
        return () => window.clearTimeout(timer)
      }
    })
  }, [jobId])

  async function upload(): Promise<void> {
    if (!file || uploading) return
    setUploading(true); setError(''); setJobId(''); setJob(null)
    try {
      const result = await analyzeViralFootprint(file, searchTerms, referenceUrl)
      setJobId(result.analysis_id)
      window.localStorage.setItem('alignVideosPublicAnalysisId', result.analysis_id)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setUploading(false) }
  }

  const report = job?.report
  const view = videoAnalysisState(uploading, job)
  const sourceView = videoSourceState(source)
  const viralityScore = report ? videoViralityScore(report) : null
  const comparisons = report?.comparisons?.filter((item) => !item.is_match) || []
  const largest = report?.matches.reduce<ViralFootprintMatch | null>((best, match) =>
    (match.views ?? 0) > (best?.views ?? 0) ? match : best, null)
  return <div style={{ maxWidth: 950 }}>
    <div style={sectionEyebrow}>Viral Footprint</div>
    <h2 style={{ color: 'var(--ink)', marginBottom: 4 }}>Compare your reel with public videos</h2>
    <p style={{ color: 'var(--ink-muted)', marginTop: 0 }}>Find public reference videos, compare their footage and view counts, and check for copies of your reel.</p>
    <div style={{ ...card, padding: 18, marginBottom: 16 }}>
      <strong>{sourceView === 'available' ? 'YouTube public search ready' : sourceView === 'checking' ? 'Checking search provider…' : 'Search provider unavailable'}</strong>
      <div style={{ color: 'var(--ink-muted)', fontSize: 13, marginTop: 4 }}>{source?.detail || 'Checking the built-in search adapter…'}</div>
      <div style={{ color: 'var(--ink-muted)', fontSize: 13, marginTop: 4 }}>{source?.limitations || 'TikTok, Douyin, Instagram, and Reddit search are unavailable in this mode.'}</div>
    </div>
    <div style={{ ...card, padding: 18, marginBottom: 16 }}>
      <label htmlFor="align-video-file"><strong>Your reel</strong></label><br />
      <input id="align-video-file" type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.m4v" onChange={(event) => setFile(event.target.files?.[0] || null)} style={{ margin: '10px 0' }} />
      <label htmlFor="align-video-query" style={{ display: 'block', marginTop: 5 }}><strong>Caption or topic</strong></label>
      <input id="align-video-query" type="text" maxLength={160} value={searchTerms} onChange={(event) => setSearchTerms(event.target.value)} placeholder="A distinctive phrase, scene, person, or caption" style={{ boxSizing: 'border-box', width: '100%', margin: '8px 0', padding: 10, border: '1px solid var(--border)', borderRadius: 8 }} />
      <div style={{ color: 'var(--ink-muted)', fontSize: 12, marginBottom: 10 }}>Search terms go to YouTube to find candidates; your uploaded video stays on this device. If terms are blank, speech transcription supplies the query when possible.</div>
      <label htmlFor="align-video-reference" style={{ display: 'block' }}><strong>YouTube video to compare directly (optional)</strong></label>
      <input id="align-video-reference" type="url" value={referenceUrl} onChange={(event) => setReferenceUrl(event.target.value)} placeholder="https://www.youtube.com/shorts/…" style={{ boxSizing: 'border-box', width: '100%', margin: '8px 0 12px', padding: 10, border: '1px solid var(--border)', borderRadius: 8 }} />
      <div style={{ color: 'var(--ink-muted)', fontSize: 12, marginBottom: 12 }}>MP4, MOV, WebM, MKV or M4V · up to 200 MB · source file deleted after analysis</div>
      <button type="button" style={primaryButton} disabled={!file || view === 'uploading' || sourceView !== 'available'} onClick={() => { void upload() }}>{view === 'uploading' ? 'Uploading…' : 'Search and compare'}</button>
    </div>
    {error && <div role="alert" style={{ color: '#ad2929', marginBottom: 16 }}>{error}</div>}
    {job && view === 'processing' && <div role="status" style={{ ...card, padding: 18 }}>Analyzing {job.filename}: {stage[job.status] || job.status}…</div>}
    {job && view === 'failed' && <div role="alert" style={{ ...card, padding: 18, color: '#ad2929' }}>Analysis failed: {job.error || 'Unknown error'}</div>}
    {report && <>
      <div style={{ ...card, padding: 20, marginBottom: 16 }}>
        <div style={sectionEyebrow}>Content virality · verified copies</div>
        <div style={{ fontSize: viralityScore == null ? 26 : 38, fontWeight: 700, color: 'var(--ink)' }}>{viralityScore == null ? 'Virality not established' : <>{viralityScore} <small style={{ fontSize: 16 }}>/ 100</small></>}</div>
        {report.matches.length ? <div>{number(report.content_virality.observed_views)} observed views · {report.content_virality.detected_copies} verified copies · {report.content_virality.platforms.join(', ')}</div>
          : <p style={{ margin: '8px 0' }}>No public copy was verified in this search. An original or undiscovered reel has no measured copy-spread score yet.</p>}
        {largest && <div style={{ marginTop: 8, fontSize: 13 }}>Largest detected post: {largest.platform} · {number(largest.views)} views</div>}
        <p style={{ fontSize: 12, color: 'var(--ink-muted)' }}>This report measures detected copies. It does not predict how many views your upload will get.</p>
      </div>
      {report.benchmark && report.benchmark.measured_posts > 0 && <div style={{ ...card, padding: 18, marginBottom: 16 }}>
        <div style={sectionEyebrow}>Public reference videos · search sample</div>
        <div style={{ marginTop: 8 }}>{number(report.benchmark.median_views)} median views · {number(report.benchmark.highest_views)} highest views</div>
        <p style={{ fontSize: 12, color: 'var(--ink-muted)', marginBottom: 0 }}>Observed counts from {report.benchmark.measured_posts} public reference videos returned for this search. These describe the sample, rather than forecast your reel's reach.</p>
      </div>}
      <div style={{ ...card, padding: 16, marginBottom: 16 }}>
        <strong>Search coverage</strong><div>{report.coverage.query ? `Query: ${report.coverage.query}` : 'Direct YouTube comparison'} · {report.coverage.search_results} results · {report.coverage.checked} clips visually checked</div>
        {report.coverage.duration_excluded != null && <small>{report.coverage.duration_excluded} long videos excluded · {report.coverage.limit_excluded || 0} beyond the comparison limit</small>}
        {report.coverage.comparison_errors > 0 && <small>{report.coverage.comparison_errors} clips could not be downloaded or decoded.</small>}
      </div>
      <h3 style={{ color: 'var(--ink)' }}>{report.matches.length ? 'Matching public videos' : 'No verified match in the checked search results'}</h3>
      {report.matches.map((match) => <MatchCard key={match.video_id} match={match} />)}
      {comparisons.length > 0 && <>
        <h3 style={{ color: 'var(--ink)' }}>Closest footage comparisons</h3>
        <p style={{ fontSize: 13, color: 'var(--ink-muted)' }}>Sorted by visual similarity. Each reference video's views belong to that public post. An exact caption or a direct YouTube link can help locate a copy.</p>
        {comparisons.map((item) => <MatchCard key={`comparison-${item.video_id}`} match={item} comparison />)}
      </>}
      {report.matches.some((match) => match.posted_at) && <div style={{ ...card, padding: 16, marginTop: 16 }}>
        <strong>Detected posting dates</strong>
        {[...report.matches].filter((match) => match.posted_at).sort((a, b) => (a.posted_at || '').localeCompare(b.posted_at || '')).map((match) =>
          <div key={`date-${match.video_id}`} style={{ fontSize: 13, marginTop: 7 }}>{new Date(match.posted_at || '').toLocaleDateString()} · {match.platform} · {number(match.views)} current views</div>)}
      </div>}
      {report.notes.map((note) => <p key={note} style={{ color: 'var(--ink-muted)', fontSize: 12, margin: '4px 0' }}>{note}</p>)}
    </>}
  </div>
}
