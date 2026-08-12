import { useEffect } from 'react'
import ErrorBoundary from './components/ErrorBoundary'
import ErrorPopup from './components/ErrorPopup'
import { reportError } from './state/errors'
import { verifyHfToken } from './api/client'
import { refreshLibrary } from './state/actions'
import { useAppStore } from './state/store'
import NavBar from './components/NavBar'
import HfGateModal from './components/HfGateModal'
import DistributionGateModal from './components/DistributionGateModal'
import LeadgenGateModal from './components/LeadgenGateModal'
import UpdateBanner from './components/UpdateBanner'
import ScrapDrawer from './components/ScrapDrawer'
import QueueIndicator from './components/QueueIndicator'
import Home from './routes/Home'
import Research from './routes/Research'
import CreateHub from './routes/CreateHub'
import SocialPost from './routes/SocialPost'
import MastodonPost from './routes/MastodonPost'
import Engage from './routes/Engage'
import Analytics from './routes/Analytics'
import Manage from './routes/Manage'
import SettingsScreen from './routes/Settings'
import BlogWriter from './routes/BlogWriter'
import EmailWriter from './routes/EmailWriter'
import GuestPost from './routes/GuestPost'
import TutorialMaker from './routes/TutorialMaker'
import DocuMaker from './routes/DocuMaker'
import Distribute from './routes/Distribute'
import Community from './routes/Community'
import Library from './routes/Library'
import Reader from './routes/Reader'

// Dev-only: launch with DEBUG_ROUTE=research|create|engage|analytics|manage|community|blog|guest|tutorial|docu|social|mastodon|distribute|library|hf-gate
// to jump straight to a screen for visual verification (see main/index.ts).
function applyDebugRoute(): void {
  const route = window.api?.debugRoute
  if (!route) return
  const s = useAppStore.getState()
  const toolRoutes = ['blog', 'guest', 'tutorial', 'docu', 'social', 'mastodon', 'email'] as const
  if ((toolRoutes as readonly string[]).includes(route)) {
    s.openTool(route as (typeof toolRoutes)[number])
  } else if (route === 'hf-gate') {
    s.openHfGate()
  } else if (route === 'settings') {
    s.goSettings()
  } else if (route === 'distribution-gate') {
    s.goDistribute()
    s.openDistributionGate()
  } else if (route === 'research') s.goResearch()
  else if (route === 'create') s.goCreate()
  else if (route === 'engage') s.goEngage()
  else if (route === 'analytics') s.goAnalytics()
  else if (route === 'manage') s.goManage()
  else if (route === 'community') s.goCommunity()
  else if (route === 'distribute') s.goDistribute()
  else if (route === 'library') s.goLibrary()
}

function MainContent(): React.JSX.Element {
  const route = useAppStore((s) => s.route)
  const tool = useAppStore((s) => s.tool)
  const readerItem = useAppStore((s) => s.readerItem)

  if (readerItem) return <Reader />
  if (route === 'home') return <Home />
  if (route === 'research') return <Research />
  if (route === 'engage') return <Engage />
  if (route === 'analytics') return <Analytics />
  if (route === 'manage') return <Manage />
  if (route === 'community') return <Community />
  if (route === 'distribute') return <Distribute />
  if (route === 'library') return <Library />
  if (route === 'settings') return <SettingsScreen />
  if (route === 'create') {
    if (tool === 'blog') return <BlogWriter />
    if (tool === 'guest') return <GuestPost />
    if (tool === 'tutorial') return <TutorialMaker />
    if (tool === 'docu') return <DocuMaker />
    if (tool === 'social') return <SocialPost />
    if (tool === 'mastodon') return <MastodonPost />
    if (tool === 'email') return <EmailWriter />
    return <CreateHub />
  }
  return <Home />
}

function App(): React.JSX.Element {
  // Only used to re-key the error boundary — see the comment where it's applied.
  const activeRoute = useAppStore((s) => s.route)
  const hfChecked = useAppStore((s) => s.hfChecked)
  const hfGateOpen = useAppStore((s) => s.hfGateOpen)
  const distributionGateOpen = useAppStore((s) => s.distributionGateOpen)
  const leadgenGateOpen = useAppStore((s) => s.leadgenGateOpen)
  const setHfChecked = useAppStore((s) => s.setHfChecked)
  const setHfStatus = useAppStore((s) => s.setHfStatus)
  const setUpdateInfo = useAppStore((s) => s.setUpdateInfo)

  useEffect(() => {
    let cancelled = false

    async function boot(): Promise<void> {
      void refreshLibrary()
      window.api.update
        .check()
        .then((result) => !cancelled && setUpdateInfo(result))
        .catch(() => {})
      try {
        const stored = await window.api.settings.getHfToken()
        if (stored) {
          const result = await verifyHfToken(stored)
          if (!cancelled && result.valid) {
            setHfStatus(true, result.username)
          }
        }
      } finally {
        if (!cancelled) {
          setHfChecked(true)
          applyDebugRoute()
        }
      }
    }

    boot()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Download progress is pushed from the main process, not polled. Subscribing separately
  // from boot() so the listener is attached for the life of the app: a download started from
  // the Settings screen has to keep driving the banner after Settings closes.
  useEffect(() => {
    return window.api.update.onState(setUpdateInfo)
  }, [setUpdateInfo])

  // Anything thrown where nobody was catching. Without these, a rejected promise in an
  // effect goes to the console — which, in a packaged desktop app, is nowhere the user can
  // look. Registered once, before the first render that could fail.
  useEffect(() => {
    function onError(event: ErrorEvent): void {
      reportError({
        message: event.message || 'Unexpected error',
        source: event.filename ? `${event.filename.split('/').pop()}:${event.lineno}` : 'app',
        detail: event.error?.stack ?? ''
      })
    }
    function onRejection(event: PromiseRejectionEvent): void {
      const reason = event.reason
      reportError({
        message: reason instanceof Error ? reason.message : String(reason),
        source: 'unhandled rejection',
        detail: reason instanceof Error ? (reason.stack ?? '') : ''
      })
    }
    window.addEventListener('error', onError)
    window.addEventListener('unhandledrejection', onRejection)
    return () => {
      window.removeEventListener('error', onError)
      window.removeEventListener('unhandledrejection', onRejection)
    }
  }, [])

  if (!hfChecked) {
    return (
      <div style={{ display: 'flex', height: '100vh', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink-muted)' }}>Loading Mr. AI Marketer…</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      {/* Each of these is optional furniture. A crash in one must cost that widget and
          nothing else — they live outside the route boundary below, so without their own
          they would take the nav bar and the whole screen down with them. */}
      <ErrorBoundary fallback={null}>
        <UpdateBanner />
      </ErrorBoundary>
      <NavBar />
      <main style={{ flex: 1, overflowY: 'auto' }}>
        {/* Keyed on the route so recovering from a crash on one screen doesn't leave the
            boundary latched when the user navigates to a working one. */}
        <ErrorBoundary key={activeRoute}>
          <MainContent />
        </ErrorBoundary>
      </main>
      {/* Outside <main> so it stays put while the route changes, and above it so a
          scroll on the page never moves the shelf. */}
      <ErrorBoundary fallback={null}>
        <ScrapDrawer />
      </ErrorBoundary>
      {/* Renders nothing unless something is queued behind something else. */}
      <ErrorBoundary fallback={null}>
        <QueueIndicator />
      </ErrorBoundary>
      {hfGateOpen && <HfGateModal />}
      {distributionGateOpen && <DistributionGateModal />}
      {leadgenGateOpen && <LeadgenGateModal />}
      <ErrorPopup />
    </div>
  )
}

export default App
