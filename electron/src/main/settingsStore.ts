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

export interface AppSettings {
  hfToken: string
  youtubeApiKey: string
  hfAssets: HfAssetSettings
  // Mastodon Post Creator. The instance is not a secret and is the more important
  // of the two — it decides the rules and the character limit. The token is only
  // needed for full-text search and for linking a published post back to its draft.
  mastodonInstance: string
  mastodonAccessToken: string
  // Engage, Tumblr side. Nothing else in the app reads these yet.
  tumblr: TumblrSettings
  googleAds: GoogleAdsSettings
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
  Omit<AppSettings, 'googleAds' | 'brandForge' | 'topicScout' | 'telegram' | 'hfAssets' | 'tumblr'>
> & {
  hfAssets?: Partial<HfAssetSettings>
  googleAds?: Partial<GoogleAdsSettings>
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
  tumblr: { consumerKey: '', consumerSecret: '', oauthToken: '', oauthTokenSecret: '', blog: '' },
  googleAds: { developerToken: '', clientId: '', clientSecret: '', refreshToken: '', loginCustomerId: '' },
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

export function getSettings(): AppSettings {
  return readSettings()
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
