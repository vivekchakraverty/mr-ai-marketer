import { create } from 'zustand'

/**
 * One place every error in the app surfaces.
 *
 * Errors already get handled locally — most screens catch what they call and render a red
 * card. That is good for context and bad for two things: an error thrown somewhere nobody is
 * catching (a render crash, a rejected promise in an effect) used to vanish into the console,
 * and none of the inline cards can be copied out to report a bug.
 *
 * This store is deliberately outside React so it can be raised from anywhere — the API
 * client, a `window.onerror` handler, an error boundary — without any of them needing a hook
 * or a component tree.
 */
export interface AppError {
  id: number
  /** The one-line summary. Usually the backend's own `detail`, which is written for people. */
  message: string
  /** Where it came from: an API path, "render", "unhandled rejection". */
  source: string
  /** Stack or response body, when there is one. Shown collapsed. */
  detail: string
  at: Date
}

interface ErrorState {
  current: AppError | null
  /** Raised while one is already showing; surfaced as a count rather than a modal queue. */
  suppressed: number
  report: (input: { message: string; source?: string; detail?: string }) => void
  dismiss: () => void
}

let nextId = 1

/** Errors worth showing nobody: a cancelled fetch is a navigation, not a fault. */
function isNoise(message: string): boolean {
  const m = message.toLowerCase()
  return (
    m.includes('abort') ||
    m.includes('resizeobserver loop') ||
    // A backend that is still starting is expected during the first seconds of a launch;
    // App.tsx polls until it answers and shows its own splash meanwhile.
    m.includes('failed to fetch')
  )
}

export const useErrors = create<ErrorState>((set, get) => ({
  current: null,
  suppressed: 0,
  report: ({ message, source = 'app', detail = '' }) => {
    const text = (message || '').trim() || 'Something went wrong.'
    if (isNoise(text)) return

    const current = get().current
    // Don't stack modals, and don't let a loop of the same failure count up forever: an
    // identical message while one is open is the same event as far as the user is concerned.
    if (current) {
      if (current.message !== text) set({ suppressed: get().suppressed + 1 })
      return
    }
    set({
      current: { id: nextId++, message: text, source, detail: (detail || '').trim(), at: new Date() },
      suppressed: 0
    })
  },
  dismiss: () => set({ current: null, suppressed: 0 })
}))

/** Raise an error from outside React — the API client, global handlers, the error boundary. */
export function reportError(input: { message: string; source?: string; detail?: string }): void {
  useErrors.getState().report(input)
}
