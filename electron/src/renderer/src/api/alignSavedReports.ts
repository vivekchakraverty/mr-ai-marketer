import { backendUrl } from './client'

export type AlignReportKind = 'music' | 'visual_art'
export interface SavedAlignReport<T = Record<string, unknown>> {
  id: string; kind: AlignReportKind; title: string; document: T; created_at: string; updated_at: string
}
export type SavedAlignSummary = Omit<SavedAlignReport, 'document'>

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${backendUrl}/align/saved-reports${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { ...(window.api?.apiToken ? { 'X-MRAIM-Token': window.api.apiToken } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string }
    throw new Error(payload.detail || `Saving Align report failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const alignSavedReportsApi = {
  list: (kind: AlignReportKind) => request<{ reports: SavedAlignSummary[] }>(`?kind=${kind}`),
  get: <T>(id: string) => request<SavedAlignReport<T>>(`/${encodeURIComponent(id)}`),
  save: <T>(kind: AlignReportKind, title: string, document: T, id = '') =>
    request<SavedAlignReport<T>>('', { kind, title, document, id })
}
