import { BROADCAST_CHANNELS, COMMUNITY_CHANNELS, PLATFORM_SETUP_GUIDES } from './platformSetupGuides'

/**
 * The walkthrough's running order.
 *
 * Built from PLATFORM_SETUP_GUIDES rather than listed by hand, so a channel added there
 * appears here without anyone remembering to. The two fixed steps at either end are the
 * ones that are not a channel: the Hugging Face token everything else bills to, and the
 * summary.
 *
 * `sharesConnectionWith` channels are filtered out — Discord replies reuse the Discord bot
 * connection and have no form of their own, so a step for one would be a dead end.
 */
export type SetupStepKind = 'welcome' | 'hf' | 'cloud' | 'channel' | 'done'

export interface SetupStep {
  /** Stable across releases: it is persisted as setupWizard.resumeAt. */
  id: string
  kind: SetupStepKind
  title: string
  /** Only for kind === 'channel'. */
  channel?: string
}

const CHANNEL_ORDER = [...BROADCAST_CHANNELS, ...COMMUNITY_CHANNELS]

export const SETUP_STEPS: SetupStep[] = [
  { id: 'welcome', kind: 'welcome', title: 'Welcome' },
  { id: 'hf', kind: 'hf', title: 'Hugging Face' },
  // Before the channels, because connecting Mastodon or Bluesky can then hand the credential
  // to the Space in the same step instead of asking for it twice.
  { id: 'cloud', kind: 'cloud', title: 'Posting while closed' },
  ...CHANNEL_ORDER.filter((channel) => {
    const guide = PLATFORM_SETUP_GUIDES[channel]
    return guide && !guide.sharesConnectionWith
  }).map((channel) => ({
    id: `channel:${channel}`,
    kind: 'channel' as const,
    title: PLATFORM_SETUP_GUIDES[channel].label,
    channel
  })),
  { id: 'done', kind: 'done', title: 'All set' }
]

export function stepIndex(id: string): number {
  const found = SETUP_STEPS.findIndex((s) => s.id === id)
  // An unknown id means a step that existed when the user last stopped and does not now.
  // Starting over beats resuming into nothing.
  return found === -1 ? 0 : found
}
