import { backendUrl } from './client'

export interface SavedPersonaOption {
  runId: string
  runName: string
  personaId: string
  label: string
  confidence: string
}

/** Existing saved personas remain usable as Marketing Plan targets. */
export async function listSavedPersonaOptions(): Promise<{ options: SavedPersonaOption[] }> {
  const response = await fetch(`${backendUrl}/personas/options`, {
    headers: window.api?.apiToken ? { 'X-MRAIM-Token': window.api.apiToken } : {}
  })
  if (!response.ok) throw new Error(`Could not load saved personas (${response.status})`)
  return response.json()
}
