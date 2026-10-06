export type VideoJobState = { status: string; report: { matches: unknown[] } | null }
export type SourceState = { available: boolean } | null

export function videoAnalysisState(uploading: boolean, job: VideoJobState | null): 'ready' | 'uploading' | 'processing' | 'failed' | 'no_matches' | 'matches' {
  if (uploading) return 'uploading'
  if (!job) return 'ready'
  if (job.status === 'failed') return 'failed'
  if (job.status !== 'completed') return 'processing'
  return job.report?.matches.length ? 'matches' : 'no_matches'
}

export function videoSourceState(source: SourceState): 'checking' | 'available' | 'unavailable' {
  return source === null ? 'checking' : source.available ? 'available' : 'unavailable'
}

export function videoViralityScore(report: { matches: unknown[]; content_virality: { score: number | null } }): number | null {
  // Saved reports from the first implementation returned 0 for no matches.
  return report.matches.length && report.content_virality.score != null ? report.content_virality.score : null
}

export function startVideoPolling<T extends { status: string }>(options: {
  read: () => Promise<T>
  publish: (job: T) => void
  onError: (error: unknown) => void
  schedule: (callback: () => void) => () => void
}): () => void {
  let alive = true
  let cancelTimer: (() => void) | undefined
  const poll = async (): Promise<void> => {
    try {
      const job = await options.read()
      if (!alive) return
      options.publish(job)
      if (job.status === 'completed' || job.status === 'failed') return
    } catch (error) {
      if (!alive) return
      options.onError(error)
    }
    if (alive) cancelTimer = options.schedule(() => { void poll() })
  }
  void poll()
  return () => { alive = false; cancelTimer?.() }
}
