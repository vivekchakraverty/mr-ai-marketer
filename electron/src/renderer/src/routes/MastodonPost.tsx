import { useEffect, useState } from 'react'
import {
  acceptMastodonPolicy,
  collectMastodonNiche,
  generateMastodonImage,
  generateMastodonPost,
  getMastodonPolicy,
  getMastodonStatus,
  listMastodonNiches,
  saveSocialNiche,
  markMastodonPublished,
  suggestMastodonImagePrompt,
  revokeMastodonPolicy,
  type SocialGeneratedImage,
  type MastodonCollectResponse,
  type MastodonGenerateResponse,
  type MastodonNiche,
  type MastodonPolicy,
  type MastodonRule,
  type MastodonStatus
} from '../api/client'
import { refreshLibrary } from '../state/actions'
import { useAppStore } from '../state/store'
import { label, primaryButtonSmall, secondaryButtonSmall, select, textarea, textInput } from '../styles/styleKit'
import BrandVoiceSelect from '../components/BrandVoiceSelect'
import NichePanel from '../components/NichePanel'
import HashtagSuggester from '../components/HashtagSuggester'
import PostingTimePanel from '../components/PostingTimePanel'
import PostImagePanel from '../components/PostImagePanel'
import SaveCompositionButton from '../components/SaveCompositionButton'
import SendToEngageButton from '../components/SendToEngageButton'
import VideoEmbedInput from '../components/VideoEmbedInput'
import UploadVideoButton, { type ChosenVideo } from '../components/UploadVideoButton'
import ScreenBackdrop from '../components/ScreenBackdrop'
import SaveButton from '../components/SaveButton'

/**
 * The Mastodon Post Creator.
 *
 * Structurally the Bluesky generator's sibling, with one screen in front of it
 * that the Bluesky one has no need for: the instance's own rules. Mastodon has
 * no central terms of service, several instances require generative-AI use to be
 * disclosed, and several ban commercial promotion outright — so the composer
 * stays locked until those rules have been read and accepted, and the backend
 * enforces the same thing rather than trusting this screen to.
 */
export default function MastodonPost(): React.JSX.Element {
  const fields = useAppStore((s) => s.fields.mastodon)
  const setField = useAppStore((s) => s.setMastodonField)
  const goCreate = useAppStore((s) => s.goCreate)
  const goSettings = useAppStore((s) => s.goSettings)

  // What the two panels below currently hold, so one button can keep the finished
  // post — words, tags and picture — as a single Library entry instead of three
  // unrelated ones.
  const [keptImage, setKeptImage] = useState<SocialGeneratedImage | null>(null)
  const [videoUrl, setVideoUrl] = useState('')
  const [videoFile, setVideoFile] = useState<ChosenVideo | null>(null)
  const [videoFileAlt, setVideoFileAlt] = useState('')
  const [keptTags, setKeptTags] = useState<string[]>([])

  const [status, setStatus] = useState<MastodonStatus | null>(null)
  const [policy, setPolicy] = useState<MastodonPolicy | null>(null)
  const [niches, setNiches] = useState<MastodonNiche[]>([])
  const [result, setResult] = useState<MastodonGenerateResponse | null>(null)
  const [collected, setCollected] = useState<MastodonCollectResponse | null>(null)

  const [instanceDraft, setInstanceDraft] = useState(fields.instance)
  const [loadingPolicy, setLoadingPolicy] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [collecting, setCollecting] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [brandVoiceId, setBrandVoiceId] = useState('')
  // Recent drafts for this request, sent back on a rewrite so the model is told what not
  // to repeat. Capped at three — enough to break it out of its favourite opening without
  // spending the prompt budget on old drafts.
  const [previousTexts, setPreviousTexts] = useState<string[]>([])
  const [showNiches, setShowNiches] = useState(false)
  const [copied, setCopied] = useState(false)

  const [postedUrl, setPostedUrl] = useState('')
  const [linking, setLinking] = useState(false)
  const [linked, setLinked] = useState('')

  // Remember the instance across sessions — it is the one setting that makes
  // every other call on this screen answerable.
  useEffect(() => {
    void (async () => {
      const settings = await window.api.settings.getAll()
      const saved = settings.mastodonInstance ?? ''
      if (saved && !fields.instance) {
        setField('instance', saved)
        setInstanceDraft(saved)
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    void refreshAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fields.instance])

  async function refreshAll(): Promise<void> {
    try {
      const n = await listMastodonNiches(fields.instance)
      setNiches(n)
      if (!fields.niche && n.length) setField('niche', n[0].name)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
    if (!fields.instance) {
      setStatus(null)
      setPolicy(null)
      return
    }
    setLoadingPolicy(true)
    try {
      const [s, p] = await Promise.all([
        getMastodonStatus(fields.instance),
        getMastodonPolicy(fields.instance)
      ])
      setStatus(s)
      setPolicy(p)
      setError('')
    } catch (err) {
      setPolicy(null)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoadingPolicy(false)
    }
  }

  async function handleConnectInstance(): Promise<void> {
    const value = instanceDraft.trim()
    if (!value) return
    await window.api.settings.setAll({ mastodonInstance: value })
    setField('instance', value)
  }

  async function handleAccept(): Promise<void> {
    if (!policy) return
    setAccepting(true)
    setError('')
    try {
      await acceptMastodonPolicy(policy.instance, policy.policyHash)
      await refreshAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setAccepting(false)
    }
  }

  async function handleRevoke(): Promise<void> {
    if (!policy) return
    try {
      await revokeMastodonPolicy(policy.instance)
      await refreshAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleCollect(): Promise<void> {
    if (!fields.niche || !fields.instance) return
    setCollecting(true)
    setError('')
    setCollected(null)
    try {
      setCollected(await collectMastodonNiche(fields.instance, fields.niche))
      await refreshAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setCollecting(false)
    }
  }

  /**
   * Generate a post, or rewrite the one on screen.
   *
   * `rewrite` keeps the current draft visible while the next one is written — clearing it
   * removed the button that had just been clicked, which reads as nothing happening.
   */
  async function handleGenerate(rewrite = false): Promise<void> {
    if (!fields.userInput.trim() || !fields.niche || !fields.instance) return
    setLoading(true)
    setError('')
    if (!rewrite) setResult(null)
    setLinked('')
    setPostedUrl('')
    try {
      const res = await generateMastodonPost(
        fields.instance,
        fields.niche,
        fields.userInput,
        fields.sourceUrl,
        fields.discloseAi,
        brandVoiceId,
        rewrite ? previousTexts : []
      )
      setResult(res)
      // A fresh generate starts the history over: a new topic should not be steered away
      // from the wording of the last one.
      setPreviousTexts((prev) => (rewrite ? [...prev, res.text].slice(-3) : [res.text]))
      void refreshLibrary()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      // A 409 here means the rules moved under us. Re-reading the policy swaps
      // the composer back out for the approval screen, which is the only honest
      // thing to show at that point.
      void refreshAll()
    } finally {
      setLoading(false)
    }
  }

  async function handleLink(): Promise<void> {
    if (!result?.generationId || !postedUrl.trim()) return
    setLinking(true)
    setError('')
    try {
      const res = await markMastodonPublished(
        fields.instance,
        result.generationId,
        postedUrl.trim(),
        fields.niche
      )
      setLinked(res.webUrl || res.postedUri)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLinking(false)
    }
  }

  const gateOpen = Boolean(policy?.accepted)
  const nicheRow = niches.find((n) => n.name === fields.niche)

  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '22px 34px 60px' }}>
      <ScreenBackdrop video="mastodon" />
      <div
        style={{ font: "700 13px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer', marginBottom: 14 }}
        onClick={goCreate}
      >
        ← Create
      </div>

      <div style={{ marginBottom: 20, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)' }}>Mastodon Post Creator</div>
          <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
            Learns from what actually got boosted in your niche — after you've read the house rules of the server
            you're posting to.
          </div>
        </div>
        <div
          style={{
            width: 34,
            height: 34,
            background: 'var(--tool-mastodon)',
            borderRadius: '49% 51% 53% 47%',
            border: '2.5px solid var(--border)',
            animation: 'bob 3.4s ease-in-out infinite',
            flexShrink: 0
          }}
        />
      </div>

      {/* --- instance picker --------------------------------------------- */}
      <Card>
        <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)', marginBottom: 2 }}>Which server?</div>
        <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 12 }}>
          Mastodon isn't one place. Each instance writes its own rules and sets its own character limit, so nothing
          here works until it knows where you're posting.
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <input
            value={instanceDraft}
            onChange={(e) => setInstanceDraft(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void handleConnectInstance()}
            placeholder="mastodon.social"
            style={{ ...textInput, flex: 1 }}
          />
          <div
            style={{ ...primaryButtonSmall, opacity: instanceDraft.trim() ? 1 : 0.6 }}
            onClick={instanceDraft.trim() ? handleConnectInstance : undefined}
          >
            {loadingPolicy ? 'Reading rules…' : 'Look it up'}
          </div>
        </div>
        {status && fields.instance && (
          <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 10 }}>
            {status.reachable ? (
              <>
                {status.title} · {status.maxCharacters} character limit
              </>
            ) : (
              <span style={{ color: 'var(--danger-ink)' }}>{status.detail || 'Could not reach that server.'}</span>
            )}
          </div>
        )}
      </Card>

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

      {status && !status.configured && (
        <Banner
          tone="warn"
          title="It needs a key before it can write anything"
          body={`Missing: ${status.missing.filter((m) => m !== 'Mastodon instance').join(', ')}. Pop them into Settings and come back.`}
          actionLabel="Open Settings"
          onAction={goSettings}
        />
      )}

      {/* --- the rules gate ------------------------------------------------ */}
      {policy && <RulesPanel policy={policy} accepting={accepting} onAccept={handleAccept} onRevoke={handleRevoke} />}

      {/* --- composer, locked until the rules are accepted ----------------- */}
      {gateOpen && policy && (
        <>
          <div style={{ marginTop: 16 }}>
            <Card>
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>
                <div>
                  <label style={label}>Niche</label>
                  <select value={fields.niche} onChange={(e) => setField('niche', e.target.value)} style={select}>
                    {!niches.length && <option value="">No niches yet — add one below</option>}
                    {niches.map((n) => (
                      <option key={n.name} value={n.name}>
                        {n.name} ({n.exemplars} exemplars)
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label style={label}>Grounding</label>
                  <div
                    style={{ ...secondaryButtonSmall, opacity: collecting || !fields.niche ? 0.6 : 1, textAlign: 'center' }}
                    onClick={collecting || !fields.niche ? undefined : handleCollect}
                  >
                    {collecting ? 'Reading timelines…' : 'Collect from hashtags'}
                  </div>
                </div>
              </div>

              {collected && (
                <div style={{ font: "600 12px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
                  Scanned {collected.scanned}, kept {collected.stored}, pool now {collected.exemplars}.
                  {Object.keys(collected.skipped).length > 0 && (
                    <>
                      {' '}
                      Skipped:{' '}
                      {Object.entries(collected.skipped)
                        .map(([why, n]) => `${n} ${why}`)
                        .join(', ')}
                      .
                    </>
                  )}
                </div>
              )}

              {nicheRow && nicheRow.exemplars === 0 && (
                <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-faint)' }}>
                  Nothing collected for this niche yet — drafts will lean on Mastodon norms alone until you collect.
                </div>
              )}

              <div
                style={{ ...secondaryButtonSmall, alignSelf: 'flex-start' }}
                onClick={() => setShowNiches((v) => !v)}
              >
                {showNiches ? 'Hide niches' : 'Manage niches'}
              </div>

              <div>
                <label style={label}>What's the post about?</label>
                <textarea
                  rows={3}
                  value={fields.userInput}
                  onChange={(e) => setField('userInput', e.target.value)}
                  placeholder="e.g. my CLI tool finally works on Windows, wry about how long it took"
                  style={textarea}
                />
              </div>

              <div>
                <label style={label}>Link to write about — optional</label>
                <input
                  value={fields.sourceUrl}
                  onChange={(e) => setField('sourceUrl', e.target.value)}
                  placeholder="https://… a release note, article or changelog to pull the facts from"
                  style={textInput}
                />
              </div>

              <div>
                <BrandVoiceSelect
                  value={brandVoiceId}
                  onChange={setBrandVoiceId}
                  hint="Posts follow this brand's tone and guardrails."
                />
              </div>

              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 9,
                  font: "600 12.5px 'Quicksand'",
                  color: 'var(--ink-muted)',
                  cursor: 'pointer'
                }}
              >
                <input
                  type="checkbox"
                  checked={fields.discloseAi}
                  onChange={(e) => setField('discloseAi', e.target.checked)}
                />
                Add an AI disclosure line — required by some instances, good manners on all of them
              </label>

              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div
                  style={{ ...primaryButtonSmall, opacity: loading || !fields.niche ? 0.6 : 1 }}
                  onClick={loading || !fields.niche ? undefined : () => void handleGenerate(false)}
                >
                  {loading ? (fields.sourceUrl.trim() ? 'Reading the link…' : 'Thinking…') : 'Write it'}
                </div>
                <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginLeft: 'auto' }}>
                  {policy.maxCharacters} character limit on {policy.instance}
                </div>
              </div>
            </Card>
          </div>

          {showNiches && (
            <NichePanel
              niches={niches}
              countsHint={`Counts are for ${policy.instance} — each server builds its own pool, so a niche can be full here and empty elsewhere.`}
              onCollect={async (name) => {
                setCollected(await collectMastodonNiche(fields.instance, name))
                await refreshAll()
              }}
              onAdd={async (name, keywords) => {
                // Niches are shared with the Bluesky tool — one list, two ways of filling
                // it — so this is deliberately the Social Post endpoint and not a copy.
                // Creating one also queues a first fill on both sides; the panel polls it.
                await saveSocialNiche(name, keywords)
                await refreshAll()
              }}
              onRefresh={refreshAll}
            />
          )}

          {result && (
            <Draft
              result={result}
              copied={copied}
              onCopy={() => {
                void navigator.clipboard.writeText(result.text)
                setCopied(true)
                setTimeout(() => setCopied(false), 1600)
              }}
              onRetry={() => void handleGenerate(true)}
              postedUrl={postedUrl}
              setPostedUrl={setPostedUrl}
              linking={linking}
              linked={linked}
              onLink={handleLink}
              instance={policy.instance}
            />
          )}

          {result && (
            <PostImagePanel
              onImage={setKeptImage}
              postText={result.text}
              onSuggest={() => suggestMastodonImagePrompt(result.text, fields.niche)}
              onGenerate={(prompt) => generateMastodonImage(prompt, result.text)}
            />
          )}

          {/* Read-only timing guidance — it never schedules or sends anything.
              The instance is the user's own choice from the server box above, and
              the curve is measured on that server alone. */}
          <PostingTimePanel platform="mastodon" instance={policy.instance} />

          {(fields.niche || fields.userInput.trim()) && (
            <HashtagSuggester
              onSelectionChange={setKeptTags}
              draft={(result?.text ?? '').trim() || fields.userInput}
              postText={result?.text ?? ''}
              niche={fields.niche}
              platform="mastodon"
              charLimit={policy.maxCharacters}
            />
          )}

          {/* --- keep the finished thing ------------------------------------- */}
          {result && (
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
              <VideoEmbedInput
            network="mastodon"
            value={videoUrl}
            onChange={setVideoUrl}
          />
          <UploadVideoButton
            network="mastodon"
            value={videoFile}
            onChange={setVideoFile}
            alt={videoFileAlt}
            onAltChange={setVideoFileAlt}
          />
          <SendToEngageButton
            network="mastodon"
            label="Mastodon"
            postText={result.text}
            tags={keptTags}
            imageUrl={keptImage?.url ?? ''}
            videoUrl={videoUrl}
            videoFileUrl={videoFile?.url ?? ''}
            videoFileAlt={videoFileAlt}
          />
          <SaveCompositionButton
                tool="Social"
                title={`Mastodon post · ${fields.niche || 'untitled'}`}
                subtitle="Mastodon post"
                postText={result.text}
                tags={keptTags}
                imageUrl={keptImage?.url ?? ''}
              />
            </div>
          )}
        </>
      )}
    </div>
  )
}

/**
 * The approval screen. Renders the instance's rules verbatim.
 *
 * Rules that touch AI, automation or commercial use are pulled to the top and
 * marked, but nothing is ever hidden or summarised away — deciding on the user's
 * behalf which of their server's rules matter is exactly the judgement call this
 * screen exists to hand back to them.
 */
function RulesPanel({
  policy,
  accepting,
  onAccept,
  onRevoke
}: {
  policy: MastodonPolicy
  accepting: boolean
  onAccept: () => void
  onRevoke: () => void
}): React.JSX.Element {
  const [showAbout, setShowAbout] = useState(false)
  const relevant = policy.rules.filter((r) => r.relevant)
  const rest = policy.rules.filter((r) => !r.relevant)

  if (policy.accepted) {
    return (
      <div
        style={{
          marginTop: 14,
          background: 'var(--tip-bg)',
          border: '2px solid var(--border)',
          borderRadius: 16,
          padding: '12px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: 14
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ font: "700 13.5px 'Quicksand'", color: 'var(--ink)' }}>
            {policy.instance} rules accepted ✓
          </div>
          <div style={{ font: "600 12.5px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 2 }}>
            {policy.rules.length} rules, read on{' '}
            {policy.acceptedAt ? new Date(policy.acceptedAt).toLocaleDateString() : 'an earlier session'}. If the
            server edits them, generation stops until you've read the new version.
          </div>
        </div>
        <div style={secondaryButtonSmall} onClick={onRevoke}>
          Review again
        </div>
      </div>
    )
  }

  return (
    <div
      style={{
        marginTop: 14,
        background: 'var(--surface-paper)',
        border: '2.5px solid var(--border-paper)',
        borderRadius: 20,
        padding: 20,
        boxShadow: 'var(--shadow-paper)'
      }}
    >
      <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)' }}>
        Read {policy.instance}&apos;s rules first
      </div>
      <div style={{ font: "600 13px/1.6 'Quicksand'", color: 'var(--ink-muted)', margin: '4px 0 16px' }}>
        {policy.changedSinceAccepted
          ? 'This server has changed its rules since you last accepted them. Here is the current version.'
          : 'These are published by the server, not by us. Nothing gets generated for this instance until you have read them.'}
      </div>

      {relevant.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div
            style={{
              font: "700 11.5px 'Quicksand'",
              letterSpacing: '.05em',
              textTransform: 'uppercase',
              color: 'var(--accent-deep)',
              marginBottom: 8
            }}
          >
            Rules about AI, automation, or commercial use
          </div>
          {relevant.map((r) => (
            <RuleRow key={r.id} rule={r} highlight />
          ))}
        </div>
      )}

      {rest.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div
            style={{
              font: "700 11.5px 'Quicksand'",
              letterSpacing: '.05em',
              textTransform: 'uppercase',
              color: 'var(--ink-faint)',
              marginBottom: 8
            }}
          >
            {relevant.length > 0 ? 'Its other rules' : 'Its rules'}
          </div>
          {rest.map((r) => (
            <RuleRow key={r.id} rule={r} />
          ))}
        </div>
      )}

      {policy.extendedDescription && (
        <div style={{ marginBottom: 16 }}>
          <div
            style={{
              font: "700 12.5px 'Quicksand'",
              color: 'var(--accent)',
              cursor: 'pointer'
            }}
            onClick={() => setShowAbout((v) => !v)}
          >
            {showAbout ? '▾' : '▸'} Its About page — worth reading, and often where the rule that
            actually affects you lives
          </div>
          {showAbout && (
            <div
              style={{
                marginTop: 8,
                maxHeight: 300,
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
              {policy.extendedDescription}
            </div>
          )}
        </div>
      )}

      <div
        style={{
          borderTop: '2px dashed var(--border-soft)',
          paddingTop: 14,
          display: 'flex',
          alignItems: 'center',
          gap: 14
        }}
      >
        <div style={{ flex: 1, font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-faint)' }}>
          Accepting records that you read <i>this</i> version. Following them is on you — this tool can flag what a
          server says, not keep you out of trouble with it.
        </div>
        <div style={{ ...primaryButtonSmall, opacity: accepting ? 0.6 : 1 }} onClick={accepting ? undefined : onAccept}>
          {accepting ? 'Saving…' : "I've read these — let me write"}
        </div>
      </div>
    </div>
  )
}

function RuleRow({ rule, highlight = false }: { rule: MastodonRule; highlight?: boolean }): React.JSX.Element {
  return (
    <div
      style={{
        padding: '9px 12px',
        marginBottom: 6,
        borderRadius: 10,
        background: highlight ? 'var(--accent-soft-bg)' : 'transparent',
        borderLeft: highlight ? '3px solid var(--accent)' : '3px solid var(--border-soft)'
      }}
    >
      <div style={{ font: "700 13px/1.5 'Quicksand'", color: 'var(--ink)' }}>{rule.text}</div>
      {rule.hint && (
        <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 3 }}>{rule.hint}</div>
      )}
    </div>
  )
}

function Draft({
  result,
  copied,
  onCopy,
  onRetry,
  postedUrl,
  setPostedUrl,
  linking,
  linked,
  onLink,
  instance
}: {
  result: MastodonGenerateResponse
  copied: boolean
  onCopy: () => void
  onRetry: () => void
  postedUrl: string
  setPostedUrl: (v: string) => void
  linking: boolean
  linked: string
  onLink: () => void
  instance: string
}): React.JSX.Element {
  return (
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
            tool="Mastodon"
            title={`Mastodon post · ${instance || 'draft'}`}
            subtitle="Mastodon post"
            content={result.text}
          />
          <div
            style={{ font: "700 12px 'Quicksand'", color: result.overLimit ? 'var(--danger-ink)' : 'var(--ink-faint)' }}
          >
            {result.characters} / {result.maxCharacters} characters
            {result.overLimit ? ' — too long, trim it' : ''}
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

        <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
          <div style={primaryButtonSmall} onClick={onCopy}>
            {copied ? 'Copied ✓' : 'Copy'}
          </div>
          <div style={secondaryButtonSmall} onClick={onRetry}>
            Try again
          </div>
        </div>
      </div>

      {/* --- what this instance asks of you ------------------------------- */}
      <div
        style={{
          marginTop: 14,
          background: 'var(--tip-bg)',
          border: '2px solid var(--border)',
          borderRadius: 16,
          padding: 16
        }}
      >
        <div style={{ font: "700 14px 'Kalam'", color: 'var(--ink)' }}>Before you post this to {instance}</div>
        <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
          <li style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)' }}>
            Post it as <b>{result.compliance.suggestedVisibility}</b> unless you're posting by hand as well — most
            instances ask that of anything automated.
          </li>
          {result.compliance.disclosureApplied && (
            <li style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)' }}>
              The disclosure line is already in the draft. Reword it however you like, but keep one.
            </li>
          )}
          {result.compliance.notes.map((n) => (
            <li key={n} style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)' }}>
              {n}
            </li>
          ))}
        </ul>
      </div>

      {/* --- provenance --------------------------------------------------- */}
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
          Where this came from — {result.exemplars.length} exemplars
        </summary>
        <div style={{ marginTop: 12 }}>
          {result.exemplars.length === 0 && (
            <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
              Nothing collected yet — this one ran on Mastodon norms alone.
            </div>
          )}
          {result.exemplars.map((e, i) => (
            <div key={e.id} style={{ marginBottom: 12 }}>
              <div style={{ font: "700 11.5px 'Quicksand'", color: 'var(--accent-deep)' }}>
                {i + 1}. similarity {e.similarity} · score {e.score}
                {e.author ? ` · @${e.author}` : ''}
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
        </div>
      </details>

      {/* --- close the loop ----------------------------------------------- */}
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
          Paste the link and it'll check back at 1, 24 and 48 hours to see how the post did, then let your own
          results compete for a place in the pool. This is the only way it learns from <i>you</i> rather than from
          strangers.
        </div>
        {linked ? (
          <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent)' }}>
            Linked ✓ — measured at 1h, 24h and 48h.
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 10 }}>
            <input
              value={postedUrl}
              onChange={(e) => setPostedUrl(e.target.value)}
              placeholder={`https://${instance}/@you/1123…`}
              style={{ ...textInput, flex: 1 }}
            />
            <div
              style={{ ...secondaryButtonSmall, opacity: linking || !postedUrl.trim() ? 0.6 : 1 }}
              onClick={linking || !postedUrl.trim() ? undefined : onLink}
            >
              {linking ? 'Linking…' : 'Link it'}
            </div>
          </div>
        )}
      </div>
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
        marginTop: 14,
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
