import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { fetchCommunityStatus, getEngageStatus, telegramAccountStatus } from '../api/client'
import { useAppStore } from '../state/store'

/**
 * What's connected, hanging off the avatar in the header.
 *
 * The accounts this app uses are configured in four different places — the Hugging Face token
 * at the top of Settings, Bluesky in the Social Post env schema, Mastodon on its own row, and
 * Telegram across two separate logins in Community. That's reasonable while you're setting
 * each one up and useless when you just want to know whether you're signed in to Bluesky.
 * This is the one place that answers that.
 *
 * Read-only on purpose. Every row points at the screen that owns the connection rather than
 * offering to change it here, so there's exactly one place each credential is edited.
 */
interface Account {
  name: string
  connected: boolean
  detail: string
  /** Where to go to set it up, in words the user can act on. */
  where: string
}

const dot = (on: boolean): CSSProperties => ({
  width: 9,
  height: 9,
  borderRadius: '50%',
  flexShrink: 0,
  background: on ? 'var(--tool-distribute)' : 'var(--ink-fainter)'
})

export default function AccountMenu(): React.JSX.Element {
  const hfUsername = useAppStore((s) => s.hfUsername)
  const hfConnected = useAppStore((s) => s.hfConnected)
  const goSettings = useAppStore((s) => s.goSettings)
  const goCommunity = useAppStore((s) => s.goCommunity)

  const [open, setOpen] = useState(false)
  const [accounts, setAccounts] = useState<Account[] | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  // Close on an outside click or Escape. Bound only while open, so the app isn't carrying a
  // document-level listener around for a menu nobody opened.
  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent): void {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent): void {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Loaded when the menu opens rather than on mount: three of these are network calls, and
  // the header renders on every screen.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    void (async () => {
      const settings = await window.api.settings.getAll().catch(() => null)
      const [bluesky, community, telegram] = await Promise.allSettled([
        getEngageStatus(),
        fetchCommunityStatus(),
        telegramAccountStatus()
      ])
      if (cancelled) return

      const bsky = bluesky.status === 'fulfilled' ? bluesky.value : null
      const comm = community.status === 'fulfilled' ? community.value : null
      const tg = telegram.status === 'fulfilled' ? telegram.value : null
      const instance = (settings?.mastodonInstance ?? '').replace(/^https?:\/\//, '').replace(/\/$/, '')
      const mastodonToken = Boolean(settings?.mastodonAccessToken)

      setAccounts([
        {
          name: 'Hugging Face',
          connected: hfConnected,
          detail: hfUsername ? `@${hfUsername}` : 'Not connected',
          where: 'Settings'
        },
        {
          name: 'Bluesky',
          connected: Boolean(bsky?.configured),
          detail: bsky?.handle ? `@${bsky.handle.replace(/^@/, '')}` : 'Not connected',
          where: 'Settings → Bluesky Post'
        },
        {
          name: 'Mastodon',
          connected: Boolean(instance),
          // An instance with no token still works for posting — the token only adds full-text
          // search and links a published post back to its draft. Saying so beats a bare tick.
          detail: instance ? (mastodonToken ? instance : `${instance} · no token`) : 'Not connected',
          where: 'Settings'
        },
        {
          name: 'Telegram bot',
          connected: Boolean(comm?.botConnected),
          detail: comm?.botUsername ? `@${comm.botUsername}` : 'Not connected',
          where: 'Community → Setup'
        },
        {
          name: 'Telegram account',
          connected: Boolean(tg?.connected),
          detail: tg?.connected
            ? tg.username
              ? `@${tg.username}`
              : tg.firstName || tg.phone || 'Signed in'
            : 'Not signed in',
          where: 'Community → Account'
        }
      ])
    })()
    return () => {
      cancelled = true
    }
  }, [open, hfConnected, hfUsername])

  const connectedCount = accounts?.filter((a) => a.connected).length ?? 0

  return (
    <div ref={wrapRef} style={{ position: 'relative', flexShrink: 0 }}>
      <span
        title={hfUsername ?? 'Not signed in'}
        onClick={() => setOpen((v) => !v)}
        style={{
          width: 34,
          height: 34,
          borderRadius: '50%',
          background: 'var(--avatar-bg)',
          border: `2.5px solid ${open ? 'var(--accent)' : 'var(--border)'}`,
          color: 'var(--avatar-ink)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          font: "700 14px 'Quicksand'",
          cursor: 'pointer',
          boxShadow: open ? 'var(--shadow-sm)' : 'none'
        }}
      >
        {(hfUsername?.[0] ?? 'V').toUpperCase()}
      </span>

      {open && (
        <div
          style={{
            position: 'absolute',
            top: 46,
            right: 0,
            width: 286,
            background: 'var(--surface)',
            border: '2.5px solid var(--border)',
            borderRadius: 18,
            boxShadow: 'var(--shadow-sm)',
            padding: '14px 16px 12px',
            // Above the header's own stacking context and the screen backdrops behind it.
            zIndex: 50
          }}
        >
          <div style={{ font: "700 16px 'Kalam'", color: 'var(--ink)' }}>Connected accounts</div>
          <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginBottom: 10 }}>
            {accounts ? `${connectedCount} of ${accounts.length} connected` : 'Checking…'}
          </div>

          {(accounts ?? []).map((a) => (
            <div
              key={a.name}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 9,
                padding: '8px 0',
                borderTop: '2px dashed var(--border-soft)'
              }}
            >
              <span style={dot(a.connected)} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--ink)' }}>{a.name}</div>
                <div
                  style={{
                    font: "600 11.5px 'Quicksand'",
                    color: a.connected ? 'var(--ink-muted)' : 'var(--ink-faint)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap'
                  }}
                  title={a.connected ? a.detail : a.where}
                >
                  {a.connected ? a.detail : a.where}
                </div>
              </div>
            </div>
          ))}

          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <div
              style={{
                flex: 1,
                textAlign: 'center',
                padding: '7px 10px',
                borderRadius: 999,
                border: '2px solid var(--border)',
                font: "700 12px 'Quicksand'",
                color: 'var(--ink-muted)',
                cursor: 'pointer'
              }}
              onClick={() => {
                setOpen(false)
                goSettings()
              }}
            >
              Settings
            </div>
            <div
              style={{
                flex: 1,
                textAlign: 'center',
                padding: '7px 10px',
                borderRadius: 999,
                border: '2px solid var(--border)',
                font: "700 12px 'Quicksand'",
                color: 'var(--ink-muted)',
                cursor: 'pointer'
              }}
              onClick={() => {
                setOpen(false)
                goCommunity()
              }}
            >
              Community
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
