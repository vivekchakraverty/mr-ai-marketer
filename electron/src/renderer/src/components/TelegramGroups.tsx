import { useEffect, useState } from 'react'
import {
  telegramAddBot,
  telegramAddMembers,
  telegramChatInvite,
  telegramChatMembers,
  telegramChats,
  telegramCreateChat,
  telegramLinkChat,
  telegramPost,
  type AddMemberResult,
  type TelegramChat,
  type TelegramChatMember
} from '../api/client'
import { card, label, primaryButtonSmall, secondaryButtonSmall, select, textInput, textarea } from '../styles/styleKit'

/**
 * Groups you own, and the things only an account can do to them: make one, add people to it,
 * post as yourself.
 *
 * The list is filtered to chats where you are the creator or an admin. That is not a
 * convenience — it is the whole point of the screen, and it keeps your private conversations
 * out of a window about running communities.
 *
 * Adding members reports one outcome per person because Telegram gives one per person.
 * People can refuse being added by non-contacts, and an account that adds strangers in bulk
 * gets rate-limited, so a row that says "send them the invite link instead" is the honest
 * answer rather than a failure to work around.
 */
interface Props {
  botUsername: string
  linkedChatId: string
  linkedGatedChatId: string
  onLinked: () => void
}

export default function TelegramGroups({
  botUsername,
  linkedChatId,
  linkedGatedChatId,
  onLinked
}: Props): React.JSX.Element {
  const [chats, setChats] = useState<TelegramChat[]>([])
  const [selected, setSelected] = useState<string>('')
  const [members, setMembers] = useState<TelegramChatMember[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  const [newTitle, setNewTitle] = useState('')
  const [newKind, setNewKind] = useState<'group' | 'channel'>('group')
  const [handles, setHandles] = useState('')
  const [results, setResults] = useState<AddMemberResult[]>([])
  const [invite, setInvite] = useState('')
  const [message, setMessage] = useState('')

  // null while the encrypted store is still being read, so the sign-in prompt doesn't flash
  // in front of someone who is already signed in.
  const [signedIn, setSignedIn] = useState<boolean | null>(null)

  const chat = chats.find((c) => c.id === selected) ?? null

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

  async function loadChats(): Promise<void> {
    const res = await telegramChats()
    setChats(res.chats)
    if (res.chats.length && !res.chats.some((c) => c.id === selected)) setSelected(res.chats[0].id)
  }

  useEffect(() => {
    // Loaded once rather than polled: this is a list of chats you own, and it only changes
    // when you change it.
    void (async () => {
      const { telegram } = await window.api.settings.getAll()
      const ok = Boolean(telegram.session && telegram.apiId)
      setSignedIn(ok)
      if (ok) void run('load', loadChats)
    })()
  }, [])

  useEffect(() => {
    if (!selected) return
    setMembers([])
    setInvite('')
    setResults([])
    void run('members', async () => {
      const res = await telegramChatMembers(selected)
      setMembers(res.members)
    })
  }, [selected])

  if (signedIn === false) {
    return (
      <div style={card}>
        <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 6 }}>Sign in first</div>
        <div style={{ font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-muted)' }}>
          Creating a group and adding people to it are things only a person can do on Telegram, never a bot. Sign in on
          the <strong>Account</strong> tab and this fills with the groups and channels you run.
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* ------------------------------------------------------------- create */}
      <div style={{ ...card, marginBottom: 14 }}>
        <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 4 }}>Create a group</div>
        <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
          Made as a supergroup, so it can have admins, join requests and more than 200 members.
          {botUsername
            ? ` Your bot @${botUsername} is added as an admin at the same time, which is what links it to the rest of this section.`
            : ' Connect a bot in Setup first if you want the paid side wired up automatically.'}
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: '2 1 220px' }}>
            <label style={label}>Name</label>
            <input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="Growth notes"
              style={textInput}
            />
          </div>
          <div style={{ flex: '1 1 140px' }}>
            <label style={label}>Type</label>
            <select value={newKind} onChange={(e) => setNewKind(e.target.value as 'group' | 'channel')} style={select}>
              <option value="group">Group — everyone can talk</option>
              <option value="channel">Channel — you post, members read</option>
            </select>
          </div>
          <div
            style={{ ...primaryButtonSmall, opacity: !newTitle.trim() || busy === 'create' ? 0.6 : 1 }}
            onClick={
              !newTitle.trim() || busy === 'create'
                ? undefined
                : () =>
                    void run('create', async () => {
                      const res = await telegramCreateChat({ title: newTitle.trim(), kind: newKind })
                      setNewTitle('')
                      setNote(
                        res.chat.botAdded
                          ? `Created "${res.chat.title}" and added your bot as an admin.`
                          : `Created "${res.chat.title}".` +
                            (res.chat.botDetail ? ` The bot couldn't be added: ${res.chat.botDetail}` : '')
                      )
                      await loadChats()
                      setSelected(res.chat.id)
                      onLinked()
                    })
            }
          >
            {busy === 'create' ? 'Creating…' : 'Create'}
          </div>
        </div>
        {note && <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 10 }}>{note}</div>}
      </div>

      {/* ------------------------------------------------------------- picker */}
      <div style={{ ...card, marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)' }}>Groups you run</div>
          <div style={secondaryButtonSmall} onClick={() => void run('load', loadChats)}>
            {busy === 'load' ? 'Refreshing…' : 'Refresh'}
          </div>
        </div>
        {chats.length === 0 && busy !== 'load' && (
          <div style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-faint)' }}>
            No groups or channels on this account where you're the creator or an admin — make one above.
          </div>
        )}
        {chats.map((c) => {
          const on = c.id === selected
          const role = c.id === linkedChatId ? 'open group' : c.id === linkedGatedChatId ? 'paid channel' : ''
          return (
            <div
              key={c.id}
              onClick={() => setSelected(c.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 11,
                padding: '11px 13px',
                marginBottom: 8,
                cursor: 'pointer',
                background: on ? 'var(--accent-soft-bg)' : 'var(--surface-tint)',
                border: `2px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
                borderRadius: 14
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>
                  {c.title}
                  {role && (
                    <span style={{ font: "700 10.5px 'Quicksand'", color: 'var(--accent-deep)', marginLeft: 8, letterSpacing: '.05em', textTransform: 'uppercase' }}>
                      {role}
                    </span>
                  )}
                </div>
                <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>
                  {c.kind === 'channel' ? 'Channel' : 'Group'} · {c.participants || members.length || 0} members ·{' '}
                  {c.creator ? 'you created it' : 'you administer it'}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {error && (
        <div style={{ ...card, borderColor: 'var(--danger-ink)', marginBottom: 14, font: "700 13px 'Quicksand'", color: 'var(--danger-ink)' }}>
          {error}
        </div>
      )}

      {chat && (
        <>
          {/* --------------------------------------------------------- members */}
          <div style={{ ...card, marginBottom: 14 }}>
            <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 4 }}>Add people to {chat.title}</div>
            <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
              One @username per line. Telegram lets people refuse being added by anyone who isn't a contact, and
              limits how many strangers an account can add — so anyone it turns down gets the invite link instead.
            </div>
            <textarea
              value={handles}
              onChange={(e) => setHandles(e.target.value)}
              rows={3}
              placeholder={'@someone\n@someone_else'}
              style={{ ...textarea, marginBottom: 10 }}
            />
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <div
                style={{ ...primaryButtonSmall, opacity: !handles.trim() || busy === 'add' ? 0.6 : 1 }}
                onClick={
                  !handles.trim() || busy === 'add'
                    ? undefined
                    : () =>
                        void run('add', async () => {
                          const list = handles
                            .split(/[\s,]+/)
                            .map((h) => h.trim())
                            .filter(Boolean)
                          const res = await telegramAddMembers(chat.id, list)
                          setResults(res.results)
                          if (res.results.some((r) => r.ok)) {
                            setHandles('')
                            const refreshed = await telegramChatMembers(chat.id)
                            setMembers(refreshed.members)
                          }
                        })
                }
              >
                {busy === 'add' ? 'Adding…' : 'Add'}
              </div>
              <div
                style={secondaryButtonSmall}
                onClick={() =>
                  void run('invite', async () => {
                    const res = await telegramChatInvite(chat.id)
                    setInvite(res.inviteLink)
                  })
                }
              >
                {busy === 'invite' ? 'Creating…' : 'Get invite link'}
              </div>
            </div>

            {invite && (
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
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
                  {invite}
                </code>
                <div style={secondaryButtonSmall} onClick={() => void navigator.clipboard.writeText(invite)}>
                  Copy
                </div>
              </div>
            )}

            {results.map((r) => (
              <div
                key={r.handle}
                style={{
                  display: 'flex',
                  gap: 10,
                  alignItems: 'baseline',
                  padding: '7px 0',
                  borderTop: '2px dashed var(--border-soft)'
                }}
              >
                <span style={{ font: "700 13px 'Quicksand'", color: 'var(--ink)', minWidth: 130 }}>{r.handle}</span>
                <span
                  style={{
                    font: "600 12.5px 'Quicksand'",
                    color: r.ok ? 'var(--tool-distribute)' : 'var(--ink-muted)'
                  }}
                >
                  {r.detail}
                </span>
              </div>
            ))}

            {members.length > 0 && (
              <div style={{ marginTop: 14 }}>
                <label style={label}>In this chat</label>
                <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
                  {members.map((m) => (
                    <span
                      key={m.id}
                      style={{
                        font: "600 12px 'Quicksand'",
                        color: 'var(--ink-body)',
                        background: 'var(--surface-tint)',
                        border: '2px solid var(--border)',
                        borderRadius: 999,
                        padding: '5px 11px'
                      }}
                    >
                      {m.username ? `@${m.username}` : m.name || m.id}
                      {m.bot ? ' · bot' : ''}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* --------------------------------------------------------- post + roles */}
          <div style={{ ...card, marginBottom: 14 }}>
            <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 4 }}>Post to {chat.title}</div>
            <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
              Posted as you. The composer on the other tabs posts as the bot — same chat, different name on the message.
            </div>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={3}
              placeholder="Morning, everyone…"
              style={{ ...textarea, marginBottom: 10 }}
            />
            <div
              style={{ ...primaryButtonSmall, opacity: !message.trim() || busy === 'post' ? 0.6 : 1, width: 'fit-content' }}
              onClick={
                !message.trim() || busy === 'post'
                  ? undefined
                  : () =>
                      void run('post', async () => {
                        await telegramPost(chat.id, message.trim())
                        setMessage('')
                        setNote('Posted.')
                      })
              }
            >
              {busy === 'post' ? 'Posting…' : 'Post'}
            </div>
          </div>

          <div style={card}>
            <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 4 }}>Use this chat for…</div>
            <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
              Which of the two roles this chat plays. The bot links the first chat it's added to automatically; this is
              how you change it, or choose between several you own.
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <div
                style={{ ...secondaryButtonSmall, opacity: chat.id === linkedChatId ? 0.6 : 1 }}
                onClick={
                  chat.id === linkedChatId
                    ? undefined
                    : () =>
                        void run('link', async () => {
                          await telegramLinkChat(chat.id, 'open', chat.title)
                          onLinked()
                        })
                }
              >
                {chat.id === linkedChatId ? 'Already the open group' : 'The open group'}
              </div>
              <div
                style={{ ...secondaryButtonSmall, opacity: chat.id === linkedGatedChatId ? 0.6 : 1 }}
                onClick={
                  chat.id === linkedGatedChatId
                    ? undefined
                    : () =>
                        void run('link', async () => {
                          await telegramLinkChat(chat.id, 'paid', chat.title)
                          onLinked()
                        })
                }
              >
                {chat.id === linkedGatedChatId ? 'Already the paid channel' : 'The paid channel'}
              </div>
              {botUsername && (
                <div
                  style={secondaryButtonSmall}
                  onClick={() =>
                    void run('bot', async () => {
                      await telegramAddBot(chat.id)
                      setNote(`@${botUsername} is now an admin of ${chat.title}.`)
                    })
                  }
                >
                  {busy === 'bot' ? 'Adding…' : `Add @${botUsername} as admin`}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
