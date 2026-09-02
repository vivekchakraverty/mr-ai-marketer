import { useEffect, useState } from 'react'
import {
  SETTINGS_PLACEHOLDER,
  connectChannel,
  disconnectChannel,
  fetchChannelPrefill,
  fetchDistributionConsoleUrl,
  verifyChannelSettings
} from '../api/client'
import { PLATFORM_SETUP_GUIDES, type PlatformSetupGuide } from '../state/platformSetupGuides'
import { label, primaryButton, secondaryButtonSmall, textInput } from '../styles/styleKit'

/**
 * Credentials this app already holds for a channel, pulled in so the user is not
 * asked to type the same app password a second time.
 *
 * Two stores feed this and they are not interchangeable. The backend owns
 * Bluesky's handle/app-password (in vendor/socialpost's env) and the SMTP
 * settings, and exposes them via the prefill endpoint — secrets as a placeholder
 * that only the backend can resolve. Mastodon's instance and token live in
 * Electron's encrypted store, which the backend never sees, so they are read here
 * directly. `window.api.settings` is already the source for the Mastodon composer,
 * so this exposes nothing that screen doesn't.
 */
async function mastodonPrefill(): Promise<Record<string, string>> {
  const settings = await window.api.settings.getAll()
  const out: Record<string, string> = {}
  const instance = (settings.mastodonInstance ?? '').trim()
  const token = (settings.mastodonAccessToken ?? '').trim()
  if (instance) out.base_url = /^https?:\/\//.test(instance) ? instance : `https://${instance}`
  if (token) out.access_token = token
  return out
}

/** What a footer needs to know to drive the form it sits under. */
export interface ChannelConnectFooterContext {
  busy: boolean
  connected: boolean
  authKind: PlatformSetupGuide['authKind']
  /** Validate and save. No-op for OAUTH2 channels, which connect in Activepieces instead. */
  connect: () => void
  disconnect: () => void
}

interface FormProps {
  channel: string
  connected: boolean
  /** False while the distribution engine is unreachable — the form still renders (so the
   * setup steps are readable and credentials can be pasted in advance), but saving needs
   * the engine. */
  engineReady?: boolean
  /** Supplied for user-added channels, whose form is generated from the piece's own auth
   * schema instead of one of the hand-written guides. */
  guide?: PlatformSetupGuide
  onConnected: () => void
  onDisconnected: () => void
  /** Rendered under the fields. The dialog supplies Connect/Disconnect/Cancel; the setup
   * walkthrough supplies Skip/Back/Next. Everything above it is identical either way. */
  footer: (ctx: ChannelConnectFooterContext) => React.ReactNode
  /** Rendered between the fields and the footer. The walkthrough uses it to offer the same
   * credential to the user's poster Space; the dialog passes nothing. */
  extra?: (values: Record<string, string>) => React.ReactNode
}

/**
 * One channel's credential form: the help steps, the prefill, the fields, the validation.
 *
 * Split out of ChannelConnectModal so the first-run walkthrough can present the same form
 * inline with its own footer instead of a second copy that would drift. Everything that
 * makes this form correct — the SETTINGS_PLACEHOLDER round trip, the per-field prefill
 * markers, the Mastodon dual-write into Electron's store — is subtle enough that a second
 * implementation would get one of them wrong.
 */
export default function ChannelConnectForm({
  channel,
  connected,
  engineReady = true,
  guide: guideOverride,
  onConnected,
  onDisconnected,
  footer,
  extra
}: FormProps): React.JSX.Element {
  const guide = guideOverride ?? PLATFORM_SETUP_GUIDES[channel]
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(guide.fields.map((f) => [f.key, f.kind === 'checkbox' ? 'true' : (f.defaultValue ?? '')]))
  )
  // Keys that arrived from Settings rather than being typed here. Drives the
  // "from Settings" markers, and is cleared per-field the moment the user edits
  // one so the marker never outlives the value it describes.
  const [prefilled, setPrefilled] = useState<Set<string>>(new Set())
  const [prefillSource, setPrefillSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [verifying, setVerifying] = useState(false)
  const [verified, setVerified] = useState<{ ok: boolean; detail: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    async function seed(): Promise<void> {
      try {
        const [backend, electron] = await Promise.all([
          fetchChannelPrefill(channel).catch(() => null),
          channel === 'mastodon' ? mastodonPrefill() : Promise.resolve({} as Record<string, string>)
        ])
        if (cancelled) return

        const found: Record<string, string> = {}
        for (const f of backend?.fields ?? []) found[f.key] = f.value
        for (const [k, v] of Object.entries(electron)) found[k] = v

        // Only touch fields this form actually has, so a stored value for a prop
        // the guide doesn't show can't leak into the payload.
        const applicable = guide.fields.filter((f) => found[f.key] !== undefined && found[f.key] !== '')
        if (applicable.length === 0) return

        setValues((cur) => {
          const next = { ...cur }
          for (const f of applicable) next[f.key] = found[f.key]
          return next
        })
        setPrefilled(new Set(applicable.map((f) => f.key)))
        setPrefillSource(
          backend?.source && backend.available
            ? backend.source
            : channel === 'mastodon'
              ? 'your Mastodon settings'
              : 'Settings'
        )
      } catch {
        // Prefill is a convenience — a failure just leaves the form blank, which
        // is exactly how it behaved before.
      }
    }
    void seed()
    return () => {
      cancelled = true
    }
  }, [channel, guide])

  function setField(key: string, v: string): void {
    setValues((cur) => ({ ...cur, [key]: v }))
    setPrefilled((cur) => {
      if (!cur.has(key)) return cur
      const next = new Set(cur)
      next.delete(key)
      return next
    })
    setVerified(null)
  }

  /** Drop a saved secret so the user can type a different one. */
  function replaceSecret(key: string): void {
    setValues((cur) => ({ ...cur, [key]: '' }))
    setPrefilled((cur) => {
      const next = new Set(cur)
      next.delete(key)
      return next
    })
    setVerified(null)
  }

  async function handleVerify(): Promise<void> {
    setVerifying(true)
    setVerified(null)
    try {
      setVerified(await verifyChannelSettings(channel))
    } catch (err) {
      setVerified({ ok: false, detail: err instanceof Error ? err.message : String(err) })
    } finally {
      setVerifying(false)
    }
  }

  const helpUrl = (guide.helpUrlFor ? guide.helpUrlFor(values) : '') || guide.helpUrl || ''

  // Only the two channels whose credentials the backend holds can be checked
  // without first creating a connection.
  const canVerify = channel === 'bluesky' || channel === 'email'

  async function handleConnect(): Promise<void> {
    const missing = guide.fields.find((f) => !f.optional && f.kind !== 'checkbox' && !(values[f.key] ?? '').trim())
    if (missing) {
      setError(`${missing.label} is required.`)
      return
    }
    setBusy(true)
    setError('')
    try {
      const payload: Record<string, unknown> = {}
      for (const f of guide.fields) {
        const raw = values[f.key] ?? ''
        if (f.kind === 'checkbox') payload[f.key] = raw === 'true'
        else if (raw === '') continue
        else if (f.key === 'port') payload[f.key] = Number(raw)
        else payload[f.key] = raw
      }
      await connectChannel(channel, guide.authKind as 'CUSTOM_AUTH' | 'SECRET_TEXT', payload)
      if (channel === 'mastodon') {
        // Distribution can be connected with credentials typed here rather than in
        // Settings. Keep that account in Electron's encrypted store as the source of truth
        // so scheduled media still has a credential after the next app restart.
        const current = await window.api.settings.getAll()
        const instance = String(payload.base_url ?? '')
          .trim()
          .replace(/^https?:\/\//, '')
          .split('/')[0]
          .replace(/\.$/, '')
          .toLowerCase()
        const accessToken = String(payload.access_token ?? '').trim()
        const accounts = (current.mastodonAccounts ?? []).filter(
          (account) =>
            account.instance
              .trim()
              .replace(/^https?:\/\//, '')
              .split('/')[0]
              .replace(/\.$/, '')
              .toLowerCase() !== instance
        )
        await window.api.settings.setAll({
          mastodonInstance: instance,
          mastodonAccessToken: accessToken,
          mastodonAccounts: [...accounts, { instance, accessToken }]
        })
      }
      onConnected()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleOpenConsole(): Promise<void> {
    setBusy(true)
    setError('')
    try {
      const { url } = await fetchDistributionConsoleUrl()
      await window.api.openExternal(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleDisconnect(): Promise<void> {
    setBusy(true)
    setError('')
    try {
      await disconnectChannel(channel)
      onDisconnected()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
        <span
          style={{
            width: 34,
            height: 34,
            borderRadius: '52% 48% 55% 45%',
            background: guide.color,
            border: '2.5px solid var(--border)',
            flexShrink: 0
          }}
        />
        <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)' }}>{guide.label}</div>
      </div>
      <p style={{ font: "600 13.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', margin: '4px 0 14px' }}>{guide.blurb}</p>

      {!engineReady && (
        <div
          style={{
            border: '2px dashed var(--border)',
            borderRadius: 14,
            padding: '10px 13px',
            font: "600 12.5px/1.6 'Quicksand'",
            color: 'var(--ink-fainter-2)',
            marginBottom: 14
          }}
        >
          The distribution engine isn't running yet — you can read the steps and fill this in, but saving won't work
          until it's up.
        </div>
      )}

      {guide.helpSteps.length > 0 && (
        <ol style={{ margin: '0 0 10px', paddingLeft: 20, font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-body)' }}>
          {guide.helpSteps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}

      {/* The steps name the page as bare text; this opens it. helpUrlFor wins where it
          returns something, because a Mastodon token is created on the user's own instance
          and that address is not knowable until they have typed it. */}
      {helpUrl && (
        <div style={{ ...secondaryButtonSmall, marginBottom: 18 }} onClick={() => void window.api.openExternal(helpUrl)}>
          Open this page →
        </div>
      )}

      {guide.authKind === 'OAUTH2' && (
        <div style={{ ...primaryButton, opacity: busy ? 0.6 : 1 }} onClick={busy ? undefined : handleOpenConsole}>
          Connect in Activepieces →
        </div>
      )}

      {prefillSource && (
        <div
          style={{
            border: '2px solid var(--border)',
            background: 'var(--accent-soft-bg)',
            borderRadius: 14,
            padding: '10px 13px',
            marginBottom: 14,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            flexWrap: 'wrap'
          }}
        >
          <div style={{ flex: 1, minWidth: 190, font: "600 12.5px/1.55 'Quicksand'", color: 'var(--ink-body)' }}>
            Filled in from {prefillSource}. Check it looks right, then connect.
          </div>
          {canVerify && (
            <div
              style={{ ...secondaryButtonSmall, opacity: verifying ? 0.6 : 1 }}
              onClick={verifying ? undefined : handleVerify}
            >
              {verifying ? 'Checking…' : 'Check they work'}
            </div>
          )}
        </div>
      )}

      {verified && (
        <div
          style={{
            font: "700 12.5px/1.55 'Quicksand'",
            color: verified.ok ? 'var(--accent-deep)' : '#a34a3a',
            marginBottom: 12
          }}
        >
          {verified.ok ? '✓ ' : ''}
          {verified.detail}
        </div>
      )}

      {guide.authKind !== 'OAUTH2' &&
        guide.fields.map((f) => (
          <div key={f.key} style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <label style={label}>{f.label}</label>
              {prefilled.has(f.key) && (
                <span
                  style={{
                    font: "700 10px 'Quicksand'",
                    letterSpacing: '.08em',
                    textTransform: 'uppercase',
                    color: 'var(--accent-deep)'
                  }}
                >
                  from settings
                </span>
              )}
            </div>
            {/* A saved secret is never shown, not even masked — the renderer was
                handed a placeholder, not the credential. It stays a placeholder
                all the way back to the backend, which swaps in the real value. */}
            {f.secret && prefilled.has(f.key) && values[f.key] === SETTINGS_PLACEHOLDER ? (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  border: '2px solid var(--border)',
                  borderRadius: 12,
                  padding: '9px 12px',
                  background: 'var(--surface-tint)'
                }}
              >
                <span style={{ flex: 1, font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)' }}>
                  Using the one saved in Settings
                </span>
                <span
                  style={{ font: "700 12px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer' }}
                  onClick={() => replaceSecret(f.key)}
                >
                  Use a different one
                </span>
              </div>
            ) : f.kind === 'select' ? (
              <select style={textInput} value={values[f.key] ?? ''} onChange={(e) => setField(f.key, e.target.value)}>
                <option value="" disabled>
                  Choose…
                </option>
                {f.options?.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : f.kind === 'checkbox' ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <input
                  type="checkbox"
                  checked={values[f.key] === 'true'}
                  onChange={(e) => setField(f.key, e.target.checked ? 'true' : 'false')}
                />
                <span style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-muted)' }}>Enabled</span>
              </div>
            ) : (
              <input
                type={f.secret ? 'password' : 'text'}
                value={values[f.key] ?? ''}
                placeholder={f.placeholder}
                onChange={(e) => setField(f.key, e.target.value)}
                style={textInput}
              />
            )}
          </div>
        ))}

      {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a', marginBottom: 12 }}>{error}</div>}

      {extra?.(values)}

      {footer({
        busy,
        connected,
        authKind: guide.authKind,
        connect: () => void handleConnect(),
        disconnect: () => void handleDisconnect()
      })}
    </>
  )
}
