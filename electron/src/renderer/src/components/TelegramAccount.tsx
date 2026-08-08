import { useEffect, useState } from 'react'
import {
  telegramAccountStatus,
  telegramLogOut,
  telegramSendCode,
  telegramSignIn,
  type TelegramAccountStatus
} from '../api/client'
import { card, label, primaryButton, secondaryButtonSmall, textInput } from '../styles/styleKit'

/**
 * Signing in to Telegram as yourself.
 *
 * Separate from the bot, and not a replacement for it. A bot cannot create a group or add
 * anyone to one — only a person can — so creating and populating groups needs an account
 * login. The bot still runs the paid channel, because Stars subscriptions are a Bot API
 * feature that an account cannot sell.
 *
 * Two things this screen is deliberate about:
 *
 * * **api_id/api_hash are the user's own.** Telegram issues them per person at
 *   my.telegram.org. Shipping one baked into the app would mean every install shared a single
 *   identity, to be rate-limited or revoked for everyone at once.
 * * **The session never reaches the database.** It comes back from the sign-in call, goes
 *   straight into Electron's encrypted store, and is sent per request from there. It is full
 *   access to the account, so it gets the same handling as the Hugging Face token.
 */
const MY_TELEGRAM = 'https://my.telegram.org/apps'

export default function TelegramAccount({ onChange }: { onChange?: () => void }): React.JSX.Element {
  const [apiId, setApiId] = useState('')
  const [apiHash, setApiHash] = useState('')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [needsPassword, setNeedsPassword] = useState(false)
  const [codeSent, setCodeSent] = useState('')
  const [status, setStatus] = useState<TelegramAccountStatus | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    void (async () => {
      const { telegram } = await window.api.settings.getAll()
      setApiId(telegram.apiId)
      setApiHash(telegram.apiHash)
      if (telegram.session) setStatus(await telegramAccountStatus().catch(() => null))
    })()
  }, [])

  async function saveCreds(): Promise<void> {
    await window.api.settings.setAll({ telegram: { apiId: apiId.trim(), apiHash: apiHash.trim() } })
  }

  async function run(name: string, fn: () => Promise<void>): Promise<void> {
    setBusy(name)
    setError('')
    try {
      await fn()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy('')
    }
  }

  if (status?.connected) {
    return (
      <div style={card}>
        <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 6 }}>Signed in to Telegram</div>
        <div style={{ font: "600 13.5px/1.7 'Quicksand'", color: 'var(--ink-body)', marginBottom: 12 }}>
          As <strong>{status.username ? `@${status.username}` : status.firstName || status.phone}</strong>. This app
          shows up in Telegram under Settings → Devices, and you can end the session from there too.
        </div>
        <div
          style={secondaryButtonSmall}
          onClick={() =>
            void run('logout', async () => {
              await telegramLogOut()
              await window.api.settings.setAll({ telegram: { session: '', username: '' } })
              setStatus(null)
              setCode('')
              setPassword('')
              setNeedsPassword(false)
              setCodeSent('')
              onChange?.()
            })
          }
        >
          {busy === 'logout' ? 'Signing out…' : 'Log out'}
        </div>
        {error && <div style={{ font: "700 13px 'Quicksand'", color: 'var(--danger-ink)', marginTop: 10 }}>{error}</div>}
      </div>
    )
  }

  return (
    <div style={card}>
      <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 6 }}>Sign in to Telegram</div>
      <div style={{ font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 14 }}>
        The bot can run a community but it can't <em>make</em> one — Telegram only lets a person create a group or add
        someone to it. Signing in here is what unlocks that. Your login stays on this machine, encrypted by Windows.
      </div>

      {/* api_id/api_hash stay visible until the account is actually connected — a stored
          session that no longer works lands here, and the fix is usually to re-copy them. */}
      <div style={{ font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 10 }}>
        First, get your own API keys: open{' '}
        <span
          style={{ color: 'var(--accent-deep)', cursor: 'pointer', fontWeight: 700 }}
          onClick={() => void window.api.openExternal(MY_TELEGRAM)}
        >
          my.telegram.org/apps
        </span>
        , log in with this same phone number, and create an app. Copy the <code>api_id</code> and{' '}
        <code>api_hash</code> it shows you.
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
        <div style={{ flex: '1 1 130px' }}>
          <label style={label}>api_id</label>
          <input value={apiId} onChange={(e) => setApiId(e.target.value)} placeholder="1234567" style={textInput} />
        </div>
        <div style={{ flex: '2 1 240px' }}>
          <label style={label}>api_hash</label>
          <input
            value={apiHash}
            onChange={(e) => setApiHash(e.target.value)}
            placeholder="0123456789abcdef…"
            type="password"
            style={textInput}
          />
        </div>
      </div>

      {/* ------------------------------------------------------------- phone */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12 }}>
        <div style={{ flex: '1 1 200px' }}>
          <label style={label}>Phone number (with country code)</label>
          <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91…" style={textInput} />
        </div>
        <div
          style={{ ...secondaryButtonSmall, opacity: busy === 'code' || !phone.trim() ? 0.6 : 1 }}
          onClick={
            busy === 'code' || !phone.trim()
              ? undefined
              : () =>
                  void run('code', async () => {
                    await saveCreds()
                    const res = await telegramSendCode(phone.trim())
                    setCodeSent(res.sentTo)
                  })
          }
        >
          {busy === 'code' ? 'Asking Telegram…' : codeSent ? 'Resend code' : 'Send code'}
        </div>
      </div>

      {codeSent && (
        <>
          <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 10 }}>
            {codeSent === 'app'
              ? 'Telegram sent the code to your Telegram app on another device.'
              : `Telegram sent the code by ${codeSent}.`}{' '}
            Type it below — don't paste it into a chat with anyone, including this app's support.
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
            <div style={{ flex: '1 1 140px' }}>
              <label style={label}>Login code</label>
              <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="12345" style={textInput} />
            </div>
            {needsPassword && (
              <div style={{ flex: '1 1 200px' }}>
                <label style={label}>Two-step verification password</label>
                <input
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  type="password"
                  style={textInput}
                />
              </div>
            )}
          </div>
          <div
            style={{ ...primaryButton, opacity: busy === 'signin' || !code.trim() ? 0.6 : 1 }}
            onClick={
              busy === 'signin' || !code.trim()
                ? undefined
                : () =>
                    void run('signin', async () => {
                      const res = await telegramSignIn(code.trim(), password)
                      if (res.needsPassword) {
                        setNeedsPassword(true)
                        return
                      }
                      await window.api.settings.setAll({
                        telegram: { session: res.session ?? '', username: res.username ?? '' }
                      })
                      setStatus(await telegramAccountStatus())
                      setPassword('')
                      onChange?.()
                    })
            }
          >
            {busy === 'signin' ? 'Signing in…' : 'Sign in'}
          </div>
        </>
      )}

      {error && <div style={{ font: "700 13px 'Quicksand'", color: 'var(--danger-ink)', marginTop: 12 }}>{error}</div>}
    </div>
  )
}
