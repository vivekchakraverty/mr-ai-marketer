import { backendUrl } from './client'

export interface Evidence {
  id: string; type: 'project' | 'align' | 'external' | 'inference'; claim: string
  source_name: string; source_url: string; publisher: string; published_at: string
  retrieved_at: string; relevance: string; source_quality: number; confidence: string
}
export interface Persona2 {
  id: string; name: string; archetype: string; description: string; priority: string
  relevance_score: number; score_components: Record<string, number>; confidence: string
  cluster_ids: string[]; signal_ids: string[]; snapshot: Record<string, string>; jobs_to_be_done: Record<string, string[]>
  motivations: { name: string; rank: number; why: string }[]
  pain_points: { text: string; importance: string }[]
  adoption_triggers: { investigate: string[]; try_it: string[]; purchase: string[]; recommend: string[]; return_to_it: string[] }
  objections: { objection: string; response: string }[]
  discovery_journey: { stage: string; touchpoints: string[]; questions: string[]; content: string[]; channels: string[]; proof: string; friction: string }[]
  channels: { name: string; affinity: string; why: string; evidence_ids: string[] }[]
  content_preferences: { types: string[]; length: string; hooks: string[]; tone: string; topics: string[]; visual_style: string; detail_level: string; proof: string; trusted_sources: string[]; conversion_examples: string[] }
  messaging: { core_message: string; value_proposition: string; pillars: string[]; resonant_words: string[]; avoid_words: string[]; hooks: string[] }
  affinities: { item: string; category: string; why: string; source: string; confidence: string; evidence_ids: string[] }[]
  reach: { place: string; kind: string; why: string; source_url: string; evidence_ids: string[] }[]
  evidence_ids: string[]; strategic_recommendations: string[]
}
export interface Persona2Report {
  id: string; created_at: string; researched_at: string; source_key: string
  source_snapshot: Persona2Context
  evidence: Evidence[]; methodology: string; model: string; usage: Record<string, unknown>
  personas: Persona2[]; strategy: Record<string, string[]>; conflicts: string[]; warnings: string[]
}
export interface Persona2Job { id: string; source_key: string; status: string; error: string; created_at: string; updated_at: string; report: Persona2Report | null }
export interface Persona2Source { key: string; label: string; kind: string; updated_at: string; clusters: number }
export interface Persona2ModelCheck {
  id: string; status: 'queued' | 'generating' | 'complete' | 'error'; error: string
  result: { persona_count?: number; models?: string[]; message?: string }
}
export interface Persona2Context { source_key: string; source_updated_at: string; project: Record<string, string>; project_origin: Record<string, 'project' | 'align'>; project_details: { id: string; label: string; value: string; origin: string }[]; clusters: { id: string; label: string; reason: string; basis: string }[]; audience_signals: { id: string; claim: string; basis: string }[]; platforms: { name: string }[]; affinities: { name: string }[]; communities: string[] }

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${backendUrl}/buyer-personas-2${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { ...(window.api?.apiToken ? { 'X-MRAIM-Token': window.api.apiToken } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string }
    throw new Error(payload.detail || `Buyer Persona request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const buyerPersonas2Api = {
  startModelCheck: () => request<Persona2ModelCheck>('/model-check', { consent: true }),
  getModelCheck: (id: string) => request<Persona2ModelCheck>(`/model-check/${encodeURIComponent(id)}`),
  sources: () => request<{ sources: Persona2Source[] }>('/sources'),
  context: (sourceKey: string) => request<Persona2Context>(`/context?source_key=${encodeURIComponent(sourceKey)}`),
  latest: (sourceKey: string) => request<{ job: Persona2Job | null; previous_report: Persona2Job | null }>(`/latest?source_key=${encodeURIComponent(sourceKey)}`),
  get: (id: string) => request<Persona2Job>(`/jobs/${encodeURIComponent(id)}`),
  generate: (source_key: string, project: Record<string, string>, regenerate: boolean, refresh_research: boolean) =>
    request<Persona2Job>('/generate', { source_key, project, regenerate, refresh_research })
}
