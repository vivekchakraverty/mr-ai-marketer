import { useEffect, useRef, useState } from 'react'
import {
  classifyVisualArt, fetchVisualArtChoices, matchVisualArt,
  type VisualArtChoices, type VisualArtReport
} from '../api/client'
import { card, primaryButton, primaryButtonSmall, sectionEyebrow, select } from '../styles/styleKit'

const MAX_IMAGE_BYTES = 20 * 1024 * 1024
const muted = { color: 'var(--ink-muted)', font: "600 12px/1.6 'Quicksand'" }
const heading = { font: "700 20px 'Kalam'", color: 'var(--ink)', margin: '0 0 9px' }
const formatName = (name: string): string => name.replaceAll('_', ' ')

export default function AlignVisualArt(): React.JSX.Element {
  const [choices, setChoices] = useState<VisualArtChoices | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [group, setGroup] = useState('')
  const [style, setStyle] = useState('')
  const [categories, setCategories] = useState<string[]>([])
  const [description, setDescription] = useState('')
  const [report, setReport] = useState<VisualArtReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const requestId = useRef(0)

  useEffect(() => {
    void fetchVisualArtChoices().then(setChoices).catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  useEffect(() => {
    if (!file) { setPreview(''); return }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  function chooseFile(next: File | null): void {
    requestId.current += 1
    setBusy(false)
    setError('')
    setNotice('')
    setReport(null)
    setDescription('')
    setGroup('')
    setStyle('')
    setCategories([])
    if (next && next.size > MAX_IMAGE_BYTES) {
      setFile(null)
      setError('Choose an image up to 20 MB.')
      return
    }
    setFile(next)
  }

  async function analyze(): Promise<void> {
    if (!file || busy) return
    const currentRequest = ++requestId.current
    setBusy(true)
    setError('')
    setNotice('Reading visible image features…')
    setReport(null)
    try {
      const labels = await classifyVisualArt(file)
      if (currentRequest !== requestId.current) return
      setGroup(labels.group)
      setStyle(labels.style)
      setCategories(labels.artsy_categories)
      setDescription(labels.description)
      if (!labels.group) {
        setNotice('The image could not be placed in a PAMELA visual group. Choose one below, then match.')
        return
      }
      setNotice('Comparing the selected labels with Artsy and PAMELA…')
      const result = await matchVisualArt(labels.group, labels.style, labels.artsy_categories)
      if (currentRequest !== requestId.current) return
      setReport(result)
      setNotice('Review the labels and benchmark evidence below.')
    } catch (err) {
      if (currentRequest !== requestId.current) return
      setNotice('You can choose the labels yourself and run the benchmark without image inference.')
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (currentRequest === requestId.current) setBusy(false)
    }
  }

  async function runMatch(): Promise<void> {
    if (!file || !group || busy) return
    const currentRequest = ++requestId.current
    setBusy(true)
    setError('')
    setReport(null)
    try {
      const result = await matchVisualArt(group, style, categories)
      if (currentRequest !== requestId.current) return
      setReport(result)
      setNotice('Benchmark match updated from your reviewed labels.')
    } catch (err) {
      if (currentRequest === requestId.current) setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (currentRequest === requestId.current) setBusy(false)
    }
  }

  function toggleCategory(category: string): void {
    setCategories((current) => current.includes(category)
      ? current.filter((item) => item !== category)
      : current.length < 4 ? [...current, category] : current)
    setReport(null)
  }

  return <div style={{ maxWidth: 980 }}>
    <div style={sectionEyebrow}>Visual Art</div>
    <h2 style={{ color: 'var(--ink)', marginBottom: 4 }}>Who might admire this artwork?</h2>
    <p style={{ ...muted, fontSize: 14, marginTop: 0 }}>
      Upload an image to describe its visible subject and style. Artsy's categories suggest where interested art viewers may browse;
      PAMELA's human ratings show how participants responded to other images with similar broad labels.
    </p>

    <section style={{ ...card, padding: 20, marginBottom: 16 }}>
      <h3 style={heading}>1. Upload artwork</h3>
      <input type="file" accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp" aria-label="Artwork image"
        onChange={(event) => chooseFile(event.target.files?.[0] ?? null)} />
      <p style={{ ...muted, margin: '8px 0 15px' }}>PNG, JPEG or WebP · up to 20 MB. The image is resized and sent to Hugging Face for optional label suggestions. The original is not saved by the app. Your token needs Inference Providers permission; the token check in Settings only confirms your login.</p>
      {preview && <img src={preview} alt="Selected artwork preview" style={{ maxWidth: '100%', maxHeight: 360, objectFit: 'contain', borderRadius: 12, border: '1px solid var(--border)', display: 'block', marginBottom: 14 }} />}
      <button type="button" disabled={!file || busy || !choices} onClick={() => { void analyze() }}
        style={{ ...primaryButton, opacity: !file || busy || !choices ? .6 : 1, padding: '10px 18px' }}>
        {busy ? 'Analyzing…' : 'Analyze artwork'}
      </button>
      {error && <div role="alert" style={{ ...muted, color: '#a34a3a', marginTop: 10 }}>{error}</div>}
      {notice && <div role="status" style={{ ...muted, marginTop: 8 }}>{notice}</div>}
    </section>

    <section style={{ ...card, padding: 20, marginBottom: 16 }}>
      <h3 style={heading}>2. Review the labels</h3>
      <p style={{ ...muted, marginTop: 0 }}>Image labels are suggestions. Choose them manually if you prefer or if Hugging Face is unavailable.</p>
      {description && <p style={{ color: 'var(--ink)', font: "600 13px/1.6 'Quicksand'" }}>{description}</p>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, marginBottom: 14 }}>
        <label style={muted}>PAMELA visual group
          <select aria-label="PAMELA visual group" value={group} onChange={(event) => { setGroup(event.target.value); setReport(null) }} style={{ ...select, marginTop: 5 }}>
            <option value="">Select a group</option>
            {choices?.groups.map((item) => <option key={item} value={item}>{formatName(item)}</option>)}
          </select>
        </label>
        <label style={muted}>PAMELA style (if clear)
          <select aria-label="PAMELA style" value={style} onChange={(event) => { setStyle(event.target.value); setReport(null) }} style={{ ...select, marginTop: 5 }}>
            <option value="">No clear style</option>
            {choices?.styles.map((item) => <option key={item} value={item}>{formatName(item)}</option>)}
          </select>
        </label>
      </div>
      <div style={{ ...muted, fontWeight: 700, marginBottom: 8 }}>Artsy categories (up to four)</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
        {choices?.artsy_categories.map((item) => <label key={item} style={{ border: '1px solid var(--border)', borderRadius: 999, padding: '6px 10px', font: "700 11px 'Quicksand'", color: 'var(--ink)', background: categories.includes(item) ? 'var(--accent-soft-bg)' : 'var(--surface)', cursor: 'pointer' }}>
          <input type="checkbox" checked={categories.includes(item)} disabled={!categories.includes(item) && categories.length >= 4}
            onChange={() => toggleCategory(item)} style={{ marginRight: 6 }} />{item}
        </label>)}
      </div>
      <button type="button" disabled={!file || !group || busy} onClick={() => { void runMatch() }} style={{ ...primaryButtonSmall, opacity: !file || !group || busy ? .6 : 1 }}>
        Match potential admirers
      </button>
    </section>

    {report && <>
      <section style={{ ...card, padding: 20, marginBottom: 16 }}>
        <h3 style={heading}>Artsy: where this look could be discovered</h3>
        {report.artsy_categories.length ? <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {report.artsy_categories.map((item) => <a key={item} href={report.artsy_category_links[item]} target="_blank" rel="noopener noreferrer" style={{ borderRadius: 999, background: 'var(--accent-soft-bg)', padding: '7px 11px', color: 'var(--accent-deep)', font: "700 12px 'Quicksand'" }}>{item} ↗</a>)}
        </div> : <p style={muted}>Choose an Artsy category above to describe this artwork.</p>}
        <p style={muted}>These are category paths for research, not evidence that Artsy members have seen or liked this upload.</p>
        <a href={report.sources.artsy} target="_blank" rel="noopener noreferrer" style={{ font: "700 12px 'Quicksand'", color: 'var(--accent-deep)' }}>Explore Artsy's Art Genome categories ↗</a>
      </section>
      <section style={{ ...card, padding: 20, marginBottom: 16 }}>
        <h3 style={heading}>PAMELA: observed preference for similar labels</h3>
        <p style={{ ...muted, marginTop: 0 }}>Training split: {report.sample.ratings.toLocaleString()} ratings from {report.sample.participants} participants. Each row counts ratings of other images with the selected label.</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
          {report.pamela_evidence.map((item) => <article key={`${item.dimension}-${item.name}`} style={{ border: '1.5px solid var(--border)', borderRadius: 12, padding: 14 }}>
            <div style={sectionEyebrow}>{item.dimension}</div>
            <h4 style={{ font: "700 17px 'Kalam'", margin: '4px 0 9px', color: 'var(--ink)' }}>{formatName(item.name)}</h4>
            <div style={{ font: "700 23px 'Kalam'", color: 'var(--ink)' }}>{item.admirer_participants} of {item.eligible_participants}</div>
            <div style={muted}>participants met the benchmark's favorable rating rule</div>
            <div style={{ ...muted, marginTop: 9 }}>Mean rating {item.mean_rating.toFixed(2)}/5 · {item.ratings.toLocaleString()} ratings across {item.images} images</div>
          </article>)}
        </div>
        <p style={{ ...muted, marginBottom: 4 }}>{report.method}</p>
        <p style={{ ...muted, color: '#a34a3a', marginTop: 4 }}>{report.boundary}</p>
        <div style={muted}>PAMELA by Anne-Sofie Maerten and colleagues · CC BY 4.0.</div>
        <a href={report.sources.pamela_dataset} target="_blank" rel="noopener noreferrer" style={{ font: "700 12px 'Quicksand'", color: 'var(--accent-deep)' }}>View the dataset card ↗</a>
        <a href={report.sources.pamela} target="_blank" rel="noopener noreferrer" style={{ font: "700 12px 'Quicksand'", color: 'var(--accent-deep)', marginLeft: 14 }}>View the supplied bucket ↗</a>
      </section>
    </>}
  </div>
}
