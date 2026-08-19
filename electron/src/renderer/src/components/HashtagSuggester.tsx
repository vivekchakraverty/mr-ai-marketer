import { useEffect, useState } from 'react'
import { suggestHashtags, type HashtagSuggestion, type HashtagSuggestResponse } from '../api/client'
import { chip, label, primaryButtonSmall, secondaryButtonSmall } from '../styles/styleKit'
import { useAppStore } from '../state/store'
import SaveButton from './SaveButton'

/**
 * In-composer hashtag suggester. Shared by the Social Post and Mastodon composers.
 *
 * Ranks tags on three axes the backend computes — suitability (does it fit THIS
 * draft), trend (is it moving now), and hashtag data (real reach/usage + whether
 * it performed in your niche) — and never invents a number: a tag with no data
 * reads as "unknown", not zero. Selecting tags builds a tray you can copy on its
 * own or appended to the post, with a live character count against the platform
 * limit.
 */

interface Props {
  // The text to analyse — the generated post if there is one, else what the post
  // is about. Empty is allowed: it will suggest from the niche alone.
  draft: string
  // The full post to optionally copy the tags onto. Usually the same as draft
  // once a post has been generated; empty before then.
  postText?: string
  // Told which tags are ticked, so the screen can keep them with the post rather than
  // leaving them on screen only. Optional — the panel is unchanged without it.
  onSelectionChange?: (tags: string[]) => void
  niche: string
  platform: string
  charLimit?: number | null
}

const BUCKET_ORDER: HashtagSuggestion['bucket'][] = ['trending', 'proven', 'onTopic']

const BUCKET_META: Record<HashtagSuggestion['bucket'], { title: string; blurb: string }> = {
  trending: { title: 'Trending now', blurb: 'Moving on the fediverse or in Google search right now' },
  proven: { title: 'Proven in your niche', blurb: 'Rode posts that actually did numbers in your corpus' },
  onTopic: { title: 'On-topic', blurb: 'Fit the draft closely; quieter, lower-competition tags' }
}

const SOURCE_LABELS: Record<string, string> = {
  corpus: 'your niche corpus',
  mastodon_trends: 'Mastodon trends',
  google_trends: 'Google Trends',
  llm: 'AI read of the draft',
  embeddings: 'semantic fit'
}

const TREND_GLYPH: Record<HashtagSuggestion['trend'], string> = {
  hot: '🔥',
  rising: '↗',
  steady: '→',
  cooling: '↘',
  unknown: ''
}

const REACH_LABEL: Record<HashtagSuggestion['reach'], string> = {
  broad: 'broad',
  balanced: 'balanced',
  niche: 'niche',
  unknown: ''
}

function fmt(n: number | null): string {
  if (n === null || n === undefined) return ''
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`
  return String(n)
}

export default function HashtagSuggester({
  draft,
  postText = '',
  niche,
  platform,
  charLimit,
  onSelectionChange
}: Props): React.JSX.Element {
  const [data, setData] = useState<HashtagSuggestResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [copied, setCopied] = useState('')
  const sendToEngage = useAppStore((s) => s.sendToEngage)

  const canRun = Boolean(draft.trim() || niche.trim())

  async function run(): Promise<void> {
    setLoading(true)
    setError('')
    try {
      setData(await suggestHashtags(draft, niche, platform))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  function toggle(tag: string): void {
    setSelected((cur) => (cur.includes(tag) ? cur.filter((t) => t !== tag) : [...cur, tag]))
  }

  function flash(which: string): void {
    setCopied(which)
    setTimeout(() => setCopied(''), 1600)
  }

  // Same reason as the image panel: reported from one place so a tag being un-ticked is
  // as visible to the caller as one being ticked.
  useEffect(() => {
    onSelectionChange?.(selected)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  const tagString = selected.map((t) => `#${t}`).join(' ')
  const combined = postText.trim() ? `${postText.trim()}\n\n${tagString}` : tagString
  const combinedLen = combined.length
  const over = Boolean(charLimit && combinedLen > charLimit)

  const byBucket = (bucket: HashtagSuggestion['bucket']): HashtagSuggestion[] =>
    (data?.suggestions ?? []).filter((s) => s.bucket === bucket)

  return (
    <div
      style={{
        marginTop: 14,
        background: 'var(--surface)',
        border: '2px solid var(--border)',
        borderRadius: 16,
        padding: 16
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ font: "700 14px 'Kalam'", color: 'var(--ink)' }}>Hashtags for this post</div>
          <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 2 }}>
            Ranked on fit to your draft, what&apos;s trending, and real usage data — not a generic top-50 list.
          </div>
        </div>
        <div
          style={{ ...primaryButtonSmall, opacity: !canRun || loading ? 0.6 : 1 }}
          onClick={!canRun || loading ? undefined : run}
        >
          {loading ? 'Reading the room…' : data ? 'Refresh' : 'Suggest hashtags'}
        </div>
      </div>

      {error && (
        <div
          style={{
            marginTop: 12,
            font: "700 12.5px 'Quicksand'",
            color: 'var(--danger-ink)',
            background: 'var(--accent-soft-bg)',
            border: '2px solid var(--border)',
            borderRadius: 12,
            padding: '10px 12px'
          }}
        >
          {error}
        </div>
      )}

      {data && (
        <div style={{ marginTop: 14 }}>
          {data.suggestions.length === 0 ? (
            <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
              Nothing worth suggesting yet — try after generating a draft, or collect some posts for this niche so it
              has performance data to lean on.
            </div>
          ) : (
            <>
              <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginBottom: 12 }}>
                Aim for about {data.recommendedCount} on {data.platform}. Tap to add.
              </div>

              {BUCKET_ORDER.map((bucket) => {
                const items = byBucket(bucket)
                if (!items.length) return null
                return (
                  <div key={bucket} style={{ marginBottom: 14 }}>
                    <div
                      style={{
                        font: "700 11px 'Quicksand'",
                        letterSpacing: '.05em',
                        textTransform: 'uppercase',
                        color: 'var(--accent-deep)',
                        marginBottom: 2
                      }}
                    >
                      {BUCKET_META[bucket].title}
                    </div>
                    <div style={{ font: "600 11px 'Quicksand'", color: 'var(--ink-faint)', marginBottom: 8 }}>
                      {BUCKET_META[bucket].blurb}
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                      {items.map((s) => (
                        <TagChip
                          key={s.tag}
                          suggestion={s}
                          selected={selected.includes(s.tag)}
                          onToggle={() => toggle(s.tag)}
                        />
                      ))}
                    </div>
                  </div>
                )
              })}

              <SourcesLine data={data} />
              {data.note && (
                <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 6 }}>
                  {data.note}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* --- selection tray ------------------------------------------------- */}
      {selected.length > 0 && (
        <div
          style={{
            marginTop: 14,
            borderTop: '2px dashed var(--border-soft)',
            paddingTop: 12
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <label style={label}>Selected ({selected.length})</label>
            <div
              style={{ font: "700 11.5px 'Quicksand'", color: over ? 'var(--danger-ink)' : 'var(--ink-faint)' }}
            >
              {postText.trim() ? 'post + tags ' : 'tags '}
              {combinedLen}
              {charLimit ? ` / ${charLimit}` : ''} chars{over ? ' — over the limit' : ''}
            </div>
          </div>
          <div
            style={{
              font: "700 13px 'Quicksand'",
              color: 'var(--accent-deep)',
              background: 'var(--surface-paper)',
              border: '2px solid var(--border-paper)',
              borderRadius: 12,
              padding: '10px 12px',
              wordBreak: 'break-word'
            }}
          >
            {tagString}
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
            <div
              style={primaryButtonSmall}
              onClick={() => {
                void navigator.clipboard.writeText(tagString)
                flash('tags')
              }}
            >
              {copied === 'tags' ? 'Copied ✓' : 'Copy tags'}
            </div>
            {postText.trim() && (
              <div
                style={secondaryButtonSmall}
                onClick={() => {
                  void navigator.clipboard.writeText(combined)
                  flash('post')
                }}
              >
                {copied === 'post' ? 'Copied ✓' : 'Copy post + tags'}
              </div>
            )}
            {/* Engage's post box belongs to Bluesky, so this only appears for a
                Bluesky draft. Sends whatever the tray currently holds — post plus
                the selected tags when there is a post, the tags alone when there
                is not, which is the case where you already pasted the post there. */}
            {platform === 'bluesky' && (
              <div
                style={secondaryButtonSmall}
                title="Open Engage with this in the Bluesky box, ready to send"
                onClick={() => sendToEngage(combined)}
              >
                {postText.trim() ? 'Send post + tags to Engage →' : 'Send tags to Engage →'}
              </div>
            )}
            {/* Nothing else keeps hashtag sets — the suggester has no library entry of its
                own — so this is a genuine save rather than a receipt for one. */}
            <SaveButton
              tool="Hashtags"
              title={`${platform} hashtags · ${niche || 'general'}`}
              subtitle="Hashtag set"
              content={tagString}
            />
            <div style={{ ...secondaryButtonSmall }} onClick={() => setSelected([])}>
              Clear
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function TagChip({
  suggestion,
  selected,
  onToggle
}: {
  suggestion: HashtagSuggestion
  selected: boolean
  onToggle: () => void
}): React.JSX.Element {
  const glyph = TREND_GLYPH[suggestion.trend]
  const vol = fmt(suggestion.volume)
  const reach = REACH_LABEL[suggestion.reach]
  const bits: string[] = []
  if (suggestion.trend !== 'unknown') bits.push(`trend: ${suggestion.trend}`)
  if (reach) bits.push(`reach: ${reach}`)
  if (vol) bits.push(`~${vol} weekly uses`)
  bits.push(`fit: ${Math.round(suggestion.suitability * 100)}%`)
  bits.push(`from ${suggestion.sources.map((s) => SOURCE_LABELS[s] ?? s).join(', ')}`)

  return (
    <div
      style={{ ...chip(selected), display: 'flex', alignItems: 'center', gap: 6 }}
      onClick={onToggle}
      title={bits.join(' · ')}
    >
      <span>#{suggestion.tag}</span>
      {glyph && <span style={{ fontSize: 11 }}>{glyph}</span>}
      {vol && (
        <span style={{ font: "700 10px 'Quicksand'", opacity: 0.75 }}>{vol}</span>
      )}
    </div>
  )
}

function SourcesLine({ data }: { data: HashtagSuggestResponse }): React.JSX.Element {
  const used = data.sourcesUsed.map((s) => {
    if (s === 'mastodon_trends') return `Mastodon trends (${data.trendInstance})`
    return SOURCE_LABELS[s] ?? s
  })
  return (
    <div style={{ font: "600 11px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 4 }}>
      {used.length > 0 && <>Sources: {used.join(' · ')}. </>}
      {data.sourcesUnavailable.length > 0 && (
        <span style={{ opacity: 0.8 }}>
          Unavailable this run: {data.sourcesUnavailable.map((s) => SOURCE_LABELS[s] ?? s).join(', ')}.
        </span>
      )}
    </div>
  )
}
