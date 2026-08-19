import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import {
  composeTumblrPost,
  deleteTumblrPost,
  getTumblrFeed,
  getTumblrNotes,
  getTumblrSession,
  getTumblrSuggestedFollows,
  reblogTumblrPost,
  toggleTumblrBlock,
  toggleTumblrFollow,
  toggleTumblrLike,
  toggleTumblrMute,
  type TumblrActionResult,
  type TumblrFeedName,
  type TumblrNotes,
  type TumblrPost,
  type TumblrPostState,
  type TumblrSession,
  type TumblrSuggestedFollows
} from '../api/client'
import PostMedia from './PostMedia'
import AttachImagePicker from './AttachImagePicker'
import SaveButton from './SaveButton'
import SuggestedFollows, { type SuggestionRow } from './SuggestedFollows'
import { useAppStore } from '../state/store'
import { primaryButtonSmall, secondaryButtonSmall, segGroup, segItem, select, textarea, textInput } from '../styles/styleKit'

/**
 * Engage, Tumblr side.
 *
 * The same job the Bluesky panel does — your own account, what is happening to
 * it, and the everyday actions on a feed — against an API that draws the lines
 * somewhere else. Four differences are visible on this screen, and each one is
 * deliberate rather than unfinished:
 *
 *   1. **Reblogging is the reply, the boost and the quote.** Tumblr's API can
 *      create posts and reblogs and cannot create a reply, so the composer on a
 *      card is "Reblog with a comment" — which is how conversations on Tumblr
 *      actually work. Replies other people wrote are readable: "Notes" opens the
 *      conversation on a post.
 *   2. **Likes are a feed, not a bookmark.** There is no save-for-later on
 *      Tumblr; the liked list is the closest thing and it gets its own tab.
 *   3. **Blocking, no muting.** Tumblr has no per-blog mute in its API. Muting a
 *      post's notifications does exist, and only its author can do it — so that
 *      button appears on your own posts.
 *   4. **Tags are a first-class field.** On Tumblr tags are the distribution
 *      mechanism, not decoration, so the composer has a box for them and every
 *      card shows the ones it carries.
 *
 * Everything published from here goes out under the blog named in Settings (or
 * the account's primary blog), which is printed next to the feed tabs so it is
 * never a guess.
 */

type Tab = TumblrFeedName | 'follows'

const TABS: { key: Tab; label: string }[] = [
  { key: 'notifications', label: 'Activity' },
  { key: 'dashboard', label: 'Dashboard' },
  { key: 'likes', label: 'Likes' },
  // Not a feed of posts, but it belongs beside them: it answers "whose posts
  // should be here" and uses the same account this screen is signed in as.
  { key: 'follows', label: 'Blogs to follow' }
]

const STATES: { key: TumblrPostState; label: string }[] = [
  { key: 'published', label: 'Publish now' },
  { key: 'queue', label: 'Add to queue' },
  { key: 'draft', label: 'Save as draft' },
  { key: 'private', label: 'Post privately' }
]

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

const cardStyle = (read: boolean | null): CSSProperties => ({
  display: 'flex',
  gap: 12,
  background: 'var(--surface)',
  border: '2px solid var(--border)',
  borderRadius: 16,
  padding: '13px 16px',
  opacity: read === true ? 0.74 : 1
})

const composerBox: CSSProperties = {
  background: 'var(--surface-paper)',
  border: '2px solid var(--border-paper)',
  borderRadius: 14,
  padding: 12,
  marginBottom: 14
}

const tagChip: CSSProperties = {
  font: "700 11px 'Quicksand'",
  color: 'var(--ink-faint)',
  background: 'var(--surface-paper)',
  border: '2px solid var(--border-soft)',
  borderRadius: 999,
  padding: '2px 8px'
}

function Avatar({ post }: { post: TumblrPost }): React.JSX.Element {
  const shape: CSSProperties = {
    width: 38,
    height: 38,
    borderRadius: 10,
    border: '2px solid var(--border)',
    flexShrink: 0,
    objectFit: 'cover'
  }
  // Tumblr's avatar route redirects to the image and needs no key, so this is a
  // plain <img> rather than anything proxied. A blog that has none falls back to
  // a coloured tile instead of a broken image.
  return post.avatar ? (
    <img src={post.avatar} alt="" style={shape} />
  ) : (
    <span style={{ ...shape, background: 'var(--tool-tumblr)' }} />
  )
}

// ---------------------------------------------------------------------------
// Reblog composer
// ---------------------------------------------------------------------------

function ReblogComposer({
  busy,
  onCancel,
  onSubmit
}: {
  busy: boolean
  onCancel: () => void
  onSubmit: (comment: string, tags: string, state: TumblrPostState) => Promise<void>
}): React.JSX.Element {
  const [comment, setComment] = useState('')
  const [tags, setTags] = useState('')
  const [state, setState] = useState<TumblrPostState>('published')

  return (
    <div style={{ marginTop: 10, display: 'grid', gap: 8 }}>
      <textarea
        autoFocus
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder="Add a comment — or leave it empty to reblog as it is"
        style={{ ...textarea, minHeight: 78, background: 'var(--surface-paper)' }}
      />
      <input
        value={tags}
        onChange={(e) => setTags(e.target.value)}
        placeholder="Tags, comma separated"
        style={textInput}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <select value={state} onChange={(e) => setState(e.target.value as TumblrPostState)} style={{ ...select, width: 'auto' }}>
          {STATES.map((s) => (
            <option key={s.key} value={s.key}>
              {s.label}
            </option>
          ))}
        </select>
        <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {/* A comment you spent time on is worth keeping even if you don't send it. */}
          <SaveButton tool="Engage" title="Reblog comment" subtitle="Tumblr draft" content={comment} />
          <div style={actionButton(false, busy)} onClick={busy ? undefined : onCancel}>
            Cancel
          </div>
          <div
            style={{ ...primaryButtonSmall, padding: '7px 14px', opacity: busy ? 0.55 : 1 }}
            onClick={busy ? undefined : () => void onSubmit(comment, tags, state)}
          >
            Reblog
          </div>
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Notes (the conversation on a post)
// ---------------------------------------------------------------------------

function NotesPanel({ notes, loading }: { notes: TumblrNotes | null; loading: boolean }): React.JSX.Element {
  if (loading) {
    return (
      <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 10 }}>Loading notes…</div>
    )
  }
  if (!notes) return <></>
  return (
    <div style={{ marginTop: 10, borderTop: '2px dashed var(--border-soft)', paddingTop: 8 }}>
      <div style={{ font: "700 11.5px 'Quicksand'", color: 'var(--ink-fainter)', marginBottom: 6 }}>
        {notes.totalNotes} notes
        {notes.totalLikes > 0 ? ` · ${notes.totalLikes} likes` : ''}
        {notes.totalReblogs > 0 ? ` · ${notes.totalReblogs} reblogs` : ''}
      </div>
      {notes.note && <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>{notes.note}</div>}
      {notes.notes.map((n, i) => (
        <div key={`${n.blogName}-${n.createdAt}-${i}`} style={{ display: 'flex', gap: 8, padding: '6px 0' }}>
          {n.avatar && (
            <img src={n.avatar} alt="" style={{ width: 24, height: 24, borderRadius: 7, border: '2px solid var(--border)' }} />
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)' }}>
              {n.blogName.replace(/\.tumblr\.com$/, '')}{' '}
              <span style={{ font: "600 11px 'Quicksand'", color: 'var(--ink-fainter)' }}>
                {n.type === 'reply' ? 'replied' : 'reblogged'} · {timeAgo(n.createdAt)}
              </span>
            </div>
            {n.text && (
              <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)', whiteSpace: 'pre-wrap' }}>
                {n.text}
              </div>
            )}
            {n.tags.length > 0 && (
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 3 }}>
                {n.tags.map((t) => (
                  <span key={t} style={tagChip}>
                    #{t}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// One card
// ---------------------------------------------------------------------------

function FeedCard({
  post,
  busyKey,
  onLike,
  onReblog,
  onFollow,
  onBlock,
  onMute,
  onDelete
}: {
  post: TumblrPost
  busyKey: string
  onLike: (post: TumblrPost) => Promise<void>
  onReblog: (post: TumblrPost, comment: string, tags: string, state: TumblrPostState) => Promise<void>
  onFollow: (post: TumblrPost) => Promise<void>
  onBlock: (post: TumblrPost) => Promise<void>
  onMute: (post: TumblrPost) => Promise<void>
  onDelete: (post: TumblrPost) => Promise<void>
}): React.JSX.Element {
  const [composerOpen, setComposerOpen] = useState(false)
  const [notes, setNotes] = useState<TumblrNotes | null>(null)
  const [notesOpen, setNotesOpen] = useState(false)
  const [notesLoading, setNotesLoading] = useState(false)

  const busy = (key: string): boolean => busyKey === `${post.id}:${key}`
  const anyBusy = Boolean(busyKey)
  // An activity row carries no reblog key, so nothing that acts on the post can
  // work from it — the buttons are absent rather than dead.
  const canActOnPost = post.isPost && Boolean(post.reblogKey)
  const shortName = post.blogName.replace(/\.tumblr\.com$/, '')

  async function toggleNotes(): Promise<void> {
    if (notesOpen) {
      setNotesOpen(false)
      return
    }
    setNotesOpen(true)
    if (notes) return
    setNotesLoading(true)
    try {
      setNotes(await getTumblrNotes(post.blogName, post.id))
    } catch {
      // Notes are extra context. Failing to load them should not put an error
      // banner over a feed that loaded perfectly well.
      setNotes({ notes: [], totalNotes: post.noteCount, totalLikes: 0, totalReblogs: 0, note: 'Could not load the notes on this one.' })
    } finally {
      setNotesLoading(false)
    }
  }

  async function submitReblog(comment: string, tags: string, state: TumblrPostState): Promise<void> {
    await onReblog(post, comment, tags, state)
    setComposerOpen(false)
  }

  return (
    <div style={cardStyle(post.isRead)}>
      <Avatar post={post} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ font: "700 14px 'Kalam'", color: 'var(--ink)' }}>{post.blogTitle || shortName}</span>
          <span
            style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', cursor: 'pointer' }}
            onClick={() => void window.api.openExternal(post.blogUrl)}
          >
            @{shortName}
          </span>
          <span style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-fainter)', marginLeft: 'auto' }}>
            {timeAgo(post.createdAt)}
          </span>
        </div>

        {post.reasonText && (
          <div style={{ font: "700 11.5px 'Quicksand'", color: 'var(--accent-deep)', margin: '2px 0' }}>
            {post.reasonText}
          </div>
        )}
        {post.isReblog && post.rebloggedFrom && (
          <div style={{ font: "700 11px 'Quicksand'", color: 'var(--ink-fainter)', margin: '2px 0' }}>
            reblogged from {post.rebloggedFrom}
          </div>
        )}
        {post.state && post.state !== 'published' && (
          <div style={{ font: "700 11px 'Quicksand'", color: 'var(--ink-fainter)', margin: '2px 0' }}>{post.state}</div>
        )}

        {post.text ? (
          <div style={{ whiteSpace: 'pre-wrap', font: "600 13px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 3 }}>
            {post.text}
          </div>
        ) : (
          <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-fainter)', marginTop: 3 }}>
            {post.isPost ? (post.media.length ? 'Media only.' : 'This post has no text.') : 'Blog activity'}
          </div>
        )}

        {/* Tumblr's per-post content flags are account-level filtering the API does
            not expose per post, so nothing here is blurred by default. */}
        <PostMedia media={post.media} />

        {post.tags.length > 0 && (
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 6 }}>
            {post.tags.slice(0, 12).map((t) => (
              <span key={t} style={tagChip}>
                #{t}
              </span>
            ))}
          </div>
        )}

        {post.noteCount > 0 && (
          <div style={{ font: "700 11px 'Quicksand'", color: 'var(--ink-fainter)', marginTop: 7 }}>
            {post.noteCount} notes
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
          {canActOnPost && (
            <div
              style={actionButton(post.liked, anyBusy || busy('like'))}
              onClick={anyBusy ? undefined : () => void onLike(post)}
            >
              {post.liked ? 'Liked' : 'Like'}
            </div>
          )}
          {canActOnPost && (
            <div
              style={actionButton(composerOpen, anyBusy || busy('reblog'))}
              onClick={anyBusy ? undefined : () => setComposerOpen(!composerOpen)}
            >
              Reblog
            </div>
          )}
          {post.isPost && (
            <div style={actionButton(notesOpen, notesLoading)} onClick={notesLoading ? undefined : () => void toggleNotes()}>
              {countLabel('Notes', post.noteCount)}
            </div>
          )}
          {post.postUrl && (
            <div style={actionButton(false, false)} onClick={() => void window.api.openExternal(post.postUrl)}>
              Open
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
          {!post.isOwn && post.blogName && (
            <div style={actionButton(post.following, anyBusy)} onClick={anyBusy ? undefined : () => void onFollow(post)}>
              {post.following ? 'Following' : 'Follow'}
            </div>
          )}
          {!post.isOwn && post.blogName && (
            <div style={actionButton(post.blocked, anyBusy)} onClick={anyBusy ? undefined : () => void onBlock(post)}>
              {post.blocked ? 'Unblock' : 'Block'}
            </div>
          )}
          {/* Only a post's author can mute activity about it — Tumblr's rule, not ours. */}
          {post.isOwn && post.isPost && (
            <div
              style={actionButton(post.muted, anyBusy || busy('mute'))}
              onClick={anyBusy ? undefined : () => void onMute(post)}
            >
              {post.muted ? 'Unmute notifications' : 'Mute notifications'}
            </div>
          )}
          {post.isOwn && post.isPost && (
            <div style={actionButton(false, anyBusy)} onClick={anyBusy ? undefined : () => void onDelete(post)}>
              Delete
            </div>
          )}
        </div>

        {composerOpen && <ReblogComposer busy={anyBusy} onCancel={() => setComposerOpen(false)} onSubmit={submitReblog} />}
        {notesOpen && <NotesPanel notes={notes} loading={notesLoading} />}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------

export default function TumblrEngage(): React.JSX.Element {
  const goSettings = useAppStore((s) => s.goSettings)

  const [session, setSession] = useState<TumblrSession | null>(null)
  const [tab, setTab] = useState<Tab>('notifications')
  const [posts, setPosts] = useState<TumblrPost[]>([])
  const [suggestions, setSuggestions] = useState<TumblrSuggestedFollows | null>(null)
  const [suggestLoading, setSuggestLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [posting, setPosting] = useState(false)
  const [busyKey, setBusyKey] = useState('')
  const [error, setError] = useState('')
  const [feedNote, setFeedNote] = useState('')

  const [postText, setPostText] = useState('')
  const [postTitle, setPostTitle] = useState('')
  const [postTags, setPostTags] = useState('')
  const [postState, setPostState] = useState<TumblrPostState>('published')
  const [postImage, setPostImage] = useState({ url: '', alt: '' })

  useEffect(() => {
    getTumblrSession()
      .then(setSession)
      .catch((err) => {
        setSession({
          configured: false,
          reachable: false,
          detail: err instanceof Error ? err.message : String(err),
          userName: '',
          blog: '',
          blogTitle: '',
          blogUrl: '',
          avatar: '',
          following: 0,
          likes: 0,
          blogs: []
        })
      })
  }, [])

  const loadFeed = useCallback(async (which: Tab): Promise<void> => {
    setTab(which)
    // The follows tab has its own loader and no post list — switching to it must
    // not fire a feed request, nor leave the previous tab's posts behind it.
    if (which === 'follows') return
    setLoading(true)
    setError('')
    try {
      const res = await getTumblrFeed(which)
      setPosts(res.posts)
      setFeedNote(res.note)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setPosts([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (session?.reachable) void loadFeed('notifications')
  }, [session?.reachable, loadFeed])

  async function loadSuggestions(query = ''): Promise<void> {
    setSuggestLoading(true)
    try {
      setSuggestions(await getTumblrSuggestedFollows(query, 30))
    } catch {
      // A suggestion list is an extra; a failure here is not the feed's problem.
      setSuggestions(null)
    } finally {
      setSuggestLoading(false)
    }
  }

  useEffect(() => {
    if (tab === 'follows' && !suggestions && !suggestLoading) void loadSuggestions()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  function applyResult(result: TumblrActionResult): void {
    if (result.post) {
      setPosts((current) => current.map((p) => (p.id === result.post?.id ? result.post : p)))
    }
    if (result.blog) {
      // Follow and block are per-blog, not per-post: one click has to move every
      // card by the same blog, or the second card would still read "Follow".
      const { blogName, following, blocked } = result.blog
      setPosts((current) => current.map((p) => (p.blogName === blogName ? { ...p, following, blocked } : p)))
    }
  }

  async function runWithBusy(key: string, fn: () => Promise<TumblrActionResult>): Promise<boolean> {
    setBusyKey(key)
    setError('')
    try {
      applyResult(await fn())
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setBusyKey('')
    }
  }

  async function handleCompose(): Promise<void> {
    if (!postText.trim()) return
    setPosting(true)
    setError('')
    try {
      const result = await composeTumblrPost({
        text: postText,
        title: postTitle,
        tags: postTags,
        state: postState,
        imageUrl: postImage.url,
        imageAlt: postImage.alt
      })
      setPostText('')
      setPostTitle('')
      setPostTags('')
      setPostImage({ url: '', alt: '' })
      if (result.post && tab === 'dashboard') setPosts((current) => [result.post as TumblrPost, ...current])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setPosting(false)
    }
  }

  async function handleReblog(
    post: TumblrPost,
    comment: string,
    tags: string,
    state: TumblrPostState
  ): Promise<void> {
    await runWithBusy(`${post.id}:reblog`, () => reblogTumblrPost(post, { comment, tags, state }))
  }

  async function handleBlock(post: TumblrPost): Promise<void> {
    if (!post.blocked) {
      const ok = window.confirm(
        `Block ${post.blogName.replace(/\.tumblr\.com$/, '')}? They will not be able to interact with ${
          session?.blog ?? 'your blog'
        }.`
      )
      if (!ok) return
    }
    await runWithBusy(`${post.id}:block`, () => toggleTumblrBlock(post.blogName, !post.blocked))
  }

  async function handleDelete(post: TumblrPost): Promise<void> {
    const ok = window.confirm('Delete this post from Tumblr?')
    if (!ok) return
    const deleted = await runWithBusy(`${post.id}:delete`, () => deleteTumblrPost(post))
    if (deleted) setPosts((current) => current.filter((p) => p.id !== post.id))
  }

  // ---- states before the feed ---------------------------------------------

  if (session && !session.configured) {
    return (
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
          <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>Connect Tumblr first</div>
          <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 2 }}>
            Tumblr signs every request with four values. Register an app at tumblr.com/oauth/apps for the
            consumer key and secret, then take a token and token secret from api.tumblr.com/console.
            {session.detail ? ` ${session.detail}` : ''}
          </div>
        </div>
        <div style={secondaryButtonSmall} onClick={goSettings}>
          Open Settings
        </div>
      </div>
    )
  }

  if (session && !session.reachable) {
    return (
      <div
        style={{
          marginBottom: 18,
          background: 'var(--surface)',
          border: '2px solid var(--border)',
          borderRadius: 16,
          padding: '12px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: 14
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--danger-ink)' }}>Tumblr would not let us in</div>
          <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 2 }}>
            {session.detail}
          </div>
        </div>
        <div style={secondaryButtonSmall} onClick={goSettings}>
          Open Settings
        </div>
      </div>
    )
  }

  if (!session) {
    return (
      <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', padding: '30px 0', textAlign: 'center' }}>
        Checking your Tumblr account…
      </div>
    )
  }

  // ---- the feed ------------------------------------------------------------

  return (
    <>
      {session.detail && (
        <div
          style={{
            font: "700 12.5px/1.5 'Quicksand'",
            color: 'var(--danger-ink)',
            background: 'var(--accent-soft-bg)',
            border: '2px solid var(--border)',
            borderRadius: 14,
            padding: '10px 13px',
            marginBottom: 14
          }}
        >
          {session.detail}
        </div>
      )}

      <div style={composerBox}>
        <input
          value={postTitle}
          onChange={(e) => setPostTitle(e.target.value)}
          placeholder="Title (optional)"
          style={{ ...textInput, marginBottom: 8 }}
        />
        <textarea
          value={postText}
          onChange={(e) => setPostText(e.target.value)}
          placeholder={`Post to ${session.blog.replace(/\.tumblr\.com$/, '') || 'Tumblr'}`}
          style={{ ...textarea, minHeight: 92, background: 'var(--surface)' }}
        />
        <input
          value={postTags}
          onChange={(e) => setPostTags(e.target.value)}
          placeholder="Tags, comma separated — this is how anyone finds it"
          style={{ ...textInput, marginTop: 8 }}
        />
        <AttachImagePicker
          url={postImage.url}
          alt={postImage.alt}
          onChange={setPostImage}
          hint="posted above your text"
          disabled={posting}
        />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginTop: 10 }}>
          <select
            value={postState}
            onChange={(e) => setPostState(e.target.value as TumblrPostState)}
            style={{ ...select, width: 'auto' }}
          >
            {STATES.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
          <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <SaveButton tool="Engage" title="Tumblr post" subtitle="Tumblr draft" content={postText} />
            <div
              style={{ ...primaryButtonSmall, opacity: posting || !postText.trim() ? 0.55 : 1 }}
              onClick={posting || !postText.trim() ? undefined : () => void handleCompose()}
            >
              {posting ? 'Posting…' : STATES.find((s) => s.key === postState)?.label ?? 'Post'}
            </div>
          </span>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={segGroup}>
          {TABS.map((t) => (
            <div key={t.key} style={segItem(tab === t.key)} onClick={() => void loadFeed(t.key)}>
              {t.label}
            </div>
          ))}
        </div>
        <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>
          as {session.blog.replace(/\.tumblr\.com$/, '')}
          {session.blogs.length > 1 ? ' — change it in Settings' : ''}
        </span>
        {tab !== 'follows' && (
          <div style={{ marginLeft: 'auto' }}>
            <div style={actionButton(false, loading)} onClick={loading ? undefined : () => void loadFeed(tab)}>
              Refresh
            </div>
          </div>
        )}
      </div>

      {/* Said once, where it matters, rather than as a dead button on every card. */}
      {tab === 'notifications' && (
        <div style={{ font: "600 12px/1.6 'Quicksand'", color: 'var(--ink-faint)', marginBottom: 12 }}>
          Tumblr's API has no read-marker, so nothing here can mark your activity as seen — the unread
          state is whatever Tumblr itself last recorded. Replies show as an excerpt; open a post's notes
          on the Dashboard to read the whole conversation.
        </div>
      )}

      {error && <div style={{ font: "700 13px 'Quicksand'", color: 'var(--danger-ink)', marginBottom: 14 }}>{error}</div>}

      {tab === 'follows' && (
        <SuggestedFollows
          scope="on Tumblr"
          keywords={suggestions?.keywords ?? []}
          note={suggestions?.note}
          loading={suggestLoading}
          onSearch={(q) => void loadSuggestions(q)}
          onFollow={async (name) => {
            await toggleTumblrFollow(name, true)
          }}
          rows={(suggestions?.blogs ?? []).map(
            (b): SuggestionRow => ({
              key: b.name,
              handle: b.name.replace(/\.tumblr\.com$/, ''),
              displayName: b.title || b.name,
              bio: b.description,
              avatar: b.avatar,
              // Tumblr only reports follower counts for your own blogs, so this is
              // always zero here and the row simply omits it.
              followers: 0,
              reason: b.reason,
              bioMatch: b.bioMatch
            })
          )}
        />
      )}

      {tab !== 'follows' && loading && (
        <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', padding: '30px 0', textAlign: 'center' }}>
          Loading...
        </div>
      )}

      {tab !== 'follows' && !loading && posts.length === 0 && !error && (
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
          {feedNote ||
            (tab === 'notifications'
              ? "Nothing new - you're all caught up."
              : tab === 'likes'
                ? "You haven't liked anything yet."
                : 'Nothing on your dashboard yet.')}
        </div>
      )}

      {tab !== 'follows' && !loading && posts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {posts.map((p, i) => (
            <FeedCard
              key={`${p.id}-${i}`}
              post={p}
              busyKey={busyKey}
              onLike={async (post) => {
                await runWithBusy(`${post.id}:like`, () => toggleTumblrLike(post))
              }}
              onReblog={handleReblog}
              onFollow={async (post) => {
                await runWithBusy(`${post.id}:follow`, () => toggleTumblrFollow(post.blogName, !post.following))
              }}
              onBlock={handleBlock}
              onMute={async (post) => {
                await runWithBusy(`${post.id}:mute`, () => toggleTumblrMute(post))
              }}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </>
  )
}
