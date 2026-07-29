import { app } from 'electron'

// Placeholder — point this at wherever you end up publishing releases (e.g. a
// raw.githubusercontent.com URL to a version.json in a repo, or your own host). The app
// just needs a small JSON file reachable at this URL shaped like:
//   { "version": "0.2.0", "url": "https://.../Mr-AI-Marketer-Setup-0.2.0.exe", "notes": "..." }
// Until that's set up, checks will just fail quietly (surfaced as an `error` in the result,
// not a crash) and the UI shows "Couldn't check for updates."
export const UPDATE_CHECK_URL = 'https://example.com/mr-ai-marketer/latest.json'

export interface UpdateManifest {
  version: string
  url: string
  notes?: string
}

export interface UpdateCheckResult {
  currentVersion: string
  updateAvailable: boolean
  latestVersion?: string
  downloadUrl?: string
  notes?: string
  error?: string
}

/** Plain dot-separated numeric version compare (e.g. "1.10.0" > "1.9.0") — no pre-release
 * qualifiers expected since releases are just bumped x.y.z tags here. */
function isNewer(candidate: string, current: string): boolean {
  const a = candidate.split('.').map((n) => parseInt(n, 10) || 0)
  const b = current.split('.').map((n) => parseInt(n, 10) || 0)
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const diff = (a[i] ?? 0) - (b[i] ?? 0)
    if (diff !== 0) return diff > 0
  }
  return false
}

export async function checkForUpdate(): Promise<UpdateCheckResult> {
  const currentVersion = app.getVersion()
  try {
    const res = await fetch(UPDATE_CHECK_URL)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const manifest = (await res.json()) as UpdateManifest
    if (!manifest.version || !manifest.url) throw new Error('Malformed version manifest')
    return {
      currentVersion,
      updateAvailable: isNewer(manifest.version, currentVersion),
      latestVersion: manifest.version,
      downloadUrl: manifest.url,
      notes: manifest.notes
    }
  } catch (err) {
    return {
      currentVersion,
      updateAvailable: false,
      error: err instanceof Error ? err.message : String(err)
    }
  }
}
