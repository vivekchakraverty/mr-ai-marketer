import type { DocuFields, GuestFields, LibraryItem, PlanFields, TutorialFields } from '../state/types'
import type { BlogFields } from '../state/types'
import type { Workbooks as TrackerWorkbooks } from '../components/tracker/formulas'
import { reportError } from '../state/errors'

export type { TrackerWorkbooks }

export const backendUrl = window.api?.backendUrl ?? 'http://127.0.0.1:8756'

/**
 * Proof that a request came from this app.
 *
 * The backend binds to 127.0.0.1, which is not the same as being private: any web page the
 * user has open can fetch it, and the backend has to keep a permissive CORS policy because
 * the packaged renderer runs from file://. Without this header the browser would hand another
 * site the response. Generated fresh per launch by the main process and delivered on argv.
 */
const apiToken = window.api?.apiToken ?? ''

/** Request headers with the token attached. Pass `extra` for Content-Type and friends. */
function authHeaders(extra?: Record<string, string>): Record<string, string> {
  return apiToken ? { ...extra, 'X-MRAIM-Token': apiToken } : { ...extra }
}

/**
 * Fetch a backend-served file as an object URL, for `<img src>` and friends.
 *
 * An `<img>` tag cannot send a request header, so pointing one straight at
 * `${backendUrl}/outputs/…` gets a 401 from the token middleware and renders as a broken
 * image — which is exactly what every generated image in the app was doing. Fetching the
 * bytes here, where the header can be attached, and handing the element a blob: URL is the
 * fix that does not involve opening /outputs to callers that cannot prove who they are.
 *
 * Opening it was the tempting one-liner and is not safe: the mount also serves
 * `outputs/influencers/influencers-<timestamp>.csv` and the tracker's exports, whose names
 * are a date and a time rather than a UUID and so are within brute-force reach of any page
 * the user has open.
 *
 * The caller owns the returned URL and must `URL.revokeObjectURL` it when done, or the blob
 * is held for the lifetime of the window.
 */
export async function fetchObjectUrl(path: string): Promise<string> {
  const res = await fetch(`${backendUrl}${path}`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`Couldn't load ${path} (HTTP ${res.status})`)
  return URL.createObjectURL(await res.blob())
}

/**
 * Turn a failed response into an Error the UI can show as-is.
 *
 * Every HTTPException this backend raises carries a `detail` written for a
 * person to read ("127.0.0.1 resolves to a non-public address…"). Surfacing the
 * raw body instead buries that message inside `HTTP 400 {"detail":"…"}`, so the
 * one useful sentence arrives wrapped in JSON noise. Pull it out when it's
 * there, and fall back to the envelope when it isn't.
 */
async function errorFrom(res: Response, path: string): Promise<Error> {
  const text = await res.text().catch(() => '')
  let message = `${path} failed: HTTP ${res.status}${text ? ` ${text}` : ''}`
  try {
    const detail = JSON.parse(text)?.detail
    if (typeof detail === 'string' && detail.trim()) {
      message = detail
    } else if (Array.isArray(detail) && detail.length) {
      // Pydantic validation errors arrive as a list of {loc, msg, …}.
      const msg = detail.map((d) => d?.msg).filter(Boolean).join('; ')
      if (msg) message = msg
    }
  } catch {
    // Not JSON — keep the envelope, which is all we have.
  }
  // Raise it globally as well as returning it. Callers still catch this and render their own
  // inline message; the popup is what makes an error copyable, and what catches the ones a
  // caller forgets to handle.
  reportError({
    message,
    source: `${res.status} ${path}`,
    detail: text && text !== message ? text : ''
  })
  return new Error(message)
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${backendUrl}${path}`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body)
  })
  if (!res.ok) throw await errorFrom(res, path)
  return res.json() as Promise<T>
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${backendUrl}${path}`, { headers: authHeaders() })
  if (!res.ok) throw await errorFrom(res, path)
  return res.json() as Promise<T>
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${backendUrl}${path}`, {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body)
  })
  if (!res.ok) throw await errorFrom(res, path)
  return res.json() as Promise<T>
}

async function deleteJson<T>(path: string): Promise<T> {
  const res = await fetch(`${backendUrl}${path}`, { method: 'DELETE', headers: authHeaders() })
  if (!res.ok) throw await errorFrom(res, path)
  return res.json() as Promise<T>
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${backendUrl}${path}`, {
    method: 'PATCH',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body)
  })
  if (!res.ok) throw await errorFrom(res, path)
  return res.json() as Promise<T>
}

export async function checkHealth(): Promise<{ ok: boolean; detail?: string }> {
  try {
    const res = await fetch(`${backendUrl}/health`)
    if (!res.ok) return { ok: false, detail: `HTTP ${res.status}` }
    const data = await res.json()
    return { ok: true, detail: JSON.stringify(data) }
  } catch (err) {
    return { ok: false, detail: err instanceof Error ? err.message : String(err) }
  }
}

export interface VerifyTokenResponse {
  valid: boolean
  username: string | null
  detail: string | null
}

export function verifyHfToken(token: string): Promise<VerifyTokenResponse> {
  return postJson('/settings/verify-hf-token', { token })
}

export interface LibraryListResponse {
  items: LibraryItem[]
  count: number
}

export function fetchLibrary(): Promise<LibraryListResponse> {
  return getJson('/library')
}

/**
 * Save generated content that the tool didn't already save for you.
 *
 * Most tools write to the Library as part of generating; this is the explicit path for the
 * ones that don't, and for a user who wants to keep something the app didn't decide to keep.
 */
export function saveToLibrary(input: {
  tool: string
  title: string
  subtitle?: string
  content?: string
  outputPath?: string
}): Promise<{ libraryId: string }> {
  return postJson('/library', input)
}

export function deleteLibraryItem(id: string): Promise<{ deleted: string }> {
  return deleteJson(`/library/${id}`)
}

// --- Backups ---------------------------------------------------------------

export interface BackupEntry {
  id: string
  createdAt: string
  databases: { name: string; bytes: number }[]
  bytes: number
  path: string
}

export function listBackups(): Promise<{ backups: BackupEntry[]; directory: string }> {
  return getJson('/backup')
}

export function createBackup(label = ''): Promise<{ backup: BackupEntry }> {
  return postJson('/backup', { label })
}

export function deleteBackup(id: string): Promise<{ deleted: string }> {
  return deleteJson(`/backup/${encodeURIComponent(id)}`)
}

export function restoreBackup(id: string): Promise<{ restored: string[]; safetyBackup: string; detail: string }> {
  return postJson(`/backup/${encodeURIComponent(id)}/restore`, {})
}

// --- Tracker export --------------------------------------------------------

export interface ExportSheet {
  name: string
  columns: string[]
  rows: string[][]
}

export function exportTracker(
  format: 'csv' | 'xlsx',
  workbook: string,
  sheets: ExportSheet[]
): Promise<{ path: string; files: string[]; format: string }> {
  return postJson('/tracker/export', { format, workbook, sheets })
}

export interface GeneratePlanResponse {
  markdown: string
  seoMarkdown: string
  socialMarkdown: string
  adsMarkdown: string
  keywordSourceNote: string
  libraryId: string
}

export async function generatePlan(fields: PlanFields): Promise<GeneratePlanResponse> {
  const settings = await window.api.settings.getAll()
  return postJson('/marketing-plan/generate', {
    name: fields.name,
    productDescription: fields.productDescription,
    budgetUsdPerMonth: fields.budgetUsdPerMonth,
    manpowerSummary: fields.manpowerSummary,
    industryKey: fields.industryKey,
    geo: fields.geo,
    hfToken: settings.hfToken,
    model: fields.model,
    googleAds: settings.googleAds
  })
}

export interface BlogImage {
  url: string
  caption: string | null
}

export interface GenerateBlogResponse {
  title: string
  markdown: string
  images: BlogImage[]
  docxPath: string | null
  docxUrl: string | null
  libraryId: string
}

/**
 * A finished Brand Studio document that other tools can write in the voice of.
 *
 * Brand Studio saves a "voice card" with every document — a compact brief of tone, voice
 * traits and guardrails, built to be handed to another model. Passing an id to a generate
 * call folds that card into the request so the output sounds like the brand.
 */
export interface BrandVoice {
  id: string
  title: string
  createdAt: string
}

export function listBrandVoices(): Promise<BrandVoice[]> {
  return getJson('/brand-forge/voices')
}

export async function generateBlog(fields: BlogFields, brandVoiceId = ''): Promise<GenerateBlogResponse> {
  const hfToken = (await window.api.settings.getHfToken()) ?? ''
  return postJson('/blog-writer/generate', { ...fields, brandVoiceId, hfToken })
}

// Email Writer runs the user's fine-tuned marketing-email model on a free HF CPU Space,
// so it takes no token here and the request can be slow (a minute or two per email).
// predictedClickRate/ctrBucket come from a small model trained on historical campaign
// data (0-1 range) — a statistical estimate only, always render it labeled as such.
export interface GenerateEmailResponse {
  text: string
  libraryId: string
  predictedClickRate: number
  ctrBucket: 'below average' | 'typical' | 'above average' | 'strong'
}

export async function generateEmail(instruction: string, brandVoiceId = ''): Promise<GenerateEmailResponse> {
  // The token is only used the first time on a machine, to fetch the CTR model from Hugging
  // Face — it isn't bundled with the app any more. Sent every call because the backend is
  // the only side that knows whether the model is already on disk.
  const settings = await window.api.settings.getAll()
  return postJson('/email-writer/generate', { instruction, brandVoiceId, hfToken: settings.hfToken })
}

export interface GuestSite {
  domain: string
  title: string
  niche: string
  page_rank: number
  guest_posts_url: string
}

export interface SearchGuestResponse {
  sites: GuestSite[]
  libraryId: string
}

export function searchGuestPosts(fields: GuestFields): Promise<SearchGuestResponse> {
  return postJson('/guest-post/search', fields)
}

export interface AnalyzeGuestResponse {
  contactUrl: string
  guestPostsUrl: string
  titleCount: number
  tierUsed: string
  sampleTitles: string[]
  suggestions: string[]
}

export async function analyzeGuestPost(domain: string, topic: string): Promise<AnalyzeGuestResponse> {
  const hfToken = (await window.api.settings.getHfToken()) ?? ''
  return postJson('/guest-post/analyze', { domain, topic, hfToken })
}

export interface TutorialStep {
  heading: string
  body: string
  timestamp: number | null
  imageUrl: string | null
  caption: string | null
}

export interface GenerateTutorialResponse {
  title: string
  intro: string
  answer: string
  steps: TutorialStep[]
  faqs: { q: string; a: string }[]
  sourceUrl: string | null
  docxPath: string | null
  docxUrl: string | null
  libraryId: string
  /** Set when comment sentiment fell below the floor, so the video was never downloaded. */
  sentimentNote?: string | null
}

export async function generateTutorial(fields: TutorialFields): Promise<GenerateTutorialResponse> {
  const settings = await window.api.settings.getAll()
  return postJson('/tutorial-maker/generate', {
    ...fields,
    hfToken: settings.hfToken,
    youtubeApiKey: settings.youtubeApiKey
  })
}

export interface DocuStep {
  heading: string
  text: string
  timestamp: number | null
  imageUrl: string | null
  caption: string | null
}

export interface GenerateDocuResponse {
  title: string
  intro: string
  prerequisites: string[]
  steps: DocuStep[]
  docxPath: string | null
  docxUrl: string | null
  libraryId: string
}

export interface DistributionJob {
  id: string
  library_item_id: string
  channel: string
  status: string
  activepieces_run_id: string | null
  resume_url: string | null
  error: string | null
  scheduled_at: string | null
  payload: string | null
  created_at: string
  updated_at: string
}

export function fetchDistributionQueue(): Promise<{ jobs: DistributionJob[] }> {
  return getJson('/distribution/queue')
}

export function approveDistributionItem(jobId: string): Promise<DistributionJob> {
  return postJson(`/distribution/queue/${jobId}/approve`, {})
}

export function rejectDistributionItem(jobId: string): Promise<DistributionJob> {
  return postJson(`/distribution/queue/${jobId}/reject`, {})
}

export interface ChannelStatus {
  channel: string
  connected: boolean
}

/** A channel the user built from the piece catalogue, rather than one of the ten bundled ones. */
export interface CustomChannelStatus extends ChannelStatus {
  label: string
  authType: string | null
  pieceName: string
  custom: true
}

export interface DistributionChannelsResponse {
  ready: boolean
  detail?: string
  channels: ChannelStatus[]
  communityChannels: ChannelStatus[]
  customChannels?: CustomChannelStatus[]
}

export interface CataloguePiece {
  name: string
  displayName: string
  description: string
  logoUrl: string | null
  version: string
  authType: string | null
  actionCount: number
  categories: string[]
  alreadyAdded: boolean
  builtIn: boolean
}

/** One prop of a piece's auth block or action, as the piece itself declares it. */
export interface PieceProp {
  key: string
  label: string
  description: string
  type: string
  required: boolean
  defaultValue?: unknown
  options: { label: string; value: string }[]
  /** Which send-payload field this prop most likely wants — a pre-selection, not a decision. */
  suggestedBinding?: string | null
}

export interface PieceAction {
  name: string
  label: string
  description: string
  props: PieceProp[]
}

export interface CataloguePieceDetail {
  name: string
  displayName: string
  version: string
  logoUrl: string | null
  auth: { type: string | null; label: string; description: string; props: PieceProp[] }
  actions: PieceAction[]
  payloadFields: string[]
}

/** Per prop: bind it to a send-payload field, or pin it to a literal typed once at setup. */
export type InputChoice = { field: string } | { value: string }

export function fetchDistributionCatalogue(q = '', limit = 60): Promise<{ total: number; pieces: CataloguePiece[] }> {
  return getJson(`/distribution/catalogue?q=${encodeURIComponent(q)}&limit=${limit}`)
}

export function fetchCataloguePiece(pieceName: string): Promise<CataloguePieceDetail> {
  return getJson(`/distribution/catalogue/${pieceName}`)
}

export function createCustomChannel(body: {
  channel: string
  label: string
  pieceName: string
  pieceVersion: string
  actionName: string
  authType: string
  inputMap: Record<string, InputChoice>
}): Promise<{ channel: string; label: string }> {
  return postJson('/distribution/custom-channels', body)
}

export function deleteCustomChannel(channel: string): Promise<{ deleted: boolean }> {
  return deleteJson(`/distribution/custom-channels/${channel}`)
}

export function fetchDistributionChannels(): Promise<DistributionChannelsResponse> {
  return getJson('/distribution/channels')
}

export function connectChannel(channel: string, type: 'CUSTOM_AUTH' | 'SECRET_TEXT', value: Record<string, unknown>): Promise<{ connected: boolean }> {
  return postJson(`/distribution/connections/${channel}`, { type, value })
}

export function disconnectChannel(channel: string): Promise<{ connected: boolean }> {
  return deleteJson(`/distribution/connections/${channel}`)
}

/**
 * Credentials the app can already supply for a channel, so the connect dialog
 * doesn't ask for something the user has entered once already.
 *
 * Secrets come back as SETTINGS_PLACEHOLDER rather than the real value — sending
 * it straight back on connect tells the backend to substitute what it holds. The
 * renderer therefore never handles a saved app password at all.
 *
 * Mastodon is missing from this endpoint on purpose: its credentials live in
 * Electron's own encrypted store, which the backend cannot read, so the modal
 * fills those from `window.api.settings` instead.
 */
export const SETTINGS_PLACEHOLDER = '__from_settings__'

export interface ChannelPrefillField {
  key: string
  value: string
  secret: boolean
}

export interface ChannelPrefill {
  channel: string
  available: boolean
  source: string | null
  fields: ChannelPrefillField[]
}

export function fetchChannelPrefill(channel: string): Promise<ChannelPrefill> {
  return getJson(`/distribution/connections/${channel}/prefill`)
}

export function verifyChannelSettings(channel: string): Promise<{ ok: boolean; detail: string }> {
  return postJson(`/distribution/connections/${channel}/verify-settings`, {})
}

export function fetchDistributionConsoleUrl(): Promise<{ url: string }> {
  return getJson('/distribution/console-url')
}

export function fetchDistributionJobs(status?: string): Promise<{ jobs: DistributionJob[] }> {
  return getJson(`/distribution/jobs${status ? `?status=${status}` : ''}`)
}

export interface SendToDistributionRequest {
  libraryItemId: string
  channels: string[]
  text: string
  channelId?: string
  pageId?: string
  imageUrl?: string
  to?: string
  from?: string
  subject?: string
  subreddit?: string
  title?: string
  scheduledAt?: string
}

export function sendToDistribution(body: SendToDistributionRequest): Promise<{ jobs: DistributionJob[] }> {
  return postJson('/distribution/send', body)
}

export async function generateDocu(fields: DocuFields, video: File): Promise<GenerateDocuResponse> {
  const hfToken = (await window.api.settings.getHfToken()) ?? ''
  const form = new FormData()
  form.append('video', video)
  form.append('product', fields.product)
  form.append('hfToken', hfToken)

  // No Content-Type: the browser has to set it so the multipart boundary is right.
  const res = await fetch(`${backendUrl}/docu-maker/generate`, {
    method: 'POST',
    headers: authHeaders(),
    body: form
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`/docu-maker/generate failed: HTTP ${res.status} ${text}`)
  }
  return res.json() as Promise<GenerateDocuResponse>
}

// ---------------------------------------------------------------------------
// Social Post Generator (vendor/socialpost)
//
// Unlike the other tools, this one reads its credentials from the backend's
// environment rather than taking them per-request — see pushSocialPostEnv, which
// the app calls on boot and whenever Settings is saved.
// ---------------------------------------------------------------------------

export interface SocialNiche {
  name: string
  keywords: string[]
  active: boolean
  posts: number
  exemplars: number
  authors: number
  generations: number
}

export interface SocialStatus {
  configured: boolean
  missing: string[]
  backend: string
  provider: string
  model: string
  niches: number
  posts: number
  exemplars: number
  readyToGround: boolean
  telemetryEnabled: boolean
  needsConsent: boolean
}

export interface SocialExemplar {
  id: number
  postUri: string
  text: string
  similarity: number
  score: number
  webUrl: string
}

export interface SocialKb {
  id: number
  source: string
  url: string
  summary: string
  decayWeight: number
}

export interface SocialSource {
  url: string
  title: string
  excerpt: string
  truncated: boolean
}

export interface SocialGenerateResponse {
  text: string
  generationId: number | null
  characters: number
  overLimit: boolean
  exemplars: SocialExemplar[]
  kbArticles: SocialKb[]
  libraryId: string | null
  source: SocialSource | null
}

export interface SocialGeneratedImage {
  url: string
  promptUsed: string
  width: number
  height: number
}

export function getSocialStatus(): Promise<SocialStatus> {
  return getJson('/social-post/status')
}

export function listSocialNiches(): Promise<SocialNiche[]> {
  return getJson('/social-post/niches')
}

/** One niche's first fill, queued automatically when the niche is created. */
export interface NicheFirstFill {
  niche: string
  state: 'queued' | 'running' | 'done' | 'failed'
  /** Absent until the Bluesky half has finished or been skipped. */
  bluesky?: { posts?: number; exemplars?: number; skipped?: string; error?: string }
  /** Absent until the Mastodon half has finished or been skipped. */
  mastodon?: {
    skipped?: string
    instances?: Record<string, { stored?: number; exemplars?: number; error?: string }>
  }
  error?: string
}

export function saveSocialNiche(
  name: string,
  keywords: string[],
  active = true
): Promise<{ name: string; weakKeywords: string[]; firstFill: { queued: boolean } | null }> {
  return postJson('/social-post/niches', { name, keywords, active })
}

/** Progress of the automatic fills. Poll while `pending`. */
export function getNicheFirstFill(): Promise<{ pending: boolean; fills: NicheFirstFill[] }> {
  return getJson('/social-post/niches/first-fill')
}

export function deleteSocialNiche(name: string, purge = false): Promise<{ removed: Record<string, number> }> {
  return deleteJson(`/social-post/niches/${encodeURIComponent(name)}?purge=${purge}`)
}

export function collectSocialNiche(name: string, limit = 25): Promise<{ posts: number; exemplars: number }> {
  return postJson(`/social-post/niches/${encodeURIComponent(name)}/collect?limit=${limit}`, {})
}

export function generateSocialPost(
  userInput: string,
  niche: string,
  platform: string,
  sourceUrl = '',
  // Posts already written for this same request. Sent on a rewrite so the model
  // is told what not to repeat — the prompt is otherwise byte-identical between
  // attempts and the model reliably reproduces its own opening line.
  avoidTexts: string[] = [],
  brandVoiceId = ''
): Promise<SocialGenerateResponse> {
  return postJson('/social-post/generate', { userInput, niche, platform, sourceUrl, avoidTexts, brandVoiceId })
}

export async function generateSocialPostImage(
  postText: string,
  niche: string,
  platform: string
): Promise<SocialGeneratedImage> {
  const settings = await window.api.settings.getAll()
  return postJson('/social-post/images', {
    postText,
    niche,
    platform,
    hfToken: settings.hfToken,
    modalTokenId: settings.brandForge.modalTokenId,
    modalTokenSecret: settings.brandForge.modalTokenSecret,
    useModal: Boolean(settings.brandForge.modalProvisionedAt.trim())
  })
}

export function markSocialPublished(
  generationId: number,
  postedUri: string,
  niche: string
): Promise<{ postedUri: string }> {
  return postJson('/social-post/published', { generationId, postedUri, niche })
}

// ---------------------------------------------------------------------------
// Hashtag Suggester (app/routers/hashtags.py)
//
// Shared by both post composers. The Mastodon instance is read from Settings so
// fediverse trend/usage data can be pulled from the server the user actually
// posts to; the Bluesky/X/LinkedIn composer sends '' and the backend falls back
// to a public instance (reported in trendInstance), used as a cross-network proxy.
// ---------------------------------------------------------------------------

export interface HashtagSuggestion {
  tag: string
  score: number
  suitability: number
  trend: 'hot' | 'rising' | 'steady' | 'cooling' | 'unknown'
  reach: 'broad' | 'balanced' | 'niche' | 'unknown'
  volume: number | null
  accounts: number | null
  bucket: 'proven' | 'trending' | 'onTopic'
  sources: string[]
}

export interface HashtagSuggestResponse {
  platform: string
  niche: string
  recommendedCount: number
  trendInstance: string
  suggestions: HashtagSuggestion[]
  sourcesUsed: string[]
  sourcesUnavailable: string[]
  note: string
}

export async function suggestHashtags(
  draft: string,
  niche: string,
  platform: string
): Promise<HashtagSuggestResponse> {
  const settings = await window.api.settings.getAll()
  return postJson('/hashtags/suggest', {
    draft,
    niche,
    platform,
    mastodonInstance: settings.mastodonInstance ?? ''
  })
}

// ---------------------------------------------------------------------------
// Posting time (app/routers/posting_time.py)
//
// The backend returns the measured curve in UTC and does no timezone conversion
// at all — the mapping into the reader's own clock happens in the renderer, where
// `Date` resolves the system zone exactly. That matters for the half-hour zones
// (India, Nepal, Chatham), which a whole-hour offset cannot express, and across a
// DST boundary, which a single offset captured at request time gets wrong for
// half the week it is applied to.
// ---------------------------------------------------------------------------

export interface PostingHour {
  hourUtc: number
  score: number
  lift: number
  volume: number
  volumeShare: number
}

export interface PostingDay {
  /** UTC weekday, Monday == 0 — that is how the statistic was measured. */
  weekday: number
  name: string
  score: number
  lift: number
}

export interface PostingTimeRecommendation {
  platform: string
  /** False when this platform has no curve that reproduces on its own data. */
  available: boolean
  unavailableReason: string | null
  hours: PostingHour[]
  days: PostingDay[]
  baseline: number
  windowHours: number
  effect: Partial<{
    bestScore: number
    worstScore: number
    swingPercentilePoints: number
    volumeEngagementCorrelation: number
    summary: string
  }>
  sample: Partial<{
    scoredPosts: number
    scoredAuthors: number
    corpusPosts: number
    corpusAuthors: number
    windowStart: string
    windowEnd: string
    collectedAt: string
    reliability: number
    source: string
    platform: string
  }>
  caveats: string[]
}

export interface InstanceMeasurement {
  instance: string
  enough: boolean
  scoredPosts: number
  scoredAuthors: number
  reliability: number
  detail: string
}

/**
 * Each platform answers only from its own data — there is no cross-platform
 * fallback. Mastodon additionally answers per instance: there is no "Mastodon"
 * to average, and the user posts to one specific server, so the host has to be
 * part of the question.
 */
export function fetchPostingTime(
  platform: string,
  instance = ''
): Promise<PostingTimeRecommendation> {
  const params = new URLSearchParams({ platform })
  if (instance) params.set('instance', instance)
  return getJson(`/posting-time/recommendation?${params}`)
}

/**
 * Read one instance's last month and decide whether it can support a curve.
 * Slow (tens of seconds) — it is paging that server's public timeline — so
 * callers must show progress. Only ever run when the user asks for it.
 */
export function measureInstance(instance: string): Promise<InstanceMeasurement> {
  return postJson(`/posting-time/measure?instance=${encodeURIComponent(instance)}`, {})
}

// ---------------------------------------------------------------------------
// Mastodon Post Creator (app/routers/mastodon_post.py)
//
// Every call carries the instance, because on the fediverse there is no single
// "Mastodon" to talk to — the server decides the rules, the character limit, and
// whether it will answer at all. The access token is read from Settings inside
// these functions rather than threaded through the components, matching how the
// other credential-bearing tools here work.
// ---------------------------------------------------------------------------

export interface MastodonRule {
  id: string
  text: string
  hint: string
  relevant: boolean
}

export interface MastodonPolicy {
  instance: string
  title: string
  version: string
  maxCharacters: number
  rules: MastodonRule[]
  extendedDescription: string
  policyHash: string
  accepted: boolean
  acceptedAt: string | null
  changedSinceAccepted: boolean
}

export interface MastodonStatus {
  instance: string
  configured: boolean
  missing: string[]
  reachable: boolean
  detail: string
  title: string
  maxCharacters: number
  rulesAccepted: boolean
  niches: number
  posts: number
  exemplars: number
  readyToGround: boolean
  provider: string
  model: string
}

export interface MastodonNiche {
  name: string
  keywords: string[]
  posts: number
  exemplars: number
}

export interface MastodonExemplar {
  id: number
  text: string
  similarity: number
  score: number
  webUrl: string
  author: string
}

export interface MastodonCompliance {
  disclosureApplied: boolean
  disclosureLine: string
  suggestedVisibility: string
  notes: string[]
}

export interface MastodonGenerateResponse {
  text: string
  generationId: number | null
  characters: number
  maxCharacters: number
  overLimit: boolean
  exemplars: MastodonExemplar[]
  compliance: MastodonCompliance
  libraryId: string | null
}

export interface MastodonCollectResponse {
  scanned: number
  stored: number
  skipped: Record<string, number>
  exemplars: number
}

async function mastodonToken(): Promise<string> {
  const settings = await window.api.settings.getAll()
  return settings.mastodonAccessToken ?? ''
}

export interface MastodonPostAnalytics {
  postUri: string
  webUrl: string
  instance: string
  text: string
  publishedAt: string
  likes: number
  reposts: number
  replies: number
  engagementRate: number
  fromApp: boolean
}

export interface MastodonAnalyticsResponse {
  posts: MastodonPostAnalytics[]
  totals: Record<string, number>
  account: string
}

/** POST, not GET: the access token belongs in a body rather than a URL. */
export async function getMastodonAnalytics(
  instance: string,
  limit = 40
): Promise<MastodonAnalyticsResponse> {
  return postJson('/mastodon-post/analytics', {
    instance,
    limit,
    accessToken: await mastodonToken()
  })
}

export function getMastodonStatus(instance: string): Promise<MastodonStatus> {
  return getJson(`/mastodon-post/status?instance=${encodeURIComponent(instance)}`)
}

export function getMastodonPolicy(instance: string): Promise<MastodonPolicy> {
  return getJson(`/mastodon-post/policy?instance=${encodeURIComponent(instance)}`)
}

export function acceptMastodonPolicy(
  instance: string,
  policyHash: string
): Promise<{ accepted: boolean; instance: string; policyHash: string }> {
  return postJson('/mastodon-post/policy/accept', { instance, policyHash })
}

export function revokeMastodonPolicy(instance: string): Promise<{ accepted: boolean }> {
  return deleteJson(`/mastodon-post/policy/accept?instance=${encodeURIComponent(instance)}`)
}

/** Niche counts are per-instance: the same niche can be well grounded on one server and
 *  empty on another, so the instance decides which corpus is being counted. */
export function listMastodonNiches(instance = ''): Promise<MastodonNiche[]> {
  const q = instance.trim() ? `?instance=${encodeURIComponent(instance.trim())}` : ''
  return getJson(`/mastodon-post/niches${q}`)
}

export async function collectMastodonNiche(
  instance: string,
  niche: string,
  limit = 60
): Promise<MastodonCollectResponse> {
  return postJson('/mastodon-post/collect', {
    instance,
    niche,
    limit,
    accessToken: await mastodonToken()
  })
}

export interface MastodonSuggestedAccount {
  account: MastodonAccount
  reason: string
  matched: string[]
  posts: number
  bioMatch: boolean
}

export interface MastodonSuggestedFollows {
  niche: string
  keywords: string[]
  accounts: MastodonSuggestedAccount[]
  note: string
}

export async function getMastodonSuggestedFollows(
  instance: string,
  niche = '',
  query = '',
  limit = 20
): Promise<MastodonSuggestedFollows> {
  return postJson('/mastodon-engage/suggested-follows', {
    instance,
    niche,
    query,
    limit,
    accessToken: await mastodonToken()
  })
}

export async function generateMastodonPost(
  instance: string,
  niche: string,
  userInput: string,
  sourceUrl = '',
  discloseAi = true,
  brandVoiceId = '',
  // Posts already written for this request, sent on a rewrite so the model is told what
  // not to repeat. Same reason as the Bluesky composer: an identical prompt reproduces
  // its own opening line however high the temperature.
  avoidTexts: string[] = []
): Promise<MastodonGenerateResponse> {
  return postJson('/mastodon-post/generate', {
    instance,
    niche,
    userInput,
    sourceUrl,
    discloseAi,
    brandVoiceId,
    avoidTexts,
    accessToken: await mastodonToken()
  })
}

export async function markMastodonPublished(
  instance: string,
  generationId: number,
  postedUrl: string,
  niche: string
): Promise<{ postedUri: string; webUrl: string }> {
  return postJson('/mastodon-post/published', {
    instance,
    generationId,
    postedUrl,
    niche,
    accessToken: await mastodonToken()
  })
}

export async function verifyMastodonToken(instance: string): Promise<{
  acct: string
  displayName: string
  url: string
  followers: number
  botFlagSet: boolean
  discoverable: boolean | null
}> {
  return postJson('/mastodon-post/verify', { instance, accessToken: await mastodonToken() })
}

// ---------------------------------------------------------------------------
// Engage, Mastodon side (app/routers/mastodon_engage.py)
//
// Every call is a POST, including the reads, because the access token travels in
// the body — the backend is localhost-only but a credential in a query string
// still lands in access logs. Instance and token are read from Settings in here
// rather than threaded through the components, same as the Post Creator above.
// ---------------------------------------------------------------------------

export type MastodonFeedName =
  | 'home'
  | 'notifications'
  | 'local'
  | 'public'
  | 'tag'
  | 'bookmarks'
  | 'favourites'

export type MastodonStatusAction =
  | 'favourite'
  | 'unfavourite'
  | 'reblog'
  | 'unreblog'
  | 'bookmark'
  | 'unbookmark'
  | 'mute'
  | 'unmute'
  | 'pin'
  | 'unpin'

export type MastodonAccountAction = 'follow' | 'unfollow' | 'mute' | 'unmute' | 'block' | 'unblock'

export interface MastodonAccount {
  id: string
  acct: string
  displayName: string
  url: string
  avatar: string
  bot: boolean
  followers: number
  note: string
}

export interface MastodonRelationship {
  accountId: string
  following: boolean
  followedBy: boolean
  requested: boolean
  muting: boolean
  blocking: boolean
  blockedBy: boolean
}

export interface MastodonMedia {
  type: string
  url: string
  previewUrl: string
  description: string
}

export interface MastodonFeedPost {
  id: string
  uri: string
  url: string
  createdAt: string
  text: string
  spoilerText: string
  sensitive: boolean
  visibility: string
  language: string
  account: MastodonAccount
  media: MastodonMedia[]
  hashtags: string[]
  favourites: number
  reblogs: number
  replies: number
  favourited: boolean
  reblogged: boolean
  bookmarked: boolean
  muted: boolean
  pinned: boolean
  inReplyToId: string | null
  isOwn: boolean
  boostedBy: string | null
  embedUrl: string
  relationship: MastodonRelationship | null
  reason: string | null
  notificationId: string | null
  isRead: boolean | null
}

export interface MastodonFeedResponse {
  feed: string
  posts: MastodonFeedPost[]
  nextMaxId: string
  tagFollowing: boolean | null
  lastReadId: string
}

export interface MastodonSession {
  instance: string
  configured: boolean
  hasToken: boolean
  reachable: boolean
  detail: string
  title: string
  version: string
  maxCharacters: number
  maxMedia: number
  visibilities: string[]
  rulesAccepted: boolean
  rulesChanged: boolean
  account: MastodonAccount | null
  embedUrl: string
}

export interface MastodonTermRule {
  id: string
  text: string
  hint: string
}

export interface MastodonTermTopic {
  topic: string
  rules: MastodonTermRule[]
}

export interface MastodonTermLimit {
  label: string
  value: string
}

export interface MastodonTerms {
  instance: string
  title: string
  version: string
  description: string
  thumbnail: string
  contactEmail: string
  aboutUrl: string
  policyHash: string
  accepted: boolean
  acceptedAt: string | null
  changedSinceAccepted: boolean
  ruleCount: number
  topics: MastodonTermTopic[]
  limits: MastodonTermLimit[]
  requires: string[]
  extendedDescription: string
}

export interface MastodonActionResult {
  ok: boolean
  post: MastodonFeedPost | null
  relationship: MastodonRelationship | null
  tagFollowing: boolean | null
}

export interface MastodonThread {
  ancestors: MastodonFeedPost[]
  status: MastodonFeedPost
  descendants: MastodonFeedPost[]
}

export interface MastodonSearchResult {
  accounts: MastodonAccount[]
  statuses: MastodonFeedPost[]
  hashtags: string[]
}

/** Instance + token together — every Engage call needs both halves. */
async function mastodonAuth(): Promise<{ instance: string; accessToken: string }> {
  const settings = await window.api.settings.getAll()
  return {
    instance: settings.mastodonInstance ?? '',
    accessToken: settings.mastodonAccessToken ?? ''
  }
}

export async function getMastodonSession(): Promise<MastodonSession> {
  return postJson('/mastodon-engage/session', await mastodonAuth())
}

export async function getMastodonTerms(): Promise<MastodonTerms> {
  return postJson('/mastodon-engage/terms', await mastodonAuth())
}

export async function getMastodonFeed(
  feed: MastodonFeedName,
  opts: { tag?: string; limit?: number; maxId?: string } = {}
): Promise<MastodonFeedResponse> {
  const body = { ...(await mastodonAuth()), feed, tag: opts.tag ?? '', limit: opts.limit ?? 30, maxId: opts.maxId ?? '' }
  return postJson(feed === 'notifications' ? '/mastodon-engage/notifications' : '/mastodon-engage/timeline', body)
}

export async function getMastodonThread(statusId: string): Promise<MastodonThread> {
  return postJson('/mastodon-engage/thread', { ...(await mastodonAuth()), statusId })
}

export async function composeMastodonStatus(post: {
  text: string
  visibility: string
  spoilerText?: string
  language?: string
  inReplyToId?: string
  /** Stable across retries of the same draft so a double-submit can't post twice. */
  idempotencyKey: string
}): Promise<MastodonActionResult> {
  return postJson('/mastodon-engage/compose', {
    ...(await mastodonAuth()),
    text: post.text,
    visibility: post.visibility,
    spoilerText: post.spoilerText ?? '',
    language: post.language ?? '',
    inReplyToId: post.inReplyToId ?? '',
    idempotencyKey: post.idempotencyKey
  })
}

export async function mastodonStatusAction(
  statusId: string,
  action: MastodonStatusAction,
  visibility = ''
): Promise<MastodonActionResult> {
  return postJson('/mastodon-engage/status-action', {
    ...(await mastodonAuth()),
    statusId,
    action,
    visibility
  })
}

export async function mastodonAccountAction(
  accountId: string,
  action: MastodonAccountAction
): Promise<MastodonActionResult> {
  return postJson('/mastodon-engage/account-action', { ...(await mastodonAuth()), accountId, action })
}

export async function mastodonTagAction(
  tag: string,
  action: 'follow' | 'unfollow'
): Promise<MastodonActionResult> {
  return postJson('/mastodon-engage/tag-action', { ...(await mastodonAuth()), tag, action })
}

export async function deleteMastodonStatus(statusId: string): Promise<MastodonActionResult> {
  return postJson('/mastodon-engage/delete-status', { ...(await mastodonAuth()), statusId })
}

export async function markMastodonNotificationsRead(lastReadId: string): Promise<MastodonActionResult> {
  return postJson('/mastodon-engage/notifications/read', { ...(await mastodonAuth()), lastReadId })
}

export async function searchMastodon(query: string, limit = 10): Promise<MastodonSearchResult> {
  return postJson('/mastodon-engage/search', { ...(await mastodonAuth()), query, limit })
}

// ---------------------------------------------------------------------------
// Engage, Tumblr side (app/routers/tumblr_engage.py)
//
// Every call is a POST, including the reads, for the same reason the Mastodon
// block above gives: the credentials travel in the body, never in a URL. Tumblr's
// are four OAuth 1.0a values rather than one token, and like the other two
// networks they are read from Settings in here rather than threaded through the
// components.
// ---------------------------------------------------------------------------

export type TumblrFeedName = 'dashboard' | 'notifications' | 'likes'

/** What a post may be created as. "queue" and "draft" are load-bearing on Tumblr. */
export type TumblrPostState = 'published' | 'queue' | 'draft' | 'private'

export interface TumblrPost {
  id: string
  reblogKey: string
  blogName: string
  blogTitle: string
  blogUrl: string
  avatar: string
  postUrl: string
  createdAt: string
  text: string
  tags: string[]
  noteCount: number
  liked: boolean
  isOwn: boolean
  /** False for an activity row carrying no post that can be acted on. */
  isPost: boolean
  isReblog: boolean
  rebloggedFrom: string
  muted: boolean
  state: string
  following: boolean
  blocked: boolean
  media: PostMediaItem[]
  reason: string | null
  reasonText: string
  isRead: boolean | null
}

export interface TumblrFeedResponse {
  feed: TumblrFeedName
  posts: TumblrPost[]
  /** Dashboard and likes page by offset; the activity feed pages by timestamp. */
  nextOffset: number
  nextBefore: number
  note: string
}

export interface TumblrBlogSummary {
  name: string
  title: string
  url: string
  primary: boolean
  followers: number
}

export interface TumblrSession {
  configured: boolean
  reachable: boolean
  detail: string
  userName: string
  blog: string
  blogTitle: string
  blogUrl: string
  avatar: string
  following: number
  likes: number
  blogs: TumblrBlogSummary[]
}

export interface TumblrBlogState {
  blogName: string
  following: boolean
  blocked: boolean
}

export interface TumblrActionResult {
  ok: boolean
  post: TumblrPost | null
  blog: TumblrBlogState | null
  createdId: string
}

export interface TumblrNote {
  type: string
  blogName: string
  blogUrl: string
  avatar: string
  createdAt: string
  text: string
  tags: string[]
  postId: string
}

export interface TumblrNotes {
  notes: TumblrNote[]
  totalNotes: number
  totalLikes: number
  totalReblogs: number
  note: string
}

export interface TumblrSuggestedBlog {
  name: string
  title: string
  url: string
  avatar: string
  description: string
  posts: number
  reason: string
  matched: string[]
  bioMatch: boolean
}

export interface TumblrSuggestedFollows {
  niche: string
  keywords: string[]
  blogs: TumblrSuggestedBlog[]
  note: string
}

/** The four OAuth values plus the blog to act as — every Tumblr call needs them. */
async function tumblrAuth(): Promise<{
  consumerKey: string
  consumerSecret: string
  oauthToken: string
  oauthTokenSecret: string
  blog: string
}> {
  const { tumblr } = await window.api.settings.getAll()
  return {
    consumerKey: tumblr?.consumerKey ?? '',
    consumerSecret: tumblr?.consumerSecret ?? '',
    oauthToken: tumblr?.oauthToken ?? '',
    oauthTokenSecret: tumblr?.oauthTokenSecret ?? '',
    blog: tumblr?.blog ?? ''
  }
}

export async function getTumblrSession(): Promise<TumblrSession> {
  return postJson('/tumblr-engage/session', await tumblrAuth())
}

export async function getTumblrFeed(
  feed: TumblrFeedName,
  opts: { limit?: number; offset?: number; before?: number } = {}
): Promise<TumblrFeedResponse> {
  return postJson('/tumblr-engage/feed', {
    ...(await tumblrAuth()),
    feed,
    limit: opts.limit ?? 20,
    offset: opts.offset ?? 0,
    before: opts.before ?? 0
  })
}

export async function getTumblrNotes(
  blogName: string,
  postId: string,
  mode = 'conversation'
): Promise<TumblrNotes> {
  return postJson('/tumblr-engage/notes', { ...(await tumblrAuth()), blogName, postId, mode })
}

export async function composeTumblrPost(opts: {
  text: string
  title?: string
  tags?: string
  state?: TumblrPostState
}): Promise<TumblrActionResult> {
  return postJson('/tumblr-engage/compose', {
    ...(await tumblrAuth()),
    text: opts.text,
    title: opts.title ?? '',
    tags: opts.tags ?? '',
    state: opts.state ?? 'published'
  })
}

/** Reblog a post, with or without commentary — Tumblr's repost and quote in one. */
export async function reblogTumblrPost(
  post: TumblrPost,
  opts: { comment?: string; tags?: string; state?: TumblrPostState } = {}
): Promise<TumblrActionResult> {
  return postJson('/tumblr-engage/reblog', {
    ...(await tumblrAuth()),
    blogName: post.blogName,
    postId: post.id,
    reblogKey: post.reblogKey,
    comment: opts.comment ?? '',
    tags: opts.tags ?? '',
    state: opts.state ?? 'published'
  })
}

export async function toggleTumblrLike(post: TumblrPost): Promise<TumblrActionResult> {
  return postJson('/tumblr-engage/like', {
    ...(await tumblrAuth()),
    blogName: post.blogName,
    postId: post.id,
    reblogKey: post.reblogKey,
    enabled: !post.liked
  })
}

export async function toggleTumblrFollow(blogName: string, enabled: boolean): Promise<TumblrActionResult> {
  return postJson('/tumblr-engage/follow', { ...(await tumblrAuth()), blogName, enabled })
}

export async function toggleTumblrBlock(blogName: string, enabled: boolean): Promise<TumblrActionResult> {
  return postJson('/tumblr-engage/block', { ...(await tumblrAuth()), blogName, enabled })
}

/** Mute or unmute activity about one of your own posts. */
export async function toggleTumblrMute(post: TumblrPost): Promise<TumblrActionResult> {
  return postJson('/tumblr-engage/mute', {
    ...(await tumblrAuth()),
    blogName: post.blogName,
    postId: post.id,
    enabled: !post.muted
  })
}

export async function deleteTumblrPost(post: TumblrPost): Promise<TumblrActionResult> {
  return postJson('/tumblr-engage/delete-post', {
    ...(await tumblrAuth()),
    blogName: post.blogName,
    postId: post.id
  })
}

export async function getTumblrSuggestedFollows(
  query = '',
  limit = 30
): Promise<TumblrSuggestedFollows> {
  return postJson('/tumblr-engage/suggested-follows', {
    ...(await tumblrAuth()),
    niche: '',
    query,
    limit
  })
}

// --- settings ---------------------------------------------------------------

export interface EnvSetting {
  name: string
  group: string
  help: string
  isSecret: boolean
  choices: string[]
  value: string // masked for secrets — never the real credential
  isSet: boolean
}

export function getSocialPostSchema(): Promise<EnvSetting[]> {
  return getJson('/settings/social-post/schema')
}

export function pushSocialPostEnv(values: Record<string, string>): Promise<{ changed: string[] }> {
  return postJson('/settings/social-post/env', { values })
}

export function verifySocialPost(target: 'bluesky' | 'llm' | 'hf'): Promise<{
  valid: boolean
  detail: string
}> {
  return postJson(`/settings/social-post/verify/${target}`, {})
}

// ---------------------------------------------------------------------------
// Engage — the user's own Bluesky feeds (timeline, notifications)
// ---------------------------------------------------------------------------

export interface EngageStatus {
  configured: boolean
  handle: string | null
}

/**
 * One piece of media on a post. The Bluesky and Mastodon routers deliberately
 * emit the same shape, so `PostMedia` renders either without an adapter.
 * `isHls` is the one network-specific flag — Bluesky video arrives as an HLS
 * playlist, Mastodon's as a plain MP4.
 */
export interface PostMediaItem {
  kind: 'image' | 'video' | 'gifv' | 'audio' | 'link' | 'unknown'
  url: string
  previewUrl: string
  description: string
  isHls?: boolean
  aspectRatio?: number | null
  title?: string
  domain?: string
}

export interface FeedPost {
  uri: string
  cid: string
  webUrl: string
  isPost: boolean
  isOwnPost: boolean
  authorDid: string
  authorHandle: string
  authorName: string
  authorAvatar: string | null
  text: string
  createdAt: string
  likes: number
  reposts: number
  replies: number
  quotes: number
  bookmarks: number
  reason: string | null
  reasonSubject: string | null
  isRead: boolean | null
  viewerLike: string | null
  viewerRepost: string | null
  viewerBookmarked: boolean
  viewerThreadMuted: boolean
  viewerReplyDisabled: boolean
  authorFollowing: string | null
  authorFollowedBy: string | null
  authorMuted: boolean
  authorBlocking: string | null
  authorBlockedBy: boolean
  media: PostMediaItem[]
}

export interface FeedResponse {
  posts: FeedPost[]
}

export interface EngageActorState {
  authorDid: string
  authorFollowing: string | null
  authorFollowedBy: string | null
  authorMuted: boolean
  authorBlocking: string | null
  authorBlockedBy: boolean
}

export interface EngageActionResponse {
  ok: boolean
  post: FeedPost | null
  actor: EngageActorState | null
  createdUri: string | null
  createdCid: string | null
}

export interface SuggestedAccount {
  did: string
  handle: string
  displayName: string
  description: string
  avatar: string | null
  followers: number
  reason: string
  matched: string[]
  posts: number
  bioMatch: boolean
}

export interface SuggestedFollowsResponse {
  niche: string
  keywords: string[]
  accounts: SuggestedAccount[]
  note: string
}

/** `query` is a subject typed by the user; when set it replaces the saved niche entirely. */
export function getSuggestedFollows(
  niche = '',
  query = '',
  limit = 20
): Promise<SuggestedFollowsResponse> {
  const q = new URLSearchParams({ niche, query, limit: String(limit) })
  return getJson(`/engage/suggested-follows?${q}`)
}

export function getEngageStatus(): Promise<EngageStatus> {
  return getJson('/engage/status')
}

export function getEngageTimeline(limit = 30): Promise<FeedResponse> {
  return getJson(`/engage/timeline?limit=${limit}`)
}

export function getEngageNotifications(limit = 30): Promise<FeedResponse> {
  return getJson(`/engage/notifications?limit=${limit}`)
}

export function createEngagePost(text: string): Promise<EngageActionResponse> {
  return postJson('/engage/post', { text })
}

export function replyEngagePost(uri: string, cid: string, text: string): Promise<EngageActionResponse> {
  return postJson('/engage/reply', { uri, cid, text })
}

export function quoteEngagePost(uri: string, cid: string, text: string): Promise<EngageActionResponse> {
  return postJson('/engage/quote', { uri, cid, text })
}

export function toggleEngageLike(post: FeedPost): Promise<EngageActionResponse> {
  return postJson('/engage/like', { uri: post.uri, cid: post.cid, enabled: !post.viewerLike, recordUri: post.viewerLike })
}

export function toggleEngageRepost(post: FeedPost): Promise<EngageActionResponse> {
  return postJson('/engage/repost', {
    uri: post.uri,
    cid: post.cid,
    enabled: !post.viewerRepost,
    recordUri: post.viewerRepost
  })
}

export function toggleEngageBookmark(post: FeedPost): Promise<EngageActionResponse> {
  return postJson('/engage/bookmark', { uri: post.uri, cid: post.cid, enabled: !post.viewerBookmarked })
}

export function toggleEngageThreadMute(post: FeedPost): Promise<EngageActionResponse> {
  return postJson('/engage/thread-mute', { uri: post.uri, cid: post.cid, enabled: !post.viewerThreadMuted })
}

/** Follow by DID, for suggestions — the toggle below needs a whole post to act on. */
export function followEngageActor(did: string): Promise<EngageActionResponse> {
  return postJson('/engage/follow', { did, enabled: true })
}

export function toggleEngageFollow(post: FeedPost): Promise<EngageActionResponse> {
  return postJson('/engage/follow', {
    did: post.authorDid,
    enabled: !post.authorFollowing,
    recordUri: post.authorFollowing
  })
}

export function toggleEngageMute(post: FeedPost): Promise<EngageActionResponse> {
  return postJson('/engage/mute', { did: post.authorDid, enabled: !post.authorMuted })
}

export function toggleEngageBlock(post: FeedPost): Promise<EngageActionResponse> {
  return postJson('/engage/block', {
    did: post.authorDid,
    enabled: !post.authorBlocking,
    recordUri: post.authorBlocking
  })
}

export function deleteEngagePost(post: FeedPost): Promise<EngageActionResponse> {
  return postJson('/engage/delete-post', { uri: post.uri, cid: post.cid })
}

export function markEngageNotificationsRead(): Promise<EngageActionResponse> {
  return postJson('/engage/notifications/read', {})
}

// ---------------------------------------------------------------------------
// Mail — direct, one-off outbound email over the user's own SMTP account
// ---------------------------------------------------------------------------

export interface MailStatus {
  configured: boolean
  host: string
  port: number
  username: string
  password: string // masked placeholder or "" — never the real credential
  fromName: string
  fromEmail: string
  useTls: boolean
  imapHost: string // blank = bounce detection off
  imapPort: number
}

export interface MailSettingsInput {
  host?: string
  port?: number
  username?: string
  password?: string
  fromName?: string
  fromEmail?: string
  useTls?: boolean
  imapHost?: string
  imapPort?: number
}

export function getMailStatus(): Promise<MailStatus> {
  return getJson('/mail/status')
}

export function saveMailSettings(values: MailSettingsInput): Promise<MailStatus> {
  return postJson('/mail/settings', values)
}

export function verifyMail(): Promise<{ valid: boolean; detail: string }> {
  return postJson('/mail/verify', {})
}

export function verifyMailImap(): Promise<{ valid: boolean; detail: string }> {
  return postJson('/mail/verify-imap', {})
}

export function sendMail(
  to: string[],
  subject: string,
  body: string,
  cc?: string[]
): Promise<{ sent: boolean }> {
  return postJson('/mail/send', { to, subject, body, cc })
}

// ---------------------------------------------------------------------------
// Mail tracking — opens/clicks/bounces for both SMTP send paths (Mail Composer
// above, and the Lead Gen Agent's outreach), read by the Analytics Email tab
// ---------------------------------------------------------------------------

export interface MailMessage {
  id: string
  source: 'composer' | 'leadgen'
  message_id: string | null
  to_addrs: string // JSON-encoded string array — parse client-side
  cc_addrs: string | null
  subject: string
  leadgen_deal_id: string | null
  status: string
  error: string | null
  created_at: string
  sent_at: string | null
  opens: number
  clicks: number
  bounces: number
}

export interface MailTrackingStats {
  sent: number
  opened: number
  clicked: number
  bounced: number
  openRate: number
  clickRate: number
  bounceRate: number
}

export function listMailMessages(source?: string, limit = 100): Promise<MailMessage[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (source) params.set('source', source)
  return getJson(`/mail-tracking/messages?${params.toString()}`)
}

export function getMailTrackingStats(source?: string): Promise<MailTrackingStats> {
  const params = source ? `?source=${source}` : ''
  return getJson(`/mail-tracking/stats${params}`)
}

export function syncMailTracking(): Promise<{ synced: number; ok: boolean }> {
  return postJson('/mail-tracking/sync', {})
}

// ---------------------------------------------------------------------------
// Bluesky analytics — selected/discovered comparable-account cohorts
// ---------------------------------------------------------------------------

export interface BlueskyAnalyticsStatus {
  configured: boolean
  handle: string | null
  cohortCount: number
  trackedPosts: number
  lastSyncedAt: string | null
}

export interface BlueskyAnalyticsAccount {
  did: string
  handle: string
  displayName: string
  followers: number
  niche: string
  source: string
  isOwner: boolean
}

export interface BlueskyAnalyticsCohort {
  accounts: BlueskyAnalyticsAccount[]
  owner: BlueskyAnalyticsAccount | null
}

export interface BlueskyAnalyticsDiscovery {
  did: string
  handle: string
  displayName: string
  followers: number
  niche: string
  matchedPosts: number
  sampleText: string
  samplePostUri: string
}

export interface BlueskyAnalyticsSummary {
  minePosts: number
  cohortPosts: number
  cohortAccounts: number
  mineMedianRate: number
  cohortMedianRate: number
  mineMedianEngagement: number
  cohortMedianEngagement: number
  lastSyncedAt: string | null
  niche: string
  followerMin: number
  followerMax: number
}

export interface BlueskyAnalyticsPost {
  uri: string
  webUrl: string
  handle: string
  displayName: string
  text: string
  createdAt: string
  capturedAt: string
  snapshotAgeHours: number
  comparisonWindow: string
  followers: number
  likes: number
  reposts: number
  replies: number
  quotes: number
  engagement: number
  engagementRate: number
  ageHours: number
  hasMedia: boolean
  isOwn: boolean
  niche: string
  benchmarkMedianRate: number
  percentile: number
}

export interface BlueskyAnalyticsDashboard {
  summary: BlueskyAnalyticsSummary
  posts: BlueskyAnalyticsPost[]
}

export function getBlueskyAnalyticsStatus(): Promise<BlueskyAnalyticsStatus> {
  return getJson('/bluesky-analytics/status')
}

export function getBlueskyAnalyticsCohort(): Promise<BlueskyAnalyticsCohort> {
  return getJson('/bluesky-analytics/cohort')
}

export function discoverBlueskyAnalyticsAccounts(
  niche: string,
  followerMin: number,
  followerMax: number,
  limit = 20
): Promise<{ accounts: BlueskyAnalyticsDiscovery[] }> {
  return postJson('/bluesky-analytics/discover', { niche, followerMin, followerMax, limit })
}

export function addBlueskyAnalyticsAccount(
  actor: string,
  niche: string,
  source: 'selected' | 'discovered' = 'selected'
): Promise<BlueskyAnalyticsAccount> {
  return postJson('/bluesky-analytics/cohort/accounts', { actor, niche, source })
}

export function removeBlueskyAnalyticsAccount(did: string): Promise<{ ok: boolean }> {
  return deleteJson(`/bluesky-analytics/cohort/accounts/${encodeURIComponent(did)}`)
}

export function syncBlueskyAnalytics(
  niche: string
): Promise<{ ok: boolean; accounts: number; posts: number; snapshots: number; syncedAt: string }> {
  return postJson('/bluesky-analytics/sync', { niche })
}

export function getBlueskyAnalyticsDashboard(
  niche: string,
  followerMin: number,
  followerMax: number
): Promise<BlueskyAnalyticsDashboard> {
  const params = new URLSearchParams({ niche, followerMin: String(followerMin), followerMax: String(followerMax), limit: '60' })
  return getJson(`/bluesky-analytics/dashboard?${params.toString()}`)
}

// ---------------------------------------------------------------------------
// BrandForge — full Brand Document from a structured intake, generated by the
// fine-tuned Qwen3-8B on the user's dedicated HF Inference Endpoint. Images use
// HF text-to-image. Both bill to the connected HF token.
// ---------------------------------------------------------------------------

export interface BrandArchetype {
  id: string
  name: string
  description: string
}

export interface BrandPersonalitySlider {
  key: string
  left: string
  right: string
}

export interface BrandPhase {
  phase: string
  sections: string[]
}

export interface BrandIntakeInput {
  brand_archetype: string
  secondary_archetype: string | null
  brand_category: string
  brand_name: string
  one_liner: string
  founding_story: string
  primary_audience: string
  secondary_audience: string
  geography: string
  business_model: string
  competitors: string[]
  differentiation_hypothesis: string
  admired_brands: string
  never_sound_like: string
  existing_assets: string
  top_12mo_goal: string
  personality: Record<string, number>
  channels: string[]
}

export interface BrandMeta {
  archetypes: BrandArchetype[]
  categories: string[]
  channels: string[]
  businessModels: string[]
  personalitySliders: BrandPersonalitySlider[]
  phases: BrandPhase[]
  sectionNames: string[]
  demo: BrandIntakeInput
}

export interface BrandSection {
  name: string
  phase: string
  content: string
}

export interface BrandSwatch {
  hex: string
  name: string
  rationale: string
}

export interface AssembleBrandResponse {
  markdown: string
  voiceCard: string
  palette: BrandSwatch[]
  docxPath: string | null
  docxUrl: string | null
  libraryId: string
}

export interface BrandImage {
  assetType: string
  url: string | null
  promptUsed: string
  error: string | null
}

export interface BrandImagesResponse {
  images: BrandImage[]
  palette: BrandSwatch[]
}

export function getBrandForgeMeta(): Promise<BrandMeta> {
  return getJson('/brand-forge/meta')
}

// One section at a time — the frontend loops over meta.sectionNames so progress
// streams in and each CPU-Space request stays short. Also used for single-section
// regeneration.
export async function generateBrandSection(intake: BrandIntakeInput, sectionName: string): Promise<BrandSection> {
  const settings = await window.api.settings.getAll()
  return postJson('/brand-forge/section', {
    intake,
    sectionName,
    spaceId: settings.brandForge.spaceId,
    hfToken: settings.hfToken,
    // Sent every time; the backend only switches to the user's own GPU when
    // both halves are present, so an empty pair keeps the hosted Space path.
    modalTokenId: settings.brandForge.modalTokenId,
    modalTokenSecret: settings.brandForge.modalTokenSecret
  })
}

export interface ModalProvisionStatus {
  status: 'idle' | 'running' | 'ready' | 'error'
  message: string
  elapsedSeconds: number
  hint: string
  appPageUrl: string
  logsUrl: string
}

/** Deploy the GPU backend into the user's own Modal workspace. Returns as soon
 * as the deploy starts — poll getModalStatus() for progress.
 *
 * Takes the credentials explicitly rather than reading the saved settings, so
 * setup works on what the user just typed without a Save first — same rule the
 * "Test connection" buttons follow. */
export function provisionModalBackend(
  modalTokenId: string,
  modalTokenSecret: string,
  hfToken: string
): Promise<ModalProvisionStatus> {
  return postJson('/brand-forge/modal/provision', { modalTokenId, modalTokenSecret, hfToken })
}

export function getModalStatus(): Promise<ModalProvisionStatus> {
  return getJson('/brand-forge/modal/status')
}

export function assembleBrandDocument(
  intake: BrandIntakeInput,
  sections: Record<string, string>
): Promise<AssembleBrandResponse> {
  return postJson('/brand-forge/assemble', { intake, sections })
}

export async function generateBrandImages(intake: BrandIntakeInput, visualBrief: string): Promise<BrandImagesResponse> {
  const settings = await window.api.settings.getAll()
  return postJson('/brand-forge/images', {
    intake,
    visualBrief,
    hfToken: settings.hfToken,
    modalTokenId: settings.brandForge.modalTokenId,
    modalTokenSecret: settings.brandForge.modalTokenSecret,
    useModal: Boolean(settings.brandForge.modalProvisionedAt.trim())
  })
}

// ---------------------------------------------------------------------------
// Lead Gen Agent — an OpenOutreach-style AI sales agent. Operated from
// Research/Strategy; its CRM is browsed in Analytics. Discovery (Overpass +
// self-hosted SearXNG) and email verification (self-hosted Reacher) are free;
// reasoning bills to the HF token; outreach copy is written by the Email Writer.
// ---------------------------------------------------------------------------

export interface LeadgenStatus {
  llmBackend: string
  llmModel: string
  llmConfigured: boolean
  smtpConfigured: boolean
  imapConfigured: boolean
  discoveryBackends: string[]
  reacherUrl: string
  searxngUrl: string
  campaigns: number
  activeCampaigns: number
}

export interface LeadgenCampaign {
  id: string
  name: string
  productDescription: string
  objective: string
  country: string
  active: boolean
  autoSend: boolean
  useBluesky: boolean
  dailyCap: number
  activeHoursStart: number
  activeHoursEnd: number
  createdAt: string
}

export interface LeadgenDeal {
  id: string
  campaign_id: string
  lead_id: string
  state: string
  outcome: string | null
  reason: string | null
  next_follow_up_at: string | null
  created_at: string
  updated_at: string
  company: string
  contact_name: string | null
  email: string | null
  domain: string | null
  region: string | null
  profile_text: string
}

export interface LeadgenChatMessage {
  id: string
  deal_id: string
  direction: 'out' | 'in'
  subject: string
  body: string
  created_at: string
}

export interface LeadgenDealDetail {
  deal: LeadgenDeal
  lead: Record<string, unknown> | null
  thread: LeadgenChatMessage[]
}

export interface LeadgenDraft {
  id: string
  deal_id: string
  kind: 'opener' | 'follow_up'
  subject: string
  body: string
  predicted_click_rate: number | null
  ctr_bucket: string | null
  status: string
  created_at: string
  company: string
  email: string | null
  profile_text: string
}

export interface LeadgenSuppression {
  email: string
  reason: string
  added_at: string
}

export interface CampaignInput {
  name: string
  productDescription: string
  objective?: string
  country?: string
  dailyCap?: number
  autoSend?: boolean
  useBluesky?: boolean
}

export interface CampaignPatchInput {
  active?: boolean
  autoSend?: boolean
  useBluesky?: boolean
  dailyCap?: number
  activeHoursStart?: number
  activeHoursEnd?: number
}

export function getLeadgenStatus(): Promise<LeadgenStatus> {
  return getJson('/leadgen/status')
}

export function listLeadgenCampaigns(): Promise<LeadgenCampaign[]> {
  return getJson('/leadgen/campaigns')
}

export function createLeadgenCampaign(input: CampaignInput): Promise<LeadgenCampaign> {
  return postJson('/leadgen/campaigns', input)
}

export function patchLeadgenCampaign(id: string, patch: CampaignPatchInput): Promise<LeadgenCampaign> {
  return patchJson(`/leadgen/campaigns/${id}`, patch)
}

export function deleteLeadgenCampaign(id: string): Promise<{ deleted: boolean }> {
  return deleteJson(`/leadgen/campaigns/${id}`)
}

export function runLeadgenCampaignOnce(
  id: string,
  maxSteps = 10
): Promise<{ steps: number; states: Record<string, number>; diagnostic: string | null }> {
  return postJson(`/leadgen/campaigns/${id}/run-once?maxSteps=${maxSteps}`, {})
}

export function getLeadgenStats(id: string): Promise<{ states: Record<string, number>; sentToday: number }> {
  return getJson(`/leadgen/campaigns/${id}/stats`)
}

export function listLeadgenDeals(campaignId: string): Promise<LeadgenDeal[]> {
  return getJson(`/leadgen/campaigns/${campaignId}/deals`)
}

export function getLeadgenDeal(dealId: string): Promise<LeadgenDealDetail> {
  return getJson(`/leadgen/deals/${dealId}`)
}

export function listLeadgenDrafts(campaignId: string): Promise<LeadgenDraft[]> {
  return getJson(`/leadgen/campaigns/${campaignId}/drafts`)
}

export function editLeadgenDraft(draftId: string, patch: { subject?: string; body?: string }): Promise<LeadgenDraft> {
  return patchJson(`/leadgen/drafts/${draftId}`, patch)
}

export function approveSendLeadgenDraft(draftId: string): Promise<{ sent: boolean; detail: string }> {
  return postJson(`/leadgen/drafts/${draftId}/approve-send`, {})
}

export function discardLeadgenDraft(draftId: string): Promise<{ discarded: boolean }> {
  return postJson(`/leadgen/drafts/${draftId}/discard`, {})
}

export function getLeadgenSuppression(): Promise<LeadgenSuppression[]> {
  return getJson('/leadgen/suppression')
}

export function addLeadgenSuppression(email: string, reason = 'manual'): Promise<{ added: string }> {
  return postJson('/leadgen/suppression', { email, reason })
}

export function getLeadgenSchema(): Promise<EnvSetting[]> {
  return getJson('/settings/leadgen/schema')
}

export function pushLeadgenEnv(values: Record<string, string>): Promise<{ changed: string[] }> {
  return postJson('/settings/leadgen/env', { values })
}

export function verifyLeadgen(
  target: 'hf' | 'llm' | 'smtp' | 'imap' | 'reacher' | 'searxng'
): Promise<{ valid: boolean; detail: string }> {
  return postJson(`/settings/leadgen/verify/${target}`, {})
}

// ---------------------------------------------------------------------------
// Topic Scout — evidence-led topic discovery with a sentiment read
// ---------------------------------------------------------------------------

export interface TopicScoutOptions {
  groups: Record<string, string[]>
  sources: string[]
  signalSources: string[]
  defaultSources: string[]
  defaultSignalSources: string[]
  sentimentModel: string
}

export interface TopicEvidence {
  title: string
  url: string
  source: string
  family: string
  published: string
  sentimentLabel: string
  sentimentScore: number
}

export interface TopicMeasurement {
  source: string
  family: string
  current: number
  baseline: number
  unit: string
  score: number | null
  changePct: number | null
  note: string
  url: string
  contextOnly: boolean
}

export interface TopicSentiment {
  label: string
  positive: number
  negative: number
  neutral: number
  polarity: number
  analyzed: number
  engine: string
}

export interface ScoutedTopic {
  label: string
  query: string
  score: number
  discoveryScore: number
  tier: string
  confidence: number
  angle: string
  familyScores: Record<string, number>
  measuredFamilies: number
  sentiment: TopicSentiment
  evidence: TopicEvidence[]
  measurements: TopicMeasurement[]
}

export interface TopicScoutResponse {
  topics: ScoutedTopic[]
  familyLabels: Record<string, string>
  sourceHealth: string[]
  sentimentNote: string
  libraryId: string | null
}

export interface TopicScoutInput {
  niche: string
  group: string
  subNiche: string
  days: number
  maxTopics: number
  sources: string[]
  signalSources: string[]
}

export function getTopicScoutOptions(): Promise<TopicScoutOptions> {
  return getJson('/topic-scout/options')
}

export async function discoverTopics(input: TopicScoutInput): Promise<TopicScoutResponse> {
  const settings = await window.api.settings.getAll()
  return postJson('/topic-scout/discover', {
    ...input,
    hfToken: settings.hfToken,
    contactEmail: settings.topicScout.contactEmail,
    githubToken: settings.topicScout.githubToken,
    reliefwebAppname: settings.topicScout.reliefwebAppname,
    fredApiKey: settings.topicScout.fredApiKey,
    twitterAuthToken: settings.topicScout.twitterAuthToken,
    twitterCt0: settings.topicScout.twitterCt0,
    geo: settings.topicScout.geo
  })
}

// ---------------------------------------------------------------------------
// Influencer Database — the bundled Instagram catalogue, filtered server-side
// ---------------------------------------------------------------------------

export interface InfluencerFacets {
  total: number
  withStats: number
  niches: { value: string; count: number }[]
  maxFollowers: number
  maxPosts: number
}

export interface InfluencerRow {
  name: string
  fullName: string
  handle: string
  profileUrl: string
  bio: string
  email: string
  mobile: string
  youtubeId: string
  followers: number | null
  following: number | null
  posts: number | null
  niche: string
  nicheSource: string
  isVerified: boolean
  isPrivate: boolean
  lastPostDate: string
  hasStats: boolean
}

export interface InfluencerQuery {
  query: string
  niches: string[]
  followerMin: number | null
  followerMax: number | null
  postsMin: number | null
  postsMax: number | null
  verifiedOnly: boolean
  excludePrivate: boolean
  withContactOnly: boolean
  withStatsOnly: boolean
  sort: string
  page: number
  pageSize: number
}

export interface InfluencerSearchResponse {
  total: number
  page: number
  pageSize: number
  rows: InfluencerRow[]
  totalFollowers: number
  medianFollowers: number
}

export interface InfluencerExportResponse {
  path: string
  filename: string
  count: number
}

export function getInfluencerFacets(): Promise<InfluencerFacets> {
  return getJson('/influencer-db/facets')
}

export function searchInfluencers(query: InfluencerQuery): Promise<InfluencerSearchResponse> {
  return postJson('/influencer-db/search', query)
}

export function exportInfluencers(query: InfluencerQuery): Promise<InfluencerExportResponse> {
  return postJson('/influencer-db/export', query)
}

// --- Tracker Studio (Manage) ----------------------------------------------
// Only input cells cross the wire; every derived column is recomputed in the
// renderer by components/tracker/formulas.ts.

export function getTrackerWorkbooks(): Promise<TrackerWorkbooks> {
  return getJson('/tracker/workbooks')
}

export function saveTrackerWorkbooks(workbooks: TrackerWorkbooks): Promise<TrackerWorkbooks> {
  return putJson('/tracker/workbooks', workbooks)
}

export function resetTrackerWorkbooks(): Promise<TrackerWorkbooks> {
  return postJson('/tracker/reset', {})
}

// ---------------------------------------------------------------------------
// Community — a subscriber-only Telegram group
// ---------------------------------------------------------------------------

export interface CommunityTier {
  id: string
  name: string
  description: string
  stars: number
  period_days: number
  active: number
}

export interface CommunityMember {
  telegram_id: string
  username: string
  first_name: string
  tier_id: string | null
  status: string
  expires_at: string | null
  in_group: number
  joined_at: string | null
}

export interface CommunityStatus {
  botConnected: boolean
  botUsername: string
  chatId: string
  chatTitle: string
  inviteLink: string
  gatedChatId: string
  gatedChatTitle: string
  gatedInviteLink: string
  groupLinked: boolean
  gatedLinked: boolean
  tiers: CommunityTier[]
  revenue: { totalStars: number; payments: number; activeMembers: number }
  lastError: string
}

export function fetchCommunityStatus(): Promise<CommunityStatus> {
  return getJson('/community/status')
}

export function connectCommunityBot(token: string): Promise<{ botUsername: string; name: string }> {
  return postJson('/community/bot', { token })
}

export function disconnectCommunityBot(): Promise<{ botConnected: boolean }> {
  return deleteJson('/community/bot')
}

export function createCommunityInvite(): Promise<{ inviteLink: string }> {
  return postJson('/community/invite-link', {})
}

export function saveCommunityTier(tier: {
  id?: string
  name: string
  description?: string
  stars: number
  periodDays?: number
  active?: boolean
}): Promise<{ tier: CommunityTier }> {
  return postJson('/community/tiers', tier)
}

export function deleteCommunityTier(id: string): Promise<{ deleted: boolean }> {
  return deleteJson(`/community/tiers/${id}`)
}

export function fetchCommunityMembers(): Promise<{ members: CommunityMember[] }> {
  return getJson('/community/members')
}

export function sweepCommunity(): Promise<{ removed: number }> {
  return postJson('/community/sweep', {})
}

export function broadcastCommunity(text: string, gated: boolean): Promise<{ sentTo: string }> {
  return postJson('/community/broadcast', { text, gated })
}

export function createGatedInvite(): Promise<{ gatedInviteLink: string; stars: number }> {
  return postJson('/community/gated-invite', {})
}

// ---------------------------------------------------------------------------
// Community — signed in as yourself, not as the bot
//
// A bot cannot create a group or add anyone to one, so those run over Telegram's client
// protocol as the account holder. The api_id/api_hash/session travel in the body of every
// call, the same way the Hugging Face token does: they live in Electron's encrypted store
// and the backend keeps them in memory only.
// ---------------------------------------------------------------------------

export interface TelegramChat {
  id: string
  title: string
  username: string
  kind: 'group' | 'channel'
  megagroup: boolean
  creator: boolean
  admin: boolean
  participants: number
  botAdded?: boolean
  botDetail?: string
}

export interface TelegramAccountStatus {
  connected: boolean
  userId: string
  username: string
  firstName: string
  phone: string
  detail?: string
}

export interface TelegramChatMember {
  id: string
  username: string
  name: string
  bot: boolean
}

export interface AddMemberResult {
  handle: string
  ok: boolean
  detail: string
}

/** The credentials every account call carries. Throws rather than sending a half-set. */
async function telegramCreds(requireSession = true): Promise<{ apiId: number; apiHash: string; session: string }> {
  const { telegram } = await window.api.settings.getAll()
  const apiId = Number(telegram.apiId)
  if (!apiId || !telegram.apiHash) throw new Error('Add your Telegram api_id and api_hash first.')
  if (requireSession && !telegram.session) throw new Error('Sign in to Telegram first.')
  return { apiId, apiHash: telegram.apiHash, session: telegram.session }
}

export async function telegramSendCode(phone: string): Promise<{ sentTo: string }> {
  const creds = await telegramCreds(false)
  return postJson('/community/account/send-code', { ...creds, phone })
}

/**
 * Finish the login.
 *
 * A 409 means the account has two-step verification and the password is still needed — the
 * code itself was fine. Surfaced as a flag rather than an error so the UI can reveal the
 * password field instead of showing the sign-in as failed.
 */
export async function telegramSignIn(
  code: string,
  password = ''
): Promise<{ needsPassword: boolean; session?: string; username?: string; firstName?: string; userId?: string }> {
  const res = await fetch(`${backendUrl}/community/account/sign-in`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ code, password })
  })
  if (res.status === 409) return { needsPassword: true }
  if (!res.ok) throw await errorFrom(res, '/community/account/sign-in')
  return { needsPassword: false, ...(await res.json()) }
}

export async function telegramAccountStatus(): Promise<TelegramAccountStatus> {
  const { telegram } = await window.api.settings.getAll()
  if (!telegram.session || !telegram.apiId) {
    return { connected: false, userId: '', username: '', firstName: '', phone: '' }
  }
  return postJson('/community/account/status', {
    apiId: Number(telegram.apiId),
    apiHash: telegram.apiHash,
    session: telegram.session
  })
}

export async function telegramLogOut(): Promise<{ connected: boolean }> {
  const creds = await telegramCreds()
  return postJson('/community/account/logout', creds)
}

export async function telegramChats(): Promise<{ chats: TelegramChat[] }> {
  const creds = await telegramCreds()
  return postJson('/community/account/chats', creds)
}

export async function telegramCreateChat(input: {
  title: string
  about?: string
  kind?: 'group' | 'channel'
  addBot?: boolean
}): Promise<{ chat: TelegramChat }> {
  const creds = await telegramCreds()
  return postJson('/community/account/chats/create', { ...creds, ...input })
}

export async function telegramChatMembers(chatId: string): Promise<{ members: TelegramChatMember[] }> {
  const creds = await telegramCreds()
  return postJson(`/community/account/chats/${encodeURIComponent(chatId)}/members`, creds)
}

export async function telegramAddMembers(chatId: string, handles: string[]): Promise<{ results: AddMemberResult[] }> {
  const creds = await telegramCreds()
  return postJson(`/community/account/chats/${encodeURIComponent(chatId)}/members/add`, { ...creds, handles })
}

export async function telegramPost(chatId: string, text: string): Promise<{ messageId: string }> {
  const creds = await telegramCreds()
  return postJson(`/community/account/chats/${encodeURIComponent(chatId)}/post`, { ...creds, text })
}

export async function telegramChatInvite(chatId: string): Promise<{ inviteLink: string }> {
  const creds = await telegramCreds()
  return postJson(`/community/account/chats/${encodeURIComponent(chatId)}/invite`, creds)
}

export async function telegramAddBot(chatId: string): Promise<{ chat: TelegramChat }> {
  const creds = await telegramCreds()
  return postJson(`/community/account/chats/${encodeURIComponent(chatId)}/add-bot`, creds)
}

export async function telegramLinkChat(chatId: string, role: 'open' | 'paid', title: string): Promise<{ linked: string }> {
  const creds = await telegramCreds()
  return postJson(`/community/account/chats/${encodeURIComponent(chatId)}/link`, { ...creds, role, title })
}

// ---------------------------------------------------------------------------
// Generation queue (app/services/genqueue.py)
//
// Every generation endpoint holds a slot in a lane; this reports how many are
// running and how many are waiting behind them. A 429 from any generation call
// means the waiting room was full — its `detail` is already user-facing text.
// ---------------------------------------------------------------------------

export interface QueueStatus {
  running: number
  waiting: number
  busy: boolean
  queued: boolean
  lanes: Record<string, { running: number; waiting: number; limit: number; maxWaiting: number }>
}

export function getQueueStatus(): Promise<QueueStatus> {
  return getJson('/queue')
}

