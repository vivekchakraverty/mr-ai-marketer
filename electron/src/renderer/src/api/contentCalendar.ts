import { backendUrl } from './client'

export interface PersonaReportChoice {
  id: string; created_at: string; label: string; source_key: string; persona_count: number
}
export interface CalendarChannel {
  name: string; posts_per_week: number; formats: string[]; reason: string
}
export interface CalendarTopic {
  week: number; theme: string; angle: string; persona: string; channel: string; format: string; goal: string
}
export interface CalendarResult {
  persona_report_id: string; source_key: string; generated_at: string; model: string
  summary: string; channels: CalendarChannel[]; topics: CalendarTopic[]; measurement: string
  generation_note?: string
  research: { name: string; date: string; scope: string; findings: string[] }[]
}
export interface CalendarJob {
  id: string; persona_report_id: string; status: 'queued' | 'generating' | 'complete' | 'error'
  created_at: string; updated_at: string; result: CalendarResult | null; error: string
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${backendUrl}/content-calendar${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { ...(window.api?.apiToken ? { 'X-MRAIM-Token': window.api.apiToken } : {}),
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string }
    throw new Error(payload.detail || `Content calendar request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const contentCalendarApi = {
  personas: () => request<{ reports: PersonaReportChoice[] }>('/personas'),
  latest: (id: string) => request<{ job: CalendarJob | null; previous_complete: CalendarJob | null }>(`/latest?persona_report_id=${encodeURIComponent(id)}`),
  get: (id: string) => request<CalendarJob>(`/jobs/${encodeURIComponent(id)}`),
  generate: (persona_report_id: string) => request<CalendarJob>('/generate', { persona_report_id })
}
