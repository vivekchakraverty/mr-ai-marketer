import { useEffect, useState } from 'react'
import {
  approveDistributionItem,
  fetchDistributionChannels,
  fetchDistributionJobs,
  fetchDistributionQueue,
  rejectDistributionItem,
  type ChannelStatus,
  type DistributionJob
} from '../api/client'
import ApprovalQueueCard from '../components/ApprovalQueueCard'
import ChannelConnectModal from '../components/ChannelConnectModal'
import MailComposer from '../components/MailComposer'
import { BROADCAST_CHANNELS, COMMUNITY_CHANNELS, PLATFORM_SETUP_GUIDES } from '../state/platformSetupGuides'
import { useAppStore } from '../state/store'
import { secondaryButtonSmall, tag } from '../styles/styleKit'

const POLL_INTERVAL_MS = 15000

/** Every channel this app can post to, shown even before (or without) an answer from the
 * distribution engine — the catalogue is static, only the connected flags are live. */
function catalogue(names: string[]): ChannelStatus[] {
  return names.map((channel) => ({ channel, connected: false }))
}

const STATUS_LABEL: Record<string, string> = {
  sending: 'Sending…',
  sent: 'Sent',
  scheduled: 'Scheduled',
  pending_approval: 'Waiting on approval',
  failed: 'Failed',
  approved: 'Approved',
  rejected: 'Rejected'
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

function ChannelCard({
  status,
  engineReady,
  onClick
}: {
  status: ChannelStatus
  /** null while the first channels fetch is still in flight. */
  engineReady: boolean | null
  onClick: () => void
}): React.JSX.Element {
  const guide = PLATFORM_SETUP_GUIDES[status.channel]
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        background: 'var(--surface)',
        border: '2.5px solid var(--border)',
        borderRadius: 16,
        padding: '13px 16px',
        cursor: 'pointer',
        boxShadow: 'var(--shadow-sm)'
      }}
    >
      <span
        style={{
          width: 30,
          height: 30,
          borderRadius: '52% 48% 55% 45%',
          background: guide.color,
          border: '2px solid var(--border)',
          flexShrink: 0
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ font: "700 14px 'Kalam'", color: 'var(--ink)' }}>{guide.label}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: status.connected ? 'var(--tool-distribute)' : 'var(--ink-fainter)'
            }}
          />
          <span style={{ font: "700 11.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
            {engineReady === null ? 'Checking…' : !engineReady ? 'Waiting on the engine' : status.connected ? 'Connected' : 'Not connected'}
          </span>
        </div>
      </div>
    </div>
  )
}

export default function Distribute(): React.JSX.Element {
  const [queue, setQueue] = useState<DistributionJob[]>([])
  const [jobs, setJobs] = useState<DistributionJob[]>([])
  const [channels, setChannels] = useState<ChannelStatus[]>([])
  const [communityChannels, setCommunityChannels] = useState<ChannelStatus[]>([])
  const [engineReady, setEngineReady] = useState<boolean | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [activeChannel, setActiveChannel] = useState<string | null>(null)
  const openDistributionGate = useAppStore((s) => s.openDistributionGate)
  const gateReportedReady = useAppStore((s) => s.distributionEngineReady)

  async function refreshChannels(): Promise<void> {
    try {
      const result = await fetchDistributionChannels()
      setEngineReady(result.ready)
      setChannels(result.channels)
      setCommunityChannels(result.communityChannels)
    } catch {
      setEngineReady(false)
    }
  }

  useEffect(() => {
    let cancelled = false

    async function refresh(): Promise<void> {
      try {
        const [queueResult, jobsResult] = await Promise.all([fetchDistributionQueue(), fetchDistributionJobs()])
        if (!cancelled) {
          setQueue(queueResult.jobs)
          setJobs(jobsResult.jobs)
        }
      } catch {
        // Distribution engine likely isn't set up yet — the inbox and history just stay empty.
      } finally {
        if (!cancelled) setLoaded(true)
      }
      // Channels are polled too (not just fetched once) so an OAuth connection made in
      // Activepieces' own browser tab shows up here without leaving and re-entering the page.
      if (!cancelled) void refreshChannels()
    }

    refresh()
    const interval = setInterval(refresh, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  // The setup gate finishing is the one moment worth reacting to immediately rather than
  // leaving the user staring at a stale banner until the next poll comes round.
  useEffect(() => {
    if (gateReportedReady) void refreshChannels()
  }, [gateReportedReady])

  function handleResolved(jobId: string): void {
    setQueue((current) => current.filter((job) => job.id !== jobId))
  }

  const connectedByChannel = new Set([...channels, ...communityChannels].filter((c) => c.connected).map((c) => c.channel))
  const recentJobs = jobs.filter((j) => j.status !== 'pending_approval').slice(0, 15)
  // The backend already returns the full catalogue even when the engine is down; these
  // fall back to the static lists for the case where the backend itself hasn't answered.
  const broadcastChannels = channels.length > 0 ? channels : catalogue(BROADCAST_CHANNELS)
  const allCommunityChannels = communityChannels.length > 0 ? communityChannels : catalogue(COMMUNITY_CHANNELS)

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '30px 34px 60px' }}>
      <div style={{ marginBottom: 22 }}>
        <div
          style={{
            font: "700 13.5px 'Quicksand'",
            letterSpacing: '.05em',
            textTransform: 'uppercase',
            color: 'var(--ink-faint)',
            textDecoration: 'underline wavy',
            textDecorationColor: 'var(--ink-faint)',
            textUnderlineOffset: 4
          }}
        >
          Distribute
        </div>
        <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)', marginTop: 6 }}>Yeet it to every channel</div>
      </div>

      <MailComposer />

      <div style={{ marginBottom: 14, display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>Approval Inbox</div>
        <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>
          Reddit posts and Discord replies wait here for a human decision before anything goes out.
        </div>
      </div>

      {loaded && queue.length === 0 && (
        <div
          style={{
            border: '2px dashed var(--border)',
            borderRadius: 20,
            padding: 40,
            textAlign: 'center',
            font: "700 15px 'Kalam'",
            color: 'var(--ink-fainter-2)',
            marginBottom: 30
          }}
        >
          Nothing waiting on approval right now.
        </div>
      )}

      {queue.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 30 }}>
          {queue.map((job) => (
            <ApprovalQueueCard
              key={job.id}
              job={job}
              onResolved={handleResolved}
              onApprove={approveDistributionItem}
              onReject={rejectDistributionItem}
            />
          ))}
        </div>
      )}

      <div style={{ marginBottom: 14 }}>
        <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>Channels</div>
        <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 2 }}>
          Tap a channel to connect it or manage its credentials.
        </div>
      </div>

      {engineReady === false && (
        <div
          style={{
            border: '2px dashed var(--border)',
            borderRadius: 20,
            padding: '14px 18px',
            font: "600 13px/1.6 'Quicksand'",
            color: 'var(--ink-fainter-2)',
            marginBottom: 14,
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            flexWrap: 'wrap'
          }}
        >
          <span style={{ flex: 1, minWidth: 260 }}>
            Every channel this app can post to is listed below, but the distribution engine isn't running yet — connecting
            and sending need it up.
          </span>
          <div style={secondaryButtonSmall} onClick={openDistributionGate}>
            Start the engine
          </div>
        </div>
      )}

      <div style={{ font: "700 12px 'Quicksand'", letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--ink-faint)', marginBottom: 8 }}>
        Broadcast
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))', gap: 10, marginBottom: 22 }}>
        {broadcastChannels.map((c) => (
          <ChannelCard key={c.channel} status={c} engineReady={engineReady} onClick={() => setActiveChannel(c.channel)} />
        ))}
      </div>

      <div style={{ font: "700 12px 'Quicksand'", letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--ink-faint)', marginBottom: 8 }}>
        Community (human-approved)
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))', gap: 10, marginBottom: 30 }}>
        {allCommunityChannels.map((c) => (
          <ChannelCard
            key={c.channel}
            status={c}
            engineReady={engineReady}
            onClick={() => setActiveChannel(PLATFORM_SETUP_GUIDES[c.channel].sharesConnectionWith ?? c.channel)}
          />
        ))}
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>Send history</div>
      </div>

      {loaded && recentJobs.length === 0 && (
        <div
          style={{
            border: '2px dashed var(--border)',
            borderRadius: 20,
            padding: 40,
            textAlign: 'center',
            font: "700 15px 'Kalam'",
            color: 'var(--ink-fainter-2)'
          }}
        >
          Nothing sent yet — push something out from your Library.
        </div>
      )}

      {recentJobs.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {recentJobs.map((job) => (
            <div
              key={job.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '11px 16px',
                background: 'var(--surface)',
                border: '2px solid var(--border)',
                borderRadius: 14
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={tag}>{PLATFORM_SETUP_GUIDES[job.channel]?.label ?? job.channel}</span>
                <span style={{ font: "700 12.5px 'Quicksand'", color: job.status === 'failed' ? '#a34a3a' : 'var(--ink-muted)' }}>
                  {STATUS_LABEL[job.status] ?? job.status}
                </span>
              </div>
              <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>{formatDate(job.updated_at)}</span>
            </div>
          ))}
        </div>
      )}

      {activeChannel && (
        <ChannelConnectModal
          channel={activeChannel}
          connected={connectedByChannel.has(activeChannel)}
          engineReady={engineReady !== false}
          onClose={() => setActiveChannel(null)}
          onChanged={refreshChannels}
        />
      )}
    </div>
  )
}
