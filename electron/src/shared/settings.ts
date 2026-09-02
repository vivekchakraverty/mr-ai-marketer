/**
 * Every setting the app persists, and the empty value of each.
 *
 * Shared rather than living in main/settingsStore.ts, which is where these grew up, because
 * the renderer needs the same defaults and had a hand-copied duplicate of EMPTY_SETTINGS at
 * the top of routes/Settings.tsx. Two literals meant every new group had to be added in both,
 * and the compiler only caught it when the shapes disagreed — a group added to one and not
 * the other type-checks fine right up until a save from the Settings screen blanks it.
 *
 * Nothing here may import from `electron`: the renderer bundles this file too.
 */

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

/**
 * The first-run credential walkthrough: how far it got, and what the user chose to skip.
 *
 * Timestamps rather than booleans, matching modalProvisionedAt — "when" answers "whether"
 * and survives being useful later, and a boolean that means two things (never started vs
 * deliberately dismissed) is the kind of flag that grows a second boolean beside it.
 *
 * Only intent is stored. Whether a channel is actually connected is not: that is
 * authoritative on the Activepieces side and already served by GET /distribution/channels,
 * so a local copy would go stale the moment someone connects LinkedIn in a browser tab.
 */
export interface SetupWizardSettings {
  /** First time the walkthrough was opened. Empty means it has never run. */
  startedAt: string
  /** Set when the user reached the end. */
  completedAt: string
  /** Set when the user chose "finish later". Either of these stops it auto-opening. */
  skippedAt: string
  /** Step id to resume on, so "Resume setup" returns to where they stopped. */
  resumeAt: string
  /** Step ids the user explicitly skipped, so the summary can say what is still missing. */
  skipped: string[]
}

/**
 * The user's own poster Space, and the credentials it holds.
 *
 * Every value here describes a repo in THEIR Hugging Face account. Nothing is shared between
 * installations and nothing is ours: duplicating the Space per user is what makes it safe to
 * leave a posting-capable credential in the cloud at all.
 *
 * `spaceToken` is a fine-grained token scoped to the outbox dataset alone, deliberately kept
 * apart from the account-wide `hfToken` at the top of Settings. It is the one that also goes
 * into the Space, so its blast radius is the whole point of it existing separately.
 *
 * The social credentials are NOT stored here — they go straight into the Space's own secrets
 * and never come back. Only the fact that they were connected, and when, is recorded.
 */
export interface CloudPostingSettings {
  /** e.g. "someone/mr-ai-marketer-poster". Empty means cloud posting is off. */
  spaceId: string
  spaceUrl: string
  outboxRepo: string
  /** Guards the Space's /status endpoint. Generated per install, never shared. */
  posterKey: string
  spaceToken: string
  /** ISO timestamp of the last successful provision, per the modalProvisionedAt idiom. */
  provisionedAt: string
  mastodonHost: string
  mastodonConnectedAt: string
  blueskyDid: string
  blueskyConnectedAt: string
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
  setupWizard: SetupWizardSettings
  cloudPosting: CloudPostingSettings
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
    | 'setupWizard'
    | 'cloudPosting'
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
  setupWizard?: Partial<SetupWizardSettings>
  cloudPosting?: Partial<CloudPostingSettings>
}

export const EMPTY_SETTINGS: AppSettings = {
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
  telegram: { apiId: '', apiHash: '', session: '', username: '' },
  setupWizard: { startedAt: '', completedAt: '', skippedAt: '', resumeAt: '', skipped: [] },
  cloudPosting: {
    spaceId: '',
    spaceUrl: '',
    outboxRepo: '',
    posterKey: '',
    spaceToken: '',
    provisionedAt: '',
    mastodonHost: '',
    mastodonConnectedAt: '',
    blueskyDid: '',
    blueskyConnectedAt: ''
  }
}
