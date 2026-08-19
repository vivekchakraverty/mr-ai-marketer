import { useEffect, useState } from 'react'
import {
  collectTumblrNiche,
  generateTumblrPost,
  getTumblrPostStatus,
  importTumblrCorpus,
  generateTumblrImage,
  markTumblrPublished,
  measureTumblrPosts,
  suggestTumblrImagePrompt,
  type SocialGeneratedImage,
  type TumblrDraft,
  type TumblrImportResult,
  type TumblrPostStatus
} from '../api/client'
import { useAppStore } from '../state/store'
import { label, primaryButtonSmall, secondaryButtonSmall, select, textarea, textInput } from '../styles/styleKit'
import BrandVoiceSelect from '../components/BrandVoiceSelect'
import PostImagePanel from '../components/PostImagePanel'
import SaveCompositionButton from '../components/SaveCompositionButton'
import ScreenBackdrop from '../components/ScreenBackdrop'

/**
 * The Tumblr Post Creator.
 *
 * The third generator, and the one whose corpus it does not gather itself. Judging a
 * Tumblr post as "high engagement" needs the author's own baseline — Tumblr publishes no
 * follower count for blogs you do not control — and computing that costs a page of API
 * calls per blog, so a separate collector project does that crawl over days and this
 * screen imports its output. That is why the first control here is Import rather than
 * Collect, and why Collect is described as a top-up.
 *
 * The screen also says out loud when a niche is borrowing. Tumblr's high-engagement
 * corpus is overwhelmingly art and fandom, so tech-leaning niches genuinely have little
 * there, and a pool that looks full while being grounded in someone else's subject
 * matter would be a worse lie than an empty one.
 */
export default function TumblrPost(): React.JSX.Element {
  const fields = useAppStore((s) => s.fields.tumblr)
  const setField = useAppStore((s) => s.setTumblrField)
  const goCreate = useAppStore((s) => s.goCreate)

  // What the two panels below currently hold, so one button can keep the finished
  // post — words, tags and picture — as a single Library entry instead of three
  // unrelated ones.
  const [keptImage, setKeptImage] = useState<SocialGeneratedImage | null>(null)
  // No hashtag panel on this screen — Tumblr's tags are a field on the post itself, not
  // a suggestion list — so the composition here is words plus picture.

  const [status, setStatus] = useState<TumblrPostStatus | null>(null)
  const [result, setResult] = useState<TumblrDraft | null>(null)
  const [imported, setImported] = useState<TumblrImportResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [showNiches, setShowNiches] = useState(false)
  const [collecting, setCollecting] = useState<string | null>(null)
  const [brandVoiceId, setBrandVoiceId] = useState('')
  const [postedUrl, setPostedUrl] = useState('')
  const [linking, setLinking] = useState(false)
  const [linked, setLinked] = useState('')

  async function refresh(): Promise<void> {
    try {
      setStatus(await getTumblrPostStatus())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => {
    void refresh()
    // Measurement of your own published posts runs here rather than on a backend timer:
    // Tumblr signs every read, and the credentials live in Electron's store, not the
    // backend. Opening the screen is when they are available. Failures are silent —
    // nothing is measured, which is the same as before, and the screen still works.
    void measureTumblrPosts()
      .then((m) => {
        if (m.rebuilt.length) void refresh()
      })
      .catch(() => {})
  }, [])

  const niches = status?.niches ?? []
  const current = niches.find((n) => n.name === fields.niche)

  async function handleCollect(name: string): Promise<void> {
    setCollecting(name)
    setError('')
    try {
      await collectTumblrNiche(name)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setCollecting(null)
    }
  }

  async function handleImport(): Promise<void> {
    setImporting(true)
    setError('')
    try {
      setImported(await importTumblrCorpus())
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setImporting(false)
    }
  }

  async function handleGenerate(retry = false): Promise<void> {
    if (!fields.userInput.trim() || !fields.niche) return
    setLoading(true)
    setError('')
    try {
      const draft = await generateTumblrPost(
        fields.userInput,
        fields.niche,
        brandVoiceId,
        fields.sourceUrl,
        // On a retry the model has to be told what it already wrote; temperature alone
        // does not move it off its preferred opening.
        retry && result ? [result.text] : []
      )
      setResult(draft)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ position: 'relative', minHeight: '100%' }}>
      <ScreenBackdrop video="engage" />
      <div style={{ position: 'relative', maxWidth: 900, margin: '0 auto', padding: '30px 34px 60px' }}>
        <div style={{ ...secondaryButtonSmall, display: 'inline-block' }} onClick={goCreate}>
          ← Create
        </div>

        <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)', marginTop: 14 }}>
          Tumblr Post Creator
        </div>
        <div style={{ font: "600 13px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 18 }}>
          Grounded in Tumblr posts that actually earned their notes. Notes are Tumblr&rsquo;s
          single engagement number — likes, reblogs and replies together — and reach is
          estimated from each blog&rsquo;s own typical note count, because Tumblr does not
          publish follower totals for blogs you don&rsquo;t control.
        </div>

        {/* --- corpus state ------------------------------------------------ */}
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div style={{ font: "700 15px 'Quicksand'", color: 'var(--ink)' }}>
                {status ? `${status.posts.toLocaleString()} posts · ${status.exemplars} exemplars` : 'Checking…'}
              </div>
              <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 3 }}>
                {status?.corpusFound
                  ? `Corpus: ${status.corpusPath}`
                  : status
                    ? `No corpus found at ${status.corpusPath}`
                    : ''}
              </div>
            </div>
            <div
              style={{ ...primaryButtonSmall, opacity: importing || !status?.corpusFound ? 0.6 : 1 }}
              onClick={importing || !status?.corpusFound ? undefined : () => void handleImport()}
            >
              {importing ? 'Importing…' : 'Import corpus'}
            </div>
          </div>

          {status?.note && (
            <div
              style={{
                font: "600 12px/1.6 'Quicksand'",
                color: 'var(--ink-muted)',
                background: 'var(--tip-bg)',
                border: '2px dashed var(--border-soft)',
                borderRadius: 12,
                padding: '9px 12px'
              }}
            >
              {status.note}
            </div>
          )}

          {imported && (
            <div style={{ font: "600 12px/1.6 'Quicksand'", color: 'var(--ink-muted)' }}>
              Imported {imported.imported.toLocaleString()} posts from {imported.blogs} blogs.{' '}
              {Object.entries(imported.perNiche)
                .sort((a, b) => b[1] - a[1])
                .map(([n, c]) => `${n} ${c}`)
                .join(' · ')}
            </div>
          )}
        </Card>

        {/* --- composer ---------------------------------------------------- */}
        <div style={{ marginTop: 16 }}>
          <Card>
            <div>
              <label style={label}>What&rsquo;s the post about?</label>
              <textarea
                value={fields.userInput}
                onChange={(e) => setField('userInput', e.target.value)}
                placeholder="the thing you want to say, in your own words"
                style={{ ...textarea, minHeight: 96 }}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <label style={label}>Niche</label>
                <select
                  value={fields.niche}
                  onChange={(e) => setField('niche', e.target.value)}
                  style={select}
                >
                  <option value="">Pick a niche</option>
                  {!niches.length && <option value="">No niches yet — add one below</option>}
                  {niches.map((n) => (
                    <option key={n.name} value={n.name}>
                      {n.name} ({n.exemplars})
                    </option>
                  ))}
                </select>
                {current?.borrowing && (
                  <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 4 }}>
                    Thin on Tumblr — this will borrow the general pool&rsquo;s register.
                  </div>
                )}
              </div>
              <div>
                <label style={label}>Brand voice</label>
                <BrandVoiceSelect value={brandVoiceId} onChange={setBrandVoiceId} />
              </div>
            </div>

            <div>
              <label style={label}>Write about a link (optional)</label>
              <input
                value={fields.sourceUrl}
                onChange={(e) => setField('sourceUrl', e.target.value)}
                placeholder="https://…"
                style={textInput}
              />
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div
                style={{
                  ...primaryButtonSmall,
                  opacity: loading || !fields.userInput.trim() || !fields.niche ? 0.55 : 1
                }}
                onClick={
                  loading || !fields.userInput.trim() || !fields.niche
                    ? undefined
                    : () => void handleGenerate()
                }
              >
                {loading ? (fields.sourceUrl.trim() ? 'Reading the link…' : 'Thinking…') : 'Write it'}
              </div>
              <div
                style={{ ...secondaryButtonSmall, marginLeft: 'auto' }}
                onClick={() => setShowNiches((v) => !v)}
              >
                {showNiches ? 'Hide niches' : 'Niches'}
              </div>
            </div>
          </Card>
        </div>

        {showNiches && <TumblrNiches status={status} onCollect={handleCollect} busy={collecting} />}

        {/* --- draft -------------------------------------------------------- */}
        {result && (
          <div style={{ marginTop: 16 }}>
            <Card>
              <div style={{ whiteSpace: 'pre-wrap', font: "600 14px/1.7 'Quicksand'", color: 'var(--ink)' }}>
                {result.text}
              </div>

              {result.tags.length > 0 && (
                <div>
                  <div style={{ font: "700 12px 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 6 }}>
                    Tags that recur across the exemplars
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {result.tags.map((t) => (
                      <span
                        key={t}
                        style={{
                          font: "600 12px 'Quicksand'",
                          background: 'var(--tip-bg)',
                          border: '2px solid var(--border-soft)',
                          borderRadius: 999,
                          padding: '3px 10px',
                          color: 'var(--ink-muted)'
                        }}
                      >
                        #{t}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {result.borrowedFrom && (
                <div style={{ font: "600 12px/1.6 'Quicksand'", color: 'var(--ink-faint)' }}>
                  Grounded in the <strong>{result.borrowedFrom}</strong> pool — this niche has too
                  little Tumblr material of its own, so the register is Tumblr&rsquo;s but the
                  subject matter isn&rsquo;t this niche&rsquo;s.
                </div>
              )}

              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <div
                  style={secondaryButtonSmall}
                  onClick={() => {
                    void navigator.clipboard.writeText(
                      result.tags.length
                        ? `${result.text}\n\n${result.tags.map((t) => `#${t}`).join(' ')}`
                        : result.text
                    )
                    setCopied(true)
                    setTimeout(() => setCopied(false), 1600)
                  }}
                >
                  {copied ? 'Copied' : 'Copy'}
                </div>
                <div style={secondaryButtonSmall} onClick={() => void handleGenerate(true)}>
                  Try again
                </div>
                <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginLeft: 'auto' }}>
                  {result.model}
                </div>
              </div>

              {/* Closing the loop. Until a draft is linked to a real post, the tool can
                  only ever learn from strangers — this is what lets your own results
                  compete for a place in the pool. */}
              <div style={{ borderTop: '2px dashed var(--border-soft)', paddingTop: 12 }}>
                <label style={label}>Published it? Paste the link</label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <input
                    value={postedUrl}
                    onChange={(e) => setPostedUrl(e.target.value)}
                    placeholder="https://your-blog.tumblr.com/post/123456789"
                    style={{ ...textInput, flex: 1 }}
                  />
                  <div
                    style={{
                      ...secondaryButtonSmall,
                      opacity: linking || !postedUrl.trim() || !result.generationId ? 0.55 : 1
                    }}
                    onClick={
                      linking || !postedUrl.trim() || !result.generationId
                        ? undefined
                        : async () => {
                            setLinking(true)
                            setError('')
                            try {
                              const r = await markTumblrPublished(
                                result.generationId,
                                result.niche,
                                postedUrl.trim()
                              )
                              setLinked(
                                `Linked — ${r.notes} notes so far. Ranked against your median of ` +
                                  `${r.medianNotes} notes` +
                                  (r.followers ? ` (${r.followers.toLocaleString()} followers).` : '.')
                              )
                              setPostedUrl('')
                            } catch (err) {
                              setError(err instanceof Error ? err.message : String(err))
                            } finally {
                              setLinking(false)
                            }
                          }
                    }
                  >
                    {linking ? 'Linking…' : 'I published this'}
                  </div>
                </div>
                <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 5 }}>
                  {linked ||
                    'Its notes get re-read at 1h, 24h and 48h whenever you open this screen, so your own posts can earn a place in the pool.'}
                </div>
              </div>

              <details>
                <summary style={{ font: "700 12px 'Quicksand'", color: 'var(--ink-muted)', cursor: 'pointer' }}>
                  What it learned from ({result.exemplars.length})
                </summary>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
                  {result.exemplars.map((ex) => (
                    <a
                      key={ex.postUrl}
                      href={ex.postUrl}
                      target="_blank"
                      rel="noreferrer"
                      style={{ textDecoration: 'none' }}
                    >
                      <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>
                        @{ex.blog} · {ex.notes.toLocaleString()} notes
                        {ex.isYours && (
                          <span
                            style={{
                              marginLeft: 6,
                              padding: '1px 7px',
                              borderRadius: 999,
                              background: 'var(--tip-bg)',
                              border: '1.5px solid var(--border-soft)',
                              color: 'var(--ink-muted)'
                            }}
                            title="Your own posts hold a reserved slot in the pool, so the tool learns from your results as well as the corpus."
                          >
                            yours
                          </span>
                        )}
                      </div>
                      <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)' }}>
                        {ex.text}
                      </div>
                    </a>
                  ))}
                </div>
              </details>
            </Card>
          </div>
        )}

        {result && (
          <PostImagePanel
            onImage={setKeptImage}
            postText={result.text}
            onSuggest={() => suggestTumblrImagePrompt(result.text, result.niche)}
            onGenerate={(prompt) => generateTumblrImage(prompt, result.text)}
          />
        )}

        {/* --- keep the finished thing ------------------------------------- */}
        {result && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
            <SaveCompositionButton
              tool="Social"
              title={`Tumblr post · ${fields.niche || 'untitled'}`}
              subtitle="Tumblr post"
              postText={result.text}
                imageUrl={keptImage?.url ?? ''}
            />
          </div>
        )}

        {error && (
          <div style={{ font: "700 13px 'Quicksand'", color: 'var(--danger-ink)', marginTop: 14 }}>{error}</div>
        )}
      </div>
    </div>
  )
}

/**
 * Tumblr's niches, listed rather than edited.
 *
 * The other two generators share one hand-written niche list and let you add to it here.
 * Tumblr's come from the collector's own classifier instead — art_design, books_writing,
 * lgbtq_community and so on — because those are the topics Tumblr is actually organised
 * around, and they are kept off the shared list so they don't fill the Bluesky and
 * Mastodon dropdowns with categories that are empty there. So there is nothing to add
 * by hand: a new Tumblr niche appears when the collector classifies posts into it.
 */
function TumblrNiches({
  status,
  onCollect,
  busy
}: {
  status: TumblrPostStatus | null
  onCollect: (name: string) => Promise<void>
  busy: string | null
}): React.JSX.Element {
  const niches = status?.niches ?? []
  return (
    <div
      style={{
        marginTop: 16,
        background: 'var(--surface-paper)',
        border: '2.5px solid var(--border-paper)',
        borderRadius: 20,
        padding: 18,
        boxShadow: 'var(--shadow-paper)'
      }}
    >
      <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)', marginBottom: 2 }}>Niches</div>
      <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 14 }}>
        These come from the collector&rsquo;s own classification of the corpus, not from the
        niche list the Bluesky and Mastodon tools share — Tumblr organises itself around
        different topics. Add one by collecting posts for it, not by typing it here.
      </div>

      {!niches.length && (
        <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)', padding: '10px 0' }}>
          Nothing imported yet. Run Import above.
        </div>
      )}

      {niches.map((n) => (
        <div
          key={n.name}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '10px 0',
            borderTop: '2px dashed var(--border-soft)'
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ font: "700 14px 'Quicksand'", color: 'var(--ink)' }}>{n.name}</div>
            <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 2 }}>
              {n.keywords.length ? n.keywords.join(' · ') : 'no tags yet'}
            </div>
          </div>
          <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-muted)', whiteSpace: 'nowrap' }}>
            {n.posts} posts · {n.exemplars} exemplars
          </div>
          <div
            style={{
              ...secondaryButtonSmall,
              opacity: !status?.connected || busy ? 0.5 : 1,
              cursor: status?.connected && !busy ? 'pointer' : 'default'
            }}
            title={
              status?.connected
                ? "Top this niche up from Tumblr's tag pages"
                : 'Connect Tumblr in Settings to top up from live tag pages'
            }
            onClick={!status?.connected || busy ? undefined : () => void onCollect(n.name)}
          >
            {busy === n.name ? 'Collecting…' : 'Top up'}
          </div>
        </div>
      ))}
    </div>
  )
}

function Card({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
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
      {children}
    </div>
  )
}
