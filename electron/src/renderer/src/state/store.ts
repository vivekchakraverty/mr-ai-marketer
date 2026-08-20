import { create } from 'zustand'
import type { UpdateState } from '../../../main/updater'
import {
  DEFAULT_BLOG_FIELDS,
  DEFAULT_DOCU_FIELDS,
  DEFAULT_EMAIL_FIELDS,
  DEFAULT_SOCIAL_FIELDS,
  DEFAULT_GUEST_FIELDS,
  DEFAULT_MASTODON_FIELDS,
  DEFAULT_TUMBLR_FIELDS,
  DEFAULT_PLAN_FIELDS,
  DEFAULT_TUTORIAL_FIELDS,
  type BlogFields,
  type DocuFields,
  type EmailFields,
  type GuestFields,
  type LibraryItem,
  type MastodonFields,
  type TumblrFields,
  type PlanFields,
  type SocialFields,
  type Route,
  type Tool,
  type TutorialFields
} from './types'

export type LibraryFilter = 'All' | 'Plans' | 'Brand' | 'Blogs' | 'Tutorials' | 'Guest' | 'Docs'

/** A finished post on its way to the composer that will send it.
 *
 * Carries the picture and the tags, not just the words. The three pieces are chosen
 * together on the creator screen, and handing over only the text meant re-picking an image
 * and re-ticking tags in a second place — with nothing to tell you which ones you had.
 */
export interface EngageHandoff {
  text: string
  /** Chosen tags, without their leading '#'. Appended to the text on arrival. */
  tags: string[]
  /** An /outputs URL for a generated image, ready for the composer's picker. */
  imageUrl: string
  /** Which composer should receive it — a Mastodon draft is not a Bluesky one. */
  network: 'bluesky' | 'mastodon' | 'tumblr'
}

/** The handoff's text with its chosen tags appended, the way they would be published.
 *
 * One helper rather than three: the composers would otherwise each decide how to join them,
 * and a post that gains its tags differently depending on which screen sent it is a bug
 * waiting to be reported as "the hashtags moved".
 */
export function withTags(draft: EngageHandoff): string {
  const tags = draft.tags.map((t) => (t.startsWith('#') ? t : `#${t}`)).join(' ')
  const body = draft.text.trim()
  return tags ? `${body}

${tags}` : body
}

interface AppState {
  route: Route
  tool: Tool
  readerItem: LibraryItem | null
  filter: LibraryFilter

  hfChecked: boolean
  hfConnected: boolean
  hfUsername: string | null
  hfGateOpen: boolean
  settingsOpen: boolean

  distributionEngineReady: boolean
  distributionGateOpen: boolean

  leadgenEngineReady: boolean
  leadgenGateOpen: boolean

  updateInfo: UpdateState | null
  updateBannerDismissed: boolean

  library: LibraryItem[]
  libraryLoading: boolean

  fields: {
    plan: PlanFields
    blog: BlogFields
    guest: GuestFields
    tutorial: TutorialFields
    docu: DocuFields
    social: SocialFields
    mastodon: MastodonFields
    tumblr: TumblrFields
    email: EmailFields
  }

  goHome: () => void
  goResearch: () => void
  goCreate: () => void
  goEngage: () => void
  goAnalytics: () => void
  goManage: () => void
  goCommunity: () => void
  goDistribute: () => void
  goLibrary: () => void
  goSettings: () => void
  openTool: (tool: Exclude<Tool, null>) => void
  openReader: (item: LibraryItem) => void
  closeReader: () => void
  setFilter: (filter: LibraryFilter) => void

  setPlanField: <K extends keyof PlanFields>(field: K, value: PlanFields[K]) => void
  setBlogField: <K extends keyof BlogFields>(field: K, value: BlogFields[K]) => void
  setGuestField: <K extends keyof GuestFields>(field: K, value: GuestFields[K]) => void
  setTutorialField: <K extends keyof TutorialFields>(field: K, value: TutorialFields[K]) => void
  setDocuField: <K extends keyof DocuFields>(field: K, value: DocuFields[K]) => void
  setSocialField: <K extends keyof SocialFields>(field: K, value: SocialFields[K]) => void
  setMastodonField: <K extends keyof MastodonFields>(field: K, value: MastodonFields[K]) => void
  setTumblrField: <K extends keyof TumblrFields>(field: K, value: TumblrFields[K]) => void
  setEmailField: <K extends keyof EmailFields>(field: K, value: EmailFields[K]) => void

  setHfStatus: (connected: boolean, username: string | null) => void
  setHfChecked: (checked: boolean) => void
  openHfGate: () => void
  closeHfGate: () => void
  openSettings: () => void
  closeSettings: () => void
  setLibrary: (items: LibraryItem[]) => void
  /** Apply an edit locally, so the shelf and the open reader agree without a refetch. */
  patchLibraryItem: (id: string, changes: Partial<LibraryItem>) => void
  setLibraryLoading: (loading: boolean) => void

  requireHf: () => boolean

  openDistributionGate: () => void
  closeDistributionGate: () => void
  setDistributionEngineReady: (ready: boolean) => void

  openLeadgenGate: () => void
  closeLeadgenGate: () => void
  setLeadgenEngineReady: (ready: boolean) => void

  setUpdateInfo: (info: UpdateState | null) => void
  dismissUpdateBanner: () => void

  // Handoff from a composer tool to Engage's Bluesky post box. Write-once: Engage
  // takes it on arrival, so coming back later does not silently repopulate a box
  // the user already emptied.
  engageDraft: EngageHandoff | null
  sendToEngage: (draft: EngageHandoff | string) => void
  // Reads and clears in one step, and returns null if it was already taken.
  // The atomicity is load-bearing: StrictMode runs an effect body twice against
  // the same closure, so a read-then-clear pair appended the draft twice. Taking
  // it makes the second pass a no-op.
  takeEngageDraft: () => EngageHandoff | null
}

export const useAppStore = create<AppState>((set, get) => ({
  route: 'home',
  tool: null,
  readerItem: null,
  filter: 'All',

  hfChecked: false,
  hfConnected: false,
  hfUsername: null,
  hfGateOpen: false,
  settingsOpen: false,

  distributionEngineReady: false,
  distributionGateOpen: false,

  leadgenEngineReady: false,
  leadgenGateOpen: false,

  updateInfo: null,
  updateBannerDismissed: false,

  library: [],
  libraryLoading: false,

  fields: {
    plan: DEFAULT_PLAN_FIELDS,
    blog: DEFAULT_BLOG_FIELDS,
    guest: DEFAULT_GUEST_FIELDS,
    tutorial: DEFAULT_TUTORIAL_FIELDS,
    docu: DEFAULT_DOCU_FIELDS,
    social: DEFAULT_SOCIAL_FIELDS,
    mastodon: DEFAULT_MASTODON_FIELDS,
    tumblr: DEFAULT_TUMBLR_FIELDS,
    email: DEFAULT_EMAIL_FIELDS
  },

  goHome: () => set({ route: 'home', tool: null, readerItem: null }),
  goResearch: () => set({ route: 'research', tool: null, readerItem: null }),
  goCreate: () => set({ route: 'create', tool: null, readerItem: null }),
  goEngage: () => set({ route: 'engage', tool: null, readerItem: null }),
  goAnalytics: () => set({ route: 'analytics', tool: null, readerItem: null }),
  goManage: () => set({ route: 'manage', tool: null, readerItem: null }),
  goCommunity: () => set({ route: 'community', tool: null, readerItem: null }),
  goDistribute: () => set({ route: 'distribute', tool: null, readerItem: null }),
  goLibrary: () => set({ route: 'library', tool: null, readerItem: null }),
  goSettings: () => set({ route: 'settings', tool: null, readerItem: null }),
  openTool: (tool) => set({ route: 'create', tool, readerItem: null }),
  openReader: (item) => set({ readerItem: item }),
  closeReader: () => set({ readerItem: null }),
  setFilter: (filter) => set({ filter }),

  setPlanField: (field, value) =>
    set((s) => ({ fields: { ...s.fields, plan: { ...s.fields.plan, [field]: value } } })),
  setBlogField: (field, value) =>
    set((s) => ({ fields: { ...s.fields, blog: { ...s.fields.blog, [field]: value } } })),
  setGuestField: (field, value) =>
    set((s) => ({ fields: { ...s.fields, guest: { ...s.fields.guest, [field]: value } } })),
  setTutorialField: (field, value) =>
    set((s) => ({ fields: { ...s.fields, tutorial: { ...s.fields.tutorial, [field]: value } } })),
  setDocuField: (field, value) =>
    set((s) => ({ fields: { ...s.fields, docu: { ...s.fields.docu, [field]: value } } })),

  setSocialField: (field, value) =>
    set((s) => ({ fields: { ...s.fields, social: { ...s.fields.social, [field]: value } } })),

  setTumblrField: (field, value) =>
    set((s) => ({ fields: { ...s.fields, tumblr: { ...s.fields.tumblr, [field]: value } } })),
  setMastodonField: (field, value) =>
    set((s) => ({ fields: { ...s.fields, mastodon: { ...s.fields.mastodon, [field]: value } } })),

  setEmailField: (field, value) =>
    set((s) => ({ fields: { ...s.fields, email: { ...s.fields.email, [field]: value } } })),

  setHfStatus: (connected, username) => set({ hfConnected: connected, hfUsername: username }),
  setHfChecked: (checked) => set({ hfChecked: checked }),
  openHfGate: () => set({ hfGateOpen: true }),
  closeHfGate: () => set({ hfGateOpen: false }),
  openSettings: () => set({ settingsOpen: true }),
  closeSettings: () => set({ settingsOpen: false }),
  setLibrary: (items) => set({ library: items }),
  patchLibraryItem: (id, changes) =>
    set((state) => ({
      library: state.library.map((i) => (i.id === id ? { ...i, ...changes } : i)),
      // The reader holds its own snapshot; without this the card updates and the
      // open document does not.
      readerItem:
        state.readerItem && state.readerItem.id === id
          ? { ...state.readerItem, ...changes }
          : state.readerItem
    })),
  setLibraryLoading: (loading) => set({ libraryLoading: loading }),

  requireHf: () => {
    if (get().hfConnected) return true
    set({ hfGateOpen: true })
    return false
  },

  openDistributionGate: () => set({ distributionGateOpen: true }),
  closeDistributionGate: () => set({ distributionGateOpen: false }),
  setDistributionEngineReady: (ready) => set({ distributionEngineReady: ready }),

  openLeadgenGate: () => set({ leadgenGateOpen: true }),
  closeLeadgenGate: () => set({ leadgenGateOpen: false }),
  setLeadgenEngineReady: (ready) => set({ leadgenEngineReady: ready }),

  // Dismissing the banner hides the "an update exists" nudge, but must not also swallow the
  // "it's downloaded, restart to install" one that comes later — that second message is the
  // payoff for a download the user explicitly asked for, and silently dropping it would
  // leave them with an update that only lands whenever they next happen to quit.
  setUpdateInfo: (info) =>
    set((s) => ({
      updateInfo: info,
      updateBannerDismissed: info?.phase === 'ready' ? false : s.updateBannerDismissed
    })),
  dismissUpdateBanner: () => set({ updateBannerDismissed: true }),

  engageDraft: null,
  // Navigating is part of the action, not a separate step the caller has to
  // remember — "send to Engage" that leaves you on the previous screen is a
  // clipboard button with extra ceremony.
  sendToEngage: (draft) =>
    set({
      // A bare string still works: several callers hand over just the words, and making
      // them all build an object to say "no image, no tags" would be ceremony.
      engageDraft:
        typeof draft === 'string'
          ? { text: draft, tags: [], imageUrl: '', network: 'bluesky' }
          : draft,
      route: 'engage',
      tool: null,
      readerItem: null
    }),
  takeEngageDraft: () => {
    const draft = get().engageDraft
    if (draft !== null) set({ engageDraft: null })
    return draft
  }
}))
