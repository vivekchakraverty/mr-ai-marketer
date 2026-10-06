import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface AppNotification {
  id: string
  kind: 'permission' | 'error' | 'success' | 'info'
  title: string
  message: string
  createdAt: string
  read: boolean
  action?: 'persona-model-check'
  status?: 'pending' | 'starting' | 'running' | 'complete' | 'declined' | 'failed'
  jobId?: string
}

type NotificationInput = Omit<AppNotification, 'createdAt' | 'read'>
interface NotificationsState {
  items: AppNotification[]
  add: (input: NotificationInput) => void
  update: (id: string, patch: Partial<AppNotification>) => void
  markAllRead: () => void
  dismiss: (id: string) => void
}

export const useNotifications = create<NotificationsState>()(persist((set) => ({
  items: [],
  add: (input) => set((state) => {
    if (state.items.some((item) => item.id === input.id)) return state
    // Unresolved requests survive history trimming.
    const unresolved = state.items.filter((item) => ['pending', 'starting', 'running'].includes(item.status || '') ||
      item.status === 'failed' && item.action === 'persona-model-check')
    const history = state.items.filter((item) => !unresolved.includes(item)).slice(0, 49)
    return { items: [{ ...input, createdAt: new Date().toISOString(), read: false }, ...unresolved, ...history] }
  }),
  update: (id, patch) => set((state) => ({ items: state.items.map((item) => item.id === id ? { ...item, ...patch, id } : item) })),
  markAllRead: () => set((state) => ({ items: state.items.map((item) => ({ ...item, read: true })) })),
  dismiss: (id) => set((state) => ({ items: state.items.filter((item) => item.id !== id) }))
}), { name: 'mr-ai-marketer-notifications', version: 1,
  partialize: (state) => ({ items: state.items.map((item) => item.status === 'starting' ?
    { ...item, status: 'failed' as const, message: 'The app closed before the model test start was confirmed. You can allow another test.' } : item) }) }))

export function notifyPersonaJob(job: { id: string; status: string; error: string }): void {
  const notifications = useNotifications.getState()
  if (job.status === 'error') {
    notifications.add({ id: `persona-error:${job.id}`, kind: 'error', title: 'Buyer persona generation failed', message: job.error })
    notifications.add({ id: `persona-model-check:${job.id}`, kind: 'permission', title: 'Allow a Hugging Face model test?',
      message: 'Run a generation test with two fictional music audiences using your saved Hugging Face token. Only synthetic text is sent to Hugging Face; your song, saved analysis and project details are excluded. Inference credits may be used.',
      action: 'persona-model-check', status: 'pending' })
  } else if (job.status === 'complete') {
    notifications.add({ id: `persona-complete:${job.id}`, kind: 'success', title: 'Buyer personas are ready',
      message: 'Your completed report has been saved. Open Buyer Persona to review it.' })
  }
}
