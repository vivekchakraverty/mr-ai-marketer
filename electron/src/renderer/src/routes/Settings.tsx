import { useEffect, useState } from 'react'
import {
  getLeadgenSchema,
  getModalStatus,
  getSocialPostSchema,
  getTumblrSession,
  provisionModalBackend,
  provisionEmailWriterModal,
  getEmailWriterModalStatus,
  pushLeadgenEnv,
  pushSocialPostEnv,
  testKeywordSurfer,
  verifyHfToken,
  verifyLeadgen,
  verifySocialPost,
  type EnvSetting,
  type ModalProvisionStatus
} from '../api/client'
import { useAppStore } from '../state/store'
import { label, primaryButtonSmall, secondaryButtonSmall, select, textInput } from '../styles/styleKit'
import { EMPTY_SETTINGS, type AppSettings } from '../../../shared/settings'
import BackupPanel from '../components/BackupPanel'
import CloudPostingPanel from '../components/CloudPostingPanel'

type Check = { state: 'idle' | 'checking' | 'ok' | 'error'; detail?: string }

export default function Settings(): React.JSX.Element {
  const goHome = useAppStore((s) => s.goHome)
  const openSetup = useAppStore((s) => s.openSetup)
  const setHfStatus = useAppStore((s) => s.setHfStatus)

  const [settings, setSettings] = useState<AppSettings>(EMPTY_SETTINGS)
  const [schema, setSchema] = useState<EnvSetting[]>([])
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [clears, setClears] = useState<Record<string, boolean>>({})
  // The Lead Gen Agent has its own env schema (kept separate so a save routes each field to
  // the right backend); its edits/clears live in their own maps.
  const [leadgenSchema, setLeadgenSchema] = useState<EnvSetting[]>([])
  const [lgEdits, setLgEdits] = useState<Record<string, string>>({})
  const [lgClears, setLgClears] = useState<Record<string, boolean>>({})
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState('')
  const [error, setError] = useState('')
  const [checks, setChecks] = useState<Record<string, Check>>({})
  // Read from the store rather than local state: the main process pushes download progress
  // into the store, and a download started here has to keep driving the banner after this
  // screen is closed. Two copies of this would drift the moment either happened.
  const updateInfo = useAppStore((s) => s.updateInfo)
  const setUpdateInfo = useAppStore((s) => s.setUpdateInfo)
  const [checkingUpdate, setCheckingUpdate] = useState(false)
  const [modalStatus, setModalStatus] = useState<ModalProvisionStatus | null>(null)
  const [emailModalStatus, setEmailModalStatus] = useState<ModalProvisionStatus | null>(null)
  const [emailModalBusy, setEmailModalBusy] = useState(false)
  const [provisioning, setProvisioning] = useState(false)

  useEffect(() => {
    async function boot(): Promise<void> {
      const s = await window.api.settings.getAll()
      setSettings(s)
      try {
        setSchema(await getSocialPostSchema())
      } catch {
        // Backend may still be starting; the shared section is still usable.
      }
      try {
        let lgSchema = await getLeadgenSchema()
        // The shared HF token powers the Lead Gen Agent too. If the agent doesn't have it yet
        // but the shared one is set, sync it over now so it "just works" without a save or a
        // restart (the backend also inherits it at spawn — see main/backend.ts).
        const lgHf = lgSchema.find((x) => x.name === 'HF_TOKEN')
        if (s.hfToken.trim() && lgHf && !lgHf.isSet) {
          await pushLeadgenEnv({ HF_TOKEN: s.hfToken.trim() })
          lgSchema = await getLeadgenSchema()
        }
        setLeadgenSchema(lgSchema)
      } catch {
        /* backend may still be starting */
      }
      setLoaded(true)
    }
    void boot()
  }, [])

  async function reloadSchema(): Promise<void> {
    try {
      setSchema(await getSocialPostSchema())
    } catch {
      /* non-fatal */
    }
    try {
      setLeadgenSchema(await getLeadgenSchema())
    } catch {
      /* non-fatal */
    }
  }

  // Shared builders: what handleSave pushes to each backend, extracted so "Test connection"
  // can push the same values on demand (see pushPendingSocialPost/pushPendingLeadgen below) —
  // a Test click must never require a separate Save first, or it just reports "not set" for
  // whatever you literally just typed.
  function buildSocialPostValues(): Record<string, string> {
    const values: Record<string, string> = { ...edits }
    for (const [name, on] of Object.entries(clears)) if (on) values[name] = ''
    if (settings.hfToken.trim()) values.HF_TOKEN = settings.hfToken.trim()
    return values
  }

  function buildLeadgenValues(): Record<string, string> {
    const values: Record<string, string> = { ...lgEdits }
    for (const [name, on] of Object.entries(lgClears)) if (on) values[name] = ''
    if (settings.hfToken.trim()) values.HF_TOKEN = settings.hfToken.trim()
    return values
  }

  // Pushes only if there's something un-saved (edits/clears actually set) — a bare Test
  // click with nothing typed skips straight to verifying, no pointless empty POST.
  async function pushPendingSocialPost(): Promise<void> {
    if (Object.keys(edits).length === 0 && Object.keys(clears).length === 0) return
    await pushSocialPostEnv(buildSocialPostValues())
    setEdits({})
    setClears({})
    await reloadSchema()
  }

  async function pushPendingLeadgen(): Promise<void> {
    if (Object.keys(lgEdits).length === 0 && Object.keys(lgClears).length === 0) return
    await pushLeadgenEnv(buildLeadgenValues())
    setLgEdits({})
    setLgClears({})
    await reloadSchema()
  }

  // Provisioning outlives an HTTP request (the first deploy builds a container
  // image), so the backend runs it in a thread and we poll. Status lives on the
  // backend, so a deploy started before this screen was opened still shows up.
  // A failure here means the build has no modal SDK — leave the section idle.
  useEffect(() => {
    getModalStatus()
      .then(setModalStatus)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (modalStatus?.status !== 'running') return
    const timer = setInterval(() => {
      getModalStatus()
        .then(setModalStatus)
        .catch(() => undefined)
    }, 3000)
    return () => clearInterval(timer)
  }, [modalStatus?.status])

  useEffect(() => {
    if (emailModalStatus?.status !== 'running') return
    const timer = setInterval(() => {
      getEmailWriterModalStatus()
        .then(setEmailModalStatus)
        .catch(() => undefined)
    }, 3000)
    return () => clearInterval(timer)
  }, [emailModalStatus?.status])

  useEffect(() => {
    if (emailModalStatus?.status !== 'ready' || settings.emailWriterModal.modalProvisionedAt) return
    const next: AppSettings = {
      ...settings,
      emailWriterModal: {
        ...settings.emailWriterModal,
        modalProvisionedAt: new Date().toISOString()
      }
    }
    setSettings(next)
    void window.api.settings.setAll(next)
  }, [emailModalStatus?.status, settings])

  /** Falls back to the Brand Studio credentials, matching what the backend does at spawn:
   *  one Modal account, and being asked for the same token twice is the worse experience. */
  async function handleProvisionEmailModal(): Promise<void> {
    setEmailModalBusy(true)
    setError('')
    try {
      await window.api.settings.setAll(settings)
      setEmailModalStatus(
        await provisionEmailWriterModal(
          settings.emailWriterModal.modalTokenId.trim() || settings.brandForge.modalTokenId.trim(),
          settings.emailWriterModal.modalTokenSecret.trim() ||
            settings.brandForge.modalTokenSecret.trim(),
          settings.hfToken.trim()
        )
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setEmailModalBusy(false)
    }
  }

  // Remember a successful deploy so the section still reads as set up after a
  // restart, when the backend's in-memory status is back to idle.
  useEffect(() => {
    if (modalStatus?.status !== 'ready' || settings.brandForge.modalProvisionedAt) return
    const next: AppSettings = {
      ...settings,
      brandForge: { ...settings.brandForge, modalProvisionedAt: new Date().toISOString() }
    }
    setSettings(next)
    void window.api.settings.setAll(next)
  }, [modalStatus?.status, settings])

  async function handleProvisionModal(): Promise<void> {
    setProvisioning(true)
    setError('')
    try {
      // Persist first so Brand Studio picks the tokens up for generation. The
      // deploy itself uses the typed values, so no Save is required first.
      await window.api.settings.setAll(settings)
      setModalStatus(
        await provisionModalBackend(
          settings.brandForge.modalTokenId.trim(),
          settings.brandForge.modalTokenSecret.trim(),
          settings.hfToken.trim()
        )
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setProvisioning(false)
    }
  }

  async function handleSave(): Promise<void> {
    setSaving(true)
    setError('')
    try {
      // 1. Shared credentials go to Electron's encrypted per-user store.
      //
      // Everything this screen actually EDITS, and nothing else. `settings` is a snapshot
      // taken when the screen mounted, so sending it whole writes back whatever those groups
      // held at that moment — reverting anything another part of the app changed in the
      // meantime. That is not hypothetical: connecting cloud posting from the walkthrough
      // stores a fresh key on this machine and on the Space, and a later "Save everything"
      // put the stale one back, leaving the Space rejecting the app with a 401 that read as
      // "your Space did not recognise this app's key".
      //
      // Neither group below has a single field on this screen, so omitting them loses
      // nothing; the panels that own them write them directly.
      const { cloudPosting: _cloud, setupWizard: _wizard, ...ownedByThisScreen } = settings
      await window.api.settings.setAll(ownedByThisScreen)
      if (settings.hfToken.trim()) {
        const res = await verifyHfToken(settings.hfToken.trim())
        setHfStatus(res.valid, res.username ?? null)
      } else {
        setHfStatus(false, null)
      }

      // 2. Everything the Social Post Generator reads lives in the backend's environment.
      const values = buildSocialPostValues()
      if (Object.keys(values).length) await pushSocialPostEnv(values)

      // 3. The Lead Gen Agent reads its own env file.
      const lgValues = buildLeadgenValues()
      if (Object.keys(lgValues).length) await pushLeadgenEnv(lgValues)

      setEdits({})
      setClears({})
      setLgEdits({})
      setLgClears({})
      await reloadSchema()
      setSaved('Saved ✓')
      setTimeout(() => setSaved(''), 2200)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  async function runCheck(target: 'bluesky' | 'llm' | 'hf'): Promise<void> {
    setChecks((c) => ({ ...c, [target]: { state: 'checking' } }))
    try {
      await pushPendingSocialPost() // save whatever's typed before testing it
      const res = await verifySocialPost(target)
      setChecks((c) => ({
        ...c,
        [target]: { state: res.valid ? 'ok' : 'error', detail: res.detail }
      }))
    } catch (err) {
      setChecks((c) => ({
        ...c,
        [target]: { state: 'error', detail: err instanceof Error ? err.message : String(err) }
      }))
    }
  }

  /** One real Keyword Surfer lookup, so a proxy can be checked in seconds rather than by
   * generating a whole plan and reading the log afterwards.
   *
   * Nothing is persisted first: the backend reads the proxy off the request body, so what
   * gets tested is exactly what is typed in the boxes above, unsaved edits included.
   */
  async function runSurferCheck(): Promise<void> {
    setChecks((c) => ({ ...c, surfer: { state: 'checking' } }))
    try {
      const res = await testKeywordSurfer(settings.keywordSurfer)
      // The exit address is what separates "blocked" from "blocked, and not even through
      // the proxy I configured" — worth showing on success and failure alike.
      const where = res.exitIp
        ? ` (seen as ${res.exitIp}${res.usingProxy ? ', via your proxy' : ', no proxy'})`
        : ''
      setChecks((c) => ({
        ...c,
        surfer: { state: res.ok ? 'ok' : 'error', detail: res.detail + where }
      }))
    } catch (err) {
      setChecks((c) => ({
        ...c,
        surfer: { state: 'error', detail: err instanceof Error ? err.message : String(err) }
      }))
    }
  }

  async function runLeadgenCheck(target: 'llm' | 'smtp' | 'imap' | 'reacher' | 'searxng'): Promise<void> {
    const key = `lg:${target}`
    setChecks((c) => ({ ...c, [key]: { state: 'checking' } }))
    try {
      await pushPendingLeadgen() // save whatever's typed before testing it
      const res = await verifyLeadgen(target)
      setChecks((c) => ({ ...c, [key]: { state: res.valid ? 'ok' : 'error', detail: res.detail } }))
    } catch (err) {
      setChecks((c) => ({
        ...c,
        [key]: { state: 'error', detail: err instanceof Error ? err.message : String(err) }
      }))
    }
  }

  // Tumblr's credentials live in the encrypted store rather than the backend's
  // environment, and the backend reads them off the request — so the test has to
  // persist what is typed first, or it would check whatever was saved last time.
  async function runTumblrCheck(): Promise<void> {
    setChecks((c) => ({ ...c, tumblr: { state: 'checking' } }))
    try {
      await window.api.settings.setAll(settings)
      const session = await getTumblrSession()
      if (!session.configured) {
        setChecks((c) => ({ ...c, tumblr: { state: 'error', detail: 'All four OAuth values are needed.' } }))
      } else if (!session.reachable) {
        setChecks((c) => ({ ...c, tumblr: { state: 'error', detail: session.detail } }))
      } else {
        setChecks((c) => ({
          ...c,
          tumblr: {
            state: session.detail ? 'error' : 'ok',
            detail: session.detail || `Signed in as ${session.userName}, posting as ${session.blog} ✓`
          }
        }))
      }
    } catch (err) {
      setChecks((c) => ({
        ...c,
        tumblr: { state: 'error', detail: err instanceof Error ? err.message : String(err) }
      }))
    }
  }

  async function handleCheckForUpdate(): Promise<void> {
    setCheckingUpdate(true)
    try {
      setUpdateInfo(await window.api.update.check())
    } finally {
      setCheckingUpdate(false)
    }
  }

  // Settings the shared section already covers, or that the desktop app manages
  // itself — hiding them keeps the screen about credentials, not plumbing.
  const HIDDEN = new Set(['HF_TOKEN', 'ALLOW_UI_CONFIG', 'SQLITE_PATH', 'HF_INGEST_TOKEN', 'TELEMETRY_DATASET'])
  const groups = schema
    .filter((s) => !HIDDEN.has(s.name))
    .reduce<Record<string, EnvSetting[]>>((acc, s) => {
      ;(acc[s.group] ||= []).push(s)
      return acc
    }, {})

  // HF_TOKEN is the shared token entered up top; hide it from the leadgen section too.
  const leadgenGroups = leadgenSchema
    .filter((s) => s.name !== 'HF_TOKEN')
    .reduce<Record<string, EnvSetting[]>>((acc, s) => {
      ;(acc[s.group] ||= []).push(s)
      return acc
    }, {})

  // Which verifier(s) to offer per leadgen group.
  const LEADGEN_CHECKS: Record<string, ('llm' | 'smtp' | 'imap' | 'reacher' | 'searxng')[]> = {
    'Language model': ['llm'],
    'Sending mailbox (SMTP)': ['smtp'],
    'Reply inbox (IMAP)': ['imap'],
    Discovery: ['searxng', 'reacher']
  }

  const VERIFIER_FOR: [RegExp, 'bluesky' | 'llm' | 'hf'][] = [
    [/bluesky/i, 'bluesky'],
    [/gemini|llm/i, 'llm']
  ]

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '26px 34px 70px' }}>
      <div
        style={{ font: "700 13px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer', marginBottom: 14 }}
        onClick={goHome}
      >
        ← Back
      </div>

      <div style={{ marginBottom: 22, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div
            style={{
              font: "700 13.5px 'Quicksand'",
              letterSpacing: '.05em',
              textTransform: 'uppercase',
              color: 'var(--ink-faint)',
              textDecoration: 'underline wavy',
              textDecorationColor: 'var(--ink-faint)',
              textUnderlineOffset: 4
            }}
          >
            Settings
          </div>
          <div style={{ font: "700 31px 'Kalam'", color: 'var(--ink)', marginTop: 6 }}>Keys, tokens & other secrets</div>
          <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
            All yours, all local. Stored encrypted on this machine under your user account — never uploaded anywhere.
          </div>
        </div>
        <div
          style={{
            width: 34,
            height: 34,
            background: 'var(--tool-plan)',
            borderRadius: '52% 48% 50% 50%',
            border: '2.5px solid var(--border)',
            animation: 'sway 4s ease-in-out infinite',
            flexShrink: 0
          }}
        />
      </div>

      {!loaded ? (
        <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)' }}>Loading…</div>
      ) : (
        <>
          {/* The walkthrough is skippable by design, so this is the way back into it. It sits
              first because someone who skipped it on launch is looking for exactly this. */}
          <Section
            title="Setup walkthrough"
            blurb="Connect your accounts one at a time, in order. Safe to re-run — it fills in what you have already set up and skips nothing you do not ask it to."
            accent="var(--tool-distribute)"
            optional
          >
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={primaryButtonSmall} onClick={() => openSetup(settings.setupWizard.resumeAt || undefined)}>
                {settings.setupWizard.completedAt ? 'Run it again' : settings.setupWizard.startedAt ? 'Resume setup' : 'Start setup'}
              </div>
              <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
                {settings.setupWizard.completedAt
                  ? 'Finished — re-running changes nothing you do not touch.'
                  : settings.setupWizard.startedAt
                    ? 'Picks up where you left off.'
                    : 'Never run.'}
              </div>
            </div>
          </Section>

          <Section
            title="Cloud posting"
            blurb="Lets scheduled Mastodon and Bluesky posts go out while this app is closed, from a poster in your own Hugging Face account."
            accent="var(--tool-distribute)"
            optional
          >
            <CloudPostingPanel />
          </Section>

          {/* ---- shared across every tool ------------------------------- */}
          <Section
            title="Hugging Face"
            blurb="The one every generator bills to. Get a token at huggingface.co/settings/tokens."
            accent="var(--tool-docu)"
          >
            <label style={label}>Token</label>
            <input
              type="password"
              value={settings.hfToken}
              onChange={(e) => setSettings((s) => ({ ...s, hfToken: e.target.value }))}
              placeholder="hf_••••••••••••••••••••"
              style={textInput}
            />
            <CheckRow check={checks.hf} onRun={() => runCheck('hf')} labelText="Test token" />
          </Section>

          <Section
            title="YouTube Data API"
            optional
            blurb="Lets Tutorial Maker rank candidate videos by comment sentiment. Without it, it just takes the top search result."
            accent="var(--tool-tutorial)"
          >
            <label style={label}>API key</label>
            <input
              type="password"
              value={settings.youtubeApiKey}
              onChange={(e) => setSettings((s) => ({ ...s, youtubeApiKey: e.target.value }))}
              placeholder="Optional"
              style={textInput}
            />
          </Section>

          <Section
            title="Data repositories"
            optional
            blurb="Three tools read a catalogue or a model that isn't shipped inside the app — it's pulled from Hugging Face on first use and cached. The Influencer Database has a public default and works with this left blank; set it only to use your own catalogue, for instance one with contact details. The other two have no default and their tool says so until you point it at a repo. Private repos work — they're fetched with the token above."
            accent="var(--accent-deep)"
          >
            <label style={label}>Influencer Database (dataset)</label>
            <input
              value={settings.hfAssets.influencerRepo}
              onChange={(e) =>
                setSettings((s) => ({ ...s, hfAssets: { ...s.hfAssets, influencerRepo: e.target.value } }))
              }
              placeholder="Optional — defaults to the public catalogue"
              style={textInput}
            />
            <label style={label}>Guest Post Suggester (dataset)</label>
            <input
              value={settings.hfAssets.guestPostRepo}
              onChange={(e) =>
                setSettings((s) => ({ ...s, hfAssets: { ...s.hfAssets, guestPostRepo: e.target.value } }))
              }
              placeholder="username/dataset-name"
              style={textInput}
            />
            <label style={label}>Email Writer CTR model (model)</label>
            <input
              value={settings.hfAssets.ctrModelRepo}
              onChange={(e) =>
                setSettings((s) => ({ ...s, hfAssets: { ...s.hfAssets, ctrModelRepo: e.target.value } }))
              }
              placeholder="username/model-name"
              style={textInput}
            />
            <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 8 }}>
              Changing these takes effect next time the app starts — the backend reads them when it launches.
            </div>
          </Section>

          <Section
            title="Mastodon"
            optional
            blurb="The Mastodon Post Creator needs to know which server you're on — it's what decides the rules and the character limit, and you can also set it on the tool's own screen. The access token is optional: without it the tool still reads public hashtag timelines, but it can't search or link a published post back to its draft."
            accent="var(--tool-mastodon)"
          >
            <label style={label}>Instance</label>
            <input
              value={settings.mastodonInstance}
              onChange={(e) => setSettings((s) => ({ ...s, mastodonInstance: e.target.value }))}
              placeholder="mastodon.social"
              style={textInput}
            />
            <label style={{ ...label, marginTop: 10 }}>Access token</label>
            <input
              type="password"
              value={settings.mastodonAccessToken}
              onChange={(e) => setSettings((s) => ({ ...s, mastodonAccessToken: e.target.value }))}
              placeholder="Optional — Preferences → Development → New Application on your instance"
              style={textInput}
            />
          </Section>

          <Section
            title="Tumblr"
            optional
            blurb="Powers the Tumblr tab in Engage. Tumblr signs every request with four values, not one token: register an application at tumblr.com/oauth/apps for the consumer key and secret, then open api.tumblr.com/console, pick that application, and copy the token and token secret it shows you. All four are secrets."
            accent="var(--tool-tumblr)"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(
                [
                  ['consumerKey', 'Consumer key', 'From tumblr.com/oauth/apps — identifies the application'],
                  ['consumerSecret', 'Consumer secret', 'The other half of the application credentials'],
                  ['oauthToken', 'OAuth token', 'From api.tumblr.com/console — identifies you'],
                  ['oauthTokenSecret', 'OAuth token secret', 'The other half of your own credentials']
                ] as [keyof AppSettings['tumblr'], string, string][]
              ).map(([key, labelText, hint]) => (
                <div key={key}>
                  <label style={label}>{labelText}</label>
                  <input
                    type="password"
                    value={settings.tumblr[key]}
                    onChange={(e) => setSettings((s) => ({ ...s, tumblr: { ...s.tumblr, [key]: e.target.value } }))}
                    placeholder={hint}
                    style={textInput}
                  />
                </div>
              ))}
              <div>
                <label style={label}>Blog</label>
                <input
                  value={settings.tumblr.blog}
                  onChange={(e) => setSettings((s) => ({ ...s, tumblr: { ...s.tumblr, blog: e.target.value } }))}
                  placeholder="Optional — leave blank for your primary blog"
                  style={textInput}
                />
                <Help text="One Tumblr account can own several blogs, and posting, reblogging and blocking all happen as one of them. Short name (myblog) or full address (myblog.tumblr.com)." />
              </div>
            </div>
            <CheckRow check={checks.tumblr} onRun={runTumblrCheck} labelText="Test connection" />
          </Section>

          <Section
            title="Brand Studio GPU"
            optional
            blurb="Brand Studio generates on your own Modal GPU, billed to you per second. This is required — there is no shared fallback, because the hosted BrandForge Space forwards every section to its publisher's Modal function and would spend their money instead of yours. Sign up at modal.com, open Settings → API Tokens → New token, paste both halves below, add the two repos you pushed, then run the setup. Modal gives new accounts around $30 of free credit; set a cap under Usage & Billing → Budgets."
            accent="var(--tool-social)"
          >
            <label style={label}>Merged model repo</label>
            <input
              value={settings.brandForge.modelRepo}
              onChange={(e) =>
                setSettings((s) => ({ ...s, brandForge: { ...s.brandForge, modelRepo: e.target.value } }))
              }
              placeholder="your-username/your-merged-brandforge-model"
              style={textInput}
            />
            <label style={label}>Image weights bucket</label>
            <input
              value={settings.brandForge.imageBucket}
              onChange={(e) =>
                setSettings((s) => ({ ...s, brandForge: { ...s.brandForge, imageBucket: e.target.value } }))
              }
              placeholder="your-username/your-flux-bucket"
              style={textInput}
            />
            <label style={label}>Modal token ID</label>
            <input
              type="password"
              value={settings.brandForge.modalTokenId}
              onChange={(e) =>
                setSettings((s) => ({ ...s, brandForge: { ...s.brandForge, modalTokenId: e.target.value } }))
              }
              placeholder="ak-..."
              style={textInput}
            />
            <label style={{ ...label, marginTop: 10 }}>Modal token secret</label>
            <input
              type="password"
              value={settings.brandForge.modalTokenSecret}
              onChange={(e) =>
                setSettings((s) => ({ ...s, brandForge: { ...s.brandForge, modalTokenSecret: e.target.value } }))
              }
              placeholder="as-..."
              style={textInput}
            />

            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
              <button
                onClick={handleProvisionModal}
                disabled={
                  provisioning ||
                  modalStatus?.status === 'running' ||
                  !settings.brandForge.modalTokenId.trim() ||
                  !settings.brandForge.modalTokenSecret.trim()
                }
                style={primaryButtonSmall}
              >
                {modalStatus?.status === 'running' ? 'Setting up…' : 'Set up my GPU'}
              </button>
              {modalStatus?.status === 'running' && (
                <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>
                  {Math.floor(modalStatus.elapsedSeconds / 60)}m {modalStatus.elapsedSeconds % 60}s elapsed
                </span>
              )}
              {modalStatus?.status !== 'running' && settings.brandForge.modalProvisionedAt && (
                <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>
                  Set up {new Date(settings.brandForge.modalProvisionedAt).toLocaleDateString()} — re-run after
                  changing your HF token.
                </span>
              )}
            </div>

            {modalStatus && modalStatus.status !== 'idle' && (
              <div
                style={{
                  marginTop: 10,
                  padding: '8px 12px',
                  borderRadius: 12,
                  border: '2px solid var(--border)',
                  background: 'var(--surface-alt, transparent)',
                  font: "600 12px 'Quicksand'",
                  color: modalStatus.status === 'error' ? 'var(--danger, #d14343)' : 'var(--ink)'
                }}
              >
                {modalStatus.message}
                {modalStatus.status === 'running' && <div style={{ marginTop: 4 }}>{modalStatus.hint}</div>}
                {modalStatus.appPageUrl && (
                  <div style={{ marginTop: 4 }}>
                    <a href={modalStatus.appPageUrl} target="_blank" rel="noreferrer">
                      View it in your Modal dashboard
                    </a>
                  </div>
                )}
              </div>
            )}
          </Section>

          <Section
            title="Topic Scout sources"
            optional
            blurb="Topic Scout works with no setup at all — its default source stack is entirely key-free. Each field here unlocks one extra source whose provider requires identification, a key, or a session. Sentiment uses your HF token above."
            accent="var(--tool-plan)"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(
                [
                  ['geo', 'Country code', 'US — for Google Trends, YouTube and TikTok', false],
                  ['contactEmail', 'Contact email', 'Required by SEC; also gets you the polite pool on OpenAlex and PubMed', false],
                  ['githubToken', 'GitHub token', 'Optional. Raises GitHub rate limits; never used to write', true],
                  ['reliefwebAppname', 'ReliefWeb appname', 'ReliefWeb requires a pre-approved appname', false],
                  ['fredApiKey', 'FRED API key', 'Only needed for the FRED economic context signal', true],
                  ['twitterAuthToken', 'X auth_token cookie', 'Optional. Enables Twitter/X as a conversation source', true],
                  ['twitterCt0', 'X ct0 cookie', 'The second half of the X session cookie pair', true]
                ] as [keyof AppSettings['topicScout'], string, string, boolean][]
              ).map(([key, labelText, hint, secret]) => (
                <div key={key}>
                  <label style={label}>{labelText}</label>
                  <input
                    type={secret ? 'password' : 'text'}
                    value={settings.topicScout[key]}
                    onChange={(e) =>
                      setSettings((s) => ({ ...s, topicScout: { ...s.topicScout, [key]: e.target.value } }))
                    }
                    placeholder={hint}
                    style={textInput}
                  />
                </div>
              ))}
            </div>
          </Section>

          <Section
            title="Marketing Plan Space"
            optional
            blurb="Generate plans on a Hugging Face Space instead of on this machine — no reference index to download. Leave it empty to keep building plans locally. Generation is billed to your own Hugging Face account either way, but your token is sent to the Space to get there, so point this only at one you host or trust."
            accent="var(--tool-plan)"
          >
            <div>
              <label style={label}>Space</label>
              <input
                type="text"
                value={settings.marketingPlan.spaceUrl}
                onChange={(e) =>
                  setSettings((s) => ({ ...s, marketingPlan: { ...s.marketingPlan, spaceUrl: e.target.value } }))
                }
                placeholder="owner/space-name or https://owner-space-name.hf.space"
                style={textInput}
              />
            </div>
          </Section>

          <Section
            title="Blog Writer and Email Writer Spaces"
            optional
            blurb="Each of these tools generates on a Hugging Face Space you deploy yourself — see the README for how. There are deliberately no defaults: a shared id would send your generation traffic to somebody else's account. Until one is set, that tool refuses with an explanation rather than pretending to work. The backend reads these when it starts, so restart the app after saving."
            accent="var(--tool-create)"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <label style={label}>Blog Writer Space</label>
                <input
                  type="text"
                  value={settings.writerSpaces.blogWriter}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      writerSpaces: { ...s.writerSpaces, blogWriter: e.target.value }
                    }))
                  }
                  placeholder="owner/space-name or https://owner-space-name.hf.space"
                  style={textInput}
                />
              </div>
              <div>
                <label style={label}>Email Writer Space</label>
                <input
                  type="text"
                  value={settings.writerSpaces.emailWriter}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      writerSpaces: { ...s.writerSpaces, emailWriter: e.target.value }
                    }))
                  }
                  placeholder="owner/space-name or https://owner-space-name.hf.space"
                  style={textInput}
                />
              </div>
            </div>
          </Section>

          <Section
            title="Email Writer GPU"
            optional
            blurb="Optional. The Email Writer runs on a free Hugging Face Space by default — free forever, and slow: about ninety seconds an email on two shared vCPUs, queued behind everyone else using it. Point it at your own Modal GPU and the same model answers in seconds. Modal's starter plan is $30 of credits a month rather than a free tier, so this spends something; at T4 rates an email is well under a cent. Leave it blank and nothing changes. If you already set up Brand Studio's GPU, those credentials are used automatically — this is only for pointing the Email Writer at a different account."
            accent="var(--tool-email)"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <label style={label}>Modal token ID</label>
                <input
                  type="text"
                  value={settings.emailWriterModal.modalTokenId}
                  onChange={(e) =>
                    setSettings((st) => ({
                      ...st,
                      emailWriterModal: { ...st.emailWriterModal, modalTokenId: e.target.value }
                    }))
                  }
                  placeholder="ak-..."
                  style={textInput}
                />
              </div>
              <div>
                <label style={label}>Modal token secret</label>
                <input
                  type="password"
                  value={settings.emailWriterModal.modalTokenSecret}
                  onChange={(e) =>
                    setSettings((st) => ({
                      ...st,
                      emailWriterModal: { ...st.emailWriterModal, modalTokenSecret: e.target.value }
                    }))
                  }
                  placeholder="as-..."
                  style={textInput}
                />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <div
                  style={{ ...secondaryButtonSmall, opacity: emailModalBusy ? 0.6 : 1 }}
                  onClick={emailModalBusy ? undefined : () => void handleProvisionEmailModal()}
                >
                  {emailModalBusy ? 'Setting up…' : 'Set up the Email Writer GPU'}
                </div>
                {emailModalStatus && (
                  <span
                    style={{
                      font: "600 12px 'Quicksand'",
                      color:
                        emailModalStatus.status === 'error'
                          ? 'var(--danger-ink)'
                          : 'var(--ink-muted)'
                    }}
                  >
                    {emailModalStatus.message || emailModalStatus.status}
                    {emailModalStatus.status === 'running' && emailModalStatus.elapsedSeconds
                      ? ` (${emailModalStatus.elapsedSeconds}s)`
                      : ''}
                  </span>
                )}
              </div>
              {emailModalStatus?.status === 'running' && emailModalStatus.hint && (
                <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)' }}>
                  {emailModalStatus.hint}
                </div>
              )}
            </div>
          </Section>

          <Section
            title="Google Ads API"
            optional
            blurb="Real search-volume and CPC data for the Marketing Plan Generator. Without it, keyword research falls back to free estimates — a headless-Chromium Keyword Surfer scrape, then Google Autocomplete and Trends, then clearly labelled LLM estimates. These credentials are used whether the plan is built here or on the Space."
            accent="var(--tool-plan)"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(
                [
                  ['developerToken', 'Developer token', true],
                  ['clientId', 'Client ID', true],
                  ['clientSecret', 'Client secret', true],
                  ['refreshToken', 'Refresh token', true],
                  ['loginCustomerId', 'Login customer ID', false]
                ] as const
              ).map(([key, text, secret]) => (
                <div key={key}>
                  <label style={label}>{text}</label>
                  <input
                    type={secret ? 'password' : 'text'}
                    value={settings.googleAds[key]}
                    onChange={(e) =>
                      setSettings((s) => ({ ...s, googleAds: { ...s.googleAds, [key]: e.target.value } }))
                    }
                    style={textInput}
                  />
                </div>
              ))}
            </div>
          </Section>

          <Section
            title="Keyword Surfer proxy"
            optional
            blurb="Keyword Surfer only publishes its volumes inside a browser on a real Google results page, so the app reads them here on your machine rather than on the Space. Google shows automated browsers a captcha from ordinary addresses, home broadband included — a residential proxy is what gets past it. Without one this source stays blocked and the other keyword sources are used instead. These stay on this machine."
            accent="var(--tool-plan)"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(
                [
                  ['proxyServer', 'Proxy server', 'http://gate.provider.com:7000', false],
                  ['proxyUsername', 'Username', 'Optional — open proxies need none', false],
                  ['proxyPassword', 'Password', '', true]
                ] as [keyof AppSettings['keywordSurfer'], string, string, boolean][]
              ).map(([key, text, hint, secret]) => (
                <div key={key}>
                  <label style={label}>{text}</label>
                  <input
                    type={secret ? 'password' : 'text'}
                    value={settings.keywordSurfer[key]}
                    onChange={(e) =>
                      setSettings((s) => ({ ...s, keywordSurfer: { ...s.keywordSurfer, [key]: e.target.value } }))
                    }
                    placeholder={hint}
                    style={textInput}
                  />
                </div>
              ))}
            </div>
            <CheckRow check={checks.surfer} onRun={runSurferCheck} labelText="Test proxy" />
            <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 6 }}>
              Starts a browser and fetches one Google results page, so give it a few seconds.
            </div>
          </Section>

          {/* ---- social post generator ---------------------------------- */}
          {Object.entries(groups).map(([group, items]) => {
            const verifier = VERIFIER_FOR.find(([re]) => re.test(group))?.[1]
            return (
              <Section
                key={group}
                title={group.replace(/\s*\(.*\)\s*$/, '')}
                blurb={group.match(/\((.*)\)/)?.[1] ?? 'Used by the Bluesky Post Creator.'}
                accent="var(--tool-social)"
              >
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {items.map((s) => (
                    <Field
                      key={s.name}
                      setting={s}
                      value={edits[s.name]}
                      cleared={Boolean(clears[s.name])}
                      onChange={(v) => setEdits((e) => ({ ...e, [s.name]: v }))}
                      onClear={(on) => setClears((c) => ({ ...c, [s.name]: on }))}
                    />
                  ))}
                </div>
                {verifier && (
                  <CheckRow
                    check={checks[verifier]}
                    onRun={() => runCheck(verifier)}
                    labelText="Test connection"
                  />
                )}
              </Section>
            )
          })}

          {/* ---- lead gen agent ----------------------------------------- */}
          {Object.entries(leadgenGroups).map(([group, items]) => (
            <Section
              key={`lg-${group}`}
              title={`Lead Gen Agent — ${group}`}
              blurb="Used by the Lead Gen Agent (Research → Lead Gen Agent)."
              accent="var(--tool-guest)"
            >
              {group === 'Language model' && (
                <div
                  style={{
                    font: "700 12.5px/1.5 'Quicksand'",
                    color: settings.hfToken.trim() ? 'var(--accent)' : '#a34a3a',
                    background: 'var(--surface)',
                    border: '2px solid var(--border)',
                    borderRadius: 12,
                    padding: '9px 12px',
                    marginBottom: 12
                  }}
                >
                  {settings.hfToken.trim()
                    ? '✓ Using the Hugging Face token from the top of this page — no need to enter it again here.'
                    : '⚠ Add your Hugging Face token in the “Hugging Face” section at the top of this page; the agent uses it for its reasoning calls.'}
                </div>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {items.map((s) => (
                  <Field
                    key={s.name}
                    setting={s}
                    value={lgEdits[s.name]}
                    cleared={Boolean(lgClears[s.name])}
                    onChange={(v) => setLgEdits((e) => ({ ...e, [s.name]: v }))}
                    onClear={(on) => setLgClears((c) => ({ ...c, [s.name]: on }))}
                  />
                ))}
              </div>
              {(LEADGEN_CHECKS[group] ?? []).map((target) => (
                <CheckRow
                  key={target}
                  check={checks[`lg:${target}`]}
                  onRun={() => runLeadgenCheck(target)}
                  labelText={`Test ${target}`}
                />
              ))}
            </Section>
          ))}

          {/* ---- backups ------------------------------------------------ */}
          <Section
            title="Backups"
            blurb="Everything this app has made lives in database files on this machine and nowhere else. A backup is the only way back from a bad reset or a failing disk."
            accent="var(--tool-distribute)"
          >
            <BackupPanel />
          </Section>

          {/* ---- updates ------------------------------------------------ */}
          <Section
            title="App updates"
            blurb={`You're on version ${updateInfo?.currentVersion ?? '0.1.0'}.`}
            accent="var(--tool-guest)"
          >
            {updateInfo?.phase === 'available' && (
              <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent-deep)', marginBottom: 10 }}>
                Version {updateInfo.latestVersion} is out.{' '}
                <span
                  style={{ textDecoration: 'underline', cursor: 'pointer' }}
                  onClick={() => void window.api.update.download()}
                >
                  Download it →
                </span>
                <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
                  Around 600MB the first time. Later updates are usually much smaller — only
                  the parts that changed get downloaded.
                </div>
              </div>
            )}

            {updateInfo?.phase === 'downloading' && (
              <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent-deep)', marginBottom: 10 }}>
                Downloading {updateInfo.latestVersion}… {updateInfo.percent ?? 0}%
                <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
                  You can keep working. The app only restarts when you tell it to.
                </div>
              </div>
            )}

            {updateInfo?.phase === 'ready' && (
              <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent-deep)', marginBottom: 10 }}>
                Version {updateInfo.latestVersion} is downloaded.{' '}
                <span
                  style={{ textDecoration: 'underline', cursor: 'pointer' }}
                  onClick={() => void window.api.update.install()}
                >
                  Restart and install →
                </span>
                <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
                  Or just close the app when you're done — it installs on the way out. Your
                  data and settings are kept either way.
                </div>
              </div>
            )}

            {updateInfo?.phase === 'up-to-date' && (
              <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent)', marginBottom: 10 }}>
                You&apos;re up to date ✓
              </div>
            )}

            {/* Running from source. Not a failure, and not something a developer can fix by
                clicking the button again — so say which it is. */}
            {updateInfo?.devMode && (
              <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 10 }}>
                Updates are off in a development build.
              </div>
            )}

            {updateInfo?.phase === 'error' && (
              <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--danger-ink)', marginBottom: 10 }}>
                {updateInfo.error}
              </div>
            )}

            {updateInfo?.phase !== 'downloading' && (
              <div
                style={{ ...secondaryButtonSmall, opacity: checkingUpdate ? 0.6 : 1, display: 'inline-block' }}
                onClick={checkingUpdate ? undefined : handleCheckForUpdate}
              >
                {checkingUpdate ? 'Checking…' : 'Check for updates'}
              </div>
            )}
          </Section>

          {error && (
            <div
              style={{
                font: "700 13px 'Quicksand'",
                color: 'var(--danger-ink)',
                background: 'var(--accent-soft-bg)',
                border: '2px solid var(--border)',
                borderRadius: 14,
                padding: '11px 14px',
                marginTop: 16
              }}
            >
              {error}
            </div>
          )}

          <div
            style={{
              position: 'sticky',
              bottom: 0,
              display: 'flex',
              gap: 10,
              alignItems: 'center',
              marginTop: 22,
              padding: '14px 0',
              background: 'var(--bg)'
            }}
          >
            <div
              style={{ ...primaryButtonSmall, opacity: saving ? 0.6 : 1 }}
              onClick={saving ? undefined : handleSave}
            >
              {saving ? 'Saving…' : 'Save everything'}
            </div>
            {saved && <div style={{ font: "700 13px 'Quicksand'", color: 'var(--accent)' }}>{saved}</div>}
          </div>
        </>
      )}
    </div>
  )
}

function Section({
  title,
  blurb,
  optional,
  accent,
  children
}: {
  title: string
  blurb: string
  optional?: boolean
  accent: string
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '2.5px solid var(--border)',
        borderRadius: 20,
        padding: 20,
        boxShadow: 'var(--shadow-md)',
        marginBottom: 16
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <div
          style={{
            width: 14,
            height: 14,
            background: accent,
            border: '2px solid var(--border)',
            borderRadius: '55% 45% 50% 50%',
            flexShrink: 0
          }}
        />
        <div style={{ font: "700 16px 'Kalam'", color: 'var(--ink)' }}>
          {title}
          {optional && (
            <span style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}> — optional</span>
          )}
        </div>
      </div>
      <div style={{ font: "600 12.5px/1.55 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 14 }}>{blurb}</div>
      {children}
    </div>
  )
}

function Field({
  setting,
  value,
  cleared,
  onChange,
  onClear
}: {
  setting: EnvSetting
  value: string | undefined
  cleared: boolean
  onChange: (v: string) => void
  onClear: (on: boolean) => void
}): React.JSX.Element {
  const help = setting.help.split('\n').slice(0, 2).join(' ')

  if (setting.choices.length) {
    return (
      <div>
        <label style={label}>{setting.name}</label>
        <select value={value ?? setting.value} onChange={(e) => onChange(e.target.value)} style={select}>
          {setting.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        {help && <Help text={help} />}
      </div>
    )
  }

  if (setting.isSecret) {
    return (
      <div>
        <label style={label}>{setting.name}</label>
        <input
          type="password"
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}
          placeholder={setting.isSet ? 'configured — leave blank to keep' : 'not set'}
          style={textInput}
          disabled={cleared}
        />
        {setting.isSet && (
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              font: "600 11.5px 'Quicksand'",
              color: 'var(--ink-faint)',
              marginTop: 5,
              cursor: 'pointer'
            }}
          >
            <input type="checkbox" checked={cleared} onChange={(e) => onClear(e.target.checked)} />
            Remove this one
          </label>
        )}
        {help && <Help text={help} />}
      </div>
    )
  }

  return (
    <div>
      <label style={label}>{setting.name}</label>
      <input value={value ?? setting.value} onChange={(e) => onChange(e.target.value)} style={textInput} />
      {help && <Help text={help} />}
    </div>
  )
}

function Help({ text }: { text: string }): React.JSX.Element {
  return (
    <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 5 }}>{text}</div>
  )
}

function CheckRow({
  check,
  onRun,
  labelText
}: {
  check: Check | undefined
  onRun: () => void
  labelText: string
}): React.JSX.Element {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
      <div
        style={{ ...secondaryButtonSmall, opacity: check?.state === 'checking' ? 0.6 : 1 }}
        onClick={check?.state === 'checking' ? undefined : onRun}
      >
        {check?.state === 'checking' ? 'Testing…' : labelText}
      </div>
      {check?.state === 'ok' && (
        <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent)' }}>{check.detail ?? 'Works ✓'}</div>
      )}
      {check?.state === 'error' && (
        <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--danger-ink)' }}>{check.detail}</div>
      )}
    </div>
  )
}
