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

export interface AppSettings {
  hfToken: string
  youtubeApiKey: string
  // Mastodon Post Creator. The instance is not a secret and is the more important
  // of the two — it decides the rules and the character limit. The token is only
  // needed for full-text search and for linking a published post back to its draft.
  mastodonInstance: string
  mastodonAccessToken: string
  googleAds: GoogleAdsSettings
  brandForge: BrandForgeSettings
  topicScout: TopicScoutSettings
}

const EMPTY_SETTINGS: AppSettings = {
  hfToken: '',
  youtubeApiKey: '',
  mastodonInstance: '',
  mastodonAccessToken: '',
  googleAds: { developerToken: '', clientId: '', clientSecret: '', refreshToken: '', loginCustomerId: '' },
  brandForge: { spaceId: '' },
  topicScout: {
    contactEmail: '',
    githubToken: '',
    reliefwebAppname: '',
    fredApiKey: '',
    twitterAuthToken: '',
    twitterCt0: '',
    geo: 'US'
  }
}

function configPath(): string {
  return join(app.getPath('userData'), 'config.enc')
}

// Legacy M1 path: a plaintext JSON file with just { hfToken }. Migrated automatically
// on first read, then left alone (new writes always go to the encrypted path).
function legacyConfigPath(): string {
  return join(app.getPath('userData'), 'config.json')
}

function readSettings(): AppSettings {
  const path = configPath()
  if (existsSync(path)) {
    try {
      const raw = readFileSync(path)
      const json = safeStorage.isEncryptionAvailable()
        ? safeStorage.decryptString(raw)
        : raw.toString('utf-8')
      return { ...EMPTY_SETTINGS, ...JSON.parse(json) }
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

export function setSettings(partial: Partial<AppSettings>): AppSettings {
  const current = readSettings()
  const next: AppSettings = {
    ...current,
    ...partial,
    googleAds: { ...current.googleAds, ...(partial.googleAds ?? {}) },
    brandForge: { ...current.brandForge, ...(partial.brandForge ?? {}) }
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
