import { app, safeStorage } from 'electron'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'

export interface GoogleAdsSettings {
  developerToken: string
  clientId: string
  clientSecret: string
  refreshToken: string
  loginCustomerId: string
}

export interface BrandForgeSettings {
  spaceId: string
  // Bring-your-own Modal: Brand Studio generation runs on the user's own GPU
  // instead of the hosted Space. Both halves of the API token are needed before
  // anything switches over; blank means the hosted default is untouched.
  modalTokenId: string
  modalTokenSecret: string
  // ISO timestamp of the last successful deploy — drives the "already set up"
  // state in Settings so we don't re-deploy on every visit.
  modalProvisionedAt: string
  // The merged model the GPU function loads, and the bucket holding the image
  // weights. modal_backend.py and modal_image_backend.py read these from the
  // environment at import, and nothing in a packaged install could write it — so
  // the bring-your-own-GPU path could never deploy, and generation silently fell
  // back to the hosted Space. No defaults: they are repos the user pushes.
  modelRepo: string
  imageBucket: string
}

/**
 * The Space that generates the marketing plan — keyword research, the SEO, social and
 * ads plans, the composed strategy, and its own grounding.
 *
 * Empty is a working configuration, not a broken one: the backend then builds the plan
 * locally exactly as it always did. It lives here rather than only in the environment for
 * the same reason brandForge.spaceId does — a packaged install has no way to set an env
 * var, so a build without this had no path to configure it at all.
 *
 * Either form gradio_client understands: "owner/space-name" or a full https://...hf.space
 * URL.
 */
export interface MarketingPlanSettings {
  spaceUrl: string
}

/**
 * The Spaces the Blog Writer and Email Writer generate on.
 *
 * config.py reads BLOG_WRITER_SPACE and EMAIL_WRITER_SPACE from the environment and has no
 * defaults on purpose: hardcoded ids meant anyone who cloned the repo sent their generation
 * traffic to the original author's account. But nothing in a packaged install could write an
 * env var either, so both tools were unreachable — refusing with a message telling the user
 * to edit `backend/.env`, a file that does not exist in an installed build. Same gap
 * brandForge.spaceId and marketingPlan.spaceUrl already close, and for the same reason.
 *
 * Either form gradio_client understands: "owner/space-name" or a full https://...hf.space
 * URL. Empty keeps the tool's existing refusal, which is honest rather than broken.
 */
export interface WriterSpaceSettings {
  blogWriter: string
  emailWriter: string
}

/**
 * Optional: run Email Writer generation on the user's own Modal GPU instead of the free
 * Hugging Face Space.
 *
 * The Space is the default and stays it — free forever, and slow: about ninety seconds an
 * email on two shared vCPUs, queued behind everyone else using it. A T4 answers in seconds.
 * The trade is that Modal's starter plan is $30 of credits a month rather than a free tier,
 * so this is opt-in and blank means nothing changes.
 *
 * Blank falls back to the Brand Studio credentials, because a person has one Modal account
 * and being asked for the same token twice is a worse experience than a sensible default.
 */
export interface EmailWriterModalSettings {
  modalTokenId: string
  modalTokenSecret: string
  //: ISO timestamp of the last successful deploy, so Settings can say "already set up".
  modalProvisionedAt: string
}

/**
 * Proxy for the Marketing Plan's Keyword Surfer tier, which scrapes Google with a
 * headless browser.
 *
 * Google captchas automated browsers from ordinary addresses — measured from both a
 * datacenter IP and a plain residential one — so without a residential proxy pool that
 * source returns nothing and the plan keeps whatever the other keyword tiers found. Empty
 * is perfectly valid; it just means Surfer stays blocked.
 *
 * Unlike the Google Ads credentials, these never leave this machine: the scrape runs in the
 * app's own browser, because a Space has no consumer network to scrape from.
 */
export interface KeywordSurferSettings {
  /** e.g. http://gate.provider.com:7000 or socks5://host:1080 */
  proxyServer: string
  /** Optional — an open proxy needs neither. */
  proxyUsername: string
  proxyPassword: string
}

// Topic Scout runs key-free by default. Everything here unlocks one extra source
// whose provider requires identification (SEC and ReliefWeb), a key (FRED), or a
// session (Twitter/X) — or just raises a rate limit (GitHub).
export interface TopicScoutSettings {
  contactEmail: string
  githubToken: string
  reliefwebAppname: string
  fredApiKey: string
  twitterAuthToken: string
  twitterCt0: string
  geo: string
}

// The Community section's account login. `apiId`/`apiHash` come from my.telegram.org and
// identify the *app*; `session` is the login itself and is full access to the account — it
// lives here, in the DPAPI-encrypted store, rather than in the backend's SQLite file.
export interface TelegramSettings {
  apiId: string
  apiHash: string
  session: string
  username: string
}

/**
 * Hugging Face repos holding the datasets and the model the app fetches on first use
 * rather than shipping (see backend/app/services/hf_assets.py).
 *
 * These are repo ids, not credentials, but they are deliberately not defaulted anywhere:
 * a build that defaults them points every installation at whoever published it. They had
 * no configuration path at all before — the backend read them from its environment, and
 * nothing in a packaged install could set that environment — so the tools that depend on
 * them failed on any machine without a dev checkout.
 */
export interface HfAssetSettings {
  /** dataset: influencer_database.xlsx — the Influencer Database catalogue */
  influencerRepo: string
  /** dataset: guest_post_database.xlsx + opr_scores.json — the Guest Post Suggester */
  guestPostRepo: string
  /** model: ctr_model.joblib + ctr_reference_stats.json — the Email Writer's CTR estimate */
  ctrModelRepo: string
}

/**
 * Tumblr's API is OAuth 1.0a, so a login is four secrets rather than one token:
 * the consumer pair identifies the application (tumblr.com/oauth/apps) and the
 * token pair identifies the user (api.tumblr.com/console). All four are needed to
 * sign a single request.
 *
 * `blog` is which of the account's blogs to act as. Not a secret, and optional —
 * blank means the account's primary blog, which the backend resolves.
 */
export interface TumblrSettings {
  consumerKey: string
  consumerSecret: string
  oauthToken: string
  oauthTokenSecret: string
  blog: string
}

/**
 * One Mastodon account, on one instance.
 *
 * A Mastodon token is issued by a single server and is worthless — and unsafe to send —
 * anywhere else, so a credential is never stored on its own: it is always paired with the
 * host that granted it. Users routinely hold accounts on more than one instance, and each
 * needs its own entry.
 */
export interface MastodonAccount {
  /** Host only, e.g. "mastodon.social". */
  instance: string
  accessToken: string
}

export interface AppSettings {
  hfToken: string
  youtubeApiKey: string
  hfAssets: HfAssetSettings
  // Mastodon Post Creator. The instance is not a secret and is the more important
  // of the two — it decides the rules and the character limit. The token is only
  // needed for full-text search and for linking a published post back to its draft.
  //
  // `mastodonInstance` is the ACTIVE account — the one the composer posts to and whose
  // rules gate applies. `mastodonAccounts` is every account the user has connected, so
  // anything that reads a *different* instance (the posting-time collector, for one) can
  // find that instance's own credential instead of misusing the active one.
  mastodonInstance: string
  mastodonAccessToken: string
  mastodonAccounts: MastodonAccount[]
  // Engage, Tumblr side. Nothing else in the app reads these yet.
  tumblr: TumblrSettings
  googleAds: GoogleAdsSettings
  keywordSurfer: KeywordSurferSettings
  marketingPlan: MarketingPlanSettings
  writerSpaces: WriterSpaceSettings
  emailWriterModal: EmailWriterModalSettings
  brandForge: BrandForgeSettings
  topicScout: TopicScoutSettings
  telegram: TelegramSettings
}

/**
 * What a caller may pass to `setSettings`.
 *
 * `Partial<AppSettings>` isn't enough: it makes each *group* optional but still demands a
 * whole group when one is given, so saving a single Telegram field would mean restating the
 * other three. The nested groups merge key-by-key on write, so the type says so too.
 */
export type SettingsPatch = Partial<
  Omit<
    AppSettings,
    | 'googleAds'
    | 'keywordSurfer'
    | 'marketingPlan'
    | 'writerSpaces'
    | 'emailWriterModal'
    | 'brandForge'
    | 'topicScout'
    | 'telegram'
    | 'hfAssets'
    | 'tumblr'
  >
> & {
  hfAssets?: Partial<HfAssetSettings>
  googleAds?: Partial<GoogleAdsSettings>
  keywordSurfer?: Partial<KeywordSurferSettings>
  marketingPlan?: Partial<MarketingPlanSettings>
  writerSpaces?: Partial<WriterSpaceSettings>
  emailWriterModal?: Partial<EmailWriterModalSettings>
  brandForge?: Partial<BrandForgeSettings>
  topicScout?: Partial<TopicScoutSettings>
  telegram?: Partial<TelegramSettings>
  tumblr?: Partial<TumblrSettings>
}

const EMPTY_SETTINGS: AppSettings = {
  hfToken: '',
  youtubeApiKey: '',
  hfAssets: { influencerRepo: '', guestPostRepo: '', ctrModelRepo: '' },
  mastodonInstance: '',
  mastodonAccessToken: '',
  mastodonAccounts: [],
  tumblr: { consumerKey: '', consumerSecret: '', oauthToken: '', oauthTokenSecret: '', blog: '' },
  googleAds: { developerToken: '', clientId: '', clientSecret: '', refreshToken: '', loginCustomerId: '' },
  keywordSurfer: { proxyServer: '', proxyUsername: '', proxyPassword: '' },
  marketingPlan: { spaceUrl: '' },
  writerSpaces: { blogWriter: '', emailWriter: '' },
  emailWriterModal: { modalTokenId: '', modalTokenSecret: '', modalProvisionedAt: '' },
  brandForge: { spaceId: '', modalTokenId: '', modalTokenSecret: '', modalProvisionedAt: '', modelRepo: '', imageBucket: '' },
  topicScout: {
    contactEmail: '',
    githubToken: '',
    reliefwebAppname: '',
    fredApiKey: '',
    twitterAuthToken: '',
    twitterCt0: '',
    geo: 'US'
  },
  telegram: { apiId: '', apiHash: '', session: '', username: '' }
}

function configPath(): string {
  return join(app.getPath('userData'), 'config.enc')
}

// Legacy M1 path: a plaintext JSON file with just { hfToken }. Migrated automatically
// on first read, then left alone (new writes always go to the encrypted path).
function legacyConfigPath(): string {
  return join(app.getPath('userData'), 'config.json')
}

// A stored config predates any field added since it was written, so the nested
// groups have to be merged key-by-key. A shallow spread would drop a whole group
// back to `undefined` for existing installs the first time one gains a field.
function withDefaults(parsed: Partial<AppSettings>): AppSettings {
  return {
    ...EMPTY_SETTINGS,
    ...parsed,
    hfAssets: { ...EMPTY_SETTINGS.hfAssets, ...(parsed.hfAssets ?? {}) },
    googleAds: { ...EMPTY_SETTINGS.googleAds, ...(parsed.googleAds ?? {}) },
    keywordSurfer: { ...EMPTY_SETTINGS.keywordSurfer, ...(parsed.keywordSurfer ?? {}) },
    marketingPlan: { ...EMPTY_SETTINGS.marketingPlan, ...(parsed.marketingPlan ?? {}) },
    writerSpaces: { ...EMPTY_SETTINGS.writerSpaces, ...(parsed.writerSpaces ?? {}) },
    emailWriterModal: { ...EMPTY_SETTINGS.emailWriterModal, ...(parsed.emailWriterModal ?? {}) },
    brandForge: { ...EMPTY_SETTINGS.brandForge, ...(parsed.brandForge ?? {}) },
    topicScout: { ...EMPTY_SETTINGS.topicScout, ...(parsed.topicScout ?? {}) },
    telegram: { ...EMPTY_SETTINGS.telegram, ...(parsed.telegram ?? {}) },
    tumblr: { ...EMPTY_SETTINGS.tumblr, ...(parsed.tumblr ?? {}) }
  }
}

function readSettings(): AppSettings {
  const path = configPath()
  if (existsSync(path)) {
    try {
      const raw = readFileSync(path)
      const json = safeStorage.isEncryptionAvailable()
        ? safeStorage.decryptString(raw)
        : raw.toString('utf-8')
      return withDefaults(JSON.parse(json))
    } catch (err) {
      console.error('[settings] failed to read/decrypt settings, resetting:', err)
      return { ...EMPTY_SETTINGS }
    }
  }

  const legacyPath = legacyConfigPath()
  if (existsSync(legacyPath)) {
    try {
      const legacy = JSON.parse(readFileSync(legacyPath, 'utf-8'))
      const migrated: AppSettings = { ...EMPTY_SETTINGS, hfToken: legacy.hfToken ?? '' }
      writeSettings(migrated)
      return migrated
    } catch (err) {
      console.error('[settings] failed to migrate legacy config:', err)
    }
  }

  return { ...EMPTY_SETTINGS }
}

function writeSettings(settings: AppSettings): void {
  const json = JSON.stringify(settings)
  const data = safeStorage.isEncryptionAvailable() ? safeStorage.encryptString(json) : Buffer.from(json, 'utf-8')
  if (!safeStorage.isEncryptionAvailable()) {
    console.warn('[settings] OS-level encryption is unavailable on this machine — storing settings as plaintext.')
  }
  writeFileSync(configPath(), data)
}

/** Host only, lower-cased — the form accounts are keyed by. */
export function normaliseInstance(value: string): string {
  return (value ?? '')
    .trim()
    .replace(/^https?:\/\//, '')
    .split('/')[0]
    .replace(/\.$/, '')
    .toLowerCase()
}

export function getSettings(): AppSettings {
  const stored = readSettings()

  // Installs that predate the account list still have a single instance/token pair. Read
  // it as the first account rather than migrating on write: nothing is lost if the user
  // downgrades, and the pair remains the active account either way.
  const accounts = stored.mastodonAccounts ?? []
  const active = normaliseInstance(stored.mastodonInstance)
  if (active && stored.mastodonAccessToken && !accounts.some((a) => normaliseInstance(a.instance) === active)) {
    return {
      ...stored,
      mastodonAccounts: [...accounts, { instance: active, accessToken: stored.mastodonAccessToken }]
    }
  }
  return { ...stored, mastodonAccounts: accounts }
}

/** The token for one instance, or '' when the user has no account there. */
export function mastodonTokenFor(instance: string): string {
  const host = normaliseInstance(instance)
  if (!host) return ''
  const match = getSettings().mastodonAccounts.find((a) => normaliseInstance(a.instance) === host)
  return match?.accessToken ?? ''
}

export function setSettings(partial: SettingsPatch): AppSettings {
  const current = readSettings()
  // Nested groups merge key-by-key: a caller that saves one Telegram field must not blank
  // the other three, which is what a plain `...partial` spread would do.
  const next: AppSettings = {
    ...current,
    ...partial,
    hfAssets: { ...current.hfAssets, ...(partial.hfAssets ?? {}) },
    googleAds: { ...current.googleAds, ...(partial.googleAds ?? {}) },
    keywordSurfer: { ...current.keywordSurfer, ...(partial.keywordSurfer ?? {}) },
    marketingPlan: { ...current.marketingPlan, ...(partial.marketingPlan ?? {}) },
    writerSpaces: { ...current.writerSpaces, ...(partial.writerSpaces ?? {}) },
    emailWriterModal: { ...current.emailWriterModal, ...(partial.emailWriterModal ?? {}) },
    brandForge: { ...current.brandForge, ...(partial.brandForge ?? {}) },
    topicScout: { ...current.topicScout, ...(partial.topicScout ?? {}) },
    telegram: { ...current.telegram, ...(partial.telegram ?? {}) },
    tumblr: { ...current.tumblr, ...(partial.tumblr ?? {}) }
  }
  writeSettings(next)
  return next
}

// Back-compat convenience wrappers used by the HF connect gate.
export function getHfToken(): string | null {
  const token = readSettings().hfToken
  return token || null
}

export function setHfToken(token: string | null): void {
  setSettings({ hfToken: token ?? '' })
}
