// One entry per Distribution channel. Field shapes are confirmed live against a real
// Activepieces instance's piece metadata (GET /api/v1/pieces/<name>) and its OpenAPI
// connection-create schema — not guessed. CUSTOM_AUTH fields post as { type, props: {...} };
// SECRET_TEXT posts as { type, secret_text }; OAUTH2 channels have no fields at all here
// since they're connected through Activepieces' own connection screen instead.

export type AuthKind = 'CUSTOM_AUTH' | 'SECRET_TEXT' | 'OAUTH2'

export interface SetupField {
  key: string
  label: string
  placeholder?: string
  secret?: boolean
  kind?: 'text' | 'select' | 'checkbox'
  options?: { label: string; value: string }[]
  /** Mirrors the piece's own required=false props — everything else is validated non-empty
   * before submitting, so typos surface as a friendly message instead of an Activepieces error. */
  optional?: boolean
  /** Initial value for select fields (otherwise they start empty and unchosen). */
  defaultValue?: string
}

export interface PlatformSetupGuide {
  channel: string
  label: string
  authKind: AuthKind
  color: string
  blurb: string
  helpSteps: string[]
  fields: SetupField[]
  /** Set for channels that piggyback on another channel's connection (e.g. Discord replies
   * reuse the Discord broadcast bot connection) — no separate connect form of their own. */
  sharesConnectionWith?: string
}

export const PLATFORM_SETUP_GUIDES: Record<string, PlatformSetupGuide> = {
  bluesky: {
    channel: 'bluesky',
    label: 'Bluesky',
    authKind: 'CUSTOM_AUTH',
    color: '#8fd3f4',
    blurb: 'Post straight to your Bluesky timeline.',
    helpSteps: [
      'Go to Settings → App Passwords on bsky.app and create a new app password.',
      'Use your handle (e.g. yourname.bsky.social) or account email as the identifier.',
      'Leave PDS Host blank unless you run your own Bluesky server.'
    ],
    fields: [
      { key: 'identifier', label: 'Handle or email', placeholder: 'yourname.bsky.social' },
      { key: 'password', label: 'App password', placeholder: 'xxxx-xxxx-xxxx-xxxx', secret: true },
      { key: 'pdsHost', label: 'PDS host (optional)', placeholder: 'https://bsky.social', optional: true }
    ]
  },
  mastodon: {
    channel: 'mastodon',
    label: 'Mastodon',
    authKind: 'CUSTOM_AUTH',
    color: '#a78bfa',
    blurb: 'Toot your update to your Mastodon instance.',
    helpSteps: [
      'On your instance, go to Profile → Preferences → Development → New Application.',
      'Give it any name, then create it.',
      'Copy the "Your access token" value.'
    ],
    fields: [
      { key: 'base_url', label: 'Instance base URL', placeholder: 'https://mastodon.social' },
      { key: 'access_token', label: 'Access token', placeholder: 'Paste your access token', secret: true }
    ]
  },
  twitter: {
    channel: 'twitter',
    label: 'X (scheduled)',
    authKind: 'CUSTOM_AUTH',
    color: '#2b2420',
    blurb: 'Schedule a post to X — this channel always sends at your chosen time.',
    helpSteps: [
      'Open your app at developer.twitter.com → Keys and tokens.',
      'Set App permissions to Read and write before generating keys.',
      'Regenerate and copy the API Key/Secret and Access Token/Secret pairs.'
    ],
    fields: [
      { key: 'consumerKey', label: 'API key', secret: true },
      { key: 'consumerSecret', label: 'API key secret', secret: true },
      { key: 'accessToken', label: 'Access token', secret: true },
      { key: 'accessTokenSecret', label: 'Access token secret', secret: true }
    ]
  },
  discord: {
    channel: 'discord',
    label: 'Discord',
    authKind: 'SECRET_TEXT',
    color: '#6fcbc0',
    blurb: 'Announce to a channel, and reply in threads once approved.',
    helpSteps: [
      'Go to discord.com/developers/applications and open (or create) your bot application.',
      'Click Bot in the sidebar.',
      'Click Reset Token (or Copy) and paste it below.'
    ],
    fields: [{ key: 'secret_text', label: 'Bot token', placeholder: 'Paste your bot token', secret: true }]
  },
  'discord-conversation': {
    channel: 'discord-conversation',
    label: 'Discord replies',
    authKind: 'SECRET_TEXT',
    color: '#6fcbc0',
    blurb: 'Human-approved replies in Discord conversations — uses the same bot as Discord above.',
    helpSteps: [],
    fields: [],
    sharesConnectionWith: 'discord'
  },
  email: {
    channel: 'email',
    label: 'Email / newsletter',
    authKind: 'CUSTOM_AUTH',
    color: '#ffcb4d',
    blurb: 'Send a newsletter blast over SMTP.',
    helpSteps: ['Use your email provider\'s SMTP host and an app-specific password if it has 2FA on.'],
    fields: [
      { key: 'host', label: 'SMTP host', placeholder: 'smtp.gmail.com' },
      { key: 'email', label: 'Email address', optional: true },
      { key: 'password', label: 'Password', secret: true, optional: true },
      {
        key: 'port',
        label: 'Port',
        kind: 'select',
        defaultValue: '587',
        options: [
          { label: '25', value: '25' },
          { label: '465', value: '465' },
          { label: '587', value: '587' },
          { label: '2525', value: '2525' }
        ]
      },
      { key: 'TLS', label: 'Require TLS', kind: 'checkbox' }
    ]
  },
  linkedin: {
    channel: 'linkedin',
    label: 'LinkedIn',
    authKind: 'OAUTH2',
    color: '#3e93bf',
    blurb: 'Connects through Activepieces’ own screen — LinkedIn’s OAuth login handles the rest.',
    helpSteps: [
      'Click "Connect in Activepieces" below.',
      'Click "+ New Connection" and choose the LinkedIn piece.',
      'Name the connection exactly "linkedin" (lowercase) so your flows can find it, then sign in.'
    ],
    fields: []
  },
  facebook: {
    channel: 'facebook',
    label: 'Facebook Page',
    authKind: 'OAUTH2',
    color: '#8fd3f4',
    blurb: 'Connects through Activepieces’ own screen — Facebook’s OAuth login handles the rest.',
    helpSteps: [
      'Click "Connect in Activepieces" below.',
      'Click "+ New Connection" and choose the Facebook Pages piece.',
      'Name the connection exactly "facebook" (lowercase) so your flows can find it, then sign in.'
    ],
    fields: []
  },
  instagram: {
    channel: 'instagram',
    label: 'Instagram Business',
    authKind: 'OAUTH2',
    color: '#ff9a7c',
    blurb: 'Connects through Activepieces’ own screen — Instagram’s OAuth login handles the rest.',
    helpSteps: [
      'Click "Connect in Activepieces" below.',
      'Click "+ New Connection" and choose the Instagram Business piece.',
      'Name the connection exactly "instagram" (lowercase) so your flows can find it, then sign in.'
    ],
    fields: []
  },
  reddit: {
    channel: 'reddit',
    label: 'Reddit',
    authKind: 'OAUTH2',
    color: '#ff7a5c',
    blurb: 'Human-approved posts to a subreddit — connects through Activepieces’ own screen.',
    helpSteps: [
      'Click "Connect in Activepieces" below.',
      'Click "+ New Connection" and choose the Reddit piece.',
      'Name the connection exactly "reddit" (lowercase) so your flows can find it, then sign in.'
    ],
    fields: []
  }
}

export const BROADCAST_CHANNELS = ['bluesky', 'mastodon', 'discord', 'linkedin', 'facebook', 'instagram', 'email', 'twitter']
export const COMMUNITY_CHANNELS = ['reddit', 'discord-conversation']
