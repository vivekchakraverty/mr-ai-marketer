import { useEffect, useRef, useState } from 'react'
import { personaApi, type Candidate, type Claim, type PersonaQuestion, type PersonaRun } from '../api/personas'
import { card, primaryButtonSmall, secondaryButtonSmall, sectionEyebrow, select, textInput, textarea } from '../styles/styleKit'
import './AudiencePersonas.css'

const STEPS = ['Interview', 'Research plan', 'Collect & analyze', 'Review', 'Personas']
const button = { ...secondaryButtonSmall, padding: '8px 14px' }

function download(name: string, text: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

function asLines(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value.join('\n') : value ?? ''
}

function Question({ question, value, onChange }: {
  question: PersonaQuestion
  value: string | string[] | undefined
  onChange: (value: string | string[]) => void
}): React.JSX.Element {
  const options = question.options ?? []
  return <div className="persona-question">
    <label htmlFor={`persona-${question.id}`} className="persona-label">{question.id} · {question.prompt}{question.required && <span aria-label="required"> *</span>}</label>
    <div className="persona-help">{question.help}</div>
    {question.type === 'single' ? <select id={`persona-${question.id}`} style={select} value={asLines(value)} onChange={e => onChange(e.target.value)}>
      <option value="">Choose an answer</option>{options.map(option => <option key={option}>{option}</option>)}<option>Don't know</option>
    </select> : question.type === 'multi' ? <div id={`persona-${question.id}`} className="persona-choices">
      {options.map(option => <label key={option}><input type="checkbox" checked={Array.isArray(value) && value.includes(option)} onChange={e => onChange(e.target.checked ? [...(Array.isArray(value) ? value : []), option] : (Array.isArray(value) ? value : []).filter(v => v !== option))} /> {option}</label>)}
      <label><input type="checkbox" checked={Array.isArray(value) && value.includes("Don't know")} onChange={e => onChange(e.target.checked ? [...(Array.isArray(value) ? value : []), "Don't know"] : (Array.isArray(value) ? value : []).filter(v => v !== "Don't know"))} /> Don't know</label>
    </div> : question.type === 'list' ? <textarea id={`persona-${question.id}`} style={{ ...textarea, minHeight: 80 }} value={asLines(value)} placeholder={question.example ?? 'One item per line'} onChange={e => onChange(e.target.value.split('\n'))} /> :
      <input id={`persona-${question.id}`} style={textInput} type={question.type === 'number' || question.type === 'scale' ? 'number' : 'text'} value={asLines(value)} placeholder={question.example ?? ''} onChange={e => onChange(e.target.value)} />}
    {question.type !== 'single' && question.type !== 'multi' && <button type="button" className="persona-unknown" onClick={() => onChange("Don't know")}>Don't know</button>}
  </div>
}

function ClaimList({ title, claims, onEvidence }: { title: string; claims: Claim[]; onEvidence: (ids: string[]) => void }): React.JSX.Element {
  return <div className="persona-claim-list"><strong>{title}</strong>
    {claims.length ? <ul>{claims.map((claim, index) => <li key={`${title}-${index}`}>
      <button type="button" className="persona-claim" onClick={() => onEvidence(claim.evidence_ids)}>{claim.text}</button>
      <span className="persona-origin">{claim.origin === 'evidence' ? `${claim.evidence_ids.length} evidence link(s)` : claim.origin}</span>
    </li>)}</ul> : <p className="persona-muted">To validate</p>}
  </div>
}

export default function AudiencePersonas(): React.JSX.Element {
  const [questions, setQuestions] = useState<PersonaQuestion[]>([])
  const [runs, setRuns] = useState<Pick<PersonaRun, 'id' | 'name' | 'step' | 'status' | 'updated_at'>[]>([])
  const [run, setRun] = useState<PersonaRun | null>(null)
  const [name, setName] = useState('Audience research')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [terms, setTerms] = useState(false)
  const [csvText, setCsvText] = useState('')
  const [csvColumns, setCsvColumns] = useState<string[]>([])
  const [csvMapping, setCsvMapping] = useState<Record<string, string>>({})
  const [splitPhrases, setSplitPhrases] = useState<Record<string, string>>({})
  const [evidenceIds, setEvidenceIds] = useState<string[] | null>(null)
  const saveQueue = useRef<Promise<unknown>>(Promise.resolve())

  async function refreshList(): Promise<void> {
    setRuns((await personaApi.list()).runs)
  }
  useEffect(() => {
    void Promise.all([personaApi.questions(), personaApi.list()]).then(([q, list]) => {
      setQuestions(q.questions)
      setRuns(list.runs)
      if (list.runs[0]) void personaApi.get(list.runs[0].id).then(setRun)
    }).catch(e => setError(String(e)))
  }, [])
  useEffect(() => {
    if (!run || !['queued', 'collecting', 'cancelling'].includes(run.status)) return
    const timer = setInterval(() => {
      void personaApi.get(run.id).then(next => {
        setRun(next)
        if (!['queued', 'collecting', 'cancelling'].includes(next.status)) void refreshList()
      }).catch(e => setError(String(e)))
    }, 1500)
    return () => clearInterval(timer)
  }, [run?.id, run?.status])
  useEffect(() => {
    if (evidenceIds === null) return
    const closeOnEscape = (event: KeyboardEvent): void => { if (event.key === 'Escape') setEvidenceIds(null) }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [evidenceIds])

  async function action(work: () => Promise<PersonaRun>): Promise<void> {
    setBusy(true); setError(''); setNote('')
    try { const next = await work(); setRun(next); await refreshList() }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  function changeAnswer(id: string, value: string | string[]): void {
    if (!run) return
    const answers = { ...run.answers, [id]: value }
    setRun({ ...run, answers })
    saveQueue.current = saveQueue.current.catch(() => undefined)
      .then(() => personaApi.save(run.id, { answers: { [id]: value } }))
      .catch(e => setError(`Autosave failed: ${String(e)}`))
  }
  async function savePending(): Promise<void> {
    if (!run) return
    await saveQueue.current
    await personaApi.save(run.id, { mode: run.mode })
  }
  function updatePlan(patch: NonNullable<PersonaRun['plan']>): void {
    if (!run) return
    setRun({ ...run, plan: patch })
  }
  async function persistPlan(): Promise<void> {
    if (run?.plan) await personaApi.save(run.id, { plan: run.plan })
  }
  async function collectApproved(): Promise<PersonaRun> {
    if (!run) throw new Error('Choose a run first.')
    await persistPlan()
    const settings = await window.api?.settings.getAll()
    const host = settings?.mastodonInstance ?? settings?.mastodonAccounts?.[0]?.instance ?? ''
    const token = settings?.mastodonAccessToken ?? settings?.mastodonAccounts?.find(a => a.instance === host)?.accessToken ?? ''
    return personaApi.collect(run.id, terms, {
      youtube_key: settings?.youtubeApiKey ?? '', mastodon_host: host, mastodon_token: token
    })
  }
  async function chooseFile(file?: File): Promise<void> {
    if (!file) return
    if (file.size > 2_000_000) { setError('Choose a CSV smaller than 2 MB.'); return }
    const text = await file.text()
    const headers = text.split(/\r?\n/, 1)[0].split(',').map(s => s.replace(/^"|"$/g, '').trim())
    setCsvText(text); setCsvColumns(headers)
    setCsvMapping({ text: headers.find(h => /text|comment|question|review|body/i.test(h)) ?? headers[0],
      source: headers.find(h => /source|channel/i.test(h)) ?? '', date: headers.find(h => /date|time/i.test(h)) ?? '' })
  }
  function editCandidate(index: number, patch: Partial<Candidate>): void {
    if (!run?.analysis) return
    const candidates = run.analysis.candidates.map((candidate, i) => i === index ? { ...candidate, ...patch } : candidate)
    setRun({ ...run, analysis: { ...run.analysis, candidates } })
  }
  function editPersona(id: string, field: 'label' | 'summary', value: string): void {
    if (!run) return
    setRun({ ...run, personas: run.personas.map(p => p.id === id ? { ...p, [field]: value } : p) })
  }
  const visibleQuestions = questions.filter(q => run && (run.mode === 'full' || q.core) &&
    (!q.condition || run.answers.A2 === q.condition || run.answers.A2 === 'Both'))
  const evidence = evidenceIds === null ? [] : run?.evidence.filter(unit => evidenceIds.includes(unit.id)) ?? []
  const csvImportPanel = run && <div className="persona-import" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void chooseFile(e.dataTransfer.files[0]) }}>
    <label htmlFor="persona-file">Import your CSV (drag here or choose a file)</label>
    <input id="persona-file" type="file" accept=".csv,text/csv" onChange={e => void chooseFile(e.target.files?.[0])} />
    {csvColumns.length > 0 && <div className="persona-mapping">{(['text', 'source', 'date'] as const).map(key => <label key={key}>{key} column
      <select style={select} value={csvMapping[key] ?? ''} onChange={e => setCsvMapping({ ...csvMapping, [key]: e.target.value })}>
        <option value="">None</option>{csvColumns.map(column => <option key={column} value={column}>{column}</option>)}
      </select></label>)}<button type="button" style={button} disabled={busy || ['queued', 'collecting', 'cancelling'].includes(run.status)} onClick={() => void action(async () => { await persistPlan(); return personaApi.importCsv(run.id, csvText, csvMapping) })}>Import anonymized rows</button></div>}
    <small>{run.imported.length} imported evidence units saved. Names, handles, email, phone and profile links are removed before storage. Import only data you may analyze.</small>
  </div>

  return <section className="persona-root" aria-label="Audience Personas">
    <div className="persona-top">
      <div><div style={sectionEyebrow}>Audience research</div><h2>Audience Personas</h2>
        <p>Build evidence-linked buyer profiles from your own data and approved public sources.</p></div>
      <div className="persona-run-controls">
        <label htmlFor="persona-run-select">Saved runs</label>
        <select id="persona-run-select" style={select} value={run?.id ?? ''} onChange={e => void action(() => personaApi.get(e.target.value))}>
          {!run && <option value="">Choose a run</option>}
          {runs.map(item => <option key={item.id} value={item.id}>{item.name} · {item.status}</option>)}
        </select>
      </div>
    </div>
    <div className="persona-create" style={card}>
      <label htmlFor="persona-run-name">New research run</label>
      <input id="persona-run-name" style={textInput} value={name} onChange={e => setName(e.target.value)} />
      <button type="button" style={primaryButtonSmall} disabled={busy || !name.trim()} onClick={() => void action(() => personaApi.create(name.trim(), false))}>Start interview</button>
      <button type="button" style={button} disabled={busy} onClick={() => void action(() => personaApi.create('Demo · fictional shop software', true))}>Try offline demo</button>
    </div>
    {error && <div className="persona-error" role="alert">{error}</div>}
    {note && <div className="persona-note" role="status">{note}</div>}
    {!run ? <div className="persona-empty">Start a run to interview your team, or try the fictional offline demo.</div> : <>
      <nav className="persona-stepper" aria-label="Persona steps">{STEPS.map((step, index) => <button key={step} type="button" className={run.step === index + 1 ? 'active' : ''}
        aria-current={run.step === index + 1 ? 'step' : undefined} disabled={index + 1 > run.step || busy || run.status === 'collecting'}
        onClick={() => void action(() => personaApi.save(run.id, { step: index + 1 }))}><span>{index + 1}</span>{step}</button>)}</nav>
      <div className="persona-progress" role="progressbar" aria-valuenow={run.step} aria-valuemin={1} aria-valuemax={5} aria-label="Wizard progress"><span style={{ width: `${run.step * 20}%` }} /></div>

      {run.step === 1 && <div className="persona-panel" style={card}>
        <h3>1. Interview</h3><p className="persona-muted">Answers save automatically. Avoid names, emails and other personal details.</p>
        <div className="persona-mode"><button type="button" className={run.mode === 'quick' ? 'active' : ''} onClick={() => { setRun({ ...run, mode: 'quick' }); void personaApi.save(run.id, { mode: 'quick' }) }}>Quick · core questions</button>
          <button type="button" className={run.mode === 'full' ? 'active' : ''} onClick={() => { setRun({ ...run, mode: 'full' }); void personaApi.save(run.id, { mode: 'full' }) }}>Full interview</button></div>
        {visibleQuestions.map((q, index) => <div key={q.id}>{index === 0 || visibleQuestions[index - 1].section !== q.section ? <h4>{q.section}</h4> : null}<Question question={q} value={run.answers[q.id]} onChange={value => changeAnswer(q.id, value)} /></div>)}
        <div className="persona-actions"><button type="button" style={primaryButtonSmall} disabled={busy} onClick={() => void action(async () => { await savePending(); return personaApi.plan(run.id) })}>Build research plan →</button></div>
      </div>}

      {run.step === 2 && <div className="persona-panel" style={card}>
        <h3>2. Research plan</h3><p className="persona-muted">Edit queries and choose sources. Only sources you enable will run after approval.</p>
        {!run.plan ? <div className="persona-empty">The plan is empty. Return to the interview.</div> : <>
          <div className="persona-seeds"><strong>Seeds</strong> {run.plan.seeds.map(seed => <span key={seed}>{seed}</span>)}</div>
          <label className="persona-label" htmlFor="persona-queries">Search queries · one per line ({run.plan.queries.length})</label>
          <textarea id="persona-queries" style={{ ...textarea, minHeight: 140 }} value={run.plan.queries.join('\n')} onChange={e => updatePlan({ ...run.plan!, queries: e.target.value.split('\n').filter(Boolean).slice(0, 60) })} />
          <label className="persona-label" htmlFor="persona-urls">Approved public websites · one HTTPS URL per line</label>
          <textarea id="persona-urls" style={{ ...textarea, minHeight: 70 }} value={run.plan.urls.join('\n')} onChange={e => updatePlan({ ...run.plan!, urls: e.target.value.split('\n').filter(Boolean).slice(0, 8) })} />
          <div className="persona-source-table" role="group" aria-label="Data sources">{run.plan.sources.map(source => <label key={source.id}>
            <input type="checkbox" checked={source.enabled} disabled={source.excluded} onChange={e => updatePlan({ ...run.plan!, sources: run.plan!.sources.map(s => s.id === source.id ? { ...s, enabled: e.target.checked } : s) })} />
            <span>{source.label}</span><small>{source.excluded ? 'excluded in interview' : source.requests ? `up to ${source.requests} requests` : 'local'}</small>
          </label>)}</div>
          {csvImportPanel}
          <label className="persona-terms"><input type="checkbox" checked={terms} onChange={e => setTerms(e.target.checked)} /> I confirm I may use the enabled public sources under their terms.</label>
          <div className="persona-actions"><button type="button" style={button} disabled={busy} onClick={() => void action(() => personaApi.save(run.id, { step: 1 }))}>← Back</button>
            <button type="button" style={primaryButtonSmall} disabled={busy || (run.plan.sources.some(s => s.enabled && !['demo', 'first_party'].includes(s.id)) && !terms)} onClick={() => void action(collectApproved)}>Approve & run →</button></div>
        </>}
      </div>}

      {run.step === 3 && <div className="persona-panel" style={card}>
        <h3>3. Collect & analyze</h3><p className="persona-muted">Each source runs independently. Collection can be cancelled; no partial personas will be published.</p>
        <div className="persona-status" role="status">{run.status === 'queued' || run.status === 'collecting' || run.status === 'cancelling' ? 'Working…' : run.status === 'review' ? 'Analysis is ready to review.' : run.job_error || 'Ready to collect.'}</div>
        <div className="persona-source-table">{Object.entries(run.source_status).map(([id, status]) => <div key={id}><strong>{id}</strong><span>{status.status} · {status.count} units</span>{status.error && <small>{status.error}</small>}</div>)}</div>
        {csvImportPanel}
        <div className="persona-actions">{['queued', 'collecting', 'cancelling'].includes(run.status) ? <button type="button" style={button} onClick={() => void action(() => personaApi.cancel(run.id))}>Cancel run</button> : <>
          <button type="button" style={button} onClick={() => void action(() => personaApi.save(run.id, { step: 2 }))}>← Back</button>
          {run.analysis ? <button type="button" style={primaryButtonSmall} onClick={() => void action(() => personaApi.save(run.id, { step: 4 }))}>Review segments →</button> : <button type="button" style={primaryButtonSmall} onClick={() => void action(collectApproved)}>Run again</button>}
        </>}</div>
      </div>}

      {run.step === 4 && <div className="persona-panel" style={card}>
        <h3>4. Review candidate segments</h3><p className="persona-muted">Correct the labels and priorities. Evidence share is from this run, not a market size estimate.</p>
        {!run.analysis ? <div className="persona-empty">No analysis yet.</div> : <>
          {run.analysis.thin && <div className="persona-note">Only {run.analysis.sample_size} usable units from {run.analysis.source_count} source(s). These are hypotheses. Add first-party evidence and customer interviews.</div>}
          <div className="persona-candidates">{run.analysis.candidates.map((candidate, index) => <div key={candidate.id} className="persona-candidate">
            <label className="persona-label" htmlFor={`candidate-${index}`}>Segment label</label><input id={`candidate-${index}`} style={textInput} value={candidate.label} onChange={e => editCandidate(index, { label: e.target.value })} />
            <div className="persona-muted">{Math.round(candidate.evidence_share * 100)}% of units · Priority score {Math.round((candidate.score ?? 0) * 100)}/100 · {candidate.confidence}</div>
            {candidate.snippets.map((snippet, i) => <button key={i} type="button" className="persona-snippet" onClick={() => setEvidenceIds(snippet.evidence_ids)}>“{snippet.text}”</button>)}
            <div className="persona-review-grid">{([['rings_true', 'Rings true'], ['business_value', 'Business value'], ['priority', 'Strategic priority']] as const).map(([key, title]) => <label key={key}>{title}
              <select style={select} value={candidate.review[key]} onChange={e => editCandidate(index, { review: { ...candidate.review, [key]: Number(e.target.value) } })}>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select>
            </label>)}</div>
            <label className="persona-label">Merge with
              <select style={select} value={candidate.review.merge_with ?? ''} onChange={e => editCandidate(index, { review: { ...candidate.review, merge_with: e.target.value } })}><option value="">Keep separate</option>{run.analysis!.candidates.filter(c => c.id !== candidate.id).map(c => <option key={c.id} value={c.id}>{c.label}</option>)}</select>
            </label>
            <label className="persona-label">What is missing?<textarea style={{ ...textarea, minHeight: 65 }} value={candidate.review.missing} onChange={e => editCandidate(index, { review: { ...candidate.review, missing: e.target.value } })} /></label>
            {run.analysis!.candidates.length < 4 && <div className="persona-split"><label className="persona-label" htmlFor={`split-${candidate.id}`}>Split by a phrase found in the evidence</label>
              <input id={`split-${candidate.id}`} style={textInput} placeholder="e.g. migration" value={splitPhrases[candidate.id] ?? ''} onChange={e => setSplitPhrases({ ...splitPhrases, [candidate.id]: e.target.value })} />
              <button type="button" style={button} disabled={(splitPhrases[candidate.id] ?? '').trim().length < 2} onClick={() => void action(() => personaApi.split(run.id, candidate.id, splitPhrases[candidate.id]))}>Split evidence</button></div>}
          </div>)}</div>
          <div className="persona-actions"><button type="button" style={button} onClick={() => void action(() => personaApi.save(run.id, { step: 3 }))}>← Back</button>
            <button type="button" style={primaryButtonSmall} onClick={() => void action(() => personaApi.review(run.id, run.analysis!.candidates))}>Create personas →</button></div>
        </>}
      </div>}

      {run.step === 5 && <div className="persona-panel" style={card}>
        <h3>5. Personas</h3>{!run.personas.length ? <div className="persona-empty">Review segments to create personas.</div> : <>
          {run.analysis && <div className="persona-report"><strong>Research report</strong><p>{run.analysis.method}. {run.analysis.sample_size} usable units from {run.analysis.source_count} source(s). {run.analysis.k_reason}</p>
            <p>Source counts: {Object.entries((run.manifest.source_counts as Record<string, number> | undefined) ?? {}).map(([source, count]) => `${source} ${count}`).join(' · ') || 'No completed sources'}.</p>
            {run.analysis.limitations.map(item => <p key={item}>{item}</p>)}
            {run.diff?.previous_labels?.length ? <p>Before: {run.diff.previous_labels.join(', ')}. After: {run.diff.current_labels.join(', ')}. Evidence growth: {Math.round((run.diff.evidence_growth ?? 0) * 100)}%.</p> : null}
            {run.diff?.relative_engagement_shift != null && <p>Relative engagement shifted {Math.round(run.diff.relative_engagement_shift * 100)}% from the prior run.</p>}
            {typeof (run.manifest.signals as Record<string, unknown> | undefined)?.wikipedia === 'object' && <p>Wikipedia trend is a topic-attention signal only, not buyer evidence.</p>}
            <p>Re-run after new evidence grows by about 30%, or when relative engagement changes materially.</p></div>}
          <div className="persona-candidates">{run.personas.map(persona => <article key={persona.id} className="persona-card">
            <div className="persona-card-head"><div><div style={sectionEyebrow}>Buyer context</div><h4>{persona.label}</h4></div><span className="persona-confidence">{persona.confidence}</span></div>
            <label className="persona-label">Label<input style={textInput} value={persona.label} onChange={e => editPersona(persona.id, 'label', e.target.value)} /></label>
            <label className="persona-label">Summary<textarea style={{ ...textarea, minHeight: 65 }} value={persona.summary} onChange={e => editPersona(persona.id, 'summary', e.target.value)} /></label>
            <button type="button" style={button} onClick={() => void action(() => personaApi.edit(run.id, persona.id, persona.label, persona.summary))}>Save my edits</button>
            {persona.edited_by_you && <span className="persona-origin">Edited by you</span>}
            <p className="persona-muted">{persona.confidence_reasons.join(' · ')}</p>
            <ClaimList title="Context" claims={[persona.context]} onEvidence={setEvidenceIds} />
            <ClaimList title="Jobs to be done" claims={persona.jobs_to_be_done} onEvidence={setEvidenceIds} />
            <ClaimList title="Goals" claims={persona.goals} onEvidence={setEvidenceIds} />
            <ClaimList title="Pains" claims={persona.pains} onEvidence={setEvidenceIds} />
            <details className="persona-details"><summary>Decision, channels and messaging</summary>
              <ClaimList title="Trigger events" claims={persona.triggers} onEvidence={setEvidenceIds} />
              <ClaimList title="Objections" claims={persona.objections} onEvidence={setEvidenceIds} />
              <ClaimList title="Decision criteria" claims={persona.decision_criteria} onEvidence={setEvidenceIds} />
              <ClaimList title="Buying role" claims={[persona.buying_role]} onEvidence={setEvidenceIds} />
              <ClaimList title="Where to reach them" claims={persona.channels} onEvidence={setEvidenceIds} />
              <ClaimList title="Content preferences" claims={persona.content_preferences} onEvidence={setEvidenceIds} />
              <ClaimList title="Value propositions" claims={persona.value_props} onEvidence={setEvidenceIds} />
              <ClaimList title="Customer phrases to use" claims={persona.phrases_to_use} onEvidence={setEvidenceIds} />
              <ClaimList title="Phrases to avoid" claims={persona.phrases_to_avoid} onEvidence={setEvidenceIds} />
              <ClaimList title="Draft hook ideas" claims={persona.hook_ideas} onEvidence={setEvidenceIds} />
              <div className="persona-claim-list"><strong>KPIs to watch</strong><ul>{persona.kpis.map(kpi => <li key={kpi}>{kpi}</li>)}</ul></div>
            </details>
            <div className="persona-claim-list"><strong>Validation interviews</strong><ol>{persona.validation.interview_questions.map(q => <li key={q}>{q}</li>)}</ol></div>
            <div className="persona-claim-list"><strong>Message experiments</strong><ol>{persona.validation.experiments.map(q => <li key={q}>{q}</li>)}</ol></div>
            {persona.demographics.length > 0 && <p className="persona-muted">Demographics: {persona.demographics.map(d => `${d.text} (${d.origin})`).join('; ')}</p>}
          </article>)}</div>
          <div className="persona-actions"><button type="button" style={button} onClick={() => void action(() => personaApi.save(run.id, { step: 4 }))}>← Back</button>
            <button type="button" style={primaryButtonSmall} onClick={() => void action(() => personaApi.save(run.id, { step: 2 }))}>Refresh with new data</button>
            <button type="button" style={button} onClick={() => download(`${run.name}.json`, JSON.stringify({ personas: run.personas, analysis: run.analysis, manifest: run.manifest }, null, 2), 'application/json')}>Export JSON</button>
            <button type="button" style={button} onClick={() => download(`${run.name}.md`, run.personas.map(p => `# ${p.label}\n\n${p.summary}\n\nConfidence: ${p.confidence}\n\n## Pains\n${p.pains.map(c => `- ${c.text} [${c.evidence_ids.join(', ')}]`).join('\n')}\n\n## Goals\n${p.goals.map(c => `- ${c.text} [${c.evidence_ids.join(', ')}]`).join('\n')}`).join('\n\n'), 'text/markdown')}>Export Markdown</button>
            <button type="button" style={button} onClick={() => { void navigator.clipboard.writeText(run.personas.map(p => `${p.label}: ${p.summary}`).join('\n')); setNote('Persona summaries copied.') }}>Copy summaries</button></div>
        </>}
      </div>}

      <div className="persona-footer"><button type="button" className="persona-delete" onClick={() => { if (window.confirm(`Delete all data for “${run.name}”?`)) void (async () => { try { await personaApi.delete(run.id); setRun(null); await refreshList() } catch (e) { setError(String(e)) } })() }}>Delete this run's data</button><small>Data stays on this device. Review privacy obligations under GDPR or India's DPDP Act for your own imports.</small></div>
    </>}
    {evidenceIds !== null && <div className="persona-drawer-backdrop" onClick={() => setEvidenceIds(null)}><aside className="persona-drawer" role="dialog" aria-modal="true" aria-label="Claim evidence" onClick={e => e.stopPropagation()}>
      <button type="button" autoFocus style={button} onClick={() => setEvidenceIds(null)}>Close</button><h3>Evidence behind this claim</h3>
      {evidence.length ? evidence.map(unit => <div key={unit.id} className="persona-evidence"><strong>{unit.source} · {unit.kind}</strong><small>{unit.date} · {unit.domain}</small><p>{unit.text.split(/\s+/).slice(0, 25).join(' ')}</p>
        {typeof unit.meta.relative_engagement === 'number' && <small>{unit.meta.relative_engagement}× the preceding 30-day account median ({String(unit.meta.baseline_posts)} comparison posts)</small>}
        <small>Evidence ID: {unit.id}</small></div>) : <p>This is an assumption or draft idea. Validate it with customers.</p>}
    </aside></div>}
  </section>
}
