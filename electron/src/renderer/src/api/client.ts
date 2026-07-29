import type { DocuFields, GuestFields, LibraryItem, PlanFields, TutorialFields } from '../state/types'
import type { BlogFields } from '../state/types'

export const backendUrl = window.api?.backendUrl ?? 'http://127.0.0.1:8756'

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
  try {
    const detail = JSON.parse(text)?.detail
    if (typeof detail === 'string' && detail.trim()) return new Error(detail)
    // Pydantic validation errors arrive as a list of {loc, msg, …}.
    if (Array.isArray(detail) && detail.length) {
      const msg = detail.map((d) => d?.msg).filter(Boolean).join('; ')
      if (msg) return new Error(msg)
    }
  } catch {
    // Not JSON — fall through to the envelope, which is all we have.
  }
  return new Error(`${path} failed: HTTP ${res.status}${text ? ` ${text}` : ''}`)
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${backendUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  if (!res.ok) throw await errorFrom(res, path)
  return res.json() as Promise<T>
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${backendUrl}${path}`)
  if (!res.ok) throw await errorFrom(res, path)
  return res.json() as Promise<T>
}

async function deleteJson<T>(path: string): Promise<T> {
  const res = await fetch(`${backendUrl}${path}`, { method: 'DELETE' })
  if (!res.ok) throw await errorFrom(res, path)
  return res.json() as Promise<T>
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${backendUrl}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
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

export async function generateBlog(fields: BlogFields): Promise<GenerateBlogResponse> {
  const hfToken = (await window.api.settings.getHfToken()) ?? ''
  return postJson('/blog-writer/generate', { ...fields, hfToken })
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

export function generateEmail(instruction: string): Promise<GenerateEmailResponse> {
  return postJson('/email-writer/generate', { instruction })
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

export interface DistributionChannelsResponse {
  ready: boolean
  detail?: string
  channels: ChannelStatus[]
  communityChannels: ChannelStatus[]
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

  const res = await fetch(`${backendUrl}/docu-maker/generate`, { method: 'POST', body: form })
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

export function getSocialStatus(): Promise<SocialStatus> {
  return getJson('/social-post/status')
}

export function listSocialNiches(): Promise<SocialNiche[]> {
  return getJson('/social-post/niches')
}

export function saveSocialNiche(
  name: string,
  keywords: string[],
  active = true
): Promise<{ name: string; weakKeywords: string[] }> {
  return postJson('/social-post/niches', { name, keywords, active })
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
  sourceUrl = ''
): Promise<SocialGenerateResponse> {
  return postJson('/social-post/generate', { userInput, niche, platform, sourceUrl })
}

export function markSocialPublished(
  generationId: number,
  postedUri: string,
  niche: string
): Promise<{ postedUri: string }> {
  return postJson('/social-post/published', { generationId, postedUri, niche })
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

export function listMastodonNiches(): Promise<MastodonNiche[]> {
  return getJson('/mastodon-post/niches')
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

export async function generateMastodonPost(
  instance: string,
  niche: string,
  userInput: string,
  sourceUrl = '',
  discloseAi = true
): Promise<MastodonGenerateResponse> {
  return postJson('/mastodon-post/generate', {
    instance,
    niche,
    userInput,
    sourceUrl,
    discloseAi,
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

export function verifySocialPost(target: 'supabase' | 'bluesky' | 'llm' | 'hf'): Promise<{
  valid: boolean
  detail: string
}> {
  return postJson(`/settings/social-post/verify/${target}`, {})
}

export interface TopicSuggestion {
  topic: string
  whyNow: string
  sources: string[]
}

export interface TopicsResponse {
  suggestions: TopicSuggestion[]
  corpusPosts: number
  signals: string[]
  note: string
}

export function suggestSocialTopics(niche: string, n = 5): Promise<TopicsResponse> {
  return getJson(`/social-post/topics?niche=${encodeURIComponent(niche)}&n=${n}`)
}

// ---------------------------------------------------------------------------
// Engage — the user's own Bluesky feeds (timeline, notifications)
// ---------------------------------------------------------------------------

export interface EngageStatus {
  configured: boolean
  handle: string | null
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
    hfToken: settings.hfToken
  })
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
    hfToken: settings.hfToken
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
