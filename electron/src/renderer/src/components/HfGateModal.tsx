import { useState } from 'react'
import { verifyHfToken } from '../api/client'
import { useAppStore } from '../state/store'
import { label, primaryButton, textInputOnSurface } from '../styles/styleKit'

export default function HfGateModal(): React.JSX.Element {
  const [token, setToken] = useState('')
  const [status, setStatus] = useState<'idle' | 'checking' | 'error'>('idle')
  const [error, setError] = useState('')
  const setHfStatus = useAppStore((s) => s.setHfStatus)
  const closeHfGate = useAppStore((s) => s.closeHfGate)

  async function handleVerify(): Promise<void> {
    if (!token.trim()) return
    setStatus('checking')
    setError('')
    try {
      const result = await verifyHfToken(token.trim())
      if (result.valid) {
        await window.api.settings.setHfToken(token.trim())
        setHfStatus(true, result.username)
        closeHfGate()
      } else {
        setStatus('error')
        setError(result.detail ?? 'That token could not be verified.')
      }
    } catch (err) {
      setStatus('error')
      setError(err instanceof Error ? err.message : String(err))
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
        zIndex: 50
      }}
    >
      <div
        style={{
          width: 460,
          background: 'var(--surface)',
          border: '2.5px solid var(--border)',
          borderRadius: 22,
          padding: 34,
          boxShadow: '9px 10px 0 rgba(43,36,32,.22)'
        }}
      >
        <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.16em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
          Connect your account
        </div>
        <div style={{ font: "700 27px 'Kalam'", color: 'var(--ink)', marginTop: 6 }}>Connect Hugging Face</div>
        <p style={{ font: "600 14px/1.6 'Quicksand'", color: 'var(--ink-body)', marginTop: 10 }}>
          Every generator in this studio calls Hugging Face Inference Providers, billed to your own account — never
          to us. Create a free token, then paste it below to connect.
        </p>
        <div
          style={{ font: "700 13px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer', marginTop: 6 }}
          onClick={() => window.open('https://huggingface.co/settings/tokens', '_blank')}
        >
          Get a token at huggingface.co/settings/tokens →
        </div>

        <div style={{ marginTop: 20 }}>
          <label style={label}>Hugging Face token</label>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="hf_••••••••••••••••••••••••"
            style={textInputOnSurface}
          />
        </div>

        {status === 'error' && (
          <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a', marginTop: 10 }}>{error}</div>
        )}

        <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
          <div
            style={{ ...primaryButton, flex: 1, opacity: status === 'checking' ? 0.6 : 1 }}
            onClick={status === 'checking' ? undefined : handleVerify}
          >
            {status === 'checking' ? 'Verifying…' : 'Connect account'}
          </div>
          <div
            style={{
              background: 'var(--surface)',
              border: '2px solid var(--border)',
              color: 'var(--ink)',
              borderRadius: 999,
              padding: 14,
              textAlign: 'center',
              font: "700 13px 'Quicksand'",
              cursor: 'pointer'
            }}
            onClick={closeHfGate}
          >
            Not now
          </div>
        </div>
      </div>
    </div>
  )
}
