import { useEffect, useState, type CSSProperties } from 'react'
import {
  createEngagePost,
  deleteEngagePost,
  getEngageNotifications,
  getEngageStatus,
  getEngageTimeline,
  getSuggestedFollows,
  followEngageActor,
  type SuggestedFollowsResponse,
  markEngageNotificationsRead,
  quoteEngagePost,
  replyEngagePost,
  toggleEngageBlock,
  toggleEngageBookmark,
  toggleEngageFollow,
  toggleEngageLike,
  toggleEngageMute,
  toggleEngageRepost,
  toggleEngageThreadMute,
  type EngageActionResponse,
  type EngageActorState,
  type EngageStatus,
  type FeedPost
} from '../api/client'
import MastodonEngage from '../components/MastodonEngage'
import TumblrEngage from '../components/TumblrEngage'
import PostMedia from '../components/PostMedia'
import SuggestedFollows, { type SuggestionRow } from '../components/SuggestedFollows'
import ScreenBackdrop from '../components/ScreenBackdrop'
import { useAppStore } from '../state/store'
import { primaryButtonSmall, secondaryButtonSmall, segGroup, segItem, sectionEyebrow, textarea } from '../styles/styleKit'
import SaveButton from '../components/SaveButton'

type Network = 'bluesky' | 'mastodon' | 'tumblr'
type Feed = 'notifications' | 'timeline' | 'follows'

const NETWORKS: { key: Network; label: string }[] = [
  { key: 'bluesky', label: 'Bluesky' },
  { key: 'mastodon', label: 'Mastodon' },
  { key: 'tumblr', label: 'Tumblr' }
]
type PostAction = 'like' | 'repost' | 'bookmark' | 'threadMute'
type ActorAction = 'follow' | 'mute' | 'block'
type TextAction = 'reply' | 'quote'

const FEEDS: { key: Feed; label: string }[] = [
  { key: 'notifications', label: 'Notifications' },
  { key: 'timeline', label: 'Timeline' },
  // Not a feed of posts, but it belongs beside them: it answers "whose posts should be
  // here" and shares the account this screen is already signed in as.
  { key: 'follows', label: 'People to follow' }
]

const REASON_VERB: Record<string, string> = {
  like: 'liked your post',
  repost: 'reposted your post',
  follow: 'followed you',
  mention: 'mentioned you',
  reply: 'replied to you',
  quote: 'quoted your post',
  'like-via-repost': 'liked your repost',
  'repost-via-repost': 'reposted your repost',
  'subscribed-post': 'posted',
  'starterpack-joined': 'joined from your starter pack',
  verified: 'verified you',
  unverified: 'removed verification'
}

// Bluesky's actual ceiling. This read 3000 until a draft handed over from the
// Social Post composer made the gap matter: nothing validates length on the way
// out (the router just strips and sends), so an over-long post counted up to a
// limit that did not exist and then failed at the API. Typing is capped here and
// the counter goes red for a handed-over draft, which maxLength cannot truncate.
const POST_TEXT_LIMIT = 300

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

function countLabel(label: string, count: number): string {
  return count > 0 ? `${label} ${count}` : label
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

const cardStyle = (read: boolean | null): CSSProperties => ({
  display: 'flex',
  gap: 12,
  background: 'var(--surface)',
  border: '2px solid var(--border)',
  borderRadius: 16,
  padding: '13px 16px',
  opacity: read === false ? 1 : read === true ? 0.74 : 1
})

const composerBox: CSSProperties = {
  background: 'var(--surface-paper)',
  border: '2px solid var(--border-paper)',
  borderRadius: 14,
  padding: 12,
  marginBottom: 14
}

function InlineComposer({
  mode,
  busy,
  onCancel,
  onSubmit
}: {
  mode: TextAction
  busy: boolean
  onCancel: () => void
  onSubmit: (text: string) => Promise<void>
}): React.JSX.Element {
  const [text, setText] = useState('')
  const disabled = busy || !text.trim()
  return (
    <div style={{ marginTop: 10, display: 'grid', gap: 8 }}>
      <textarea
        autoFocus
        value={text}
        maxLength={POST_TEXT_LIMIT}
        onChange={(e) => setText(e.target.value)}
        placeholder={mode === 'reply' ? 'Write a reply' : 'Add a quote'}
        style={{ ...textarea, minHeight: 78, background: 'var(--surface-paper)' }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <span style={{ font: "700 11px 'Quicksand'", color: 'var(--ink-fainter)' }}>
          {text.length}/{POST_TEXT_LIMIT}
        </span>
        <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {/* A reply you spent time on is worth keeping even if you don't send it — and
              nothing else in Engage writes to the Library. */}
          <SaveButton
            tool="Engage"
            title={`${mode === 'reply' ? 'Reply' : 'Quote'} draft`}
            subtitle="Bluesky draft"
            content={text}
          />
          <div style={actionButton(false, busy)} onClick={busy ? undefined : onCancel}>
            Cancel
          </div>
          <div
            style={{ ...primaryButtonSmall, padding: '7px 14px', opacity: disabled ? 0.55 : 1 }}
            onClick={disabled ? undefined : () => void onSubmit(text)}
          >
            {mode === 'reply' ? 'Reply' : 'Quote'}
          </div>
        </span>
      </div>
    </div>
  )
}

function FeedCard({
  post,
  busyKey,
  onPostAction,
  onActorAction,
  onTextAction,
  onDelete
}: {
  post: FeedPost
  busyKey: string
  onPostAction: (action: PostAction, post: FeedPost) => Promise<void>
  onActorAction: (action: ActorAction, post: FeedPost) => Promise<void>
  onTextAction: (action: TextAction, post: FeedPost, text: string) => Promise<void>
  onDelete: (post: FeedPost) => Promise<void>
}): React.JSX.Element {
  const [composer, setComposer] = useState<TextAction | null>(null)
  const verb = post.reason ? REASON_VERB[post.reason] || post.reason : null
  const busy = (key: string): boolean => busyKey === `${post.uri}:${key}`
  const postActionsDisabled = !post.isPost || Boolean(busyKey)
  const replyDisabled = postActionsDisabled || post.viewerReplyDisabled
  const canModerateAuthor = !post.isOwnPost

  async function submitText(action: TextAction, text: string): Promise<void> {
    await onTextAction(action, post, text)
    setComposer(null)
  }

  return (
    <div style={cardStyle(post.isRead)}>
      {post.authorAvatar ? (
        <img
          src={post.authorAvatar}
          alt=""
          style={{
            width: 38,
            height: 38,
            borderRadius: '52% 48% 55% 45%',
            border: '2px solid var(--border)',
            flexShrink: 0,
            objectFit: 'cover'
          }}
        />
      ) : (
        <span
          style={{
            width: 38,
            height: 38,
            borderRadius: '52% 48% 55% 45%',
            background: 'var(--tool-social)',
            border: '2px solid var(--border)',
            flexShrink: 0
          }}
        />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ font: "700 14px 'Kalam'", color: 'var(--ink)' }}>{post.authorName}</span>
          <span
            style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', cursor: 'pointer' }}
            onClick={() => void window.api.openExternal(`https://bsky.app/profile/${post.authorHandle}`)}
          >
            @{post.authorHandle}
          </span>
          <span style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-fainter)', marginLeft: 'auto' }}>
            {timeAgo(post.createdAt)}
          </span>
        </div>
        {verb && <div style={{ font: "700 11.5px 'Quicksand'", color: 'var(--accent-deep)', margin: '2px 0' }}>{verb}</div>}
        {post.authorMuted && (
          <div style={{ font: "700 11px 'Quicksand'", color: 'var(--ink-fainter)', margin: '2px 0' }}>Muted account</div>
        )}
        {post.text ? (
          <div style={{ whiteSpace: 'pre-wrap', font: "600 13px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 3 }}>
            {post.text}
          </div>
        ) : (
          <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-fainter)', marginTop: 3 }}>
            {post.isPost ? (post.media.length ? 'Media only.' : 'Post has no text.') : 'Profile activity'}
          </div>
        )}

        {/* Bluesky has no per-post sensitive flag the way Mastodon does — content
            filtering there is label-based and applied by the viewer's moderation
            prefs, which this panel does not read. So media renders unblurred. */}
        <PostMedia media={post.media} />

        <div style={{ display: 'flex', gap: 14, marginTop: 7, font: "700 11px 'Quicksand'", color: 'var(--ink-fainter)', flexWrap: 'wrap' }}>
          {post.replies > 0 && <span>{post.replies} replies</span>}
          {post.reposts > 0 && <span>{post.reposts} reposts</span>}
          {post.quotes > 0 && <span>{post.quotes} quotes</span>}
          {post.likes > 0 && <span>{post.likes} likes</span>}
          {post.bookmarks > 0 && <span>{post.bookmarks} saves</span>}
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
          <div
            title={post.viewerReplyDisabled ? 'Replies are disabled for this post' : 'Reply'}
            style={actionButton(false, replyDisabled)}
            onClick={replyDisabled ? undefined : () => setComposer(composer === 'reply' ? null : 'reply')}
          >
            {countLabel('Reply', post.replies)}
          </div>
          <div
            style={actionButton(Boolean(post.viewerLike), postActionsDisabled || busy('like'))}
            onClick={postActionsDisabled ? undefined : () => void onPostAction('like', post)}
          >
            {countLabel(post.viewerLike ? 'Liked' : 'Like', post.likes)}
          </div>
          <div
            style={actionButton(Boolean(post.viewerRepost), postActionsDisabled || busy('repost'))}
            onClick={postActionsDisabled ? undefined : () => void onPostAction('repost', post)}
          >
            {countLabel(post.viewerRepost ? 'Reposted' : 'Repost', post.reposts)}
          </div>
          <div
            style={actionButton(false, postActionsDisabled || busy('quote'))}
            onClick={postActionsDisabled ? undefined : () => setComposer(composer === 'quote' ? null : 'quote')}
          >
            {countLabel('Quote', post.quotes)}
          </div>
          <div
            style={actionButton(post.viewerBookmarked, postActionsDisabled || busy('bookmark'))}
            onClick={postActionsDisabled ? undefined : () => void onPostAction('bookmark', post)}
          >
            {countLabel(post.viewerBookmarked ? 'Saved' : 'Save', post.bookmarks)}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
          {canModerateAuthor && (
            <div
              style={actionButton(Boolean(post.authorFollowing), Boolean(busyKey))}
              onClick={busyKey ? undefined : () => void onActorAction('follow', post)}
            >
              {post.authorFollowing ? 'Following' : 'Follow'}
            </div>
          )}
          {canModerateAuthor && (
            <div
              style={actionButton(post.authorMuted, Boolean(busyKey))}
              onClick={busyKey ? undefined : () => void onActorAction('mute', post)}
            >
              {post.authorMuted ? 'Unmute' : 'Mute'}
            </div>
          )}
          {canModerateAuthor && (
            <div
              style={actionButton(Boolean(post.authorBlocking), Boolean(busyKey))}
              onClick={busyKey ? undefined : () => void onActorAction('block', post)}
            >
              {post.authorBlocking ? 'Unblock' : 'Block'}
            </div>
          )}
          {post.isPost && (
            <div
              style={actionButton(post.viewerThreadMuted, postActionsDisabled || busy('threadMute'))}
              onClick={postActionsDisabled ? undefined : () => void onPostAction('threadMute', post)}
            >
              {post.viewerThreadMuted ? 'Unmute thread' : 'Mute thread'}
            </div>
          )}
          <div style={actionButton(false, false)} onClick={() => void window.api.openExternal(post.webUrl)}>
            Open
          </div>
          {post.isOwnPost && post.isPost && (
            <div style={actionButton(false, Boolean(busyKey))} onClick={busyKey ? undefined : () => void onDelete(post)}>
              Delete
            </div>
          )}
        </div>

        {composer && (
          <InlineComposer mode={composer} busy={Boolean(busyKey)} onCancel={() => setComposer(null)} onSubmit={(text) => submitText(composer, text)} />
        )}
      </div>
    </div>
  )
}

export default function Engage(): React.JSX.Element {
  const goSettings = useAppStore((s) => s.goSettings)
  const engageDraft = useAppStore((s) => s.engageDraft)
  const takeEngageDraft = useAppStore((s) => s.takeEngageDraft)

  const [network, setNetwork] = useState<Network>('bluesky')
  const [status, setStatus] = useState<EngageStatus | null>(null)
  const [feed, setFeed] = useState<Feed>('notifications')
  const [suggestions, setSuggestions] = useState<SuggestedFollowsResponse | null>(null)
  const [suggestLoading, setSuggestLoading] = useState(false)
  const [posts, setPosts] = useState<FeedPost[]>([])
  const [postText, setPostText] = useState('')
  const [loading, setLoading] = useState(false)
  const [posting, setPosting] = useState(false)
  async function loadSuggestions(query = ''): Promise<void> {
    setSuggestLoading(true)
    try {
      // Deep enough for several pages; the panel reveals fifteen at a time.
      setSuggestions(await getSuggestedFollows('', query, 60))
    } catch {
      // A suggestion list is an extra. Failing it should not put an error banner over a
      // timeline that loaded perfectly well.
      setSuggestions(null)
    } finally {
      setSuggestLoading(false)
    }
  }

  const [busyKey, setBusyKey] = useState('')
  const [error, setError] = useState('')

  // Only reachable via a handed-over draft — maxLength stops typing past the cap
  // but does not truncate a value set programmatically.
  const postOverLimit = postText.length > POST_TEXT_LIMIT

  // A draft handed over from a composer tool (Social Post's "Send to Engage").
  // Consumed once, so returning to Engage later does not refill a box the user
  // has since emptied.
  //
  // The append branch is defensive rather than load-bearing: `postText` is local
  // state and this screen unmounts on every route change, so in practice the box
  // is always empty on arrival. It costs one line and stops the handoff eating a
  // draft if the composer is ever persisted.
  useEffect(() => {
    if (!engageDraft) return
    // Take rather than read-then-clear. StrictMode runs this body twice against
    // the same closure, so `engageDraft` is still set on the second pass; only an
    // atomic take makes that pass a no-op instead of appending the post again.
    const draft = takeEngageDraft()
    if (!draft) return
    setNetwork('bluesky')
    setPostText((current) => (current.trim() ? `${current.trimEnd()}\n\n${draft}` : draft))
  }, [engageDraft, takeEngageDraft])

  // Only asked for once the Bluesky side is actually being looked at — opening
  // Engage on the Mastodon tab should not log into Bluesky in the background.
  useEffect(() => {
    if (network !== 'bluesky' || status) return
    getEngageStatus()
      .then(setStatus)
      .catch(() => setStatus({ configured: false, handle: null }))
  }, [network, status])

  async function loadFeed(which: Feed = feed): Promise<void> {
    setFeed(which)
    // The follows tab has its own loader and no post list — switching to it should not
    // fire a timeline request, nor leave the previous tab's posts behind it.
    if (which === 'follows') {
      if (!suggestions) void loadSuggestions()
      return
    }
    setLoading(true)
    setError('')
    try {
      const res = which === 'notifications' ? await getEngageNotifications() : await getEngageTimeline()
      setPosts(res.posts)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (status?.configured) void loadFeed('notifications')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.configured])

  function applyActionResult(result: EngageActionResponse): void {
    if (result.post) {
      setPosts((current) => current.map((p) => (p.uri === result.post?.uri ? result.post : p)))
    }
    if (result.actor) {
      applyActorState(result.actor)
    }
  }

  function applyActorState(actor: EngageActorState): void {
    setPosts((current) =>
      current.map((p) =>
        p.authorDid === actor.authorDid
          ? {
              ...p,
              authorFollowing: actor.authorFollowing,
              authorFollowedBy: actor.authorFollowedBy,
              authorMuted: actor.authorMuted,
              authorBlocking: actor.authorBlocking,
              authorBlockedBy: actor.authorBlockedBy
            }
          : p
      )
    )
  }

  async function handleCreatePost(): Promise<void> {
    if (!postText.trim()) return
    setPosting(true)
    setError('')
    try {
      const result = await createEngagePost(postText)
      setPostText('')
      if (result.post) setPosts((current) => [result.post as FeedPost, ...current])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setPosting(false)
    }
  }

  async function runWithBusy(key: string, fn: () => Promise<EngageActionResponse>): Promise<boolean> {
    setBusyKey(key)
    setError('')
    try {
      applyActionResult(await fn())
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setBusyKey('')
    }
  }

  async function handlePostAction(action: PostAction, post: FeedPost): Promise<void> {
    const request = {
      like: () => toggleEngageLike(post),
      repost: () => toggleEngageRepost(post),
      bookmark: () => toggleEngageBookmark(post),
      threadMute: () => toggleEngageThreadMute(post)
    }[action]
    await runWithBusy(`${post.uri}:${action}`, request)
  }

  async function handleActorAction(action: ActorAction, post: FeedPost): Promise<void> {
    if (action === 'block' && !post.authorBlocking) {
      const ok = window.confirm(`Block @${post.authorHandle}? You will stop seeing each other on Bluesky.`)
      if (!ok) return
    }
    const request = {
      follow: () => toggleEngageFollow(post),
      mute: () => toggleEngageMute(post),
      block: () => toggleEngageBlock(post)
    }[action]
    await runWithBusy(`${post.uri}:${action}`, request)
  }

  async function handleTextAction(action: TextAction, post: FeedPost, text: string): Promise<void> {
    const request =
      action === 'reply' ? () => replyEngagePost(post.uri, post.cid, text) : () => quoteEngagePost(post.uri, post.cid, text)
    await runWithBusy(`${post.uri}:${action}`, request)
  }

  async function handleDelete(post: FeedPost): Promise<void> {
    const ok = window.confirm('Delete this post from Bluesky?')
    if (!ok) return
    const deleted = await runWithBusy(`${post.uri}:delete`, () => deleteEngagePost(post))
    if (deleted) setPosts((current) => current.filter((p) => p.uri !== post.uri))
  }

  async function handleMarkRead(): Promise<void> {
    await runWithBusy('notifications:read', markEngageNotificationsRead)
    setPosts((current) => current.map((p) => ({ ...p, isRead: true })))
  }

  return (
    <div style={{ maxWidth: network === 'mastodon' ? 1120 : 900, margin: '0 auto', padding: '30px 34px 60px' }}>
      <ScreenBackdrop video="engage" />
      <div style={{ marginBottom: 18 }}>
        <div style={sectionEyebrow}>Engage</div>
        <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)', marginTop: 4 }}>What's happening with you</div>
        <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
          {network === 'bluesky'
            ? "Your own Bluesky account - who's talking to you, and what's moving through your feed."
            : network === 'mastodon'
              ? "Your own Mastodon community - its house rules, the server itself, and the everyday things you do there."
              : 'Your own Tumblr blog - your activity, your dashboard, and the reblogging that passes for conversation there.'}
        </div>
      </div>

      <div style={{ ...segGroup, maxWidth: 380, marginBottom: 18 }}>
        {NETWORKS.map((n) => (
          <div key={n.key} style={segItem(network === n.key)} onClick={() => setNetwork(n.key)}>
            {n.label}
          </div>
        ))}
      </div>

      {network === 'mastodon' && <MastodonEngage />}

      {network === 'tumblr' && <TumblrEngage />}

      {network === 'bluesky' && status && !status.configured && (
        <div
          style={{
            marginBottom: 18,
            background: 'var(--accent-soft-bg)',
            border: '2px solid var(--border)',
            borderRadius: 16,
            padding: '12px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: 14
          }}
        >
          <div style={{ flex: 1 }}>
            <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>Connect Bluesky first</div>
            <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 2 }}>
              Use the same handle and app password from the Bluesky Post Creator settings.
            </div>
          </div>
          <div style={secondaryButtonSmall} onClick={goSettings}>
            Open Settings
          </div>
        </div>
      )}

      {network === 'bluesky' && status?.configured && (
        <>
          <div style={composerBox}>
            <textarea
              value={postText}
              maxLength={POST_TEXT_LIMIT}
              onChange={(e) => setPostText(e.target.value)}
              placeholder="Post to Bluesky"
              style={{ ...textarea, minHeight: 92, background: 'var(--surface)' }}
            />
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginTop: 10 }}>
              <span
                style={{
                  font: "700 11px 'Quicksand'",
                  color: postOverLimit ? 'var(--danger-ink)' : 'var(--ink-fainter)'
                }}
              >
                {postText.length}/{POST_TEXT_LIMIT}
                {postOverLimit ? ' — too long, trim it' : ''}
              </span>
              <div
                style={{ ...primaryButtonSmall, opacity: posting || !postText.trim() || postOverLimit ? 0.55 : 1 }}
                onClick={
                  posting || !postText.trim() || postOverLimit ? undefined : () => void handleCreatePost()
                }
              >
                Post
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
            <div style={segGroup}>
              {FEEDS.map((f) => (
                <div key={f.key} style={segItem(feed === f.key)} onClick={() => void loadFeed(f.key)}>
                  {f.label}
                </div>
              ))}
            </div>
            <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>@{status.handle}</span>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {feed === 'notifications' && (
                <div style={actionButton(false, Boolean(busyKey) || loading)} onClick={busyKey || loading ? undefined : () => void handleMarkRead()}>
                  Mark read
                </div>
              )}
              {feed !== 'follows' && (
                <div style={actionButton(false, loading)} onClick={loading ? undefined : () => void loadFeed(feed)}>
                  Refresh
                </div>
              )}
            </div>
          </div>

          {error && <div style={{ font: "700 13px 'Quicksand'", color: 'var(--danger-ink)', marginBottom: 14 }}>{error}</div>}

          {feed === 'follows' && (
            <SuggestedFollows
              scope="on Bluesky"
              keywords={suggestions?.keywords ?? []}
              note={suggestions?.note}
              loading={suggestLoading}
              onSearch={(q) => void loadSuggestions(q)}
              onFollow={async (did) => {
                await followEngageActor(did)
              }}
              rows={(suggestions?.accounts ?? []).map(
                (a): SuggestionRow => ({
                  key: a.did,
                  handle: a.handle,
                  displayName: a.displayName,
                  bio: a.description,
                  avatar: a.avatar,
                  followers: a.followers,
                  reason: a.reason,
                  bioMatch: a.bioMatch
                })
              )}
            />
          )}

          {feed !== 'follows' && loading && (
            <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', padding: '30px 0', textAlign: 'center' }}>
              Loading...
            </div>
          )}

          {feed !== 'follows' && !loading && posts.length === 0 && !error && (
            <div
              style={{
                border: '2px dashed var(--border)',
                borderRadius: 20,
                padding: 40,
                textAlign: 'center',
                font: "700 15px 'Kalam'",
                color: 'var(--ink-fainter-2)'
              }}
            >
              {feed === 'notifications' ? "Nothing new - you're all caught up." : 'Nothing in your timeline yet.'}
            </div>
          )}

          {feed !== 'follows' && !loading && posts.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {posts.map((p, i) => (
                <FeedCard
                  key={`${p.uri}-${i}`}
                  post={p}
                  busyKey={busyKey}
                  onPostAction={handlePostAction}
                  onActorAction={handleActorAction}
                  onTextAction={handleTextAction}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
