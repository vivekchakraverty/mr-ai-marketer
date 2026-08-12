import { useState } from 'react'
import BlueskyAnalytics from '../components/BlueskyAnalytics'
import EmailTracking from '../components/EmailTracking'
import MastodonAnalytics from '../components/MastodonAnalytics'
import OutreachCrm from '../components/leadgen/OutreachCrm'
import ScreenBackdrop from '../components/ScreenBackdrop'
import SegmentedControl from '../components/SegmentedControl'
import { sectionEyebrow } from '../styles/styleKit'

type AnalyticsTab = 'Outreach CRM' | 'Email' | 'Bluesky' | 'Mastodon'

const SUBTITLE: Record<AnalyticsTab, string> = {
  'Outreach CRM':
    'Every lead the Lead Gen Agent found, where it sits in the pipeline, and the full ' +
    'conversation for each one. Run and approve campaigns in Research / Strategy → Lead Gen Agent.',
  Bluesky: 'Public post performance for your account compared with a selected or discovered cohort in the same niche and follower range.',
  Email:
    'Opens, clicks and bounces for every email this app has sent — from the Mail Composer in ' +
    'Distribute, and from the Lead Gen Agent’s outreach.',
  Mastodon:
    'How the posts on your Mastodon account have done, read live from your instance. No cohort ' +
    'comparison here — Mastodon has no searchable firehose to draw a comparable one from.'
}

export default function Analytics(): React.JSX.Element {
  const [tab, setTab] = useState<AnalyticsTab>('Outreach CRM')

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '30px 34px 60px' }}>
      {tab === 'Outreach CRM' && <ScreenBackdrop video="outreach" />}
      {tab === 'Email' && <ScreenBackdrop video="emailtrack" />}
      {tab === 'Bluesky' && <ScreenBackdrop video="bluesky" />}
      {tab === 'Mastodon' && <ScreenBackdrop video="mastodon" />}
      <div style={{ marginBottom: 22 }}>
        <div style={sectionEyebrow}>Analytics</div>
        <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)', marginTop: 4 }}>{tab}</div>
        <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>{SUBTITLE[tab]}</div>
      </div>

      <div style={{ marginBottom: 18, maxWidth: 520 }}>
        <SegmentedControl
          options={['Outreach CRM', 'Email', 'Bluesky', 'Mastodon']}
          value={tab}
          onChange={(v) => setTab(v as AnalyticsTab)}
        />
      </div>

      {tab === 'Outreach CRM' ? (
        <OutreachCrm />
      ) : tab === 'Email' ? (
        <EmailTracking />
      ) : tab === 'Bluesky' ? (
        <BlueskyAnalytics />
      ) : (
        <MastodonAnalytics />
      )}
    </div>
  )
}
