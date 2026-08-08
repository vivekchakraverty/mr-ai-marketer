import { useEffect, useState } from 'react'
import {
  broadcastCommunity,
  connectCommunityBot,
  createCommunityInvite,
  createGatedInvite,
  deleteCommunityTier,
  disconnectCommunityBot,
  fetchCommunityMembers,
  fetchCommunityStatus,
  saveCommunityTier,
  sweepCommunity,
  type CommunityMember,
  type CommunityStatus
} from '../api/client'
import ScreenBackdrop from '../components/ScreenBackdrop'
import TelegramAccount from '../components/TelegramAccount'
import TelegramEmbed from '../components/TelegramEmbed'
import TelegramGroups from '../components/TelegramGroups'
import { card, label, primaryButton, primaryButtonSmall, secondaryButtonSmall, sectionEyebrow, segGroup, segItem, textInput, textarea } from '../styles/styleKit'

const POLL_MS = 20000

/**
 * The tabs. Two of them are the chats themselves; the other three are the three separate
 * ways you act on them — as your own account (Groups), as the bot (Setup), or by signing the
 * account in at all (Account).
 */
type View = 'open' | 'paid' | 'groups' | 'account' | 'setup'

/**
 * The Community screen — an open group plus a paid channel.
 *
 * Two chats, because Telegram has no per-post visibility: everyone in a group sees every
 * message in it. So general posts go to a group anyone can be added to, and gated posts go
 * to a channel people subscribe to. The screen says that plainly rather than offering a
 * "subscribers only" toggle that would quietly post to the same room either way.
 */
export default function Community(): React.JSX.Element {
  const [status, setStatus] = useState<CommunityStatus | null>(null)
  const [members, setMembers] = useState<CommunityMember[]>([])
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const [tierName, setTierName] = useState('')
  const [tierStars, setTierStars] = useState(500)
  const [tierDays, setTierDays] = useState(30)
  const [tierDesc, setTierDesc] = useState('')

  const [message, setMessage] = useState('')
  const [gated, setGated] = useState(true)
  const [sentNote, setSentNote] = useState('')
  // Which chat the embedded client is showing. Starts on setup because that is the only
  // useful thing to do before a chat exists; once one is linked the screen moves to the
  // conversation, which is the daily job. `picked` stops that from overriding a deliberate
  // click — including a click back to Setup.
  const [view, setView] = useState<View>('setup')
  const [picked, setPicked] = useState(false)

  function choose(next: View): void {
    setPicked(true)
    setView(next)
    // Looking at a chat and then posting to a different one is a trap worth closing:
    // the composer follows the tab. The checkbox stays editable either way.
    if (next === 'open' || next === 'paid') setGated(next === 'paid')
  }

  async function refresh(): Promise<void> {
    try {
      const [s, m] = await Promise.all([fetchCommunityStatus(), fetchCommunityMembers()])
      setStatus(s)
      setMembers(m.members)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => {
    void refresh()
    // Polled because the group link arrives from Telegram, not from anything clicked here:
    // the user adds the bot to their group in another app and this screen should notice.
    const t = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (picked || !status) return
    if (status.groupLinked) setView('open')
    else if (status.gatedLinked) {
      setView('paid')
      setGated(true)
    }
  }, [status, picked])

  async function run(name: string, fn: () => Promise<unknown>): Promise<void> {
    setBusy(name)
    setError('')
    try {
      await fn()
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy('')
    }
  }

  const step = !status?.botConnected ? 1 : !(status?.groupLinked || status?.gatedLinked) ? 2 : !status?.tiers.length ? 3 : !status?.gatedInviteLink ? 4 : 5

  function StepBadge({ n }: { n: number }): React.JSX.Element {
    const done = step > n
    const current = step === n
    return (
      <span
        style={{
          width: 24,
          height: 24,
          borderRadius: '50%',
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          border: '2px solid var(--border)',
          background: done ? 'var(--tool-distribute)' : current ? 'var(--accent)' : 'var(--surface)',
          color: done || current ? 'var(--accent-ink)' : 'var(--ink-faint)',
          font: "700 12px 'Quicksand'"
        }}
      >
        {done ? '✓' : n}
      </span>
    )
  }

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '30px 34px 60px' }}>
      <ScreenBackdrop video="engage" />

      <div style={{ marginBottom: 22 }}>
        <div style={sectionEyebrow}>Community</div>
        <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)', marginTop: 4 }}>Your Telegram community</div>
        <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4, maxWidth: 640 }}>
          An open group your admins add people to, plus a paid channel for members-only posts. Telegram charges the
          subscription in Stars, renews it, and removes anyone who stops paying.
        </div>
      </div>

      {error && (
        <div style={{ ...card, borderColor: 'var(--danger-ink)', marginBottom: 16, font: "700 13px 'Quicksand'", color: 'var(--danger-ink)' }}>
          {error}
        </div>
      )}

      {/* The two chats live in here as real Telegram, not a summary of it — so posting,
          reading replies and moderating happen without leaving the app. */}
      <div style={{ ...segGroup, marginBottom: 16, maxWidth: 420 }}>
        <div style={segItem(view === 'open')} onClick={() => choose('open')}>
          Open group
        </div>
        <div style={segItem(view === 'paid')} onClick={() => choose('paid')}>
          Paid channel
        </div>
        <div style={segItem(view === 'groups')} onClick={() => choose('groups')}>
          Groups
        </div>
        <div style={segItem(view === 'account')} onClick={() => choose('account')}>
          Account
        </div>
        <div style={segItem(view === 'setup')} onClick={() => choose('setup')}>
          Setup
        </div>
      </div>

      {/* Signing in as yourself, which is what makes creating groups and adding members
          possible at all — a bot can do neither. */}
      {view === 'account' && <TelegramAccount onChange={() => void refresh()} />}

      {view === 'groups' && (
        <TelegramGroups
          botUsername={status?.botUsername ?? ''}
          linkedChatId={status?.chatId ?? ''}
          linkedGatedChatId={status?.gatedChatId ?? ''}
          onLinked={() => void refresh()}
        />
      )}

      {view === 'open' && (
        <div style={{ ...card, marginBottom: 14 }}>
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 2 }}>
            {status?.chatTitle || 'Open group'}
          </div>
          <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
            Everyone your admins have added. General posts land here.
          </div>
          <TelegramEmbed
            chatId={status?.chatId ?? ''}
            emptyHint="No open group linked yet. Add your bot to a Telegram group and it'll appear here."
          />
        </div>
      )}

      {view === 'paid' && (
        <div style={{ ...card, marginBottom: 14 }}>
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 2 }}>
            {status?.gatedChatTitle || 'Paid channel'}
          </div>
          <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
            Subscribers only. Telegram admits and removes people here based on their subscription.
          </div>
          <TelegramEmbed
            chatId={status?.gatedChatId ?? ''}
            emptyHint="No paid channel linked yet. Add your bot to a Telegram channel and it'll appear here."
          />
        </div>
      )}

      {/* Configuration lives behind its own tab once the chats are live — the daily job is
          reading and posting, not re-running setup. */}
      {view === 'setup' && (
      <>
      {/* ---------------------------------------------------------------- step 1 */}
      <div style={{ ...card, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <StepBadge n={1} />
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)' }}>Connect a Telegram bot</div>
        </div>
        {status?.botConnected ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span style={{ font: "600 13.5px 'Quicksand'", color: 'var(--ink-body)' }}>
              Connected as <strong>@{status.botUsername}</strong>
            </span>
            <div style={secondaryButtonSmall} onClick={() => void run('disconnect', disconnectCommunityBot)}>
              {busy === 'disconnect' ? 'Disconnecting…' : 'Disconnect'}
            </div>
          </div>
        ) : (
          <>
            <div style={{ font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
              Open Telegram, message <strong>@BotFather</strong>, send <code>/newbot</code> and follow the prompts. Paste
              the token it gives you below.
            </div>
            <label style={label}>Bot token</label>
            <input
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="123456789:AAE…"
              type="password"
              style={{ ...textInput, marginBottom: 12 }}
            />
            <div
              style={{ ...primaryButton, opacity: busy === 'bot' || !token.trim() ? 0.6 : 1 }}
              onClick={busy === 'bot' || !token.trim() ? undefined : () => void run('bot', () => connectCommunityBot(token.trim()))}
            >
              {busy === 'bot' ? 'Checking with Telegram…' : 'Connect bot'}
            </div>
          </>
        )}
      </div>

      {/* ---------------------------------------------------------------- step 2 */}
      <div style={{ ...card, marginBottom: 14, opacity: step >= 2 ? 1 : 0.55 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <StepBadge n={2} />
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)' }}>Add the bot to your two chats</div>
        </div>
        <div style={{ font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
          Telegram shows every message in a chat to everyone in it — there's no way to hide one post from some
          members. So members-only posts need somewhere members-only to live. Either make both in Telegram yourself and
          add <strong>{status?.botUsername ? `@${status.botUsername}` : 'your bot'}</strong> to each as an admin — this
          screen links them automatically and tells them apart on its own — or sign in on the <strong>Account</strong>{' '}
          tab and make them from the <strong>Groups</strong> tab, which adds the bot for you.
        </div>

        {[
          {
            title: 'Open group',
            blurb: 'Your admins add people by hand. Everyone here sees general posts.',
            linked: status?.groupLinked,
            name: status?.chatTitle || status?.chatId
          },
          {
            title: 'Paid channel',
            blurb: 'Members-only posts go here. Telegram handles who gets in.',
            linked: status?.gatedLinked,
            name: status?.gatedChatTitle || status?.gatedChatId
          }
        ].map((c) => (
          <div
            key={c.title}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 11,
              padding: '11px 13px',
              marginBottom: 8,
              background: 'var(--surface-tint)',
              border: '2px solid var(--border)',
              borderRadius: 14
            }}
          >
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: '50%',
                flexShrink: 0,
                background: c.linked ? 'var(--tool-distribute)' : 'var(--ink-fainter)'
              }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>
                {c.title}
                {c.linked && c.name ? ` — ${c.name}` : ''}
              </div>
              <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>{c.blurb}</div>
            </div>
            <span style={{ font: "700 11px 'Quicksand'", letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
              {c.linked ? 'linked' : 'waiting'}
            </span>
          </div>
        ))}
      </div>

      {/* ---------------------------------------------------------------- step 3 */}
      <div style={{ ...card, marginBottom: 14, opacity: step >= 3 ? 1 : 0.55 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <StepBadge n={3} />
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)' }}>Set your subscription tiers</div>
        </div>
        <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
          Priced in Telegram Stars — Telegram requires digital subscriptions to be sold in Stars, and settles the money
          with you directly. Nothing touches this app.
        </div>

        {(status?.tiers ?? []).map((t) => (
          <div
            key={t.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '10px 13px',
              marginBottom: 8,
              background: 'var(--surface-tint)',
              border: '2px solid var(--border)',
              borderRadius: 14
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ font: "700 14px 'Quicksand'", color: 'var(--ink)' }}>{t.name}</div>
              {t.description && (
                <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>{t.description}</div>
              )}
            </div>
            <span style={{ font: "700 13px 'Quicksand'", color: 'var(--accent-deep)' }}>
              {t.stars} ⭐ / {t.period_days}d
            </span>
            <div style={secondaryButtonSmall} onClick={() => void run('tier', () => deleteCommunityTier(t.id))}>
              Remove
            </div>
          </div>
        ))}

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end', marginTop: 12 }}>
          <div style={{ flex: '2 1 180px' }}>
            <label style={label}>Tier name</label>
            <input value={tierName} onChange={(e) => setTierName(e.target.value)} placeholder="Inner circle" style={textInput} />
          </div>
          <div style={{ flex: '1 1 100px' }}>
            <label style={label}>Stars</label>
            <input type="number" min={1} value={tierStars} onChange={(e) => setTierStars(Number(e.target.value))} style={textInput} />
          </div>
          <div style={{ flex: '1 1 100px' }}>
            <label style={label}>Days</label>
            <input type="number" min={1} value={tierDays} onChange={(e) => setTierDays(Number(e.target.value))} style={textInput} />
          </div>
          <div
            style={{ ...primaryButtonSmall, opacity: !tierName.trim() ? 0.6 : 1 }}
            onClick={
              !tierName.trim()
                ? undefined
                : () =>
                    void run('tier', async () => {
                      await saveCommunityTier({
                        name: tierName.trim(),
                        stars: tierStars,
                        periodDays: tierDays,
                        description: tierDesc.trim()
                      })
                      setTierName('')
                      setTierDesc('')
                    })
            }
          >
            Add tier
          </div>
        </div>
        {tierDays !== 30 && (
          <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 8 }}>
            Telegram only auto-renews 30-day subscriptions. At {tierDays} days this is charged once and the member is
            removed when it lapses.
          </div>
        )}
      </div>

      {/* ---------------------------------------------------------------- step 4 */}
      <div style={{ ...card, marginBottom: 14, opacity: step >= 4 ? 1 : 0.55 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <StepBadge n={4} />
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)' }}>Share your links</div>
        </div>
        <div style={{ font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
          The paid link is a Telegram subscription: Telegram takes the Stars, admits the member, renews them monthly
          and removes them if they stop paying. Nothing to approve by hand.
        </div>

        {[
          {
            title: 'Paid channel subscription',
            link: status?.gatedInviteLink,
            action: () => run('gated', createGatedInvite),
            busyKey: 'gated',
            enabled: Boolean(status?.gatedLinked && status?.tiers.length)
          },
          {
            title: 'Open group invite (optional)',
            link: status?.inviteLink,
            action: () => run('invite', createCommunityInvite),
            busyKey: 'invite',
            enabled: Boolean(status?.groupLinked)
          }
        ].map((row) => (
          <div key={row.title} style={{ marginBottom: 12 }}>
            <label style={label}>{row.title}</label>
            {row.link ? (
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <code
                  style={{
                    flex: 1,
                    minWidth: 240,
                    font: "600 12.5px ui-monospace, monospace",
                    background: 'var(--surface-tint)',
                    border: '2px solid var(--border)',
                    borderRadius: 12,
                    padding: '9px 12px',
                    color: 'var(--ink-body)',
                    overflowWrap: 'anywhere'
                  }}
                >
                  {row.link}
                </code>
                <div style={secondaryButtonSmall} onClick={() => void navigator.clipboard.writeText(row.link as string)}>
                  Copy
                </div>
              </div>
            ) : (
              <div
                style={{ ...primaryButtonSmall, opacity: !row.enabled || busy === row.busyKey ? 0.6 : 1, width: 'fit-content' }}
                onClick={!row.enabled || busy === row.busyKey ? undefined : () => void row.action()}
              >
                {busy === row.busyKey ? 'Creating…' : 'Create link'}
              </div>
            )}
          </div>
        ))}
      </div>

      </>
      )}

      {/* Subscription figures, the member roll and the bot's composer belong to the two
          chats the bot runs. The account tabs are a different job — creating and populating
          groups as yourself — so they don't carry them. */}
      {view !== 'groups' && view !== 'account' && (
      <>
      {/* ---------------------------------------------------------------- members */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
        {[
          ['Active members', status?.revenue.activeMembers ?? 0],
          ['Payments', status?.revenue.payments ?? 0],
          ['Stars earned', status?.revenue.totalStars ?? 0]
        ].map(([lbl, val]) => (
          <div key={String(lbl)} style={{ ...card, flex: '1 1 150px', padding: '16px 18px' }}>
            <div style={{ font: "700 26px 'Kalam'", color: 'var(--accent-deep)' }}>{String(val)}</div>
            <div style={{ font: "700 11.5px 'Quicksand'", letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
              {String(lbl)}
            </div>
          </div>
        ))}
      </div>

      <div style={{ ...card, marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, gap: 10, flexWrap: 'wrap' }}>
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)' }}>Members</div>
          <div style={secondaryButtonSmall} onClick={() => void run('sweep', sweepCommunity)}>
            {busy === 'sweep' ? 'Removing…' : 'Remove lapsed now'}
          </div>
        </div>
        {members.length === 0 && (
          <div style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-faint)' }}>
            Nobody yet. Share your invite link to get the first request.
          </div>
        )}
        {members.map((m) => (
          <div
            key={m.telegram_id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '9px 0',
              borderTop: '2px dashed var(--border-soft)',
              flexWrap: 'wrap'
            }}
          >
            <span style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)', flex: 1, minWidth: 140 }}>
              {m.username ? `@${m.username}` : m.first_name || m.telegram_id}
            </span>
            <span
              style={{
                font: "700 11px 'Quicksand'",
                letterSpacing: '.05em',
                textTransform: 'uppercase',
                color: m.status === 'active' ? 'var(--tool-distribute)' : 'var(--ink-faint)'
              }}
            >
              {m.status}
            </span>
            {m.expires_at && (
              <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>
                until {m.expires_at.slice(0, 10)}
              </span>
            )}
          </div>
        ))}
      </div>

      {/* ---------------------------------------------------------------- broadcast */}
      <div style={{ ...card }}>
        <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 4 }}>Post to your community</div>
        <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
          General posts go to the open group. Members-only posts go to the paid channel — a separate chat, because
          Telegram shows every message to everyone in a room and offers no way to hide one from some of them.
        </div>
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={4}
          placeholder="This week's teardown is up…"
          style={{ ...textarea, marginBottom: 12 }}
        />
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, font: "600 13px 'Quicksand'", color: 'var(--ink-body)', cursor: 'pointer' }}>
            <input type="checkbox" checked={gated} onChange={(e) => setGated(e.target.checked)} />
            Members only (paid channel)
          </label>
          <div
            style={{ ...primaryButtonSmall, opacity: !message.trim() || busy === 'send' ? 0.6 : 1 }}
            onClick={
              !message.trim() || busy === 'send'
                ? undefined
                : () =>
                    void run('send', async () => {
                      const res = await broadcastCommunity(message.trim(), gated)
                      setSentNote(res.sentTo === 'group' ? 'Posted to the open group.' : 'Posted to the paid channel.')
                      setMessage('')
                    })
            }
          >
            {busy === 'send' ? 'Posting…' : gated ? 'Post to paid channel' : 'Post to open group'}
          </div>
          {sentNote && <span style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)' }}>{sentNote}</span>}
        </div>
      </div>
      </>
      )}
    </div>
  )
}
