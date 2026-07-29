export type Route = 'home' | 'research' | 'create' | 'engage' | 'analytics' | 'distribute' | 'library' | 'settings'
export type Tool = 'blog' | 'guest' | 'tutorial' | 'docu' | 'social' | 'mastodon' | 'email' | null

export interface LibraryItem {
  id: string
  tool: 'Plan' | 'Blog' | 'Guest' | 'Tutorial' | 'Docs' | 'Social' | 'Email' | 'Brand' | 'Topics'
  title: string
  subtitle: string
  created_at: string
  content?: string | null
  output_path?: string | null
}

export interface PlanFields {
  name: string
  productDescription: string
  budgetUsdPerMonth: number
  manpowerSummary: string
  industryKey: string
  geo: string
  model: string
}

export interface BlogFields {
  topic: string
  primaryKeyword: string
  secondaryKeyword: string
  brief: string
  targetWordCount: number
  contentGoal: string
}

export interface GuestFields {
  topic: string
  minAuthority: number
  maxResults: number
}

export interface TutorialFields {
  topic: string
  primaryKeyword: string
  secondaryKeyword: string
  contentBrief: string
  maxScreenshots: number
}

export interface DocuFields {
  product: string
}

export interface SocialFields {
  userInput: string
  niche: string
  platform: string
  sourceUrl: string
}

export interface MastodonFields {
  userInput: string
  niche: string
  sourceUrl: string
  // Not the account's — the *instance's*. Every Mastodon server sets its own
  // rules and character limit, so nothing about a draft is decidable without it.
  instance: string
  discloseAi: boolean
}

export interface EmailFields {
  instruction: string
}

// Mirrors backend/vendor/dmstrategy INDUSTRY_LABELS exactly (routers/marketing_plan.py).
export const PLAN_INDUSTRY_OPTIONS = [
  { key: 'ecommerce_retail', label: 'Ecommerce / Retail' },
  { key: 'apparel_fashion', label: 'Apparel / Fashion' },
  { key: 'b2b_saas', label: 'B2B SaaS' },
  { key: 'technology_electronics', label: 'Technology / Electronics' },
  { key: 'education', label: 'Education' },
  { key: 'finance_insurance', label: 'Finance / Insurance' },
  { key: 'health_medical', label: 'Health / Medical' },
  { key: 'home_improvement', label: 'Home Improvement' },
  { key: 'legal', label: 'Legal' },
  { key: 'real_estate', label: 'Real Estate' },
  { key: 'travel_hospitality', label: 'Travel / Hospitality' },
  { key: 'automotive', label: 'Automotive' },
  { key: 'beauty_personal_care', label: 'Beauty / Personal Care' },
  { key: 'restaurants_food', label: 'Restaurants / Food' },
  { key: 'fitness_wellness', label: 'Fitness / Wellness' },
  { key: 'nonprofit', label: 'Nonprofit' },
  { key: 'professional_services', label: 'Professional Services' },
  { key: 'furniture_home_goods', label: 'Furniture / Home Goods' },
  { key: 'industrial_manufacturing', label: 'Industrial / Manufacturing' },
  { key: 'consumer_services', label: 'Consumer Services' }
] as const

export const PLAN_MODEL_OPTIONS = [
  'Auto',
  'zai-org/GLM-5.2',
  'deepseek-ai/DeepSeek-V4-Pro',
  'meta-llama/Llama-3.3-70B-Instruct',
  'deepseek-ai/DeepSeek-V3.2',
  'openai/gpt-oss-120b'
] as const

export const BLOG_GOAL_OPTIONS = ['Informational', 'Persuasive', 'Authoritative', 'Thought Leadership'] as const

export const DEFAULT_PLAN_FIELDS: PlanFields = {
  name: '',
  productDescription: '',
  budgetUsdPerMonth: 2000,
  manpowerSummary: '',
  industryKey: 'ecommerce_retail',
  geo: '',
  model: 'Auto'
}

export const DEFAULT_BLOG_FIELDS: BlogFields = {
  topic: 'How to price a SaaS product',
  primaryKeyword: 'saas pricing strategy',
  secondaryKeyword: '',
  brief: '',
  targetWordCount: 1200,
  contentGoal: 'Informational'
}

export const DEFAULT_GUEST_FIELDS: GuestFields = {
  topic: '',
  minAuthority: 0,
  maxResults: 25
}

export const DEFAULT_TUTORIAL_FIELDS: TutorialFields = {
  topic: '',
  primaryKeyword: '',
  secondaryKeyword: '',
  contentBrief: '',
  maxScreenshots: 6
}

export const DEFAULT_SOCIAL_FIELDS: SocialFields = {
  userInput: '',
  niche: '',
  platform: 'bluesky',
  sourceUrl: ''
}

export const DEFAULT_MASTODON_FIELDS: MastodonFields = {
  userInput: '',
  niche: '',
  sourceUrl: '',
  instance: '',
  // On by default: several instances require generative-AI use to be disclosed,
  // and the safe default is the one that keeps you inside their rules.
  discloseAi: true
}

export const DEFAULT_DOCU_FIELDS: DocuFields = {
  product: ''
}

export const DEFAULT_EMAIL_FIELDS: EmailFields = {
  instruction: ''
}
