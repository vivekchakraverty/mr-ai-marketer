import { useEffect, useState } from 'react'
import { fetchDistributionChannels, sendToDistribution, type DistributionJob } from '../api/client'
import { useAppStore } from '../state/store'
import BackendImage from './BackendImage'
import { PLATFORM_SETUP_GUIDES } from '../state/platformSetupGuides'
import { label, primaryButton, secondaryButtonSmall, tag, textInput, textarea } from '../styles/styleKit'

interface Props {
  libraryItemId: string
  title: string
  defaultText: string
  onClose: () => void
}

const STATUS_LABEL: Record<string, string> = {
  sending: 'Sending…',
  sent: 'Sent',
  scheduled: 'Scheduled',
  pending_approval: 'Waiting on approval',
  failed: 'Failed',
  approved: 'Approved',
  rejected: 'Rejected'
}

export default function SendToDistributionModal({ libraryItemId, title, defaultText, onClose }: Props): React.JSX.Element {
  const [loading, setLoading] = useState(true)
  const [ready, setReady] = useState(false)
  const [connectedChannels, setConnectedChannels] = useState<string[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [text, setText] = useState(defaultText)
  const [channelId, setChannelId] = useState('')
  const [pageId, setPageId] = useState('')
  const [imageUrl, setImageUrl] = useState('')
  const [to, setTo] = useState('')
  const [from, setFrom] = useState('')
  const [subject, setSubject] = useState(title)
  const [subreddit, setSubreddit] = useState('')
  const [redditTitle, setRedditTitle] = useState(title)
  const [schedule, setSchedule] = useState(false)
  const [scheduledAt, setScheduledAt] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [results, setResults] = useState<DistributionJob[] | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchDistributionChannels()
      .then((res) => {
        if (cancelled) return
        setReady(res.ready)
        setConnectedChannels([...res.channels, ...res.communityChannels].filter((c) => c.connected).map((c) => c.channel))
      })
      .catch(() => setReady(false))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [])

  function toggleChannel(channel: string): void {
    setSelected((cur) => (cur.includes(channel) ? cur.filter((c) => c !== channel) : [...cur, channel]))
  }

  // Images generated in the post creators file themselves in the Library with the PNG
  // beside them, so the ones worth attaching are already on the shelf — no separate
  // picker state, and no second place for them to live.
  const library = useAppStore((st) => st.library)
  const recentImages = library
    .filter((i) => /\.(png|jpe?g|webp)$/i.test(i.output_path ?? ''))
    .slice(0, 8)
    .map((i) => {
      const p = (i.output_path ?? '').split('\\').join('/')
      return { title: i.title, url: '/outputs/' + p.split('/outputs/').slice(1).join('/outputs/') }
    })

  const needsChannelId = selected.includes('discord') || selected.includes('discord-conversation')
  const needsPageId = selected.includes('facebook') || selected.includes('instagram')
  const needsImageUrl = selected.includes('instagram')
  // Channels whose flow template actually binds an image prop. Verified against the
  // Activepieces piece registry: bluesky imageUrls, mastodon media, linkedin imageUrl,
  // discord files, instagram photo. Anything else silently ignores the attachment, so it
  // is named here rather than implied.
  // A /outputs URL names a file on this machine; the engine is in a container that
  // cannot reach it yet. A pasted public URL is fine.
  const isLocalImage = imageUrl.startsWith('/outputs/')
  const imageChannels = selected.filter((c) =>
    ['bluesky', 'mastodon', 'linkedin', 'discord', 'instagram'].includes(c)
  )
  const needsEmail = selected.includes('email')
  const needsReddit = selected.includes('reddit')

  function validate(): string | null {
    if (selected.length === 0) return 'Pick at least one channel.'
    if (!text.trim()) return 'Post text is required.'
    if (needsChannelId && !channelId.trim()) return 'Discord channel ID is required.'
    if (needsPageId && !pageId.trim()) return 'Page ID is required.'
    if (needsImageUrl && !imageUrl.trim()) return 'Instagram needs an image URL.'
    if (isLocalImage)
      return 'That image lives on this machine and the posting engine cannot fetch it yet. Paste a public image URL, or clear the image.'
    if (needsEmail && (!to.trim() || !from.trim())) return 'Email needs both To and From addresses.'
    if (needsReddit && (!subreddit.trim() || !redditTitle.trim())) return 'Reddit needs a subreddit and a post title.'
    if (schedule) {
      if (!scheduledAt) return 'Pick a date and time, or untick "Schedule for later".'
      if (Number.isNaN(new Date(scheduledAt).getTime())) return 'That scheduled date/time is not valid.'
    }
    return null
  }

  async function handleSend(): Promise<void> {
    const problem = validate()
    if (problem) {
      setError(problem)
      return
    }
    setSending(true)
    setError('')
    try {
      const res = await sendToDistribution({
        libraryItemId,
        channels: selected,
        text,
        channelId: needsChannelId ? channelId : undefined,
        pageId: needsPageId ? pageId : undefined,
        imageUrl: imageUrl.trim() || undefined,
        to: needsEmail ? to : undefined,
        from: needsEmail ? from : undefined,
        subject: needsEmail ? subject : undefined,
        subreddit: needsReddit ? subreddit : undefined,
        title: needsReddit ? redditTitle : undefined,
        scheduledAt: schedule && scheduledAt ? new Date(scheduledAt).toISOString() : undefined
      })
      setResults(res.jobs)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSending(false)
    }
  }

  const allChannels = Object.keys(PLATFORM_SETUP_GUIDES).filter((c) => !PLATFORM_SETUP_GUIDES[c].sharesConnectionWith || connectedChannels.includes(c))

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
          width: 560,
          maxHeight: '88vh',
          overflowY: 'auto',
          background: 'var(--surface)',
          border: '2.5px solid var(--border)',
          borderRadius: 22,
          padding: 32,
          boxShadow: '9px 10px 0 rgba(43,36,32,.22)'
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.16em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
          Send to Distribution
        </div>
        <div style={{ font: "700 26px 'Kalam'", color: 'var(--ink)', margin: '6px 0 18px' }}>{title}</div>

        {loading && <p style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)' }}>Checking your channels…</p>}

        {!loading && !ready && (
          <p style={{ font: "600 14px/1.6 'Quicksand'", color: 'var(--ink-body)' }}>
            The distribution engine isn't set up yet. Open Distribute from the nav to get it running.
          </p>
        )}

        {!loading && ready && !results && (
          <>
            <label style={label}>Channels</label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
              {allChannels.map((channel) => {
                const guide = PLATFORM_SETUP_GUIDES[channel]
                const connected = connectedChannels.includes(channel)
                const on = selected.includes(channel)
                return (
                  <div
                    key={channel}
                    title={connected ? undefined : 'Not connected — set it up in Distribute'}
                    onClick={connected ? () => toggleChannel(channel) : undefined}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 7,
                      padding: '7px 13px',
                      borderRadius: 999,
                      font: "700 12.5px 'Quicksand'",
                      border: '2px solid var(--border)',
                      cursor: connected ? 'pointer' : 'not-allowed',
                      opacity: connected ? 1 : 0.45,
                      background: on ? 'var(--accent)' : 'var(--surface)',
                      color: on ? 'var(--accent-ink)' : 'var(--ink-muted)'
                    }}
                  >
                    <span style={{ width: 9, height: 9, borderRadius: '50%', background: guide.color, border: '1.5px solid var(--border)' }} />
                    {guide.label}
                  </div>
                )
              })}
            </div>

            <label style={label}>Post text</label>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} style={{ ...textarea, marginBottom: 14 }} />

            {needsChannelId && (
              <div style={{ marginBottom: 14 }}>
                <label style={label}>Discord channel ID</label>
                <input value={channelId} onChange={(e) => setChannelId(e.target.value)} style={textInput} placeholder="e.g. 123456789012345678" />
              </div>
            )}
            {needsPageId && (
              <div style={{ marginBottom: 14 }}>
                <label style={label}>Page ID</label>
                <input value={pageId} onChange={(e) => setPageId(e.target.value)} style={textInput} />
              </div>
            )}
            {(imageChannels.length > 0 || needsImageUrl) && (
              <div style={{ marginBottom: 14 }}>
                <label style={label}>
                  Image {needsImageUrl ? '(required by Instagram)' : '(optional)'}
                </label>
                {recentImages.length > 0 && (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '6px 0 8px' }}>
                    {recentImages.map((img) => (
                      <div
                        key={img.url}
                        title={img.title}
                        onClick={() => setImageUrl(imageUrl === img.url ? '' : img.url)}
                        style={{
                          width: 74,
                          height: 74,
                          borderRadius: 10,
                          overflow: 'hidden',
                          cursor: 'pointer',
                          border:
                            imageUrl === img.url
                              ? '3px solid var(--accent)'
                              : '2px solid var(--border)'
                        }}
                      >
                        <BackendImage
                          url={img.url}
                          alt={img.title}
                          saveable={false}
                          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                        />
                      </div>
                    ))}
                  </div>
                )}
                <input
                  value={imageUrl}
                  onChange={(e) => setImageUrl(e.target.value)}
                  style={textInput}
                  placeholder="Pick one above, or paste a public image URL"
                />
                <div
                  style={{
                    font: "600 11.5px/1.5 'Quicksand'",
                    color: isLocalImage ? 'var(--danger-ink)' : 'var(--ink-faint)',
                    marginTop: 5
                  }}
                >
                  {/* An image from this machine cannot be fetched by the engine yet — it
                      runs in its own container and the backend only listens on loopback.
                      Saying so beats a flow that fails with a download error. */}
                  {isLocalImage
                    ? 'Images generated here cannot be attached yet — the posting engine cannot reach this machine. Paste a public image URL instead, or send as text.'
                    : imageUrl
                      ? `Will be attached on ${imageChannels.join(', ') || 'no selected channel'}.`
                      : 'Posts go out as text only unless you add one.'}
                </div>
              </div>
            )}
            {needsEmail && (
              <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
                <div style={{ flex: 1 }}>
                  <label style={label}>To</label>
                  <input value={to} onChange={(e) => setTo(e.target.value)} style={textInput} placeholder="reader@example.com" />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={label}>From</label>
                  <input value={from} onChange={(e) => setFrom(e.target.value)} style={textInput} placeholder="you@example.com" />
                </div>
              </div>
            )}
            {needsEmail && (
              <div style={{ marginBottom: 14 }}>
                <label style={label}>Subject</label>
                <input value={subject} onChange={(e) => setSubject(e.target.value)} style={textInput} />
              </div>
            )}
            {needsReddit && (
              <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
                <div style={{ flex: 1 }}>
                  <label style={label}>Subreddit</label>
                  <input value={subreddit} onChange={(e) => setSubreddit(e.target.value)} style={textInput} placeholder="e.g. marketing" />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={label}>Post title</label>
                  <input value={redditTitle} onChange={(e) => setRedditTitle(e.target.value)} style={textInput} />
                </div>
              </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18 }}>
              <input type="checkbox" checked={schedule} onChange={(e) => setSchedule(e.target.checked)} />
              <span style={{ font: "700 13px 'Quicksand'", color: 'var(--ink-muted)' }}>Schedule for later</span>
              {schedule && (
                <input
                  type="datetime-local"
                  value={scheduledAt}
                  onChange={(e) => setScheduledAt(e.target.value)}
                  style={{ ...textInput, width: 'auto' }}
                />
              )}
            </div>

            {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a', marginBottom: 12 }}>{error}</div>}

            <div style={{ display: 'flex', gap: 10 }}>
              <div style={{ ...primaryButton, flex: 1, opacity: sending ? 0.6 : 1 }} onClick={sending ? undefined : handleSend}>
                {sending ? 'Sending…' : 'Send'}
              </div>
              <div style={secondaryButtonSmall} onClick={onClose}>
                Cancel
              </div>
            </div>
          </>
        )}

        {results && (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 20 }}>
              {results.map((job) => (
                <div
                  key={job.id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '10px 14px',
                    border: '2px solid var(--border)',
                    borderRadius: 12
                  }}
                >
                  <span style={tag}>{PLATFORM_SETUP_GUIDES[job.channel]?.label ?? job.channel}</span>
                  <span style={{ font: "700 12.5px 'Quicksand'", color: job.status === 'failed' ? '#a34a3a' : 'var(--ink-muted)' }}>
                    {STATUS_LABEL[job.status] ?? job.status}
                  </span>
                </div>
              ))}
            </div>
            {results.some((j) => j.status === 'failed' && j.error) && (
              <div style={{ font: "600 12.5px 'Quicksand'", color: '#a34a3a', marginBottom: 16 }}>
                {results.find((j) => j.error)?.error}
              </div>
            )}
            <div style={primaryButton} onClick={onClose}>
              Done
            </div>
          </>
        )}
      </div>
    </div>
  )
}
