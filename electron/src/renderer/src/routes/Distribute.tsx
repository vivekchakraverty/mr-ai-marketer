import { useEffect, useRef, useState } from 'react'
import {
  approveDistributionItem,
  cancelScheduledDistributionJob,
  deleteCustomChannel,
  fetchCataloguePiece,
  fetchDistributionChannels,
  fetchDistributionJobs,
  fetchDistributionQueue,
  rejectDistributionItem,
  type ChannelStatus,
  type CustomChannelStatus,
  type DistributionJob
} from '../api/client'
import AddChannelModal from '../components/AddChannelModal'
import ApprovalQueueCard from '../components/ApprovalQueueCard'
import BackendImage from '../components/BackendImage'
import ChannelConnectModal from '../components/ChannelConnectModal'
import MailComposer from '../components/MailComposer'
import {
  BROADCAST_CHANNELS,
  COMMUNITY_CHANNELS,
  PLATFORM_SETUP_GUIDES,
  type PlatformSetupGuide
} from '../state/platformSetupGuides'
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
  cancelled: 'Cancelled',
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

function formatScheduledDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function ChannelCard({
  status,
  engineReady,
  onClick,
  label: labelOverride,
  color: colorOverride
}: {
  status: ChannelStatus
  /** null while the first channels fetch is still in flight. */
  engineReady: boolean | null
  onClick: () => void
  /** Supplied for user-added channels, which have no hand-written guide to read from. */
  label?: string
  color?: string
}): React.JSX.Element {
  const guide = PLATFORM_SETUP_GUIDES[status.channel]
  const cardLabel = labelOverride ?? guide.label
  const cardColor = colorOverride ?? guide.color
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
          background: cardColor,
          border: '2px solid var(--border)',
          flexShrink: 0
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ font: "700 14px 'Kalam'", color: 'var(--ink)' }}>{cardLabel}</div>
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
  // Which history row is open. One at a time: these are read to answer a specific
  // question ("what went out?", "why did that fail?"), not browsed side by side.
  const [expandedJob, setExpandedJob] = useState<string | null>(null)
  const [cancellingJob, setCancellingJob] = useState<string | null>(null)
  const [cancelError, setCancelError] = useState('')
  const historyRefreshSequence = useRef(0)
  const [customChannels, setCustomChannels] = useState<CustomChannelStatus[]>([])
  const [addOpen, setAddOpen] = useState(false)
  // The connect form for a user-added channel is generated from its piece's own auth
  // schema, fetched when its card is opened rather than kept for every channel up front.
  const [customGuide, setCustomGuide] = useState<PlatformSetupGuide | null>(null)
  const openDistributionGate = useAppStore((s) => s.openDistributionGate)
  const gateReportedReady = useAppStore((s) => s.distributionEngineReady)

  async function refreshChannels(): Promise<void> {
    try {
      const result = await fetchDistributionChannels()
      setEngineReady(result.ready)
      setChannels(result.channels)
      setCommunityChannels(result.communityChannels)
      setCustomChannels(result.customChannels ?? [])
    } catch {
      setEngineReady(false)
    }
  }

  /** Builds a setup guide on the fly from what the piece says it needs to authenticate,
   * so a user-added channel gets the same connect form as a hand-written one. */
  async function openCustomChannel(status: CustomChannelStatus): Promise<void> {
    setActiveChannel(status.channel)
    setCustomGuide(null)
    try {
      const piece = await fetchCataloguePiece(status.pieceName)
      const authKind = (piece.auth.type ?? 'OAUTH2') as PlatformSetupGuide['authKind']
      setCustomGuide({
        channel: status.channel,
        label: status.label,
        authKind,
        color: 'var(--tool-distribute)',
        blurb: piece.auth.description?.split('\n')[0] || `Post to ${status.label} from this app.`,
        helpSteps: [],
        // SECRET_TEXT pieces declare no props — Activepieces takes the one value under the
        // fixed `secret_text` key, which is what the bundled Discord guide uses too.
        fields:
          authKind === 'SECRET_TEXT'
            ? [{ key: 'secret_text', label: piece.auth.label || 'API key or token', secret: true }]
            : piece.auth.props.map((p) => ({
                key: p.key,
                label: p.label,
                secret: p.type === 'SECRET_TEXT',
                optional: !p.required,
                kind: p.type === 'CHECKBOX' ? ('checkbox' as const) : p.options.length > 0 ? ('select' as const) : ('text' as const),
                options: p.options,
                defaultValue: typeof p.defaultValue === 'string' ? p.defaultValue : undefined
              }))
      })
    } catch {
      setActiveChannel(null)
    }
  }

  async function handleRemoveCustom(channel: string): Promise<void> {
    try {
      await deleteCustomChannel(channel)
    } finally {
      setActiveChannel(null)
      setCustomGuide(null)
      void refreshChannels()
    }
  }

  useEffect(() => {
    let cancelled = false

    async function refresh(): Promise<void> {
      const requestSequence = ++historyRefreshSequence.current
      try {
        const [queueResult, jobsResult] = await Promise.all([fetchDistributionQueue(), fetchDistributionJobs()])
        if (!cancelled) {
          setQueue(queueResult.jobs)
          // A successful cancellation increments the sequence so an older GET that was
          // already in flight cannot briefly put its stale Scheduled row back on screen.
          if (requestSequence === historyRefreshSequence.current) setJobs(jobsResult.jobs)
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

  async function handleCancelScheduled(job: DistributionJob): Promise<void> {
    if (cancellingJob) return
    const label =
      PLATFORM_SETUP_GUIDES[job.channel]?.label ??
      customChannels.find((channel) => channel.channel === job.channel)?.label ??
      job.channel
    const timing = job.scheduled_at
      ? ` scheduled for ${formatScheduledDate(job.scheduled_at)}`
      : ''
    if (!window.confirm(`Cancel the ${label} post${timing}? It will not be published.`)) return

    setCancellingJob(job.id)
    setCancelError('')
    try {
      const cancelled = await cancelScheduledDistributionJob(job.id)
      historyRefreshSequence.current += 1
      setJobs((current) => current.map((item) => (item.id === cancelled.id ? cancelled : item)))
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : String(err))
    } finally {
      setCancellingJob(null)
    }
  }

  const connectedByChannel = new Set(
    [...channels, ...communityChannels, ...customChannels].filter((c) => c.connected).map((c) => c.channel)
  )
  const activeCustom = customChannels.find((c) => c.channel === activeChannel) ?? null
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

      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
        <div style={{ font: "700 12px 'Quicksand'", letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
          Your channels
        </div>
        <div style={secondaryButtonSmall} onClick={() => setAddOpen(true)}>
          + Add a channel
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))', gap: 10, marginBottom: 30 }}>
        {customChannels.map((c) => (
          <ChannelCard
            key={c.channel}
            status={c}
            engineReady={engineReady}
            label={c.label}
            color="var(--tool-distribute)"
            onClick={() => void openCustomChannel(c)}
          />
        ))}
        {customChannels.length === 0 && (
          <div
            onClick={() => setAddOpen(true)}
            style={{
              border: '2px dashed var(--border)',
              borderRadius: 16,
              padding: '13px 16px',
              font: "600 12.5px/1.5 'Quicksand'",
              color: 'var(--ink-fainter-2)',
              cursor: 'pointer',
              gridColumn: 'span 2'
            }}
          >
            Anything else the engine can post to — Telegram, Slack, Pinterest and hundreds more — can be added here.
          </div>
        )}
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>Send history</div>
        {cancelError && (
          <div role="alert" style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a', marginTop: 5 }}>
            {cancelError}
          </div>
        )}
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
          {recentJobs.map((job) => {
            const open = expandedJob === job.id
            const channelLabel =
              PLATFORM_SETUP_GUIDES[job.channel]?.label ??
              customChannels.find((c) => c.channel === job.channel)?.label ??
              job.channel
            const cancelling = cancellingJob === job.id
            const rowTimestamp =
              job.status === 'scheduled' && job.scheduled_at
                ? `Scheduled ${formatScheduledDate(job.scheduled_at)}`
                : formatDate(job.updated_at)
            return (
              <div
                key={job.id}
                style={{
                  background: 'var(--surface)',
                  border: '2px solid var(--border)',
                  borderRadius: 14,
                  overflow: 'hidden'
                }}
              >
                <div
                  onClick={() => setExpandedJob(open ? null : job.id)}
                  title={open ? 'Hide details' : 'Show what was sent'}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '11px 16px',
                    cursor: 'pointer'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span
                      style={{
                        font: "700 12px 'Quicksand'",
                        color: 'var(--ink-faint)',
                        display: 'inline-block',
                        width: 10,
                        transform: open ? 'rotate(90deg)' : 'none',
                        transition: 'transform .12s'
                      }}
                    >
                      ›
                    </span>
                    <span style={tag}>{channelLabel}</span>
                    <span style={{ font: "700 12.5px 'Quicksand'", color: job.status === 'failed' ? '#a34a3a' : 'var(--ink-muted)' }}>
                      {STATUS_LABEL[job.status] ?? job.status}
                    </span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', whiteSpace: 'nowrap' }}>
                      {rowTimestamp}
                    </span>
                    {job.status === 'scheduled' && (
                      <button
                        type="button"
                        className="cancel-scheduled-post-button"
                        aria-label={cancelling ? `Cancelling scheduled ${channelLabel} post` : `Cancel scheduled ${channelLabel} post`}
                        aria-busy={cancelling}
                        title={cancelling ? 'Cancelling scheduled post…' : 'Cancel scheduled post'}
                        disabled={Boolean(cancellingJob)}
                        onClick={(event) => {
                          event.stopPropagation()
                          void handleCancelScheduled(job)
                        }}
                        style={{
                          width: 24,
                          height: 24,
                          display: 'grid',
                          placeItems: 'center',
                          padding: 0,
                          border: 0,
                          borderRadius: 8,
                          background: 'transparent',
                          color: 'var(--danger-ink)',
                          font: "700 18px/1 'Quicksand'",
                          cursor: cancellingJob ? 'wait' : 'pointer',
                          opacity: cancellingJob && !cancelling ? 0.4 : 1
                        }}
                      >
                        {cancelling ? '…' : '×'}
                      </button>
                    )}
                  </div>
                </div>
                {open && <JobDetails job={job} />}
              </div>
            )
          })}
        </div>
      )}

      {/* A user-added channel waits for its piece's auth schema before the form can render;
          a built-in one already has its guide compiled in. */}
      {activeChannel && (!activeCustom || customGuide) && (
        <ChannelConnectModal
          channel={activeChannel}
          connected={connectedByChannel.has(activeChannel)}
          engineReady={engineReady !== false}
          guide={customGuide ?? undefined}
          onRemove={activeCustom ? () => void handleRemoveCustom(activeCustom.channel) : undefined}
          onClose={() => {
            setActiveChannel(null)
            setCustomGuide(null)
          }}
          onChanged={refreshChannels}
        />
      )}

      {addOpen && <AddChannelModal onClose={() => setAddOpen(false)} onAdded={refreshChannels} />}
    </div>
  )
}


/**
 * What a send actually was, revealed when its row is clicked.
 *
 * The history used to show only channel, status and a date — enough to see that
 * something failed and nothing to act on it. Everything worth knowing is already stored
 * with the job: the exact text that went out, the image if there was one, when it was
 * meant to go, and the engine's own error.
 *
 * `payload` is the JSON the engine was handed. It is parsed defensively because it is
 * whatever was current when the job was queued — an older row may not have the fields a
 * newer one does, and a history view is the last place that should throw.
 */
function JobDetails({ job }: { job: DistributionJob }): React.JSX.Element {
  let payload: Record<string, unknown> = {}
  try {
    payload = job.payload ? (JSON.parse(job.payload) as Record<string, unknown>) : {}
  } catch {
    payload = {}
  }

  const text = typeof payload.text === 'string' ? payload.text : ''
  const imageUrls = Array.isArray(payload.imageUrls)
    ? payload.imageUrls.filter((value): value is string => typeof value === 'string' && Boolean(value))
    : []
  // Bluesky receives its prepared, size-safe derivative; every other channel receives
  // the original scalar media URL. Show the channel's real attachment in history.
  const imageUrl = job.channel === 'bluesky' && imageUrls[0]
    ? imageUrls[0]
    : typeof payload.imageUrl === 'string'
      ? payload.imageUrl
      : ''
  const videoUrl = typeof payload.videoUrl === 'string' ? payload.videoUrl : ''
  const videoAlt = typeof payload.videoFileAlt === 'string' ? payload.videoFileAlt : ''
  // Everything except the post body, which gets its own block above.
  const extras = Object.entries(payload).filter(
    ([k, v]) =>
      !['text', 'imageUrl', 'imageUrls', 'videoUrl', 'videoFileAlt', 'mediaUrl'].includes(k) &&
      typeof v === 'string' &&
      v
  )

  return (
    <div style={{ borderTop: '2px dashed var(--border-soft)', padding: '14px 16px', background: 'var(--surface-paper)' }}>
      {job.error && (
        <div
          style={{
            font: "600 12.5px/1.6 'Quicksand'",
            color: 'var(--danger-ink)',
            background: 'var(--tip-bg)',
            border: '2px dashed var(--border-soft)',
            borderRadius: 12,
            padding: '9px 12px',
            marginBottom: 12
          }}
        >
          {job.error}
        </div>
      )}

      {text ? (
        <div style={{ marginBottom: 12 }}>
          <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--ink-faint)', marginBottom: 5 }}>
            What was sent
          </div>
          <div style={{ font: "600 13.5px/1.7 'Quicksand'", color: 'var(--ink)', whiteSpace: 'pre-wrap' }}>{text}</div>
        </div>
      ) : (
        <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)', marginBottom: 12 }}>
          No post text was recorded for this send.
        </div>
      )}

      {imageUrl && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--ink-faint)', marginBottom: 5 }}>
            Image
          </div>
          {/* A locally generated image is served by this app; anything else is somebody
              else's URL and is shown as a link rather than fetched. */}
          {imageUrl.startsWith('/outputs/') ? (
            <BackendImage
              url={imageUrl}
              alt="Attached image"
              style={{ maxWidth: 220, borderRadius: 10, border: '2px solid var(--border)', display: 'block' }}
            />
          ) : (
            <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', wordBreak: 'break-all' }}>{imageUrl}</div>
          )}
        </div>
      )}

      {videoUrl && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--ink-faint)', marginBottom: 5 }}>
            Video
          </div>
          <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', wordBreak: 'break-all' }}>{videoUrl}</div>
          {videoAlt && (
            <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 4 }}>
              Alt text: {videoAlt}
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 18px', font: "600 12px 'Quicksand'", color: 'var(--ink-muted)' }}>
        <span>Queued {formatDate(job.created_at)}</span>
        {job.scheduled_at && <span>Scheduled for {formatScheduledDate(job.scheduled_at)}</span>}
        {job.updated_at !== job.created_at && <span>Last change {formatDate(job.updated_at)}</span>}
        {extras.map(([k, v]) => (
          <span key={k}>
            {k}: {String(v)}
          </span>
        ))}
      </div>
    </div>
  )
}
