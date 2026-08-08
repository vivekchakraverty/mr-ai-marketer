import { useState } from 'react'
import { connectChannel, disconnectChannel, fetchDistributionConsoleUrl } from '../api/client'
import { PLATFORM_SETUP_GUIDES, type PlatformSetupGuide } from '../state/platformSetupGuides'
import { label, primaryButton, secondaryButtonSmall, textInput } from '../styles/styleKit'

interface Props {
  channel: string
  connected: boolean
  /** False while the distribution engine is unreachable — the form still opens (so the setup
   * steps are readable and credentials can be pasted in advance), but saving needs the engine. */
  engineReady?: boolean
  /** Supplied for user-added channels, whose form is generated from the piece's own auth
   * schema instead of one of the hand-written guides. */
  guide?: PlatformSetupGuide
  /** Only passed for user-added channels — built-in ones cannot be removed. */
  onRemove?: () => void
  onClose: () => void
  onChanged: () => void
}

export default function ChannelConnectModal({
  channel,
  connected,
  engineReady = true,
  guide: guideOverride,
  onRemove,
  onClose,
  onChanged
}: Props): React.JSX.Element {
  const guide = guideOverride ?? PLATFORM_SETUP_GUIDES[channel]
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(guide.fields.map((f) => [f.key, f.kind === 'checkbox' ? 'true' : (f.defaultValue ?? '')]))
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function setField(key: string, v: string): void {
    setValues((cur) => ({ ...cur, [key]: v }))
  }

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
      onChanged()
      onClose()
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
      onChanged()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(43, 36, 32, 0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 60
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: 460,
          maxHeight: '85vh',
          overflowY: 'auto',
          background: 'var(--surface)',
          border: '2.5px solid var(--border)',
          borderRadius: 22,
          padding: 30,
          boxShadow: '9px 10px 0 rgba(43,36,32,.22)'
        }}
        onClick={(e) => e.stopPropagation()}
      >
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
          <ol style={{ margin: '0 0 18px', paddingLeft: 20, font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-body)' }}>
            {guide.helpSteps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        )}

        {guide.authKind === 'OAUTH2' && (
          <div style={{ ...primaryButton, opacity: busy ? 0.6 : 1 }} onClick={busy ? undefined : handleOpenConsole}>
            Connect in Activepieces →
          </div>
        )}

        {guide.authKind !== 'OAUTH2' &&
          guide.fields.map((f) => (
            <div key={f.key} style={{ marginBottom: 14 }}>
              <label style={label}>{f.label}</label>
              {f.kind === 'select' ? (
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

        <div style={{ display: 'flex', gap: 10, marginTop: 8, flexWrap: 'wrap' }}>
          {guide.authKind !== 'OAUTH2' && (
            <div style={{ ...primaryButton, flex: 1, opacity: busy ? 0.6 : 1 }} onClick={busy ? undefined : handleConnect}>
              {busy ? 'Connecting…' : connected ? 'Reconnect' : 'Connect'}
            </div>
          )}
          {connected && (
            <div style={secondaryButtonSmall} onClick={busy ? undefined : handleDisconnect}>
              Disconnect
            </div>
          )}
          {onRemove && (
            <div
              style={{ ...secondaryButtonSmall, color: 'var(--danger-ink)' }}
              onClick={busy ? undefined : onRemove}
            >
              Remove channel
            </div>
          )}
          <div style={secondaryButtonSmall} onClick={onClose}>
            {guide.authKind === 'OAUTH2' ? 'Close' : 'Cancel'}
          </div>
        </div>
      </div>
    </div>
  )
}
