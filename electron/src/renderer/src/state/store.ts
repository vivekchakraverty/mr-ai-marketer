import { create } from 'zustand'
import type { UpdateCheckResult } from '../../../main/updateChecker'
import {
  DEFAULT_BLOG_FIELDS,
  DEFAULT_DOCU_FIELDS,
  DEFAULT_EMAIL_FIELDS,
  DEFAULT_SOCIAL_FIELDS,
  DEFAULT_GUEST_FIELDS,
  DEFAULT_MASTODON_FIELDS,
  DEFAULT_PLAN_FIELDS,
  DEFAULT_TUTORIAL_FIELDS,
  type BlogFields,
  type DocuFields,
  type EmailFields,
  type GuestFields,
  type LibraryItem,
  type MastodonFields,
  type PlanFields,
  type SocialFields,
  type Route,
  type Tool,
  type TutorialFields
} from './types'

export type LibraryFilter = 'All' | 'Plans' | 'Brand' | 'Blogs' | 'Tutorials' | 'Guest' | 'Docs'

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

  updateInfo: UpdateCheckResult | null
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
    email: EmailFields
  }

  goHome: () => void
  goResearch: () => void
  goCreate: () => void
  goEngage: () => void
  goAnalytics: () => void
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
  setEmailField: <K extends keyof EmailFields>(field: K, value: EmailFields[K]) => void

  setHfStatus: (connected: boolean, username: string | null) => void
  setHfChecked: (checked: boolean) => void
  openHfGate: () => void
  closeHfGate: () => void
  openSettings: () => void
  closeSettings: () => void
  setLibrary: (items: LibraryItem[]) => void
  setLibraryLoading: (loading: boolean) => void

  requireHf: () => boolean

  openDistributionGate: () => void
  closeDistributionGate: () => void
  setDistributionEngineReady: (ready: boolean) => void

  openLeadgenGate: () => void
  closeLeadgenGate: () => void
  setLeadgenEngineReady: (ready: boolean) => void

  setUpdateInfo: (info: UpdateCheckResult | null) => void
  dismissUpdateBanner: () => void
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
    email: DEFAULT_EMAIL_FIELDS
  },

  goHome: () => set({ route: 'home', tool: null, readerItem: null }),
  goResearch: () => set({ route: 'research', tool: null, readerItem: null }),
  goCreate: () => set({ route: 'create', tool: null, readerItem: null }),
  goEngage: () => set({ route: 'engage', tool: null, readerItem: null }),
  goAnalytics: () => set({ route: 'analytics', tool: null, readerItem: null }),
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

  setUpdateInfo: (info) => set({ updateInfo: info }),
  dismissUpdateBanner: () => set({ updateBannerDismissed: true })
}))
