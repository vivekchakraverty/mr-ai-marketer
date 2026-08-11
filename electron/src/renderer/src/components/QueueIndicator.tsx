import { useEffect, useRef, useState } from 'react'
import { getQueueStatus, type QueueStatus } from '../api/client'

/**
 * Shows what generation work is in flight, and only when that is worth saying.
 *
 * The rule is deliberately narrow: nothing appears while a single request runs on its own,
 * because that is just the app working and the screen that asked for it already has its own
 * spinner. It appears when something is *queued behind* something else — the case where a
 * tool looks frozen and the honest explanation is "it is waiting its turn".
 *
 * Polling follows the same logic. It idles at a slow tick so a burst is noticed, speeds up
 * while work is in flight, and never runs faster than the information changes. /queue is a
 * counter read with no I/O behind it, so this costs nothing measurable.
 */

const IDLE_MS = 6000
const ACTIVE_MS = 1500

export default function QueueIndicator(): React.JSX.Element | null {
  const [status, setStatus] = useState<QueueStatus | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let cancelled = false

    async function tick(): Promise<void> {
      let next = IDLE_MS
      try {
        const s = await getQueueStatus()
        if (cancelled) return
        setStatus(s)
        next = s.busy || s.queued ? ACTIVE_MS : IDLE_MS
      } catch {
        // The backend may still be starting, or already gone. A queue indicator is the last
        // thing that should surface an error, so it just tries again later.
        if (cancelled) return
        setStatus(null)
      }
      timer.current = setTimeout(() => void tick(), next)
    }

    void tick()
    return () => {
      cancelled = true
      if (timer.current) clearTimeout(timer.current)
    }
  }, [])

  // Only when something is actually held up behind something else.
  if (!status || status.waiting < 1) return null

  const total = status.running + status.waiting
  const nearlyFull = Object.values(status.lanes).some((l) => l.waiting >= l.maxWaiting - 1)

  return (
    <div
      title={
        Object.entries(status.lanes)
          .filter(([, l]) => l.running || l.waiting)
          .map(([name, l]) => `${name}: ${l.running} running, ${l.waiting} waiting (max ${l.maxWaiting})`)
          .join('\n') || undefined
      }
      style={{
        position: 'fixed',
        bottom: 16,
        left: 16,
        zIndex: 58,
        display: 'flex',
        alignItems: 'center',
        gap: 9,
        background: 'var(--surface)',
        border: `2px solid ${nearlyFull ? 'var(--danger-ink)' : 'var(--border)'}`,
        borderRadius: 999,
        padding: '8px 14px 8px 11px',
        boxShadow: 'var(--shadow-sm)',
        font: "700 12px 'Quicksand'",
        color: 'var(--ink-muted)',
        maxWidth: 320
      }}
    >
      <span
        aria-hidden
        style={{
          width: 9,
          height: 9,
          borderRadius: '50%',
          background: nearlyFull ? 'var(--danger-ink)' : 'var(--accent-deep)',
          flexShrink: 0,
          animation: 'mraimQueuePulse 1.1s ease-in-out infinite'
        }}
      />
      <span>
        {nearlyFull
          ? `Busy — ${total} requests queued. Give it a minute before starting more.`
          : `${status.waiting} waiting${status.running ? ` · ${status.running} running` : ''}`}
      </span>
      <style>{'@keyframes mraimQueuePulse{0%,100%{opacity:.35}50%{opacity:1}}'}</style>
    </div>
  )
}
