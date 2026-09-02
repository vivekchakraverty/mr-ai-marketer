import { app, safeStorage } from 'electron'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'

// The shapes and their defaults live in src/shared, because the renderer needs them too —
// see the note there. Re-exported so `import ... from './settingsStore'` keeps working for
// main, preload and the renderer alike.
export type {
  GoogleAdsSettings,
  BrandForgeSettings,
  MarketingPlanSettings,
  WriterSpaceSettings,
  EmailWriterModalSettings,
  KeywordSurferSettings,
  TopicScoutSettings,
  TelegramSettings,
  SetupWizardSettings,
  CloudPostingSettings,
  HfAssetSettings,
  TumblrSettings,
  MastodonAccount,
  AppSettings,
  SettingsPatch
} from '../shared/settings'

import type { AppSettings, SettingsPatch } from '../shared/settings'
import { EMPTY_SETTINGS } from '../shared/settings'

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
    tumblr: { ...EMPTY_SETTINGS.tumblr, ...(parsed.tumblr ?? {}) },
    setupWizard: { ...EMPTY_SETTINGS.setupWizard, ...(parsed.setupWizard ?? {}) },
    cloudPosting: { ...EMPTY_SETTINGS.cloudPosting, ...(parsed.cloudPosting ?? {}) }
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
    tumblr: { ...current.tumblr, ...(partial.tumblr ?? {}) },
    setupWizard: { ...current.setupWizard, ...(partial.setupWizard ?? {}) },
    cloudPosting: { ...current.cloudPosting, ...(partial.cloudPosting ?? {}) }
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
