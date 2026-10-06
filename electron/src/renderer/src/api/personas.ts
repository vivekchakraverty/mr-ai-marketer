import { backendUrl } from './client'

export interface PersonaQuestion {
  id: string
  section: string
  prompt: string
  help: string
  type: 'text' | 'single' | 'multi' | 'number' | 'scale' | 'list'
  options?: string[]
  required: boolean
  core: boolean
  example?: string
  condition?: string
}

export interface EvidenceUnit {
  id: string
  source: string
  source_type: string
  domain: string
  date: string
  language: string
  text: string
  kind: string
  engagement: number
  meta: Record<string, unknown>
  tags: string[]
}

export interface Candidate {
  id: string
  label: string
  summary: string
  evidence_ids: string[]
  evidence_share: number
  top_pains: string[]
  top_goals: string[]
  snippets: { text: string; evidence_ids: string[] }[]
  channels: string[]
  confidence: string
  score?: number
  review: { rings_true: number; business_value: number; priority: number; missing: string; merge_with?: string }
}

export interface Claim { text: string; evidence_ids: string[]; origin: string }
export interface Persona {
  id: string
  label: string
  summary: string
  context: Claim
  jobs_to_be_done: Claim[]
  goals: Claim[]
  pains: Claim[]
  triggers: Claim[]
  objections: Claim[]
  decision_criteria: Claim[]
  buying_role: Claim
  channels: Claim[]
  content_preferences: Claim[]
  value_props: Claim[]
  phrases_to_use: Claim[]
  phrases_to_avoid: Claim[]
  hook_ideas: Claim[]
  kpis: string[]
  demographics: { text: string; origin: string }[]
  confidence: string
  score: number
  strategic_priority: number
  confidence_reasons: string[]
  evidence_ids: string[]
  edited_by_you: boolean
  validation: { interview_questions: string[]; experiments: string[] }
}

export interface PersonaRun {
  id: string
  name: string
  step: number
  status: string
  created_at: string
  updated_at: string
  mode: 'quick' | 'full'
  demo: boolean
  answers: Record<string, string | string[]>
  plan: { seeds: string[]; queries: string[]; urls: string[]; sources: { id: string; label: string; enabled: boolean; excluded?: boolean; requests: number }[] } | null
  imported: EvidenceUnit[]
  evidence: EvidenceUnit[]
  analysis: { candidates: Candidate[]; method: string; k: number; k_reason: string; sample_size: number; source_count: number; thin: boolean; limitations: string[]; tag_counts: Record<string, number> } | null
  personas: Persona[]
  source_status: Record<string, { status: string; count: number; error?: string }>
  manifest: Record<string, unknown>
  diff?: { previous_labels: string[]; current_labels: string[]; evidence_growth: number | null; relative_engagement_shift: number | null }
  job_error?: string
}

const auth = (): Record<string, string> => window.api?.apiToken ? { 'X-MRAIM-Token': window.api.apiToken } : {}

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`${backendUrl}/personas${path}`, {
    method,
    headers: { ...auth(), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  if (!response.ok) {
    let detail = `Persona request failed (${response.status})`
    try { detail = (await response.json()).detail ?? detail } catch { /* keep status */ }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return response.json() as Promise<T>
}

export const personaApi = {
  options: (): Promise<{ options: { runId: string; runName: string; personaId: string; label: string; confidence: string }[] }> => request('/options'),
  questions: (): Promise<{ version: number; questions: PersonaQuestion[] }> => request('/questions'),
  list: (): Promise<{ runs: Pick<PersonaRun, 'id' | 'name' | 'step' | 'status' | 'updated_at'>[] }> => request('/runs'),
  create: (name: string, demo: boolean): Promise<PersonaRun> => request('/runs', 'POST', { name, demo }),
  get: (id: string): Promise<PersonaRun> => request(`/runs/${id}`),
  save: (id: string, patch: Partial<Pick<PersonaRun, 'answers' | 'mode' | 'step' | 'name' | 'plan'>>): Promise<PersonaRun> => request(`/runs/${id}`, 'PATCH', patch),
  plan: (id: string): Promise<PersonaRun> => request(`/runs/${id}/plan`, 'POST'),
  importCsv: (id: string, csv_text: string, mapping: Record<string, string>): Promise<PersonaRun> => request(`/runs/${id}/import`, 'POST', { csv_text, mapping }),
  collect: (id: string, terms_confirmed: boolean, credentials: { youtube_key?: string; mastodon_host?: string; mastodon_token?: string } = {}): Promise<PersonaRun> => request(`/runs/${id}/collect`, 'POST', { approved: true, terms_confirmed, ...credentials }),
  cancel: (id: string): Promise<PersonaRun> => request(`/runs/${id}/cancel`, 'POST'),
  review: (id: string, candidates: Candidate[]): Promise<PersonaRun> => request(`/runs/${id}/review`, 'POST', { candidates }),
  split: (id: string, candidate_id: string, phrase: string): Promise<PersonaRun> => request(`/runs/${id}/split`, 'POST', { candidate_id, phrase }),
  edit: (id: string, persona_id: string, label: string, summary: string): Promise<PersonaRun> => request(`/runs/${id}/personas/${persona_id}`, 'PATCH', { label, summary }),
  delete: (id: string): Promise<{ deleted: boolean }> => request(`/runs/${id}`, 'DELETE')
}
