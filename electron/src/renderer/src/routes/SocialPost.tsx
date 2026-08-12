import { useEffect, useState } from 'react'
import {
  collectSocialNiche,
  generateSocialPost,
  generateSocialPostImage,
  getSocialStatus,
  listSocialNiches,
  markSocialPublished,
  saveSocialNiche,
  type SocialGeneratedImage,
  type SocialGenerateResponse,
  type SocialNiche,
  type SocialStatus
} from '../api/client'
import { refreshLibrary } from '../state/actions'
import { useAppStore } from '../state/store'
import { label, primaryButtonSmall, secondaryButtonSmall, select, textarea, textInput } from '../styles/styleKit'
import BackendImage from '../components/BackendImage'
import BrandVoiceSelect from '../components/BrandVoiceSelect'
import NichePanel from '../components/NichePanel'
import HashtagSuggester from '../components/HashtagSuggester'
import ScreenBackdrop from '../components/ScreenBackdrop'
import SaveButton from '../components/SaveButton'

const PLATFORMS = ['bluesky', 'x', 'linkedin', 'mastodon']

// Bluesky is the only platform we can actually measure, so it is the only one with
// a hard limit worth enforcing in the UI.
const CHAR_LIMIT: Record<string, number> = { bluesky: 300, x: 280 }

export default function SocialPost(): React.JSX.Element {
  const fields = useAppStore((s) => s.fields.social)
  const setSocialField = useAppStore((s) => s.setSocialField)
  const goCreate = useAppStore((s) => s.goCreate)
  const goSettings = useAppStore((s) => s.goSettings)
  const sendToEngage = useAppStore((s) => s.sendToEngage)

  const [status, setStatus] = useState<SocialStatus | null>(null)
  const [niches, setNiches] = useState<SocialNiche[]>([])
  const [result, setResult] = useState<SocialGenerateResponse | null>(null)
  // Recent posts for this request, sent back on a rewrite so the model is told
  // what not to repeat. Capped at three — enough to break the model out of its
  // favourite opening without spending the prompt budget on old drafts.
  const [previousTexts, setPreviousTexts] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [postImage, setPostImage] = useState<SocialGeneratedImage | null>(null)
  const [imageLoading, setImageLoading] = useState(false)
  const [imageError, setImageError] = useState('')

  const [postedUrl, setPostedUrl] = useState('')
  const [linking, setLinking] = useState(false)
  const [linked, setLinked] = useState('')

  const [showNiches, setShowNiches] = useState(false)
  const [brandVoiceId, setBrandVoiceId] = useState('')

  async function refresh(): Promise<void> {
    try {
      const [s, n] = await Promise.all([getSocialStatus(), listSocialNiches()])
      setStatus(s)
      setNiches(n)
      if (!fields.niche && n.length) setSocialField('niche', n[0].name)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /**
   * Generate a post, or rewrite the one already on screen.
   *
   * `rewrite` keeps the current result visible while the new one is being
   * written. Clearing it made the whole panel — including the button just
   * clicked — vanish, leaving the only busy indicator on the primary button
   * a couple of hundred pixels up the page, which read as nothing happening.
   */
  async function handleGenerate(rewrite = false): Promise<void> {
    if (!fields.userInput.trim() || !fields.niche) return
    if (loading) return
    setLoading(true)
    setError('')
    if (!rewrite) setResult(null)
    setPostImage(null)
    setImageError('')
    setLinked('')
    setPostedUrl('')
    try {
      const res = await generateSocialPost(
        fields.userInput,
        fields.niche,
        fields.platform,
        fields.sourceUrl,
        // What it has already written for this request. Without this the prompt
        // is byte-identical every time and the model reliably opens with the same
        // sentence, so "Try again" returned the same post in different words.
        rewrite ? previousTexts : [],
        brandVoiceId
      )
      setResult(res)
      // A fresh generate starts the history over — a new topic should not be
      // steered away from the wording of the last one.
      setPreviousTexts((prev) => (rewrite ? [...prev, res.text].slice(-3) : [res.text]))
      void refreshLibrary()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  async function handleGenerateImage(): Promise<void> {
    if (!result?.text.trim()) return
    setImageLoading(true)
    setImageError('')
    try {
      setPostImage(await generateSocialPostImage(result.text, fields.niche, fields.platform))
    } catch (err) {
      setImageError(err instanceof Error ? err.message : String(err))
    } finally {
      setImageLoading(false)
    }
  }

  async function handleLink(): Promise<void> {
    if (!result?.generationId || !postedUrl.trim()) return
    setLinking(true)
    setError('')
    try {
      const res = await markSocialPublished(result.generationId, postedUrl.trim(), fields.niche)
      setLinked(res.postedUri)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLinking(false)
    }
  }

  const limit = CHAR_LIMIT[fields.platform]
  const over = result ? Boolean(limit && result.characters > limit) : false

  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '22px 34px 60px' }}>
      <ScreenBackdrop video="social" />
      <div
        style={{ font: "700 13px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer', marginBottom: 14 }}
        onClick={goCreate}
      >
        ← Create
      </div>

      <div style={{ marginBottom: 20, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)' }}>Social Post Generator</div>
          <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
            Learns from posts that actually did numbers in your corner of the internet, then writes like they do —
            without stealing their homework.
          </div>
        </div>
        <div
          style={{
            width: 34,
            height: 34,
            background: 'var(--tool-social)',
            borderRadius: '53% 47% 47% 53%',
            border: '2.5px solid var(--border)',
            animation: 'bob 3.4s ease-in-out infinite',
            flexShrink: 0
          }}
        />
      </div>

      {/* --- setup nudges ------------------------------------------------- */}
      {status && !status.configured && (
        <Banner
          tone="warn"
          title="It needs a key before it can write anything"
          body={`Missing: ${status.missing.join(', ')}. Pop them into Settings and come back.`}
          actionLabel="Open Settings"
          onAction={goSettings}
        />
      )}
      {status?.configured && !status.readyToGround && (
        <Banner
          tone="info"
          title="Writing blind for now"
          body="No exemplars yet, so drafts lean on platform norms alone. Collect some posts below, then give them 48 hours to prove themselves — that's when the grounding kicks in."
        />
      )}
      {status?.needsConsent && (
        <Banner
          tone="info"
          title="One-time consent needed"
          body="This tool pools anonymous performance metrics to get better for everyone. Approve it in Settings to start generating."
          actionLabel="Open Settings"
          onAction={goSettings}
        />
      )}

      {/* --- composer ----------------------------------------------------- */}
      <div
        style={{
          background: 'var(--surface)',
          border: '2.5px solid var(--border)',
          borderRadius: 20,
          padding: 18,
          boxShadow: 'var(--shadow-md)',
          display: 'flex',
          flexDirection: 'column',
          gap: 14
        }}
      >
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>
          <div>
            <BrandVoiceSelect
              value={brandVoiceId}
              onChange={setBrandVoiceId}
              hint="Posts follow this brand's tone and guardrails."
            />
            <label style={label}>Niche</label>
            <select
              value={fields.niche}
              onChange={(e) => setSocialField('niche', e.target.value)}
              style={select}
            >
              {!niches.length && <option value="">No niches yet — add one below</option>}
              {niches.map((n) => (
                <option key={n.name} value={n.name}>
                  {n.name} ({n.exemplars} exemplars)
                </option>
              ))}
            </select>
          </div>
          <div>
            <label style={label}>Platform</label>
            <select
              value={fields.platform}
              onChange={(e) => setSocialField('platform', e.target.value)}
              style={select}
            >
              {PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label style={label}>What's the post about?</label>
          <textarea
            rows={3}
            value={fields.userInput}
            onChange={(e) => setSocialField('userInput', e.target.value)}
            placeholder="e.g. announce that my CLI tool finally works on Windows, mildly smug tone"
            style={textarea}
          />
        </div>

        <div>
          <label style={label}>Link to write about — optional</label>
          <input
            value={fields.sourceUrl}
            onChange={(e) => setSocialField('sourceUrl', e.target.value)}
            placeholder="https://… a release note, article or changelog to pull the facts from"
            style={textInput}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div
            style={{ ...primaryButtonSmall, opacity: loading || !fields.niche ? 0.6 : 1 }}
            onClick={loading || !fields.niche ? undefined : () => void handleGenerate()}
          >
            {loading ? (fields.sourceUrl.trim() ? 'Reading the link…' : 'Thinking…') : 'Write it'}
          </div>
          <div
            style={{ ...secondaryButtonSmall }}
            onClick={() => setShowNiches((v) => !v)}
          >
            {showNiches ? 'Hide niches' : 'Manage niches'}
          </div>
          {status && (
            <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginLeft: 'auto' }}>
              {status.provider}/{status.model.split('/').pop()} · {status.posts} posts watched
            </div>
          )}
        </div>
      </div>

      {error && (
        <div
          style={{
            marginTop: 14,
            font: "700 13px 'Quicksand'",
            color: 'var(--danger-ink)',
            background: 'var(--accent-soft-bg)',
            border: '2px solid var(--border)',
            borderRadius: 14,
            padding: '11px 14px'
          }}
        >
          {error}
        </div>
      )}

      {/* --- niches ------------------------------------------------------- */}
      {showNiches && (
        <NichePanel
          niches={niches}
          onCollect={async (name) => {
            await collectSocialNiche(name)
            await refresh()
          }}
          onAdd={async (name, keywords) => {
            await saveSocialNiche(name, keywords)
            await refresh()
          }}
        />
      )}

      {/* --- draft -------------------------------------------------------- */}
      {result && (
        <div style={{ marginTop: 18 }}>
          <div
            style={{
              background: 'var(--surface-paper)',
              border: '2.5px solid var(--border-paper)',
              borderRadius: 20,
              padding: 20,
              boxShadow: 'var(--shadow-paper)'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>Here you go</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <SaveButton
                libraryId={result.libraryId}
                tool="Social"
                title={`${fields.platform} post · ${fields.niche}`}
                subtitle="Social post"
                content={result.text}
              />
              <div
                style={{
                  font: "700 12px 'Quicksand'",
                  color: over ? 'var(--danger-ink)' : 'var(--ink-faint)'
                }}
              >
                {result.characters}
                {limit ? ` / ${limit}` : ''} characters{over ? ' — too long, trim it' : ''}
              </div>
              </div>
            </div>

            <div
              style={{
                font: "600 15px/1.6 'Quicksand'",
                color: 'var(--ink)',
                background: 'var(--surface)',
                border: '2px solid var(--border)',
                borderRadius: 14,
                padding: '14px 16px',
                whiteSpace: 'pre-wrap'
              }}
            >
              {result.text}
            </div>

            <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
              <div
                style={primaryButtonSmall}
                onClick={() => {
                  void navigator.clipboard.writeText(result.text)
                  setCopied(true)
                  setTimeout(() => setCopied(false), 1600)
                }}
              >
                {copied ? 'Copied ✓' : 'Copy'}
              </div>
              {/* Engage's post box is Bluesky's, so this only makes sense for a
                  Bluesky draft — offering it for an X or LinkedIn post would send
                  a draft written to the wrong norms to the wrong network. */}
              {fields.platform === 'bluesky' && (
                <div
                  style={secondaryButtonSmall}
                  title="Open Engage with this post in the Bluesky box, ready to send"
                  onClick={() => sendToEngage(result.text)}
                >
                  Send to Engage →
                </div>
              )}
              <div
                style={{ ...secondaryButtonSmall, opacity: loading ? 0.6 : 1 }}
                title="Write a different post for the same request"
                onClick={loading ? undefined : () => void handleGenerate(true)}
              >
                {loading ? 'Rewriting…' : 'Try again'}
              </div>
              <div
                style={{ ...secondaryButtonSmall, opacity: imageLoading ? 0.6 : 1 }}
                onClick={imageLoading ? undefined : handleGenerateImage}
              >
                {imageLoading ? 'Generating image…' : postImage ? 'Regenerate image' : 'Generate image'}
              </div>
            </div>
            {imageError && (
              <div style={{ marginTop: 10, font: "700 12.5px 'Quicksand'", color: 'var(--danger-ink)' }}>{imageError}</div>
            )}
            {postImage && (
              <div style={{ marginTop: 14 }}>
                <BackendImage
                  url={postImage.url}
                  alt="Generated visual for the post"
                  style={{
                    width: '100%',
                    maxHeight: 640,
                    objectFit: 'contain',
                    background: 'var(--surface)',
                    border: '2px solid var(--border)',
                    borderRadius: 8,
                    display: 'block'
                  }}
                />
              </div>
            )}
          </div>

          {/* --- what informed it ---------------------------------------- */}
          <details
            style={{
              marginTop: 14,
              background: 'var(--surface)',
              border: '2px solid var(--border)',
              borderRadius: 16,
              padding: '12px 16px'
            }}
          >
            <summary style={{ font: "700 13px 'Quicksand'", color: 'var(--ink)', cursor: 'pointer' }}>
              Where this came from — {result.exemplars.length} exemplars, {result.kbArticles.length} platform notes
              {result.source ? ', 1 linked source' : ''}
            </summary>
            <div style={{ marginTop: 12 }}>
              {result.source && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ font: "700 11.5px 'Quicksand'", color: 'var(--accent-deep)' }}>
                    Source material
                    <span
                      style={{ color: 'var(--accent)', cursor: 'pointer', marginLeft: 8 }}
                      onClick={() => window.api.openExternal(result.source!.url)}
                    >
                      {result.source.title || result.source.url} →
                    </span>
                  </div>
                  <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 3 }}>
                    {result.source.excerpt}
                    {result.source.truncated ? '…' : ''}
                  </div>
                </div>
              )}
              {result.exemplars.length === 0 && !result.source && (
                <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
                  Nothing to show yet — this one ran on platform norms alone.
                </div>
              )}
              {result.exemplars.map((e, i) => (
                <div key={e.id} style={{ marginBottom: 12 }}>
                  <div style={{ font: "700 11.5px 'Quicksand'", color: 'var(--accent-deep)' }}>
                    {i + 1}. similarity {e.similarity} · score {e.score}
                    {e.webUrl && (
                      <span
                        style={{ color: 'var(--accent)', cursor: 'pointer', marginLeft: 8 }}
                        onClick={() => window.api.openExternal(e.webUrl)}
                      >
                        view original →
                      </span>
                    )}
                  </div>
                  <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 3 }}>
                    {e.text}
                  </div>
                </div>
              ))}
              {result.kbArticles.map((k) => (
                <div key={k.id} style={{ marginTop: 10 }}>
                  <div style={{ font: "700 11.5px 'Quicksand'", color: 'var(--accent-deep)' }}>
                    {k.source} · weight {k.decayWeight}
                  </div>
                  <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)' }}>{k.summary}</div>
                </div>
              ))}
            </div>
          </details>

          {/* --- close the loop ------------------------------------------ */}
          <div
            style={{
              marginTop: 14,
              background: 'var(--surface)',
              border: '2px solid var(--border)',
              borderRadius: 16,
              padding: 16
            }}
          >
            <div style={{ font: "700 14px 'Kalam'", color: 'var(--ink)' }}>Did you actually post it?</div>
            <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', margin: '4px 0 10px' }}>
              Paste the link and it'll watch how the post does, then use that to write better ones. This is the only
              way it learns from <i>you</i> rather than strangers.
            </div>
            {linked ? (
              <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent)' }}>
                Linked ✓ — engagement gets measured at 1h, 24h and 48h.
              </div>
            ) : (
              <div style={{ display: 'flex', gap: 10 }}>
                <input
                  value={postedUrl}
                  onChange={(e) => setPostedUrl(e.target.value)}
                  placeholder="https://bsky.app/profile/you.bsky.social/post/3k…"
                  style={{ ...textInput, flex: 1 }}
                />
                <div
                  style={{ ...secondaryButtonSmall, opacity: linking || !postedUrl.trim() ? 0.6 : 1 }}
                  onClick={linking || !postedUrl.trim() ? undefined : handleLink}
                >
                  {linking ? 'Linking…' : 'Link it'}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* --- hashtag suggester ------------------------------------------- */}
      {(fields.niche || fields.userInput.trim()) && (
        <HashtagSuggester
          draft={(result?.text ?? '').trim() || fields.userInput}
          postText={result?.text ?? ''}
          niche={fields.niche}
          platform={fields.platform}
          charLimit={limit}
        />
      )}
    </div>
  )
}

function Banner({
  tone,
  title,
  body,
  actionLabel,
  onAction
}: {
  tone: 'warn' | 'info'
  title: string
  body: string
  actionLabel?: string
  onAction?: () => void
}): React.JSX.Element {
  return (
    <div
      style={{
        marginBottom: 14,
        background: tone === 'warn' ? 'var(--accent-soft-bg)' : 'var(--tip-bg)',
        border: '2px solid var(--border)',
        borderRadius: 16,
        padding: '12px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 14
      }}
    >
      <div style={{ flex: 1 }}>
        <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>{title}</div>
        <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 2 }}>{body}</div>
      </div>
      {actionLabel && onAction && (
        <div style={secondaryButtonSmall} onClick={onAction}>
          {actionLabel}
        </div>
      )}
    </div>
  )
}
