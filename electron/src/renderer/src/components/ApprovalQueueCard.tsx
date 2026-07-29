import { useState } from 'react'
import { primaryButtonSmall, secondaryButtonSmall, tag } from '../styles/styleKit'
import type { DistributionJob } from '../api/client'

const CHANNEL_LABELS: Record<string, string> = {
  reddit: 'Reddit',
  'discord-conversation': 'Discord reply'
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  const diffMin = Math.floor((Date.now() - d.getTime()) / 60000)
  if (diffMin < 1) return 'Just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  return d.toLocaleDateString()
}

interface Props {
  job: DistributionJob
  onResolved: (jobId: string) => void
  onApprove: (jobId: string) => Promise<unknown>
  onReject: (jobId: string) => Promise<unknown>
}

export default function ApprovalQueueCard({ job, onResolved, onApprove, onReject }: Props): React.JSX.Element {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState('')

  const text = (() => {
    try {
      return job.payload ? (JSON.parse(job.payload).text ?? '') : ''
    } catch {
      return ''
    }
  })()

  async function handle(action: 'approve' | 'reject'): Promise<void> {
    setBusy(action)
    setError('')
    try {
      await (action === 'approve' ? onApprove(job.id) : onReject(job.id))
      onResolved(job.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }

  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '2.5px solid var(--border)',
        borderRadius: 18,
        padding: 20,
        boxShadow: 'var(--shadow-sm)',
        display: 'flex',
        flexDirection: 'column',
        gap: 12
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={tag}>{CHANNEL_LABELS[job.channel] ?? job.channel}</span>
        <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>{formatDate(job.created_at)}</span>
      </div>
      <div style={{ font: "600 14px/1.6 'Quicksand'", color: 'var(--ink-body)', whiteSpace: 'pre-wrap' }}>
        {text || <span style={{ color: 'var(--ink-faint)' }}>(no preview available)</span>}
      </div>
      {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}
      <div style={{ display: 'flex', gap: 10 }}>
        <div
          style={{ ...primaryButtonSmall, opacity: busy ? 0.6 : 1, pointerEvents: busy ? 'none' : 'auto' }}
          onClick={() => handle('approve')}
        >
          {busy === 'approve' ? 'Approving…' : 'Approve'}
        </div>
        <div
          style={{ ...secondaryButtonSmall, opacity: busy ? 0.6 : 1, pointerEvents: busy ? 'none' : 'auto' }}
          onClick={() => handle('reject')}
        >
          {busy === 'reject' ? 'Rejecting…' : 'Reject'}
        </div>
      </div>
    </div>
  )
}
