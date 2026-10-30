import { useEffect, useState } from 'react'
import { cloudSpaceStatus, restartCloudPoster, type CloudSpaceStatus } from '../api/client'
import { useAppStore } from '../state/store'
import { label, primaryButtonSmall, secondaryButtonSmall } from '../styles/styleKit'
import type { CloudPostingSettings } from '../../../shared/settings'

/**
 * What the user's poster Space is doing, once they have one.
 *
 * Read-only apart from two escape hatches — reconnect, and open the Space itself. Editing a
 * credential here would mean a second implementation of the connect flow the walkthrough
 * already owns, so this sends people back to that instead, the way AccountMenu points at the
 * screen that owns each connection rather than offering to change it in place.
 *
 * A Space that does not answer is reported as asleep rather than broken, because on the free
 * tier that is almost always what it is, and the queue is safe either way. With one
 * exception, which this panel used to get wrong: a PAUSED Space is silent in the same way and
 * is NOT safe to wait on — it never wakes, and scheduled posts stop until someone restarts
 * it. The backend tells the two apart (cloud_poster._stopped) and this shows what it found.
 */
export default function CloudPostingPanel(): React.JSX.Element {
  const openSetup = useAppStore((s) => s.openSetup)
  const [cloud, setCloud] = useState<CloudPostingSettings | null>(null)
  const [status, setStatus] = useState<CloudSpaceStatus | null>(null)
  const [checking, setChecking] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [restartNote, setRestartNote] = useState('')

  useEffect(() => {
    void (async () => {
      const saved = await window.api.settings.getAll()
      setCloud(saved.cloudPosting)
      if (saved.cloudPosting.spaceId) void check()
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function check(): Promise<void> {
    setChecking(true)
    try {
      setStatus(await cloudSpaceStatus())
    } catch (err) {
      setStatus({ reachable: false, detail: err instanceof Error ? err.message : String(err) })
    } finally {
      setChecking(false)
    }
  }

  /**
   * The token is read here rather than held in state because this is the only screen that
   * needs it and it is the broad, account-wide one — the fine-grained `spaceToken` the Space
   * itself holds is scoped to the outbox dataset and cannot restart anything.
   */
  async function restart(): Promise<void> {
    setRestarting(true)
    setRestartNote('')
    try {
      const saved = await window.api.settings.getAll()
      if (!saved.hfToken.trim()) {
        setRestartNote('Add your Hugging Face token in Settings first — restarting needs it.')
        return
      }
      const result = await restartCloudPoster({ hfToken: saved.hfToken, spaceId: cloud?.spaceId })
      setRestartNote(result.detail)
      // Not checked immediately: a Space that has just been told to restart reports the stage
      // it was already in for a few seconds, which would read as "the restart did nothing".
      setTimeout(() => void check(), 20000)
    } catch (err) {
      setRestartNote(err instanceof Error ? err.message : String(err))
    } finally {
      setRestarting(false)
    }
  }

  if (!cloud) return <div style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-faint)' }}>Loading…</div>

  if (!cloud.spaceId) {
    return (
      <div>
        <p style={{ font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-body)', margin: '0 0 12px' }}>
          Not set up. Scheduled Mastodon and Bluesky posts currently need this app running at the moment they go out.
        </p>
        <div style={primaryButtonSmall} onClick={() => openSetup('cloud')}>
          Set up cloud posting
        </div>
      </div>
    )
  }

  const dot = (on: boolean): React.CSSProperties => ({
    width: 8,
    height: 8,
    borderRadius: 5,
    flexShrink: 0,
    background: on ? 'var(--tool-distribute)' : 'var(--ink-fainter)',
    border: '1.5px solid var(--border)'
  })

  // A stage is only ever reported for a Space that is STOPPED, so its presence is the signal.
  // Without this the row said "Asleep or waking" about a Space that was never going to wake.
  const stopped = Boolean(status && !status.reachable && status.stage)

  const rows: [string, boolean, string][] = [
    [
      'Space',
      Boolean(status?.reachable),
      status?.reachable
        ? 'Awake'
        : status?.stage === 'PAUSED'
          ? 'Paused — not posting'
          : status?.stage === 'RUNTIME_ERROR'
            ? 'Crashed — not posting'
            : 'Asleep or waking'
    ],
    [
      'Mastodon',
      Boolean(status?.mastodonConfigured),
      status?.mastodonConfigured ? cloud.mastodonHost || 'Connected' : 'Not connected'
    ],
    [
      'Bluesky',
      Boolean(status?.blueskyConfigured) && !status?.needsBlueskyReauth,
      status?.needsBlueskyReauth
        ? 'Needs reconnecting'
        : status?.blueskyConfigured
          ? 'Connected'
          : 'Not connected'
    ]
  ]

  return (
    <div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginBottom: 14 }}>
        {rows.map(([name, on, detail]) => (
          <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span style={dot(on)} />
            <span style={{ font: "700 13px 'Quicksand'", color: 'var(--ink)', minWidth: 78 }}>{name}</span>
            <span style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)' }}>{detail}</span>
          </div>
        ))}
      </div>

      {status?.reachable && typeof status.queued === 'number' && (
        <p style={{ font: "600 12.5px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '0 0 12px' }}>
          {status.queued === 0
            ? 'Nothing waiting.'
            : `${status.queued} post${status.queued === 1 ? '' : 's'} waiting to go out.`}
          {status.lastTickAt && ` Last checked ${new Date(status.lastTickAt).toLocaleString()}.`}
        </p>
      )}

      {status && !status.reachable && status.detail && !stopped && (
        <p style={{ font: "600 12.5px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '0 0 12px' }}>
          {status.detail} Anything already queued still goes out when it wakes.
        </p>
      )}

      {stopped && (
        <div
          style={{
            border: '2px dashed var(--border)',
            background: 'var(--tip-bg)',
            borderRadius: 12,
            padding: '9px 12px',
            font: "600 12.5px/1.6 'Quicksand'",
            color: 'var(--danger-ink)',
            marginBottom: 12
          }}
        >
          {status?.detail}
          {status?.restartable && (
            <div
              style={{ ...primaryButtonSmall, marginTop: 9, opacity: restarting ? 0.6 : 1 }}
              onClick={restarting ? undefined : () => void restart()}
            >
              {restarting ? 'Restarting…' : 'Restart this Space'}
            </div>
          )}
        </div>
      )}

      {restartNote && (
        <p style={{ font: "600 12.5px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '0 0 12px' }}>
          {restartNote}
        </p>
      )}

      {status?.needsBlueskyReauth && (
        <div
          style={{
            border: '2px dashed var(--border)',
            background: 'var(--tip-bg)',
            borderRadius: 12,
            padding: '9px 12px',
            font: "600 12.5px/1.6 'Quicksand'",
            color: 'var(--danger-ink)',
            marginBottom: 12
          }}
        >
          Your Space can no longer renew its Bluesky session — usually because the app password it came from was
          revoked. Reconnect and it will pick up where it left off.
        </div>
      )}

      <div style={label}>Keep it awake</div>
      <p style={{ font: "600 12px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '4px 0 0' }}>
        Optional backstop for when the Space has actually slept. <strong>cron-job.org</strong> is free — add this
        address, every 5 minutes, notifications off. It cannot help a <em>paused</em> Space, which ignores requests
        and only comes back from a restart.
      </p>
      <code
        style={{
          display: 'block',
          font: "600 12px 'Quicksand'",
          background: 'var(--surface-paper)',
          border: '2px solid var(--border-soft)',
          borderRadius: 10,
          padding: '8px 10px',
          margin: '4px 0 12px',
          wordBreak: 'break-all'
        }}
      >
        {cloud.spaceUrl}/tick
      </code>

      <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
        <div style={{ ...secondaryButtonSmall, opacity: checking ? 0.6 : 1 }} onClick={checking ? undefined : () => void check()}>
          {checking ? 'Checking…' : 'Check now'}
        </div>
        <div style={secondaryButtonSmall} onClick={() => openSetup('cloud')}>
          Reconnect credentials
        </div>
        <div style={secondaryButtonSmall} onClick={() => void window.api.openExternal(`https://huggingface.co/spaces/${cloud.spaceId}`)}>
          Open your Space →
        </div>
        <div style={secondaryButtonSmall} onClick={() => void window.api.openExternal('https://cron-job.org')}>
          Open cron-job.org →
        </div>
      </div>
    </div>
  )
}
