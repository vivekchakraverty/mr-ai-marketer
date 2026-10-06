import { useEffect, useMemo, useState } from 'react'
import { buyerPersonas2Api, type Evidence, type Persona2, type Persona2Context, type Persona2Job, type Persona2Source } from '../api/buyerPersonas2'
import { useAppStore } from '../state/store'
import { DEFAULT_PLAN_FIELDS } from '../state/types'
import { notifyPersonaJob } from '../state/notifications'
import { card, primaryButtonSmall, secondaryButtonSmall, sectionEyebrow, select } from '../styles/styleKit'
import './BuyerPersonas2.css'

const STAGES = ['Awareness', 'Interest', 'Evaluation', 'Conversion', 'Retention', 'Advocacy']
const STRATEGY_LABELS: Record<string, string> = {
  audience_priorities: 'Audience priorities', positioning: 'Positioning', product: 'Product', content: 'Content',
  channels: 'Channels', community: 'Community', launch: 'Launch', risks: 'Risks', experiments: 'Experiments to run'
}
function list(items: string[] | undefined): React.JSX.Element {
  return <ul className="bp2-list">{(items || []).map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
}
function evidenceLink(item: Evidence): React.JSX.Element {
  const url = item.source_url.startsWith('https://') ? item.source_url : ''
  return <div className="bp2-evidence" key={item.id}>
    <div><strong>{item.source_name || item.publisher || item.type}</strong> <span className="bp2-chip">{item.type}</span> <span className="bp2-muted">{item.source_quality ? `Quality ${item.source_quality}/100` : ''}</span></div>
    <div>{item.claim}</div>
    {url && <a href={url} target="_blank" rel="noopener noreferrer">View source ↗</a>}
    {item.publisher && <small>Domain: {item.publisher}</small>}
    {item.type === 'external' && item.relevance && <small>Research question: {item.relevance}</small>}
    {item.published_at && <small>Published {item.published_at}</small>}
    {item.retrieved_at && <small>Retrieved {new Date(item.retrieved_at).toLocaleDateString()}</small>}
  </div>
}

function PersonaDetail({ persona, evidence, clusters, signals, sourceKey }: { persona: Persona2; evidence: Evidence[]; clusters: Persona2Context['clusters']; signals: Persona2Context['audience_signals']; sourceKey: string }): React.JSX.Element {
  const linked = evidence.filter((item) => persona.evidence_ids.includes(item.id) || persona.cluster_ids.includes(item.id))
  const platforms = persona.channels.filter((item) => item.affinity === 'High')
  return <article className="bp2-persona" id={`bp2-${persona.id}`}>
    <div className="bp2-persona-head"><div><div className="bp2-eyebrow">{persona.priority} · {persona.confidence} confidence</div><h3>{persona.archetype || persona.name}</h3><p>{persona.description}</p><small>{persona.name} is a fictional mnemonic, not a real person.</small></div><div className="bp2-score"><strong>{persona.relevance_score}</strong><span>Relevance / 100</span></div></div>
    <details className="bp2-detail"><summary>Why this relevance score?</summary><div className="bp2-score-grid">{Object.entries(persona.score_components).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><meter min="0" max="100" value={value} /> <b>{value}</b></div>)}</div><p className="bp2-muted">Weighted from Align (35%), project fit (22%), external evidence (15%), channels (10%), behavior (10%), and commercial signals (8%). Scores are heuristic fit indicators.</p></details>
    <div className="bp2-grid">
      <section><h4>Snapshot</h4><dl className="bp2-facts">{Object.entries(persona.snapshot).filter(([, value]) => value).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{value}</dd></div>)}</dl></section>
      <section><h4>Jobs to be done</h4>{Object.entries(persona.jobs_to_be_done).map(([key, values]) => <div key={key}><strong>{key.replaceAll('_', ' ')}</strong>{list(values)}</div>)}</section>
      <section><h4>Ranked motivations</h4>{[...persona.motivations].sort((a, b) => a.rank - b.rank).map((item) => <p key={item.name}><span className="bp2-chip">#{item.rank}</span> <strong>{item.name}</strong> — {item.why}</p>)}</section>
      <section><h4>Pain points</h4>{persona.pain_points.map((item) => <p key={item.text}><span className="bp2-chip">{item.importance}</span> {item.text}</p>)}</section>
      <section><h4>Purchase and adoption triggers</h4>{Object.entries(persona.adoption_triggers).map(([stage, values]) => <div key={stage}><strong>{stage}</strong>{list(values)}</div>)}</section>
      <section><h4>Likely objections and responses</h4>{persona.objections.map((item) => <div className="bp2-pair" key={item.objection}><strong>{item.objection}</strong><p>Address it: {item.response}</p></div>)}</section>
    </div>
    <details className="bp2-detail" open><summary>Discovery journey</summary><div className="bp2-journey">{STAGES.map((stage) => { const item = persona.discovery_journey.find((part) => part.stage === stage); return item ? <div key={stage}><strong>{stage}</strong><p><b>Touchpoints:</b> {item.touchpoints.join(', ')}</p><p><b>Questions:</b> {item.questions.join(' · ')}</p><p><b>Content:</b> {item.content.join(', ')}</p><p><b>Channels:</b> {item.channels.join(', ')}</p><p><b>Proof:</b> {item.proof}</p><p><b>Friction:</b> {item.friction}</p></div> : null })}</div></details>
    <div className="bp2-grid">
      <section><h4>Channel and platform affinity</h4>{persona.channels.map((item) => <div className="bp2-pair" key={item.name}><strong>{item.name} <span className="bp2-chip">{item.affinity}</span></strong><p>{item.why}</p></div>)}{!platforms.length && <p className="bp2-muted">No high affinity channel is established yet.</p>}</section>
      <section><h4>Content preferences</h4><dl className="bp2-facts">{Object.entries(persona.content_preferences).filter(([key]) => key !== 'conversion_examples').map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{Array.isArray(value) ? value.join(', ') : value}</dd></div>)}</dl><h5>Content most likely to convert</h5>{list(persona.content_preferences.conversion_examples)}</section>
      <section><h4>Messaging</h4><p><b>Core message:</b> {persona.messaging.core_message}</p><p><b>Value proposition:</b> {persona.messaging.value_proposition}</p><b>Pillars</b>{list(persona.messaging.pillars)}<p><b>Words/themes that resonate:</b> {persona.messaging.resonant_words.join(', ')}</p><p><b>Avoid:</b> {persona.messaging.avoid_words.join(', ')}</p><b>Example hooks</b>{list(persona.messaging.hooks)}</section>
      <section><h4>Comparable interests and affinities</h4>{persona.affinities.map((item) => <div className="bp2-pair" key={item.item}><strong>{item.item}</strong> <span className="bp2-chip">{item.confidence}</span><p>{item.why}</p><small>{item.category} · {item.source}</small></div>)}<h4>Where to reach them</h4>{persona.reach.map((item) => <p key={item.place}><strong>{item.place}</strong> · {item.kind} — {item.why} {item.source_url.startsWith('https://') && <a href={item.source_url} target="_blank" rel="noopener noreferrer">Source ↗</a>}</p>)}</section>
    </div>
    <details className="bp2-detail"><summary>Evidence and audience traceability</summary><p><b>Derived from saved clusters:</b> {persona.cluster_ids.map((id) => clusters.find((item) => item.id === id)?.label || id).join(', ') || 'No saved audience cluster linked'}</p><p><b>Contributing signals:</b> {(persona.signal_ids || []).map((id) => signals.find((item) => item.id === id)?.claim || id).join(' · ') || 'No detailed signal linked'}</p>{(['project', 'align', 'external', 'inference'] as const).map((type) => <div key={type}><h5>{type === 'align' ? sourceKey.startsWith('persona:') ? 'Saved audience analysis' : 'Align audience mapping' : type === 'project' ? 'Project data' : type === 'external' ? 'External research' : 'AI inference'}</h5>{linked.filter((item) => item.type === type).map(evidenceLink)}{!linked.some((item) => item.type === type) && <p className="bp2-muted">No {type} evidence linked.</p>}</div>)}</details>
    <details className="bp2-detail"><summary>Actions for this persona</summary>{list(persona.strategic_recommendations)}</details>
  </article>
}

export default function BuyerPersonas2(): React.JSX.Element {
  const plan = useAppStore((state) => state.fields.plan)
  const project = useMemo(() => ({ name: plan.name, product_description: plan.productDescription, geo: plan.geo,
    industry: plan.industryKey === DEFAULT_PLAN_FIELDS.industryKey ? '' : plan.industryKey,
    marketing_resources: plan.manpowerSummary,
    marketing_budget: plan.budgetUsdPerMonth === DEFAULT_PLAN_FIELDS.budgetUsdPerMonth ? '' : `$${plan.budgetUsdPerMonth}/month` }),
  [plan.name, plan.productDescription, plan.geo, plan.industryKey, plan.manpowerSummary, plan.budgetUsdPerMonth])
  const [sources, setSources] = useState<Persona2Source[]>([])
  const [sourceKey, setSourceKey] = useState('')
  const [context, setContext] = useState<Persona2Context | null>(null)
  const [job, setJob] = useState<Persona2Job | null>(null)
  const [report, setReport] = useState<Persona2Job['report']>(null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const [selectedId, setSelectedId] = useState('')

  useEffect(() => { if (job) notifyPersonaJob(job) }, [job?.id, job?.status, job?.error])

  useEffect(() => { void buyerPersonas2Api.sources().then(({ sources: found }) => { setSources(found); if (found[0]) setSourceKey(found[0].key) }).catch((err) => setError(String(err))) }, [])
  useEffect(() => {
    let alive = true
    setContext(null); setJob(null); setReport(null); setError('')
    void Promise.all([buyerPersonas2Api.context(sourceKey), buyerPersonas2Api.latest(sourceKey)]).then(([input, latest]) => {
      if (!alive) return
      const saved = latest.job?.report || latest.previous_report?.report || null
      setContext(input); setJob(latest.job); setReport(saved); setSelectedId(saved?.personas[0]?.id || '')
      if (latest.job?.status === 'error') setError(latest.job.error)
    }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)) })
    return () => { alive = false }
  }, [sourceKey])
  useEffect(() => {
    if (!job || !['queued', 'researching', 'generating'].includes(job.status)) return
    const timer = window.setInterval(() => { void buyerPersonas2Api.get(job.id).then((next) => {
      setJob(next)
      if (next.report) { setReport(next.report); setSelectedId(next.report.personas[0]?.id || '') }
      if (next.status === 'error') setError(next.error)
    }).catch((err) => setError(err instanceof Error ? err.message : String(err))) }, 1800)
    return () => window.clearInterval(timer)
  }, [job?.id, job?.status])

  async function generate(regenerate: boolean, refresh: boolean): Promise<void> {
    setStarting(true); setError('')
    try {
      const next = await buyerPersonas2Api.generate(sourceKey, project, regenerate, refresh)
      setJob(next)
      if (next.report) { setReport(next.report); setSelectedId(next.report.personas[0]?.id || '') }
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setStarting(false) }
  }

  const busy = starting || !!job && ['queued', 'researching', 'generating'].includes(job.status)
  const active = report?.personas.find((item) => item.id === selectedId) || report?.personas[0]
  const platforms = Array.from(new Set(report?.personas.flatMap((item) => item.channels.map((channel) => channel.name)) || [])).slice(0, 8)
  const inputFields = { ...context?.project, ...Object.fromEntries(Object.entries(project).filter(([, value]) => value?.trim())) }
  const inputOrigins = { ...context?.project_origin, ...Object.fromEntries(Object.entries(project).filter(([, value]) => value?.trim()).map(([key]) => [key, 'project'])) }
  const projectInputKeys = Object.keys(inputFields).filter((key) => inputFields[key] && inputOrigins[key] === 'project')
  const alignProfileKeys = Object.keys(inputFields).filter((key) => inputFields[key] && inputOrigins[key] === 'align')
  const inputsChanged = !!report && !!context && (
    report.source_snapshot.source_key !== sourceKey ||
    Object.entries(inputFields).some(([key, value]) => value && report.source_snapshot.project[key] !== value) ||
    (!!context.source_updated_at && report.source_snapshot.source_updated_at !== context.source_updated_at)
  )
  const researchDate = report?.researched_at ? new Date(report.researched_at) : null
  const researchAge = researchDate && !Number.isNaN(researchDate.getTime()) ? Math.max(0, Math.floor((Date.now() - researchDate.getTime()) / 86_400_000)) : null
  const researchUpdated = researchAge === null ? 'Not yet researched' : researchAge === 0 ? 'Updated today' : researchAge === 1 ? 'Updated yesterday' : `Updated ${researchAge} days ago`
  return <div className="bp2-root">
    <section style={{ ...card, padding: 22 }}>
      <div style={sectionEyebrow}>Evidence-based audience strategy</div>
      <p className="bp2-intro">Generate evidence-based buyer personas using your project information, Align audience mapping, and external market research.</p>
      <label className="bp2-source"><strong>Audience source</strong><select style={select} value={sourceKey} onChange={(event) => setSourceKey(event.target.value)}><option value="">Project information only</option>{sources.map((source) => <option value={source.key} key={source.key}>{source.kind}: {source.label}</option>)}</select></label>
      <div className="bp2-inputs"><h3>Inputs used</h3><div className="bp2-input-grid"><div><b>Project information</b>{projectInputKeys.map((key) => <p key={key}>✓ {key.replaceAll('_', ' ')}</p>)}{!!context?.project_details?.length && <p>✓ {context.project_details.length} saved questionnaire answers</p>}{!projectInputKeys.length && !context?.project_details?.length && <p>None available</p>}</div><div><b>Align audience mapping</b><p>{context?.clusters.length ? `✓ ${context.clusters.length} audience cluster${context.clusters.length === 1 ? '' : 's'}` : 'Not generated for this source'}</p><p>{context?.audience_signals?.length || 0} detailed signals · {context?.affinities.length || 0} comparable affinities · {context?.platforms.length || 0} channel signals</p>{alignProfileKeys.length > 0 && <p>Work profile: {alignProfileKeys.map((key) => key.replaceAll('_', ' ')).join(', ')}</p>}</div><div><b>External research</b><p>{researchUpdated}</p><p>{report?.evidence.filter((item) => item.type === 'external').length || 0} verified sources</p></div></div></div>
      {context && !context.clusters.length && <p className="bp2-note">Audience Mapping has not been generated yet. Personas can still be created, but running an audience analysis in Align will improve them.</p>}
      {inputsChanged && <p className="bp2-note">Project or Align inputs have changed since this report was generated. Regenerate to use the latest evidence.</p>}
      <div className="bp2-actions"><button type="button" style={primaryButtonSmall} disabled={busy || !context} onClick={() => void generate(!!report, false)}>{busy ? job?.status === 'researching' ? 'Researching…' : 'Generating…' : report ? 'Regenerate Personas' : 'Generate Buyer Personas'}</button>{report && <button type="button" style={secondaryButtonSmall} disabled={busy} onClick={() => void generate(true, true)}>Refresh Research</button>}</div>
      {error && <p role="alert" className="bp2-error">{error}</p>}
      {job?.status === 'error' && report && <p className="bp2-note">Your previous report remains available. The latest attempt failed.</p>}
    </section>
    {report && <>
      {report.warnings.map((warning, index) => <p className="bp2-note" key={index}>{warning}</p>)}
      {report.conflicts.length > 0 && <section style={{ ...card, marginTop: 16 }}><h3>Potential audience assumption mismatch</h3>{list(report.conflicts)}</section>}
      <section style={{ ...card, marginTop: 16, overflowX: 'auto' }}><div style={sectionEyebrow}>At a glance</div><h2>Compare personas</h2><table className="bp2-table"><thead><tr><th>Persona</th><th>Priority</th><th>Relevance</th><th>Main motivation</th><th>Biggest pain</th><th>Conversion trigger</th><th>Best platform</th><th>Best content</th><th>Purchase barrier</th><th>Confidence</th></tr></thead><tbody>{report.personas.map((item) => <tr key={item.id}><th><button className="bp2-text-button" onClick={() => setSelectedId(item.id)}>{item.archetype}</button></th><td>{item.priority}</td><td>{item.relevance_score}/100</td><td>{item.motivations[0]?.name || '—'}</td><td>{item.pain_points[0]?.text || '—'}</td><td>{item.adoption_triggers.purchase?.[0] || '—'}</td><td>{item.channels.find((channel) => channel.affinity === 'High')?.name || '—'}</td><td>{item.content_preferences.conversion_examples[0] || '—'}</td><td>{item.objections[0]?.objection || '—'}</td><td>{item.confidence}</td></tr>)}</tbody></table></section>
      <div className="bp2-tabs" role="tablist" aria-label="Generated personas">{report.personas.map((item) => <button type="button" role="tab" aria-selected={active?.id === item.id} key={item.id} onClick={() => setSelectedId(item.id)}>{item.archetype}</button>)}</div>
      {active && <PersonaDetail persona={active} evidence={report.evidence} clusters={report.source_snapshot.clusters} signals={report.source_snapshot.audience_signals || []} sourceKey={report.source_key} />}
      <section style={{ ...card, marginTop: 18 }}><div style={sectionEyebrow}>Cross-persona planning</div><h2>Persona × platform</h2><div className="bp2-scroll"><table className="bp2-table"><thead><tr><th>Persona</th>{platforms.map((platform) => <th key={platform}>{platform}</th>)}</tr></thead><tbody>{report.personas.map((persona) => <tr key={persona.id}><th>{persona.archetype}</th>{platforms.map((platform) => <td key={platform}>{persona.channels.find((item) => item.name === platform)?.affinity || '—'}</td>)}</tr>)}</tbody></table></div><h2>Persona × message</h2><div className="bp2-scroll"><table className="bp2-table"><thead><tr><th>Persona</th><th>Core need</th><th>Best message</th><th>Strongest proof</th><th>Primary objection</th></tr></thead><tbody>{report.personas.map((persona) => <tr key={persona.id}><th>{persona.archetype}</th><td>{persona.jobs_to_be_done.functional?.[0] || '—'}</td><td>{persona.messaging.core_message}</td><td>{persona.content_preferences.proof}</td><td>{persona.objections[0]?.objection || '—'}</td></tr>)}</tbody></table></div></section>
      <section style={{ ...card, marginTop: 18 }}><div style={sectionEyebrow}>What this means for your strategy</div><div className="bp2-grid">{Object.entries(STRATEGY_LABELS).map(([key, title]) => <div key={key}><h3>{title}</h3>{list(report.strategy[key])}</div>)}</div></section>
      <details className="bp2-detail"><summary>Methodology and all sources</summary><p>{report.methodology}</p><p className="bp2-muted">Model: {report.model} · Generated {new Date(report.created_at).toLocaleString()} · Research {new Date(report.researched_at).toLocaleString()}</p>{report.evidence.map(evidenceLink)}</details>
    </>}
  </div>
}
