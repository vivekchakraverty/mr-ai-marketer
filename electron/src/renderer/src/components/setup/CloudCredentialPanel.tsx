import { useEffect, useState } from 'react'
import { connectCloudBluesky, connectCloudMastodon } from '../../api/client'
import { label, secondaryButtonSmall, textInputOnSurface } from '../../styles/styleKit'

interface Props {
  channel: string
  /** Whatever the connect form above currently holds, so nothing is typed twice. */
  values: Record<string, string>
}

/**
 * Offers the same channel to the user's poster Space, in the step where they are already
 * connecting it.
 *
 * The two networks need genuinely different things here, and pretending otherwise would
 * misdescribe what is being handed over:
 *
 *   Mastodon — a SECOND application, scoped to write:statuses and write:media. It cannot be
 *   derived from the one above, because a token's scopes are fixed when it is created, so
 *   this asks for a new one. The backend then checks behaviourally that it really cannot
 *   read the account, and says so either way rather than assuming.
 *
 *   Bluesky — nothing new to create. The handle and app password above are enough to mint a
 *   refresh session, and only that session goes to the Space. So this is one button, and the
 *   app password never leaves the machine.
 *
 * Not shown at all until a Space exists, because there would be nowhere to put the
 * credential — the row points back at the step that creates one instead.
 */
export default function CloudCredentialPanel({ channel, values }: Props): React.JSX.Element | null {
  const [spaceId, setSpaceId] = useState('')
  const [hfToken, setHfToken] = useState('')
  const [scopedToken, setScopedToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null)

  useEffect(() => {
    void (async () => {
      const saved = await window.api.settings.getAll()
      setSpaceId(saved.cloudPosting.spaceId)
      setHfToken(saved.hfToken)
    })()
  }, [])

  if (channel !== 'mastodon' && channel !== 'bluesky') return null

  const host = String(values.base_url ?? '')
    .trim()
    .replace(/^https?:\/\//, '')
    .split('/')[0]
    .replace(/\.$/, '')
    .toLowerCase()

  async function connect(): Promise<void> {
    setBusy(true)
    setResult(null)
    try {
      if (channel === 'mastodon') {
        const res = await connectCloudMastodon({
          hfToken,
          spaceId,
          instance: host,
          accessToken: scopedToken.trim()
        })
        await window.api.settings.setAll({
          cloudPosting: { mastodonHost: res.instance, mastodonConnectedAt: new Date().toISOString() }
        })
        setResult({ ok: true, detail: res.detail })
      } else {
        const res = await connectCloudBluesky({
          hfToken,
          spaceId,
          identifier: String(values.identifier ?? '').trim(),
          appPassword: String(values.password ?? '').trim(),
          pdsHost: String(values.pdsHost ?? '').trim()
        })
        await window.api.settings.setAll({
          cloudPosting: { blueskyDid: res.did, blueskyConnectedAt: new Date().toISOString() }
        })
        setResult({ ok: true, detail: res.detail })
      }
    } catch (err) {
      setResult({ ok: false, detail: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  const frame: React.CSSProperties = {
    border: '2px dashed var(--border)',
    borderRadius: 14,
    padding: '12px 14px',
    marginTop: 16
  }

  if (!spaceId) {
    return (
      <div style={frame}>
        <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--ink)' }}>Posting while the app is closed</div>
        <p style={{ font: "600 12.5px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '5px 0 0' }}>
          Set up cloud posting on the earlier step and you can hand this channel to your own Space here, so scheduled
          posts go out without the app running.
        </p>
      </div>
    )
  }

  const ready =
    channel === 'mastodon'
      ? Boolean(host && scopedToken.trim())
      : Boolean(String(values.identifier ?? '').trim() && String(values.password ?? '').trim())

  return (
    <div style={frame}>
      <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--ink)' }}>
        Also let your Space post this
      </div>

      {channel === 'mastodon' ? (
        <>
          <p style={{ font: "600 12.5px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '5px 0 10px' }}>
            Your Space needs its own token, because a token&apos;s permissions are fixed when it is made. Create a
            second application and tick <strong>only</strong> write:statuses and write:media — that one can post for
            you and cannot read your account.
          </p>
          {host && (
            <div
              style={{ ...secondaryButtonSmall, marginBottom: 10 }}
              onClick={() => void window.api.openExternal(`https://${host}/settings/applications/new`)}
            >
              Create it on {host} →
            </div>
          )}
          <div style={label}>Write-only access token</div>
          <input
            style={textInputOnSurface}
            type="password"
            value={scopedToken}
            placeholder="Paste the second token"
            onChange={(e) => setScopedToken(e.target.value)}
          />
        </>
      ) : (
        <p style={{ font: "600 12.5px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: '5px 0 10px' }}>
          Nothing more to create. Your Space is given a session that renews itself — not the app password above, which
          never leaves this machine. Revoking that app password on bsky.app ends the session too.
        </p>
      )}

      <div
        style={{ ...secondaryButtonSmall, marginTop: 10, opacity: busy || !ready ? 0.5 : 1 }}
        onClick={busy || !ready ? undefined : () => void connect()}
      >
        {busy ? 'Connecting…' : channel === 'mastodon' ? 'Give it to my Space' : 'Use these for my Space'}
      </div>

      {result && (
        <div
          style={{
            font: "600 12.5px/1.6 'Quicksand'",
            color: result.ok ? 'var(--ink-body)' : '#a34a3a',
            marginTop: 10
          }}
        >
          {result.ok ? '✓ ' : ''}
          {result.detail}
        </div>
      )}
    </div>
  )
}
