import { useEffect, useRef, useState, type CSSProperties } from 'react'
import {
  acceptMastodonPolicy,
  composeMastodonStatus,
  deleteMastodonStatus,
  getMastodonFeed,
  getMastodonSession,
  getMastodonTerms,
  getMastodonThread,
  markMastodonNotificationsRead,
  mastodonAccountAction,
  mastodonStatusAction,
  mastodonTagAction,
  revokeMastodonPolicy,
  searchMastodon,
  type MastodonAccountAction,
  type MastodonFeedName,
  type MastodonFeedPost,
  type MastodonRelationship,
  type MastodonSearchResult,
  type MastodonSession,
  type MastodonMedia,
  type MastodonStatusAction,
  type MastodonTerms,
  type PostMediaItem
} from '../api/client'
import PostMedia from './PostMedia'
import { mastodonThemeCss } from './mastodonTheme'
import { useAppStore } from '../state/store'
import { chip, label, primaryButtonSmall, secondaryButtonSmall, segGroup, segItem, select, textarea, textInput } from '../styles/styleKit'

/**
 * Engage, Mastodon side.
 *
 * Three layers, in the order they appear, and the order matters:
 *
 *   1. The community's terms. Mastodon has no central terms of service — each
 *      server writes its own, and they genuinely differ about AI, automation and
 *      commercial posting. So what this community allows and forbids sits *above*
 *      the window into it, in the server's own words, and the actions below stay
 *      locked until it has been read. The backend enforces that too; this screen
 *      is not trusted to.
 *   2. The instance itself, embedded. A Mastodon server sends
 *      frame-ancestors 'none', so this is an Electron <webview> rather than an
 *      iframe — the real client, with its own login session, for everything a
 *      native panel will never cover.
 *   3. The common activities, natively: post with a visibility and a content
 *      warning, reply, boost, favourite, bookmark, mute a thread, pin, delete,
 *      follow/mute/block an account, follow a hashtag, search, and work through
 *      notifications.
 */

type Busy = string

const FEEDS: { key: MastodonFeedName; label: string; needsToken: boolean }[] = [
  { key: 'home', label: 'Home', needsToken: true },
  { key: 'notifications', label: 'Notifications', needsToken: true },
  { key: 'local', label: 'This server', needsToken: false },
  { key: 'public', label: 'Federated', needsToken: false },
  { key: 'tag', label: 'Hashtag', needsToken: false },
  { key: 'bookmarks', label: 'Bookmarks', needsToken: true },
  { key: 'favourites', label: 'Favourites', needsToken: true }
]

/** Mastodon's notification types, as a sentence. */
const REASON_VERB: Record<string, string> = {
  mention: 'mentioned you',
  reblog: 'boosted your post',
  favourite: 'favourited your post',
  follow: 'followed you',
  follow_request: 'asked to follow you',
  poll: 'a poll you were in has ended',
  status: 'posted',
  update: 'edited a post you boosted',
  'admin.sign_up': 'signed up on your server',
  'admin.report': 'filed a moderation report',
  severed_relationships: 'relationships were severed by a server block',
  moderation_warning: 'you have a moderation warning'
}

const VISIBILITY_LABEL: Record<string, string> = {
  public: 'Public — anyone, and it appears in public timelines',
  unlisted: 'Unlisted — public but kept out of timelines',
  private: 'Followers only',
  direct: 'Direct — only the people you mention'
}

const VISIBILITY_SHORT: Record<string, string> = {
  public: 'Public',
  unlisted: 'Unlisted',
  private: 'Followers',
  direct: 'Direct'
}

/** Sections of the Mastodon web UI the embed's toolbar jumps to. */
const EMBED_LINKS: { label: string; path: string }[] = [
  { label: 'Home', path: '/home' },
  { label: 'Notifications', path: '/notifications' },
  { label: 'This server', path: '/public/local' },
  { label: 'Explore', path: '/explore' },
  { label: 'Messages', path: '/conversations' }
]

/**
 * <webview> is a DOM element Electron registers in the renderer, not a React
 * component, so React's JSX types have never heard of it. Casting the tag name
 * keeps the props we pass checked without an ambient declaration that would leak
 * `webview` into every file in the app.
 */
type WebviewHandle = HTMLElement & {
  reload: () => void
  goBack: () => void
  canGoBack: () => boolean
  getURL: () => string
  /** Blink-level stylesheet injection. Returns a key that removeInsertedCSS undoes. */
  insertCSS: (css: string) => Promise<string>
  removeInsertedCSS: (key: string) => Promise<void>
}

const WebView = 'webview' as unknown as React.FC<{
  ref?: React.Ref<WebviewHandle>
  src: string
  partition: string
  allowpopups?: string
  style?: CSSProperties
}>

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const diffMin = Math.floor((Date.now() - then) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  return `${Math.floor(diffHr / 24)}d ago`
}

function countLabel(text: string, count: number): string {
  return count > 0 ? `${text} ${count}` : text
}

function actionButton(active = false, disabled = false): CSSProperties {
  return {
    ...secondaryButtonSmall,
    padding: '6px 11px',
    font: "700 11.5px 'Quicksand'",
    background: active ? 'var(--accent-soft-bg)' : 'var(--surface)',
    color: active ? 'var(--accent-deep)' : 'var(--ink-muted)',
    opacity: disabled ? 0.48 : 1,
    pointerEvents: disabled ? 'none' : 'auto',
    boxShadow: 'none'
  }
}

const panel: CSSProperties = {
  background: 'var(--surface)',
  border: '2.5px solid var(--border)',
  borderRadius: 20,
  padding: 18,
  boxShadow: 'var(--shadow-md)'
}

const eyebrow: CSSProperties = {
  font: "700 11.5px 'Quicksand'",
  letterSpacing: '.05em',
  textTransform: 'uppercase',
  color: 'var(--accent-deep)',
  marginBottom: 8
}

const muted: CSSProperties = { font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)' }

/**
 * Mastodon's attachment shape -> the shared one PostMedia renders.
 *
 * The only real difference is the field name (`type` vs `kind`); the vocabularies
 * already agree on image/video/gifv/audio. Mastodon serves video as a direct MP4,
 * so `isHls` stays false and the player needs no hls.js for this side.
 */
function toPostMediaItem(m: MastodonMedia): PostMediaItem {
  const known = ['image', 'video', 'gifv', 'audio'] as const
  const kind = (known as readonly string[]).includes(m.type)
    ? (m.type as PostMediaItem['kind'])
    : 'unknown'
  return {
    kind,
    url: m.url || m.previewUrl,
    previewUrl: m.previewUrl || m.url,
    description: m.description,
    isHls: false
  }
}

// ---------------------------------------------------------------------------
// The terms region — above the embed, deliberately
// ---------------------------------------------------------------------------

function CommunityTerms({
  terms,
  busy,
  onAccept,
  onRevoke,
  onReload
}: {
  terms: MastodonTerms
  busy: boolean
  onAccept: () => void
  onRevoke: () => void
  onReload: () => void
}): React.JSX.Element {
  // Collapsed once accepted — it has been read, and a wall of rules above every
  // visit stops being information and becomes furniture. One click brings it back,
  // and a rule change upstream forces it open again.
  const [open, setOpen] = useState(!terms.accepted)
  const [showAbout, setShowAbout] = useState(false)

  useEffect(() => {
    if (!terms.accepted || terms.changedSinceAccepted) setOpen(true)
  }, [terms.accepted, terms.changedSinceAccepted, terms.policyHash])

  const header = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
      {terms.thumbnail ? (
        <img
          src={terms.thumbnail}
          alt=""
          style={{
            width: 38,
            height: 38,
            borderRadius: '52% 48% 55% 45%',
            border: '2px solid var(--border)',
            objectFit: 'cover',
            flexShrink: 0
          }}
        />
      ) : (
        <span
          style={{
            width: 38,
            height: 38,
            borderRadius: '52% 48% 55% 45%',
            background: 'var(--tool-mastodon)',
            border: '2px solid var(--border)',
            flexShrink: 0
          }}
        />
      )}
      <div style={{ flex: 1, minWidth: 180 }}>
        <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)' }}>
          House rules of {terms.title}
        </div>
        <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 1 }}>
          {terms.instance} · {terms.ruleCount} published rules
          {terms.accepted && terms.acceptedAt
            ? ` · read on ${new Date(terms.acceptedAt).toLocaleDateString()}`
            : ''}
          {terms.version ? ` · Mastodon ${terms.version}` : ''}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {terms.accepted && (
          <span
            style={{
              ...chip(true),
              cursor: 'default',
              padding: '6px 12px',
              font: "700 11.5px 'Quicksand'"
            }}
          >
            Accepted ✓
          </span>
        )}
        <div style={actionButton(false, false)} onClick={() => setOpen((v) => !v)}>
          {open ? 'Hide rules' : 'Read rules'}
        </div>
      </div>
    </div>
  )

  return (
    <div
      style={{
        ...panel,
        background: 'var(--surface-paper)',
        border: '2.5px solid var(--border-paper)',
        boxShadow: 'var(--shadow-paper)',
        marginBottom: 14
      }}
    >
      {header}

      {terms.changedSinceAccepted && (
        <div
          style={{
            marginTop: 12,
            background: 'var(--accent-soft-bg)',
            border: '2px solid var(--border)',
            borderRadius: 14,
            padding: '10px 13px',
            font: "700 12.5px/1.6 'Quicksand'",
            color: 'var(--danger-ink)'
          }}
        >
          {terms.instance} has edited its rules since you accepted them. Nothing here will post or
          interact until you read the current version.
        </div>
      )}

      {open && (
        <>
          {terms.description && (
            <div style={{ ...muted, marginTop: 12, whiteSpace: 'pre-wrap' }}>{terms.description}</div>
          )}

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
              gap: 12,
              marginTop: 14
            }}
          >
            <div
              style={{
                background: 'var(--surface)',
                border: '2px solid var(--border)',
                borderRadius: 14,
                padding: '12px 14px'
              }}
            >
              <div style={eyebrow}>What this server lets you do</div>
              {terms.limits.map((l) => (
                <div key={l.label} style={{ display: 'flex', gap: 8, marginBottom: 5 }}>
                  <span style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)', minWidth: 108 }}>
                    {l.label}
                  </span>
                  <span style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)', flex: 1 }}>
                    {l.value}
                  </span>
                </div>
              ))}
            </div>

            <div
              style={{
                background: 'var(--tip-bg)',
                border: '2px solid var(--border)',
                borderRadius: 14,
                padding: '12px 14px'
              }}
            >
              <div style={{ ...eyebrow, color: 'var(--ink)' }}>What it asks of you</div>
              {terms.requires.length === 0 ? (
                <div style={muted}>
                  Nothing this server publishes speaks to AI, automation or commercial posting
                  specifically. Its conduct rules still apply — they are below, in full.
                </div>
              ) : (
                <ul style={{ margin: 0, paddingLeft: 17 }}>
                  {terms.requires.map((r) => (
                    <li key={r} style={{ ...muted, marginBottom: 4 }}>
                      {r}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div style={{ marginTop: 16 }}>
            <div style={eyebrow}>Its rules, word for word</div>
            {terms.topics.length === 0 && (
              <div style={muted}>
                This server publishes no rules through its API. That is not the same as having none —
                check its About page before you post.
              </div>
            )}
            {terms.topics.map((topic) => (
              <div key={topic.topic} style={{ marginBottom: 12 }}>
                <div
                  style={{
                    font: "700 11.5px 'Quicksand'",
                    letterSpacing: '.04em',
                    textTransform: 'uppercase',
                    color: 'var(--ink-faint)',
                    marginBottom: 5
                  }}
                >
                  {topic.topic}
                </div>
                {topic.rules.map((rule) => (
                  <div
                    key={`${topic.topic}-${rule.id}-${rule.text}`}
                    style={{
                      padding: '8px 12px',
                      marginBottom: 5,
                      borderRadius: 10,
                      background: 'var(--surface)',
                      borderLeft: '3px solid var(--border-soft)'
                    }}
                  >
                    <div style={{ font: "700 12.5px/1.5 'Quicksand'", color: 'var(--ink)' }}>{rule.text}</div>
                    {rule.hint && <div style={{ ...muted, marginTop: 2 }}>{rule.hint}</div>}
                  </div>
                ))}
              </div>
            ))}
          </div>

          {terms.extendedDescription && (
            <div style={{ marginTop: 6 }}>
              <div
                style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer' }}
                onClick={() => setShowAbout((v) => !v)}
              >
                {showAbout ? '▾' : '▸'} Its About page — often where the restriction that actually
                affects you lives
              </div>
              {showAbout && (
                <div
                  style={{
                    marginTop: 8,
                    maxHeight: 280,
                    overflowY: 'auto',
                    font: "600 12px/1.6 'Quicksand'",
                    color: 'var(--ink-muted)',
                    background: 'var(--surface)',
                    border: '2px solid var(--border)',
                    borderRadius: 12,
                    padding: '12px 14px',
                    whiteSpace: 'pre-wrap'
                  }}
                >
                  {terms.extendedDescription}
                </div>
              )}
            </div>
          )}

          <div
            style={{
              borderTop: '2px dashed var(--border-soft)',
              marginTop: 14,
              paddingTop: 12,
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              flexWrap: 'wrap'
            }}
          >
            <div style={{ flex: 1, minWidth: 220, font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-faint)' }}>
              {terms.accepted
                ? 'Accepted against this exact wording. If the server edits its rules, everything here stops until you have read the new version.'
                : 'Accepting records that you read this version. Following them is on you — this can show you what a server says, not keep you out of trouble with it.'}
              {terms.contactEmail ? ` Moderators: ${terms.contactEmail}.` : ''}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <div style={actionButton(false, busy)} onClick={busy ? undefined : onReload}>
                Re-check
              </div>
              <div
                style={actionButton(false, busy)}
                onClick={busy ? undefined : () => void window.api.openExternal(terms.aboutUrl)}
              >
                Full page ↗
              </div>
              {terms.accepted ? (
                <div style={actionButton(false, busy)} onClick={busy ? undefined : onRevoke}>
                  Withdraw
                </div>
              ) : (
                <div
                  style={{ ...primaryButtonSmall, opacity: busy ? 0.6 : 1 }}
                  onClick={busy ? undefined : onAccept}
                >
                  {busy ? 'Saving…' : "I've read these"}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// The embed
// ---------------------------------------------------------------------------

function InstanceEmbed({
  host,
  path,
  locked,
  pending,
  onPath
}: {
  host: string
  path: string
  locked: boolean
  /** Rules still loading — don't claim they haven't been read yet. */
  pending: boolean
  onPath: (path: string) => void
}): React.JSX.Element {
  const ref = useRef<WebviewHandle | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [themed, setThemed] = useState(() => localStorage.getItem('mraim.mastodonThemed') !== 'off')
  const url = `https://${host}${path}`

  /**
   * Paint the embed in the app's colours.
   *
   * Re-applied on every `dom-ready` because a full page load drops injected CSS, and
   * Mastodon is a single-page app that still does real loads on reload and on sign-in.
   * The insertion key is kept so turning the toggle off can remove exactly what was added
   * rather than reloading the page and losing the user's place.
   */
  useEffect(() => {
    const view = ref.current
    if (!view || locked || collapsed) return

    let key = ''
    let cancelled = false

    async function apply(): Promise<void> {
      if (cancelled || !themed) return
      try {
        key = await view!.insertCSS(await mastodonThemeCss())
      } catch {
        // insertCSS is unavailable until the guest page is attached; the dom-ready
        // listener below covers that case.
      }
    }

    void apply()
    view.addEventListener('dom-ready', apply)
    return () => {
      cancelled = true
      view.removeEventListener('dom-ready', apply)
      if (key) void view.removeInsertedCSS(key).catch(() => {})
    }
  }, [themed, locked, collapsed, host])

  return (
    <div style={{ ...panel, padding: 12, marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: collapsed ? 0 : 10 }}>
        <span style={{ font: "700 13px 'Kalam'", color: 'var(--ink)', marginRight: 4 }}>{host}</span>
        {EMBED_LINKS.map((link) => (
          <div
            key={link.path}
            style={actionButton(path === link.path, locked)}
            onClick={locked ? undefined : () => onPath(link.path)}
          >
            {link.label}
          </div>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <div
            style={actionButton(false, locked)}
            onClick={
              locked
                ? undefined
                : () => {
                    try {
                      ref.current?.reload()
                    } catch {
                      // The webview is not attached yet — nothing to reload.
                    }
                  }
            }
          >
            ↻
          </div>
          <div
            style={actionButton(themed, false)}
            title={
              themed
                ? "Showing this server in the app's colours. Turn off for Mastodon's own theme."
                : "Showing Mastodon's own theme."
            }
            onClick={() => {
              const next = !themed
              setThemed(next)
              localStorage.setItem('mraim.mastodonThemed', next ? 'on' : 'off')
            }}
          >
            Theme
          </div>
          <div style={actionButton(false, false)} onClick={() => void window.api.openExternal(url)}>
            Open in browser ↗
          </div>
          <div style={actionButton(false, false)} onClick={() => setCollapsed((v) => !v)}>
            {collapsed ? 'Show' : 'Hide'}
          </div>
        </div>
      </div>

      {!collapsed &&
        (locked ? (
          <div
            style={{
              border: '2px dashed var(--border)',
              borderRadius: 16,
              padding: 40,
              textAlign: 'center'
            }}
          >
            <div style={{ font: "700 15px 'Kalam'", color: 'var(--ink-fainter-2)' }}>
              {pending ? `Checking where you stand with ${host}…` : `${host} opens here once you have read its rules above.`}
            </div>
            {!pending && (
              <div style={{ ...muted, marginTop: 6 }}>
                They are the server&apos;s own words, not ours, and they are the whole reason this asks
                first.
              </div>
            )}
          </div>
        ) : (
          <>
            <WebView
              ref={ref}
              src={url}
              // Its own cookie jar, kept across restarts, so logging in once is enough
              // and nothing here shares a session with the rest of the app.
              partition="persist:mastodon"
              allowpopups="true"
              style={{
                width: '100%',
                height: 560,
                border: '2px solid var(--border)',
                borderRadius: 14,
                background: 'var(--surface)',
                display: 'inline-flex'
              }}
            />
            <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-fainter)', marginTop: 7 }}>
              This is {host} itself, signed in separately from the app. Links to other servers open in
              your browser.
            </div>
          </>
        ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

function Composer({
  maxCharacters,
  visibilities,
  busy,
  replyTo,
  suggestedVisibility,
  onCancelReply,
  onSubmit
}: {
  maxCharacters: number
  visibilities: string[]
  busy: boolean
  replyTo: MastodonFeedPost | null
  suggestedVisibility: string
  onCancelReply: () => void
  onSubmit: (draft: {
    text: string
    visibility: string
    spoilerText: string
    language: string
    idempotencyKey: string
  }) => Promise<boolean>
}): React.JSX.Element {
  const [text, setText] = useState('')
  const [spoiler, setSpoiler] = useState('')
  const [showSpoiler, setShowSpoiler] = useState(false)
  const [visibility, setVisibility] = useState(suggestedVisibility)
  const [language, setLanguage] = useState('')
  // One key per draft, not per click: a double-submit or a retried timeout then
  // lands on the same post server-side instead of publishing twice.
  const [key, setKey] = useState(() => crypto.randomUUID())

  useEffect(() => {
    if (replyTo) {
      setVisibility(replyTo.visibility === 'direct' ? 'direct' : visibility)
      setText((current) => (current ? current : `@${replyTo.account.acct} `))
      if (replyTo.spoilerText && !spoiler) {
        setShowSpoiler(true)
        setSpoiler(replyTo.spoilerText.startsWith('re: ') ? replyTo.spoilerText : `re: ${replyTo.spoilerText}`)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replyTo?.id])

  const used = text.length + spoiler.length
  const over = used > maxCharacters
  const disabled = busy || !text.trim() || over

  async function submit(): Promise<void> {
    const posted = await onSubmit({
      text,
      visibility,
      spoilerText: showSpoiler ? spoiler : '',
      language,
      idempotencyKey: key
    })
    if (!posted) return
    setText('')
    setSpoiler('')
    setShowSpoiler(false)
    setKey(crypto.randomUUID())
  }

  return (
    <div
      style={{
        background: 'var(--surface-paper)',
        border: '2px solid var(--border-paper)',
        borderRadius: 16,
        padding: 14,
        marginBottom: 14
      }}
    >
      {replyTo && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            marginBottom: 9,
            font: "700 12px 'Quicksand'",
            color: 'var(--accent-deep)'
          }}
        >
          <span style={{ flex: 1 }}>
            Replying to @{replyTo.account.acct} — “{replyTo.text.slice(0, 70)}
            {replyTo.text.length > 70 ? '…' : ''}”
          </span>
          <div style={actionButton(false, busy)} onClick={busy ? undefined : onCancelReply}>
            Cancel reply
          </div>
        </div>
      )}

      {showSpoiler && (
        <input
          value={spoiler}
          onChange={(e) => setSpoiler(e.target.value)}
          placeholder="Content warning — what is behind the fold"
          style={{ ...textInput, marginBottom: 8 }}
        />
      )}

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={replyTo ? 'Write a reply' : `What's happening?`}
        style={{ ...textarea, minHeight: 96, background: 'var(--surface)' }}
      />

      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', marginTop: 10 }}>
        <div style={{ minWidth: 210, flex: '1 1 210px' }}>
          <label style={label}>Who can see it</label>
          <select value={visibility} onChange={(e) => setVisibility(e.target.value)} style={select}>
            {visibilities.map((v) => (
              <option key={v} value={v}>
                {VISIBILITY_LABEL[v] ?? v}
              </option>
            ))}
          </select>
        </div>
        <div style={{ width: 92 }}>
          <label style={label}>Language</label>
          <input
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            maxLength={8}
            placeholder="en"
            style={textInput}
          />
        </div>
        <div style={actionButton(showSpoiler, busy)} onClick={busy ? undefined : () => setShowSpoiler((v) => !v)}>
          {showSpoiler ? 'Drop warning' : 'Content warning'}
        </div>
        <span
          style={{
            font: "700 11.5px 'Quicksand'",
            color: over ? 'var(--danger-ink)' : 'var(--ink-fainter)',
            marginLeft: 'auto'
          }}
        >
          {used}/{maxCharacters}
          {over ? ' — too long for this server' : ''}
        </span>
        <div
          style={{ ...primaryButtonSmall, opacity: disabled ? 0.55 : 1 }}
          onClick={disabled ? undefined : () => void submit()}
        >
          {busy ? 'Posting…' : replyTo ? 'Reply' : 'Post'}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// One post
// ---------------------------------------------------------------------------

function StatusCard({
  post,
  busyKey,
  locked,
  onStatusAction,
  onAccountAction,
  onReply,
  onThread,
  onDelete,
  onOpenInEmbed,
  onHashtag
}: {
  post: MastodonFeedPost
  busyKey: Busy
  locked: boolean
  onStatusAction: (post: MastodonFeedPost, action: MastodonStatusAction) => void
  onAccountAction: (post: MastodonFeedPost, action: MastodonAccountAction) => void
  onReply: (post: MastodonFeedPost) => void
  /** Load the whole conversation this post sits in. */
  onThread: (post: MastodonFeedPost) => void
  onDelete: (post: MastodonFeedPost) => void
  onOpenInEmbed: (post: MastodonFeedPost) => void
  onHashtag: (tag: string) => void
}): React.JSX.Element {
  // A content warning is a request, so it starts honoured rather than expanded.
  const [revealed, setRevealed] = useState(false)
  const busy = (key: string): boolean => busyKey === `${post.id}:${key}`
  const anyBusy = Boolean(busyKey)
  const verb = post.reason ? REASON_VERB[post.reason] ?? post.reason.replace(/[._]/g, ' ') : null
  const isStatus = Boolean(post.id)
  const rel = post.relationship
  const hidden = Boolean(post.spoilerText) && !revealed

  return (
    <div
      style={{
        display: 'flex',
        gap: 12,
        background: 'var(--surface)',
        border: '2px solid var(--border)',
        borderRadius: 16,
        padding: '13px 16px',
        opacity: post.isRead === true ? 0.74 : 1
      }}
    >
      {post.account.avatar ? (
        <img
          src={post.account.avatar}
          alt=""
          style={{
            width: 38,
            height: 38,
            borderRadius: '52% 48% 55% 45%',
            border: '2px solid var(--border)',
            objectFit: 'cover',
            flexShrink: 0
          }}
        />
      ) : (
        <span
          style={{
            width: 38,
            height: 38,
            borderRadius: '52% 48% 55% 45%',
            background: 'var(--tool-mastodon)',
            border: '2px solid var(--border)',
            flexShrink: 0
          }}
        />
      )}

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ font: "700 14px 'Kalam'", color: 'var(--ink)' }}>
            {post.account.displayName || post.account.acct}
          </span>
          <span
            style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', cursor: 'pointer' }}
            onClick={() => onOpenInEmbed(post)}
            title="Open this account in the embed"
          >
            @{post.account.acct}
          </span>
          {post.account.bot && <span style={{ font: "700 10.5px 'Quicksand'", color: 'var(--ink-fainter)' }}>BOT</span>}
          <span style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-fainter)', marginLeft: 'auto' }}>
            {VISIBILITY_SHORT[post.visibility] ?? post.visibility} · {timeAgo(post.createdAt)}
          </span>
        </div>

        {verb && (
          <div style={{ font: "700 11.5px 'Quicksand'", color: 'var(--accent-deep)', margin: '2px 0' }}>{verb}</div>
        )}
        {post.boostedBy && (
          <div style={{ font: "700 11.5px 'Quicksand'", color: 'var(--ink-faint)', margin: '2px 0' }}>
            boosted by @{post.boostedBy}
          </div>
        )}
        {rel?.blockedBy && (
          <div style={{ font: "700 11px 'Quicksand'", color: 'var(--danger-ink)', margin: '2px 0' }}>
            This account blocks you.
          </div>
        )}

        {post.spoilerText && (
          <div style={{ marginTop: 5, display: 'flex', gap: 9, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ font: "700 12.5px 'Quicksand'", color: 'var(--ink)' }}>⚠ {post.spoilerText}</span>
            <div style={actionButton(false, false)} onClick={() => setRevealed((v) => !v)}>
              {revealed ? 'Hide' : 'Show anyway'}
            </div>
          </div>
        )}

        {!hidden && post.text && (
          <div style={{ whiteSpace: 'pre-wrap', font: "600 13px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
            {post.text}
          </div>
        )}
        {!hidden && !post.text && isStatus && (
          <div style={{ ...muted, marginTop: 4 }}>{post.media.length ? 'Media only.' : 'No text.'}</div>
        )}

        {!hidden && post.media.length > 0 && (
          <PostMedia media={post.media.map(toPostMediaItem)} sensitive={post.sensitive} revealed={revealed} />
        )}

        {post.hashtags.length > 0 && (
          <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            {post.hashtags.slice(0, 8).map((t) => (
              <span
                key={t}
                style={{ ...chip(false), padding: '4px 10px', font: "700 11px 'Quicksand'" }}
                onClick={() => onHashtag(t)}
              >
                #{t}
              </span>
            ))}
          </div>
        )}

        {isStatus && (
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <div style={actionButton(false, locked || anyBusy)} onClick={locked || anyBusy ? undefined : () => onReply(post)}>
              {countLabel('Reply', post.replies)}
            </div>
            <div
              style={actionButton(post.favourited, locked || anyBusy || busy('favourite'))}
              onClick={
                locked || anyBusy
                  ? undefined
                  : () => onStatusAction(post, post.favourited ? 'unfavourite' : 'favourite')
              }
            >
              {countLabel(post.favourited ? 'Favourited' : 'Favourite', post.favourites)}
            </div>
            <div
              style={actionButton(post.reblogged, locked || anyBusy || busy('reblog'))}
              onClick={
                locked || anyBusy ? undefined : () => onStatusAction(post, post.reblogged ? 'unreblog' : 'reblog')
              }
              title={
                post.visibility === 'private' || post.visibility === 'direct'
                  ? 'Mastodon will refuse to boost a followers-only or direct post'
                  : 'Boost'
              }
            >
              {countLabel(post.reblogged ? 'Boosted' : 'Boost', post.reblogs)}
            </div>
            <div
              style={actionButton(post.bookmarked, locked || anyBusy || busy('bookmark'))}
              onClick={
                locked || anyBusy
                  ? undefined
                  : () => onStatusAction(post, post.bookmarked ? 'unbookmark' : 'bookmark')
              }
            >
              {post.bookmarked ? 'Bookmarked' : 'Bookmark'}
            </div>
            <div
              style={actionButton(post.muted, locked || anyBusy || busy('mute'))}
              onClick={locked || anyBusy ? undefined : () => onStatusAction(post, post.muted ? 'unmute' : 'mute')}
            >
              {post.muted ? 'Unmute thread' : 'Mute thread'}
            </div>
            {post.isOwn && (
              <div
                style={actionButton(post.pinned, locked || anyBusy || busy('pin'))}
                onClick={locked || anyBusy ? undefined : () => onStatusAction(post, post.pinned ? 'unpin' : 'pin')}
              >
                {post.pinned ? 'Unpin' : 'Pin to profile'}
              </div>
            )}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
          {!post.isOwn && post.account.id && (
            <>
              <div
                style={actionButton(Boolean(rel?.following), locked || anyBusy)}
                onClick={
                  locked || anyBusy
                    ? undefined
                    : () => onAccountAction(post, rel?.following ? 'unfollow' : 'follow')
                }
              >
                {rel?.following ? 'Following' : rel?.requested ? 'Requested' : 'Follow'}
                {rel?.followedBy ? ' · follows you' : ''}
              </div>
              <div
                style={actionButton(Boolean(rel?.muting), locked || anyBusy)}
                onClick={locked || anyBusy ? undefined : () => onAccountAction(post, rel?.muting ? 'unmute' : 'mute')}
              >
                {rel?.muting ? 'Unmute' : 'Mute'}
              </div>
              <div
                style={actionButton(Boolean(rel?.blocking), locked || anyBusy)}
                onClick={
                  locked || anyBusy ? undefined : () => onAccountAction(post, rel?.blocking ? 'unblock' : 'block')
                }
              >
                {rel?.blocking ? 'Unblock' : 'Block'}
              </div>
            </>
          )}
          {isStatus && (post.replies > 0 || post.inReplyToId) && (
            <div style={actionButton(false, anyBusy)} onClick={anyBusy ? undefined : () => onThread(post)}>
              Conversation
            </div>
          )}
          {isStatus && (
            <div style={actionButton(false, false)} onClick={() => onOpenInEmbed(post)}>
              View in embed
            </div>
          )}
          {post.url && (
            <div style={actionButton(false, false)} onClick={() => void window.api.openExternal(post.url)}>
              Open ↗
            </div>
          )}
          {post.isOwn && isStatus && (
            <div style={actionButton(false, locked || anyBusy)} onClick={locked || anyBusy ? undefined : () => onDelete(post)}>
              Delete
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// The screen
// ---------------------------------------------------------------------------

export default function MastodonEngage(): React.JSX.Element {
  const goSettings = useAppStore((s) => s.goSettings)

  const [session, setSession] = useState<MastodonSession | null>(null)
  const [terms, setTerms] = useState<MastodonTerms | null>(null)
  const [feed, setFeed] = useState<MastodonFeedName>('home')
  const [posts, setPosts] = useState<MastodonFeedPost[]>([])
  const [nextMaxId, setNextMaxId] = useState('')
  const [lastReadId, setLastReadId] = useState('')
  const [tagDraft, setTagDraft] = useState('')
  const [tag, setTag] = useState('')
  const [tagFollowing, setTagFollowing] = useState<boolean | null>(null)
  const [searchDraft, setSearchDraft] = useState('')
  const [searchResult, setSearchResult] = useState<MastodonSearchResult | null>(null)
  // Set while the feed list is showing one conversation instead of a timeline.
  const [threadOf, setThreadOf] = useState('')
  const [replyTo, setReplyTo] = useState<MastodonFeedPost | null>(null)
  const [embedPath, setEmbedPath] = useState('/home')

  const [loading, setLoading] = useState(false)
  const [posting, setPosting] = useState(false)
  const [termsBusy, setTermsBusy] = useState(false)
  const [termsLoading, setTermsLoading] = useState(true)
  const [busyKey, setBusyKey] = useState<Busy>('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const locked = !terms?.accepted
  // Never hardcoded except as a last resort: 500 is Mastodon's stock limit, and the
  // instances that matter here disagree with it (hachyderm.io allows 2263).
  const maxCharacters = session?.maxCharacters || 500

  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function refresh(): Promise<void> {
    setError('')
    setTermsLoading(true)
    try {
      const s = await getMastodonSession()
      setSession(s)
      if (!s.instance || !s.reachable) {
        setTerms(null)
        return
      }
      // Three round trips to someone else's server (instance, rules, About), so
      // this is slow enough to need saying out loud.
      setTerms(await getMastodonTerms())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setTermsLoading(false)
    }
  }

  // Load the feed once the gate is open and there is a token to load it with.
  useEffect(() => {
    if (!session?.hasToken || locked) return
    void loadFeed(feed, { reset: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.hasToken, locked])

  async function loadFeed(
    which: MastodonFeedName,
    opts: { reset?: boolean; hashtag?: string } = {}
  ): Promise<void> {
    const nextTag = opts.hashtag ?? tag
    if (which === 'tag' && !nextTag.trim()) {
      setFeed('tag')
      setPosts([])
      return
    }
    setFeed(which)
    setLoading(true)
    setError('')
    if (opts.reset) setThreadOf('')
    try {
      const res = await getMastodonFeed(which, {
        tag: nextTag,
        maxId: opts.reset ? '' : nextMaxId
      })
      setPosts((current) => (opts.reset ? res.posts : [...current, ...res.posts]))
      setNextMaxId(res.nextMaxId)
      setTagFollowing(res.tagFollowing)
      if (which === 'notifications') setLastReadId(res.lastReadId)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      if (opts.reset) setPosts([])
    } finally {
      setLoading(false)
    }
  }

  async function handleAccept(): Promise<void> {
    if (!terms) return
    setTermsBusy(true)
    setError('')
    try {
      await acceptMastodonPolicy(terms.instance, terms.policyHash)
      await refresh()
      setNotice(`Rules accepted for ${terms.instance}.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      // A 409 means the rules moved while they were on screen. Re-reading them is
      // the only honest thing to show next.
      await refresh()
    } finally {
      setTermsBusy(false)
    }
  }

  async function handleRevoke(): Promise<void> {
    if (!terms) return
    if (!window.confirm(`Withdraw your acceptance of ${terms.instance}'s rules? Posting and interacting from here will stop until you accept them again.`)) {
      return
    }
    setTermsBusy(true)
    try {
      await revokeMastodonPolicy(terms.instance)
      setPosts([])
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setTermsBusy(false)
    }
  }

  function replacePost(updated: MastodonFeedPost): void {
    setPosts((current) => current.map((p) => (p.id && p.id === updated.id ? { ...updated, reason: p.reason, notificationId: p.notificationId, isRead: p.isRead } : p)))
  }

  function applyRelationship(rel: MastodonRelationship): void {
    setPosts((current) =>
      current.map((p) => (p.account.id === rel.accountId ? { ...p, relationship: rel } : p))
    )
  }

  async function handleCompose(draft: {
    text: string
    visibility: string
    spoilerText: string
    language: string
    idempotencyKey: string
  }): Promise<boolean> {
    setPosting(true)
    setError('')
    setNotice('')
    try {
      const res = await composeMastodonStatus({ ...draft, inReplyToId: replyTo?.id ?? '' })
      if (replyTo) {
        // The server answers with the reply, not the post replied to, so nudge the
        // parent's count rather than pretending we know its new state.
        const parentId = replyTo.id
        setPosts((current) => current.map((p) => (p.id === parentId ? { ...p, replies: p.replies + 1 } : p)))
        setReplyTo(null)
        setNotice('Reply posted.')
      } else {
        if (res.post) setPosts((current) => [res.post as MastodonFeedPost, ...current])
        setNotice('Posted.')
      }
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setPosting(false)
    }
  }

  async function handleStatusAction(post: MastodonFeedPost, action: MastodonStatusAction): Promise<void> {
    setBusyKey(`${post.id}:${action.replace(/^un/, '')}`)
    setError('')
    try {
      const res = await mastodonStatusAction(post.id, action)
      if (res.post) replacePost(res.post)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyKey('')
    }
  }

  async function handleAccountAction(post: MastodonFeedPost, action: MastodonAccountAction): Promise<void> {
    if (action === 'block' && !window.confirm(`Block @${post.account.acct}? You will disappear from each other's timelines.`)) {
      return
    }
    setBusyKey(`${post.id}:${action}`)
    setError('')
    try {
      const res = await mastodonAccountAction(post.account.id, action)
      if (res.relationship) applyRelationship(res.relationship)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyKey('')
    }
  }

  async function handleDelete(post: MastodonFeedPost): Promise<void> {
    if (!window.confirm('Delete this post from your instance? This cannot be undone.')) return
    setBusyKey(`${post.id}:delete`)
    setError('')
    try {
      await deleteMastodonStatus(post.id)
      setPosts((current) => current.filter((p) => p.id !== post.id))
      setNotice('Deleted.')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyKey('')
    }
  }

  async function handleTagFollow(): Promise<void> {
    if (!tag.trim()) return
    setBusyKey(`tag:${tag}`)
    setError('')
    try {
      const res = await mastodonTagAction(tag, tagFollowing ? 'unfollow' : 'follow')
      setTagFollowing(res.tagFollowing)
      setNotice(res.tagFollowing ? `Following #${tag} — its posts will reach your home timeline.` : `Unfollowed #${tag}.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyKey('')
    }
  }

  async function handleMarkRead(): Promise<void> {
    const newest = posts.find((p) => p.notificationId)?.notificationId
    if (!newest) return
    setBusyKey('notifications:read')
    try {
      await markMastodonNotificationsRead(newest)
      setPosts((current) => current.map((p) => ({ ...p, isRead: p.notificationId ? true : p.isRead })))
      setLastReadId(newest)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyKey('')
    }
  }

  async function openThread(post: MastodonFeedPost): Promise<void> {
    setLoading(true)
    setError('')
    try {
      const t = await getMastodonThread(post.id)
      setPosts([...t.ancestors, t.status, ...t.descendants])
      setThreadOf(post.id)
      setSearchResult(null)
      setNextMaxId('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  function leaveThread(): void {
    setThreadOf('')
    void loadFeed(feed, { reset: true })
  }

  async function handleSearch(): Promise<void> {
    const query = searchDraft.trim()
    if (!query) {
      setSearchResult(null)
      return
    }
    setLoading(true)
    setError('')
    try {
      const res = await searchMastodon(query)
      setSearchResult(res)
      setPosts(res.statuses)
      setNextMaxId('')
      setThreadOf('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  function openHashtag(name: string): void {
    const clean = name.replace(/^#/, '')
    setTag(clean)
    setTagDraft(clean)
    setSearchResult(null)
    void loadFeed('tag', { reset: true, hashtag: clean })
  }

  function openInEmbed(post: MastodonFeedPost): void {
    // Always the local permalink form (/@acct/id): those are the ids this instance
    // issued, so a remote post still resolves — and the embed stays on the server
    // whose rules were accepted rather than wandering onto someone else's.
    setEmbedPath(post.id ? `/@${post.account.acct}/${post.id}` : `/@${post.account.acct}`)
  }

  // --- gates before the screen can do anything ------------------------------

  if (session && !session.instance) {
    return (
      <Notice
        title="Pick your Mastodon server first"
        body="Engage needs to know which instance you're on — it decides the rules, the character limit, and what it will even answer. Set it in the Mastodon Post Creator or Settings."
        actionLabel="Open Settings"
        onAction={goSettings}
      />
    )
  }

  if (session && !session.reachable) {
    return (
      <Notice
        title={`Could not reach ${session.instance}`}
        body={session.detail || 'The server did not answer. It may be down, or the address may be wrong.'}
        actionLabel="Try again"
        onAction={() => void refresh()}
      />
    )
  }

  return (
    <>
      {session?.account && (
        <div style={{ ...muted, marginBottom: 12 }}>
          Signed in as <b>@{session.account.acct}</b> on {session.instance} · {session.account.followers} followers
        </div>
      )}

      {/* 1. What this community allows and forbids — above the embed, on purpose. */}
      {terms ? (
        <CommunityTerms
          terms={terms}
          busy={termsBusy}
          onAccept={handleAccept}
          onRevoke={handleRevoke}
          onReload={() => void refresh()}
        />
      ) : (
        <div
          style={{
            ...panel,
            background: 'var(--surface-paper)',
            border: '2.5px solid var(--border-paper)',
            boxShadow: 'var(--shadow-paper)',
            marginBottom: 14
          }}
        >
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)' }}>
            {termsLoading
              ? `Reading ${session?.instance || 'the server'}’s house rules…`
              : `Could not read ${session?.instance || 'the server'}’s rules`}
          </div>
          <div style={{ ...muted, marginTop: 4 }}>
            {termsLoading
              ? 'Straight from the server: its rules, its About page and the limits it enforces.'
              : error || 'The server did not answer. Nothing here will act on it until its rules can be read.'}
          </div>
          {!termsLoading && (
            <div style={{ ...secondaryButtonSmall, marginTop: 12, display: 'inline-block' }} onClick={() => void refresh()}>
              Try again
            </div>
          )}
        </div>
      )}

      {/* 2. The instance itself. */}
      {session?.instance && (
        <InstanceEmbed
          host={session.instance}
          path={embedPath}
          locked={locked}
          pending={termsLoading && !terms}
          onPath={setEmbedPath}
        />
      )}

      {/* 3. Doing things from here. */}
      {session && !session.hasToken && (
        <Notice
          title="Add an access token to act from here"
          body="Reading and posting through this panel needs a token from your instance: Preferences → Development → New application, with read, write and follow scopes. The embed above works without one — you just log into it directly."
          actionLabel="Open Settings"
          onAction={goSettings}
        />
      )}

      {session?.hasToken && (
        <div style={panel}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
            <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)', flex: 1 }}>
              {!terms && termsLoading
                ? 'Checking the house rules…'
                : locked
                  ? 'Locked until the rules are read'
                  : 'Post, reply, boost, follow'}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {feed === 'notifications' && (
                <div
                  style={actionButton(false, Boolean(busyKey) || loading || !posts.length)}
                  onClick={busyKey || loading ? undefined : () => void handleMarkRead()}
                >
                  Mark read
                </div>
              )}
              <div
                style={actionButton(false, loading || locked)}
                onClick={loading || locked ? undefined : () => void loadFeed(feed, { reset: true })}
              >
                Refresh
              </div>
            </div>
          </div>

          {locked ? (
            <div style={{ ...muted, paddingBottom: 4 }}>
              {!terms && termsLoading
                ? `Asking ${session.instance} what it allows.`
                : `${session.instance} publishes ${terms?.ruleCount ?? 0} rules. Read them above and accept, and this
                   panel turns on — the backend checks the same thing on every request, so nothing here can act on
                   the server before that.`}
            </div>
          ) : (
            <>
              <Composer
                maxCharacters={maxCharacters}
                visibilities={session.visibilities}
                busy={posting}
                replyTo={replyTo}
                suggestedVisibility="public"
                onCancelReply={() => setReplyTo(null)}
                onSubmit={handleCompose}
              />

              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
                <div style={{ ...segGroup, flex: '1 1 380px' }}>
                  {FEEDS.map((f) => (
                    <div
                      key={f.key}
                      style={segItem(feed === f.key && !searchResult && !threadOf)}
                      onClick={() => {
                        setSearchResult(null)
                        void loadFeed(f.key, { reset: true })
                      }}
                    >
                      {f.label}
                    </div>
                  ))}
                </div>
              </div>

              {feed === 'tag' && (
                <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                  <input
                    value={tagDraft}
                    onChange={(e) => setTagDraft(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && openHashtag(tagDraft)}
                    placeholder="hashtag, e.g. rustlang"
                    style={{ ...textInput, flex: '1 1 200px' }}
                  />
                  <div style={secondaryButtonSmall} onClick={() => openHashtag(tagDraft)}>
                    Show
                  </div>
                  {tag && (
                    <div
                      style={{
                        ...(tagFollowing ? primaryButtonSmall : secondaryButtonSmall),
                        opacity: busyKey ? 0.6 : 1
                      }}
                      onClick={busyKey ? undefined : () => void handleTagFollow()}
                    >
                      {tagFollowing ? `Following #${tag}` : `Follow #${tag}`}
                    </div>
                  )}
                </div>
              )}

              <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                <input
                  value={searchDraft}
                  onChange={(e) => setSearchDraft(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && void handleSearch()}
                  placeholder="Search people, posts, hashtags — or paste a link to pull it in"
                  style={{ ...textInput, flex: '1 1 260px' }}
                />
                <div style={secondaryButtonSmall} onClick={() => void handleSearch()}>
                  Search
                </div>
                {searchResult && (
                  <div
                    style={secondaryButtonSmall}
                    onClick={() => {
                      setSearchResult(null)
                      setSearchDraft('')
                      void loadFeed(feed, { reset: true })
                    }}
                  >
                    Clear
                  </div>
                )}
              </div>

              {searchResult && (searchResult.accounts.length > 0 || searchResult.hashtags.length > 0) && (
                <div
                  style={{
                    background: 'var(--surface-paper)',
                    border: '2px solid var(--border-paper)',
                    borderRadius: 14,
                    padding: 12,
                    marginBottom: 12
                  }}
                >
                  {searchResult.accounts.length > 0 && (
                    <>
                      <div style={eyebrow}>People</div>
                      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 10 }}>
                        {searchResult.accounts.map((a) => (
                          <span
                            key={a.id}
                            style={{ ...chip(false), padding: '5px 11px', font: "700 11.5px 'Quicksand'" }}
                            onClick={() => setEmbedPath(`/@${a.acct}`)}
                            title="Open in the embed"
                          >
                            @{a.acct}
                          </span>
                        ))}
                      </div>
                    </>
                  )}
                  {searchResult.hashtags.length > 0 && (
                    <>
                      <div style={eyebrow}>Hashtags</div>
                      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
                        {searchResult.hashtags.map((t) => (
                          <span
                            key={t}
                            style={{ ...chip(false), padding: '5px 11px', font: "700 11.5px 'Quicksand'" }}
                            onClick={() => openHashtag(t)}
                          >
                            #{t}
                          </span>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}

              {threadOf && (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    marginBottom: 10,
                    font: "700 12.5px 'Quicksand'",
                    color: 'var(--accent-deep)'
                  }}
                >
                  <span style={{ flex: 1 }}>
                    One conversation, oldest first — replies to any post in it land in the right place.
                  </span>
                  <div style={actionButton(false, loading)} onClick={loading ? undefined : leaveThread}>
                    Back to {FEEDS.find((f) => f.key === feed)?.label ?? 'feed'}
                  </div>
                </div>
              )}

              {notice && (
                <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent-deep)', marginBottom: 10 }}>
                  {notice}
                </div>
              )}
              {error && (
                <div style={{ font: "700 12.5px/1.6 'Quicksand'", color: 'var(--danger-ink)', marginBottom: 10 }}>
                  {error}
                </div>
              )}

              {loading && posts.length === 0 && (
                <div style={{ ...muted, padding: '26px 0', textAlign: 'center' }}>Loading…</div>
              )}

              {!loading && posts.length === 0 && (
                <div
                  style={{
                    border: '2px dashed var(--border)',
                    borderRadius: 18,
                    padding: 34,
                    textAlign: 'center',
                    font: "700 14px 'Kalam'",
                    color: 'var(--ink-fainter-2)'
                  }}
                >
                  {feed === 'notifications'
                    ? "Nothing new — you're caught up."
                    : feed === 'tag' && !tag
                      ? 'Type a hashtag to see who is posting about it.'
                      : 'Nothing here yet.'}
                </div>
              )}

              {posts.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {posts.map((p, i) => (
                    <StatusCard
                      key={`${p.notificationId || p.id || p.account.id}-${i}`}
                      post={p}
                      busyKey={busyKey}
                      locked={locked}
                      onStatusAction={(post, action) => void handleStatusAction(post, action)}
                      onAccountAction={(post, action) => void handleAccountAction(post, action)}
                      onReply={(post) => setReplyTo(post)}
                      onThread={(post) => void openThread(post)}
                      onDelete={(post) => void handleDelete(post)}
                      onOpenInEmbed={openInEmbed}
                      onHashtag={openHashtag}
                    />
                  ))}
                </div>
              )}

              {nextMaxId && !searchResult && !threadOf && (
                <div
                  style={{ ...secondaryButtonSmall, marginTop: 12, textAlign: 'center', opacity: loading ? 0.6 : 1 }}
                  onClick={loading ? undefined : () => void loadFeed(feed)}
                >
                  {loading ? 'Loading…' : 'Load more'}
                </div>
              )}

              {lastReadId && feed === 'notifications' && (
                <div style={{ font: "600 11px 'Quicksand'", color: 'var(--ink-fainter)', marginTop: 8 }}>
                  Read marker at {lastReadId}. Mastodon tracks one marker per timeline rather than a flag
                  per notification.
                </div>
              )}
            </>
          )}
        </div>
      )}

      {!session && <div style={{ ...muted, padding: '30px 0', textAlign: 'center' }}>Checking your instance…</div>}
      {session && !session.hasToken && error && (
        <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--danger-ink)', marginTop: 10 }}>{error}</div>
      )}
    </>
  )
}

function Notice({
  title,
  body,
  actionLabel,
  onAction
}: {
  title: string
  body: string
  actionLabel?: string
  onAction?: () => void
}): React.JSX.Element {
  return (
    <div
      style={{
        background: 'var(--accent-soft-bg)',
        border: '2px solid var(--border)',
        borderRadius: 16,
        padding: '13px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        marginBottom: 14
      }}
    >
      <div style={{ flex: 1 }}>
        <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>{title}</div>
        <div style={{ ...muted, marginTop: 2 }}>{body}</div>
      </div>
      {actionLabel && onAction && (
        <div style={secondaryButtonSmall} onClick={onAction}>
          {actionLabel}
        </div>
      )}
    </div>
  )
}
