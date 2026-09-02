import { useEffect, useRef, useState } from 'react'
import { cloudProvisionStatus, provisionCloudPoster, type CloudProvisionStatus } from '../../api/client'
import { label, secondaryButtonSmall, textInputOnSurface } from '../../styles/styleKit'
import SetupStepShell from './SetupStepShell'

interface Props {
  shell: { stepId: string; onBack?: () => void; onFinishLater: () => void }
  onDone: () => void
  onSkip: () => void
}

/**
 * Give the user their own poster Space, so scheduled posts go out with the app closed.
 *
 * The one thing this step must not do is overpromise. Free Hugging Face hardware sleeps when
 * idle and cannot be told not to, so the honest guarantee is "normally on time, worst case
 * late, never lost" — a pass fires everything whose time has passed, not just what is due
 * this minute. That sentence is on screen rather than buried in a README, because someone who
 * schedules a 3am post and finds it went out at 7am should have been told, not surprised.
 *
 * The token asked for here is deliberately a second, narrow one. Hugging Face has no API to
 * mint a token, so this cannot be automated away — the deep link and the explanation are the
 * best that can be done, and handing over the account-wide token instead would defeat the
 * whole reason the Space is per-user.
 *
 * The Space is built from resources/poster-space, which ships inside the app, rather than
 * copied from a template we host. So there is no third party in the chain that receives the
 * user's posting credential — the code that gets it is the code they already have.
 */
export default function CloudStep({ shell, onDone, onSkip }: Props): React.JSX.Element {
  const [spaceToken, setSpaceToken] = useState('')
  const [status, setStatus] = useState<CloudProvisionStatus | null>(null)
  const [error, setError] = useState('')
  // True when the Space predates this visit: hides the create form and shows what exists.
  const [already, setAlready] = useState(false)
  // A Space exists but its token is not on this machine, so a repair must ask for one.
  const [needsToken, setNeedsToken] = useState(false)
  // Whether a provision actually RAN here. What gates the settings write — not `already`,
  // which would also block a deliberate re-run and leave the new key on the Space but not on
  // this machine, which is the same desync in the other direction.
  const ranProvision = useRef(false)
  const poll = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    // Someone arriving here from Settings or the Distribute row already HAS a Space. Without
    // this they would be shown the create form again — asked for a token they already gave,
    // with no sign of the Space they own and no way to see its keep-awake address.
    void (async () => {
      const saved = await window.api.settings.getAll()
      if (saved.cloudPosting.spaceId) {
        setStatus({
          status: 'ready',
          message: 'Your poster Space is set up.',
          elapsedSeconds: 0,
          spaceId: saved.cloudPosting.spaceId,
          spaceUrl: saved.cloudPosting.spaceUrl,
          outboxRepo: saved.cloudPosting.outboxRepo,
          posterKey: saved.cloudPosting.posterKey
        })
        setAlready(true)
        setNeedsToken(!saved.cloudPosting.spaceToken.trim())
      }
    })()
    return () => {
      if (poll.current) clearInterval(poll.current)
    }
  }, [])

  /** The token this provision ran with. Empty input on a repair means the stored one. */
  const tokenUsed = useRef('')

  async function absorb(next: CloudProvisionStatus): Promise<void> {
    setStatus(next)
    if (next.status !== 'ready' || !next.spaceId || !ranProvision.current) return
    if (poll.current) clearInterval(poll.current)
    // Persist before advancing: the backend reads these from its environment at spawn, so a
    // provision that is not written down is a Space the app cannot talk to next launch.
    await window.api.settings.setAll({
      cloudPosting: {
        spaceId: next.spaceId,
        spaceUrl: next.spaceUrl ?? '',
        outboxRepo: next.outboxRepo ?? '',
        posterKey: next.posterKey ?? '',
        // Never the raw input: a repair leaves it empty, and writing that would erase the
        // token the Space needs and make the NEXT repair impossible.
        spaceToken: tokenUsed.current,
        provisionedAt: new Date().toISOString()
      }
    })
  }

  async function start(): Promise<void> {
    setError('')
    try {
      const saved = await window.api.settings.getAll()
      if (!saved.hfToken.trim()) {
        setError('Add your Hugging Face token on the previous step first.')
        return
      }
      // A repair re-uses the token already stored, so nobody has to make a third one.
      const token = spaceToken.trim() || saved.cloudPosting.spaceToken
      if (!token) {
        setError('This needs a fine-grained Hugging Face token for the Space.')
        return
      }
      ranProvision.current = true
      tokenUsed.current = token
      await absorb(await provisionCloudPoster({ hfToken: saved.hfToken, spaceToken: token }))
      poll.current = setInterval(() => {
        void cloudProvisionStatus()
          .then(absorb)
          .catch(() => {})
      }, 2500)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const running = status?.status === 'running'
  const ready = status?.status === 'ready'

  return (
    <SetupStepShell
      {...shell}
      eyebrow="Step 2"
      title="Posting while the app is closed"
      blurb="Scheduled posts normally need this app running at the moment they go out. This gives you your own poster on Hugging Face that does it for you."
      onSkip={onSkip}
      onNext={ready ? onDone : () => void start()}
      nextLabel={ready ? 'Continue' : 'Set it up'}
      nextBusy={running}
    >
      <p style={{ font: "600 13.5px/1.75 'Quicksand'", color: 'var(--ink-body)', margin: '0 0 16px' }}>
        It is created in <em>your</em> Hugging Face account, holding <em>your</em> queue, and built from the
        poster that ships inside this app rather than copied from one of ours. Nothing is shared with anyone else,
        and no credential of yours is ever held by us.
      </p>

      <div
        style={{
          border: '2px dashed var(--border)',
          borderRadius: 14,
          padding: '10px 13px',
          font: "600 12.5px/1.6 'Quicksand'",
          color: 'var(--ink-fainter-2)',
          marginBottom: 16
        }}
      >
        On the free tier a Space sleeps when idle, and Hugging Face does not let a free Space turn that off. So:
        normally on time, worst case late, <strong>never lost</strong> — anything whose time has passed goes out as
        soon as it wakes. Upgrade the Space later if exact timing matters.
      </div>

      {(!ready || needsToken) && (
        <>
          <div style={label}>A token just for this Space</div>
          <input
            style={textInputOnSurface}
            type="password"
            value={spaceToken}
            placeholder="hf_..."
            onChange={(e) => setSpaceToken(e.target.value)}
          />
          <p style={{ font: "600 12.5px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '8px 0 0' }}>
            Create a <strong>fine-grained</strong> token with read and write on your own repos. This one is stored in
            the Space; keeping it separate from the token on the last step is what keeps that one out of the cloud.
          </p>
          <div
            style={{ ...secondaryButtonSmall, marginTop: 10 }}
            onClick={() => void window.api.openExternal('https://huggingface.co/settings/tokens/new?tokenType=fineGrained')}
          >
            Open this page →
          </div>
        </>
      )}

      {status && status.status !== 'idle' && (
        <div
          style={{
            font: "600 12.5px/1.6 'Quicksand'",
            color: status.status === 'error' ? '#a34a3a' : 'var(--ink-body)',
            marginTop: 14
          }}
        >
          {status.message}
        </div>
      )}

      {ready && status?.spaceUrl && (
        <div style={{ marginTop: 14 }}>
          <div style={label}>Optional: keep it awake</div>
          <p style={{ font: "600 12.5px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '4px 0 8px' }}>
            Your Space already keeps itself awake while it is running, and this app wakes it when a post is nearly
            due. A pinger is the backstop for the times it has genuinely been asleep — it bounds how late a post can
            be to about one ping apart.
          </p>
          <p style={{ font: "600 12.5px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '0 0 8px' }}>
            <strong>cron-job.org</strong> is free and does exactly this. Add the address below, set it to every 5
            minutes, and turn notifications <em>off</em> — a sleeping Space is normal here, and an uptime monitor will
            otherwise email you about it.
          </p>
          <code
            style={{
              display: 'block',
              font: "600 12px 'Quicksand'",
              background: 'var(--surface-paper)',
              border: '2px solid var(--border-soft)',
              borderRadius: 10,
              padding: '8px 10px',
              wordBreak: 'break-all'
            }}
          >
            {status.spaceUrl}/tick
          </code>
          <div style={{ display: 'flex', gap: 9, marginTop: 8, flexWrap: 'wrap' }}>
            <div
              style={secondaryButtonSmall}
              onClick={() => void navigator.clipboard.writeText(`${status.spaceUrl}/tick`)}
            >
              Copy
            </div>
            <div style={secondaryButtonSmall} onClick={() => void window.api.openExternal('https://cron-job.org')}>
              Open cron-job.org →
            </div>
          </div>
        </div>
      )}

      {already && (
        <div style={{ marginTop: 14 }}>
          <div
            style={{ ...secondaryButtonSmall, opacity: running ? 0.6 : 1 }}
            onClick={running ? undefined : () => void start()}
          >
            {running ? 'Repairing…' : 'Repair this Space'}
          </div>
          <p style={{ font: "600 12px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '6px 0 0' }}>
            Re-uploads the poster and re-pushes its credentials. Use this if Distribute says your Space did not
            recognise this app&apos;s key — nothing queued is lost.
          </p>
        </div>
      )}

      {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a', marginTop: 12 }}>{error}</div>}
    </SetupStepShell>
  )
}
