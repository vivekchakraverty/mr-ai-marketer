import { useState } from 'react'
import { generatePlan, type GeneratePlanResponse } from '../api/client'
import { refreshLibrary } from '../state/actions'
import { useAppStore } from '../state/store'
import { PLAN_INDUSTRY_OPTIONS, PLAN_MODEL_OPTIONS } from '../state/types'
import BrandForge from '../components/BrandForge'
import InfluencerDb from '../components/InfluencerDb'
import MarkdownPanel from '../components/MarkdownPanel'
import SendToDistributionModal from '../components/SendToDistributionModal'
import TopicScout from '../components/TopicScout'
import LeadGenPanel from '../components/leadgen/LeadGenPanel'
import ScreenBackdrop from '../components/ScreenBackdrop'
import { card, label, primaryButton, primaryButtonSmall, sectionEyebrow, segGroup, segItem, select, textInput, textarea } from '../styles/styleKit'
import SaveButton from '../components/SaveButton'

type ResearchTool = 'plan' | 'brand' | 'scout' | 'leads' | 'influencers'
const RESEARCH_TOOLS: { key: ResearchTool; label: string }[] = [
  { key: 'plan', label: 'Marketing Plan' },
  { key: 'brand', label: 'Brand Studio' },
  { key: 'scout', label: 'Topic Scout' },
  { key: 'leads', label: 'Lead Gen Agent' },
  { key: 'influencers', label: 'Influencer Database' }
]

const TOOL_HEADINGS: Record<ResearchTool, { title: string; subtitle: string }> = {
  plan: {
    title: 'Marketing Plan Generator',
    subtitle:
      'Describe your business and budget — get an SEO, social and paid-ads plan, composed into one 10-section strategy grounded in real marketing research.'
  },
  brand: {
    title: 'Brand Studio',
    subtitle:
      "Define your brand's identity, voice, messaging, story and visual direction — a full 12-section Brand Document written by your fine-tuned BrandForge model, grounded in the branding books it learned from."
  },
  scout: {
    title: 'Topic Scout',
    subtitle:
      'Find the stories gaining momentum in your niche before they feel obvious. News, community, code, research, search, video and social evidence gets clustered into topics, each one measured against the window before it and read for tone — with the receipts attached.'
  },
  leads: {
    title: 'Lead Gen Agent',
    subtitle:
      'Describe what you sell and who you sell to — an autonomous agent finds matching businesses, qualifies them as it learns your taste, verifies their emails, and drafts personalized outreach for you to approve. Track the whole pipeline in Analytics.'
  },
  influencers: {
    title: 'Influencer Database',
    subtitle:
      'Browse the bundled Instagram catalogue — filter by niche, follower count and post count, narrow to verified or contactable profiles, and export the shortlist as a CSV.'
  }
}

type Tab = 'full' | 'seo' | 'social' | 'ads'
const TABS: { key: Tab; label: string }[] = [
  { key: 'full', label: 'Full Plan' },
  { key: 'seo', label: 'SEO Plan' },
  { key: 'social', label: 'Social Plan' },
  { key: 'ads', label: 'Ads Plan' }
]

export default function Research(): React.JSX.Element {
  const fields = useAppStore((s) => s.fields.plan)
  const setPlanField = useAppStore((s) => s.setPlanField)
  const requireHf = useAppStore((s) => s.requireHf)

  const [result, setResult] = useState<GeneratePlanResponse | null>(null)
  const [tab, setTab] = useState<Tab>('full')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showSend, setShowSend] = useState(false)
  const [researchTool, setResearchTool] = useState<ResearchTool>('plan')

  async function handleGenerate(): Promise<void> {
    if (!requireHf()) return
    if (!fields.productDescription.trim()) {
      setError('Please describe your product or service.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const res = await generatePlan(fields)
      setResult(res)
      setTab('full')
      void refreshLibrary()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const tabMarkdown: Record<Tab, string> = result
    ? { full: result.markdown, seo: result.seoMarkdown, social: result.socialMarkdown, ads: result.adsMarkdown }
    : { full: '', seo: '', social: '', ads: '' }

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '30px 34px 60px' }}>
      {researchTool === 'plan' && <ScreenBackdrop video="plan" />}
      {researchTool === 'brand' && <ScreenBackdrop video="brand" />}
      {researchTool === 'scout' && <ScreenBackdrop video="scout" />}
      {researchTool === 'leads' && <ScreenBackdrop video="leads" />}
      {researchTool === 'influencers' && <ScreenBackdrop video="influencers" />}
      <div style={{ marginBottom: 22 }}>
        <div style={sectionEyebrow}>Research / Strategy</div>
        <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)', marginTop: 4 }}>
          {TOOL_HEADINGS[researchTool].title}
        </div>
        <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>
          {TOOL_HEADINGS[researchTool].subtitle}
        </div>

        <div style={{ ...segGroup, marginTop: 14 }}>
          {RESEARCH_TOOLS.map((t) => (
            <div
              key={t.key}
              style={segItem(researchTool === t.key)}
              onClick={() => setResearchTool(t.key)}
            >
              {t.label}
            </div>
          ))}
        </div>
      </div>

      {researchTool === 'brand' && <BrandForge />}
      {researchTool === 'scout' && <TopicScout />}
      {researchTool === 'leads' && <LeadGenPanel />}
      {researchTool === 'influencers' && <InfluencerDb />}

      <div style={{ display: researchTool === 'plan' ? 'flex' : 'none', gap: 24, alignItems: 'flex-start' }}>
        <div style={{ width: 400, flexShrink: 0, ...card, display: 'flex', flexDirection: 'column', gap: 17 }}>
          <div style={sectionEyebrow}>Brief</div>

          <div>
            <label style={label}>Business / product name</label>
            <input
              value={fields.name}
              onChange={(e) => setPlanField('name', e.target.value)}
              placeholder="e.g. Lumen Analytics (optional, for your Library)"
              style={textInput}
            />
          </div>

          <div>
            <label style={label}>Product / service description</label>
            <textarea
              value={fields.productDescription}
              onChange={(e) => setPlanField('productDescription', e.target.value)}
              placeholder="e.g. Handmade full-grain leather laptop bags, sold direct-to-consumer online."
              rows={4}
              style={textarea}
            />
          </div>

          <div>
            <label style={label}>Monthly budget (USD)</label>
            <input
              type="number"
              min={0}
              step={100}
              value={fields.budgetUsdPerMonth}
              onChange={(e) => setPlanField('budgetUsdPerMonth', Number(e.target.value))}
              style={textInput}
            />
          </div>

          <div>
            <label style={label}>Available manpower</label>
            <textarea
              value={fields.manpowerSummary}
              onChange={(e) => setPlanField('manpowerSummary', e.target.value)}
              placeholder="e.g. 2 people: 1 generalist marketer full-time, 1 designer 10 hrs/week"
              rows={2}
              style={textarea}
            />
          </div>

          <div>
            <label style={label}>Industry</label>
            <select value={fields.industryKey} onChange={(e) => setPlanField('industryKey', e.target.value)} style={select}>
              {PLAN_INDUSTRY_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label style={label}>Geography (country code, optional)</label>
            <input value={fields.geo} onChange={(e) => setPlanField('geo', e.target.value)} placeholder="e.g. US" style={textInput} />
          </div>

          <div>
            <label style={label}>Model</label>
            <select value={fields.model} onChange={(e) => setPlanField('model', e.target.value)} style={select}>
              {PLAN_MODEL_OPTIONS.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          </div>

          <div style={{ ...primaryButton, opacity: loading ? 0.6 : 1 }} onClick={loading ? undefined : handleGenerate}>
            {loading ? 'Generating… this can take a few minutes' : 'Generate plan'}
          </div>
          {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a' }}>{error}</div>}
        </div>

        <div style={{ flex: 1 }}>
          {result && (
            <div style={{ ...card, padding: '34px 38px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <div style={sectionEyebrow}>Marketing plan</div>
                  <h1 style={{ font: "700 30px/1.2 'Kalam'", color: 'var(--ink)', margin: '8px 0 0' }}>
                    {fields.name || 'Your business'} — Growth Plan
                  </h1>
                </div>
              </div>
              <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 8 }}>
                Keyword data: {result.keywordSourceNote}
              </div>

              <div style={{ display: 'flex', gap: 6, marginTop: 18, background: 'var(--surface)', border: '2px solid var(--border)', borderRadius: 12, padding: 5, width: 'fit-content' }}>
                {TABS.map((t) => (
                  <div
                    key={t.key}
                    style={{
                      padding: '7px 14px',
                      borderRadius: 9,
                      font: "700 12.5px 'Quicksand'",
                      cursor: 'pointer',
                      ...(tab === t.key ? { background: 'var(--accent)', color: 'var(--accent-ink)' } : { color: 'var(--ink-muted)' })
                    }}
                    onClick={() => setTab(t.key)}
                  >
                    {t.label}
                  </div>
                ))}
              </div>

              <div style={{ marginTop: 6 }}>
                <MarkdownPanel markdown={tabMarkdown[tab]} />
              </div>

              <div
                style={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  gap: 10,
                  marginTop: 26,
                  paddingTop: 20,
                  borderTop: '2px dashed var(--border-soft)'
                }}
              >
                <SaveButton
                  libraryId={result.libraryId}
                  tool="Plan"
                  title={`${fields.name || 'Your business'} — Growth Plan`}
                  subtitle="Marketing plan"
                  content={result.markdown}
                />
                <div style={primaryButtonSmall} onClick={() => setShowSend(true)}>
                  Send to Distribution →
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
      {showSend && result && (
        <SendToDistributionModal
          libraryItemId={result.libraryId}
          title={`${fields.name || 'Your business'} — Growth Plan`}
          defaultText={result.socialMarkdown || result.markdown}
          onClose={() => setShowSend(false)}
        />
      )}
    </div>
  )
}
