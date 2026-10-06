import { useEffect, useState } from 'react'
import {
  analyzeWritingFile,
  fetchWritingProfiles,
  reviewWritingProfile,
  type WritingProfile
} from '../api/client'
import AlignMusic from './AlignMusic'
import AlignVideos from './AlignVideos'
import AlignGames from './AlignGames'
import AlignVisualArt from './AlignVisualArt'
import { card, label, primaryButton, primaryButtonSmall, sectionEyebrow, textInput, textarea } from '../styles/styleKit'

const fieldStyle = { ...textInput, boxSizing: 'border-box' as const }
const textAreaStyle = { ...textarea, boxSizing: 'border-box' as const, minHeight: 100 }

function tags(value: string): string[] {
  return value.split(',').map((part) => part.trim()).filter(Boolean)
}

export default function Align(): React.JSX.Element {
  const [tab, setTab] = useState<'Writing' | 'Music' | 'Videos' | 'Games' | 'Visual Art'>('Writing')
  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '30px 34px 60px' }}>
      <div style={{ marginBottom: 22 }}>
        <div style={sectionEyebrow}>Align</div>
        <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)', marginTop: 6 }}>Find the audience who will love your work</div>
        <div style={{ font: "600 14px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4, maxWidth: 760 }}>
          Find readers for writing, explore a song's audience, compare videos, analyze a game's player fit, or explore potential visual art admirers.
        </div>
      </div>
      <div role="tablist" aria-label="Align sections" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, borderBottom: '2px solid var(--border)', marginBottom: 18 }}>
        {(['Writing', 'Music', 'Videos', 'Games', 'Visual Art'] as const).map((item) => <button key={item} type="button" role="tab" aria-selected={tab === item} onClick={() => setTab(item)} style={{ ...primaryButtonSmall, borderRadius: '12px 12px 0 0', boxShadow: 'none', background: tab === item ? 'var(--accent)' : 'var(--surface)', color: tab === item ? '#fff' : 'var(--ink-muted)' }}>{item}</button>)}
      </div>
      {tab === 'Writing' ? <WritingPanel /> : tab === 'Music' ? <AlignMusic /> : tab === 'Videos' ? <AlignVideos /> : tab === 'Games' ? <AlignGames /> : <AlignVisualArt />}
    </div>
  )
}

function WritingPanel(): React.JSX.Element {
  const [books, setBooks] = useState<WritingProfile[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [analysisMeta, setAnalysisMeta] = useState('')
  const [fingerprintText, setFingerprintText] = useState('')

  const selected = books.find((book) => book.id === selectedId) ?? null

  async function refresh(preferredId?: number): Promise<void> {
    const result = await fetchWritingProfiles()
    setBooks(result.books)
    const nextId = preferredId ?? selectedId
    if (nextId && result.books.some((book) => book.id === nextId)) setSelectedId(nextId)
    else if (result.books[0]) setSelectedId(result.books[0].id)
    else setSelectedId(null)
  }

  useEffect(() => {
    void refresh().catch((err) => setError(err instanceof Error ? err.message : String(err)))
    // First load only. Later updates use the explicit refresh calls after save/analyze.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const current = books.find((book) => book.id === selectedId)
    setFingerprintText(current ? JSON.stringify(current.fingerprint_json, null, 2) : '')
  }, [selectedId, books])

  async function handleAnalyze(): Promise<void> {
    if (!file || busy) return
    setBusy(true)
    setError('')
    setNotice('Reading the manuscript in sections and building its reader fingerprint…')
    setAnalysisMeta('')
    try {
      const result = await analyzeWritingFile(file, title)
      await refresh(result.book.id)
      setNotice('Fingerprint created. Review the details and save any changes.')
      setAnalysisMeta(`${result.chunksAnalyzed} sections · ${result.charactersAnalyzed.toLocaleString()} characters · ${result.model}`)
      setFile(null)
      setTitle('')
      const input = document.getElementById('align-writing-file') as HTMLInputElement | null
      if (input) input.value = ''
    } catch (err) {
      setNotice('')
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleSave(): Promise<void> {
    if (!selected || saving) return
    try {
      const fingerprint = JSON.parse(fingerprintText) as WritingProfile['fingerprint_json']
      if (!fingerprint || typeof fingerprint !== 'object' || Array.isArray(fingerprint)) throw new Error('Fingerprint must be a JSON object.')
      patchSelected({ fingerprint_json: fingerprint })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Fingerprint JSON is invalid.')
      return
    }
    setSaving(true)
    setError('')
    try {
      const result = await reviewWritingProfile(selected)
      await refresh(result.book.id)
      setNotice('Your reviewed writing profile is saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  function patchSelected(changes: Partial<WritingProfile>): void {
    if (!selected) return
    setBooks((current) => current.map((book) => book.id === selected.id ? { ...book, ...changes } : book))
  }

  function patchFingerprint(raw: string): void {
    if (!selected) return
    setFingerprintText(raw)
    try {
      const value: unknown = JSON.parse(raw)
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Fingerprint must be a JSON object.')
      patchSelected({ fingerprint_json: value as WritingProfile['fingerprint_json'] })
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Fingerprint JSON is invalid.')
    }
  }

  function patchMessage(message: string): void {
    if (!selected) return
    const current = selected.fingerprint_json
    const oldValue = current.themes_message
    const themesMessage = oldValue && typeof oldValue === 'object' && !Array.isArray(oldValue)
      ? { ...(oldValue as Record<string, unknown>), message }
      : { tags: selected.themes, message }
    patchSelected({ fingerprint_json: { ...current, themes_message: themesMessage } })
    setFingerprintText(JSON.stringify({ ...current, themes_message: themesMessage }, null, 2))
  }

  const plan = selected?.fingerprint_json.platform_plan ?? []

  return (
    <>
      <div style={{ ...card, marginBottom: 18 }}>
        <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)', marginBottom: 5 }}>Analyze a creative work</div>
        <div style={{ font: "600 12.5px/1.6 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 15 }}>
          Upload TXT, Markdown, DOCX, text-based PDF, or EPUB. The manuscript is read in sections and sent to Hugging Face using the token already saved in Settings. The file itself is not retained.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1fr) minmax(250px, 2fr) auto', alignItems: 'end', gap: 12 }}>
          <label>
            <span style={label}>Title (optional)</span>
            <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Use the file's title" style={fieldStyle} />
          </label>
          <label>
            <span style={label}>Manuscript file</span>
            <input
              id="align-writing-file"
              type="file"
              accept=".txt,.md,.markdown,.rst,.docx,.pdf,.epub"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              style={{ ...fieldStyle, padding: '8px 10px' }}
            />
          </label>
          <button type="button" disabled={!file || busy} onClick={() => void handleAnalyze()} style={{ ...primaryButton, padding: '11px 18px', whiteSpace: 'nowrap', opacity: !file || busy ? .6 : 1 }}>
            {busy ? 'Analyzing…' : 'Analyze work'}
          </button>
        </div>
        {(notice || error) && <div role={error ? 'alert' : 'status'} style={{ marginTop: 12, font: "700 12.5px/1.5 'Quicksand'", color: error ? '#a34a3a' : 'var(--ink-muted)' }}>{error || notice}</div>}
        {analysisMeta && <div style={{ marginTop: 5, font: "600 11px 'Quicksand'", color: 'var(--ink-faint)' }}>{analysisMeta}</div>}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(190px, .8fr) minmax(440px, 2fr)', gap: 18, alignItems: 'start' }}>
        <aside style={{ ...card, padding: 18 }}>
          <div style={{ font: "700 17px 'Kalam'", color: 'var(--ink)', marginBottom: 10 }}>Writing profiles</div>
          {books.length === 0 && <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-faint)' }}>Your analyzed works will appear here.</div>}
          <div style={{ display: 'grid', gap: 7 }}>
            {books.map((book) => (
              <button key={book.id} type="button" onClick={() => { setSelectedId(book.id); setNotice(''); setError('') }} style={{ textAlign: 'left', border: `2px solid ${book.id === selectedId ? 'var(--accent-deep)' : 'var(--border)'}`, borderRadius: 12, padding: '10px 12px', background: book.id === selectedId ? 'var(--accent-soft-bg)' : 'var(--surface)', color: 'var(--ink)', cursor: 'pointer' }}>
                <span style={{ display: 'block', font: "700 13px 'Quicksand'" }}>{book.title}</span>
                <span style={{ display: 'block', marginTop: 3, font: "600 10.5px 'Quicksand'", color: 'var(--ink-faint)' }}>{book.subgenres.slice(0, 2).join(' · ') || 'Needs review'}</span>
              </button>
            ))}
          </div>
        </aside>

        {selected ? (
          <section style={{ ...card, padding: 22 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 14 }}>
              <div>
                <div style={sectionEyebrow}>Review the fingerprint</div>
                <div style={{ font: "700 21px 'Kalam'", color: 'var(--ink)', marginTop: 3 }}>{selected.title}</div>
              </div>
              <button type="button" disabled={saving} onClick={() => void handleSave()} style={{ ...primaryButtonSmall, opacity: saving ? .6 : 1 }}>{saving ? 'Saving…' : 'Save review'}</button>
            </div>
            <label style={{ display: 'block', marginBottom: 12 }}>
              <span style={label}>Title</span>
              <input value={selected.title} onChange={(event) => patchSelected({ title: event.target.value })} style={fieldStyle} />
            </label>
            <label style={{ display: 'block', marginBottom: 12 }}>
              <span style={label}>Spoiler-light description</span>
              <textarea value={selected.blurb} onChange={(event) => patchSelected({ blurb: event.target.value })} rows={5} style={{ ...textAreaStyle, minHeight: 120 }} />
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              {([
                ['subgenres', 'Genres / subgenres'], ['themes', 'Themes'], ['tropes', 'Tropes'], ['comps', 'Comparable titles']
              ] as const).map(([key, labelText]) => (
                <label key={key}>
                  <span style={label}>{labelText}</span>
                  <input value={selected[key].join(', ')} onChange={(event) => patchSelected({ [key]: tags(event.target.value) })} style={fieldStyle} />
                </label>
              ))}
            </div>
            <label style={{ display: 'block', marginTop: 12, marginBottom: 16 }}>
              <span style={label}>Tone</span>
              <input value={selected.tone} onChange={(event) => patchSelected({ tone: event.target.value })} style={fieldStyle} />
            </label>

            <label style={{ display: 'block', marginBottom: 14 }}>
              <span style={label}>Central message</span>
              <textarea
                value={String((selected.fingerprint_json.themes_message as { message?: unknown } | undefined)?.message ?? '')}
                onChange={(event) => patchMessage(event.target.value)}
                rows={2}
                style={textAreaStyle}
              />
            </label>

            {(() => {
              const intensity = selected.fingerprint_json.content_intensity as { violence?: string; sexual_content?: string; heat?: number; flags?: string[] } | undefined
              if (!intensity) return null
              return (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: 15 }}>
                  {[intensity.violence && `Violence: ${intensity.violence}`, intensity.sexual_content && `Sexual content: ${intensity.sexual_content}`, ...((intensity.flags ?? []).map((flag) => `Content: ${flag}`))].filter(Boolean).map((item) => (
                    <span key={item} style={{ padding: '5px 9px', borderRadius: 999, background: 'var(--accent-soft-bg)', color: 'var(--ink-muted)', font: "700 10.5px 'Quicksand'" }}>{item}</span>
                  ))}
                </div>
              )
            })()}

            <div style={{ borderTop: '1px solid var(--border)', paddingTop: 15, marginTop: 6 }}>
              <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)', marginBottom: 9 }}>Audience channel plan</div>
              <div style={{ display: 'grid', gap: 9 }}>
                {plan.map((item) => (
                  <div key={item.platform} style={{ border: '1.5px solid var(--border)', borderRadius: 12, padding: 12, background: 'var(--surface)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                      <span style={{ font: "700 14px 'Quicksand'", color: 'var(--ink)' }}>{item.platform}</span>
                      <span style={{ font: "700 11px 'Quicksand'", color: 'var(--accent-deep)' }}>{Math.round(item.score * 100)}% fit</span>
                    </div>
                    <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-muted)', marginTop: 4 }}>{item.seedCommunities}</div>
                    <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 5 }}>Try: {item.starterIdea}</div>
                    <div style={{ font: "700 10px 'Quicksand'", color: 'var(--danger-ink)', marginTop: 5 }}>Review the community rules before participating or sharing your work.</div>
                  </div>
                ))}
                {plan.length === 0 && <div style={{ font: "600 12px 'Quicksand'", color: 'var(--ink-faint)' }}>Add genre tags to build an audience plan.</div>}
              </div>
            </div>

            <details style={{ marginTop: 16 }}>
              <summary style={{ font: "700 12px 'Quicksand'", color: 'var(--ink-muted)', cursor: 'pointer' }}>Advanced fingerprint JSON</summary>
              <textarea
                aria-label="Advanced fingerprint JSON"
                value={fingerprintText}
                onChange={(event) => patchFingerprint(event.target.value)}
                rows={18}
                spellCheck={false}
                style={{ ...textAreaStyle, minHeight: 300, marginTop: 8, fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 11 }}
              />
            </details>
          </section>
        ) : (
          <section style={{ ...card, padding: 40, textAlign: 'center', font: "700 15px 'Kalam'", color: 'var(--ink-faint)' }}>
            Upload a finished work to create its fingerprint and reader plan.
          </section>
        )}
      </div>
    </>
  )
}
