import { useEffect, useMemo, useState } from 'react'
import {
  assembleBrandDocument,
  generateBrandImages,
  generateBrandSection,
  getBrandForgeMeta,
  type AssembleBrandResponse,
  type BrandImage,
  type BrandIntakeInput,
  type BrandMeta,
  type BrandSection
} from '../api/client'
import { refreshLibrary } from '../state/actions'
import { useAppStore } from '../state/store'
import BackendImage from './BackendImage'
import MarkdownPanel from './MarkdownPanel'
import SaveButton from './SaveButton'
import {
  card,
  chip,
  label,
  primaryButton,
  primaryButtonSmall,
  secondaryButtonSmall,
  sectionEyebrow,
  segGroup,
  segItem,
  select,
  textInput,
  textarea
} from '../styles/styleKit'

/**
 * Brand Studio — lives in Research / Strategy. Sends a structured intake to the
 * BrandForge Space (fine-tuned Qwen3 on free CPU) and builds a 12-section Brand
 * Document. Because CPU generation is slow, it generates one section at a time so
 * progress streams in and each request stays short; the backend assembles the
 * finished document (markdown/docx/voice card). Brand images use HF text-to-image.
 */

function emptyIntake(meta: BrandMeta): BrandIntakeInput {
  return {
    brand_archetype: '',
    secondary_archetype: null,
    brand_category: meta.categories[0] ?? '',
    brand_name: '',
    one_liner: '',
    founding_story: '',
    primary_audience: '',
    secondary_audience: '',
    geography: '',
    business_model: 'B2C',
    competitors: [],
    differentiation_hypothesis: '',
    admired_brands: '',
    never_sound_like: '',
    existing_assets: '',
    top_12mo_goal: '',
    personality: Object.fromEntries(meta.personalitySliders.map((s) => [s.key, 4])),
    channels: []
  }
}

const errText = { font: "700 12.5px 'Quicksand'", color: 'var(--danger-ink)' } as const

interface Progress {
  done: number
  total: number
  current: string
}

export default function BrandForge(): React.JSX.Element {
  const requireHf = useAppStore((s) => s.requireHf)

  const [meta, setMeta] = useState<BrandMeta | null>(null)
  const [metaError, setMetaError] = useState('')
  const [intake, setIntake] = useState<BrandIntakeInput | null>(null)
  const [competitorsText, setCompetitorsText] = useState('')

  const [sections, setSections] = useState<BrandSection[]>([])
  const [result, setResult] = useState<AssembleBrandResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [error, setError] = useState('')
  const [regen, setRegen] = useState<Record<string, boolean>>({})

  const [images, setImages] = useState<BrandImage[] | null>(null)
  const [imagesLoading, setImagesLoading] = useState(false)
  const [imagesError, setImagesError] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    getBrandForgeMeta()
      .then((m) => {
        setMeta(m)
        setIntake(emptyIntake(m))
      })
      .catch((err) => setMetaError(err instanceof Error ? err.message : String(err)))
  }, [])

  const set = <K extends keyof BrandIntakeInput>(key: K, value: BrandIntakeInput[K]): void =>
    setIntake((cur) => (cur ? { ...cur, [key]: value } : cur))

  const setSlider = (key: string, value: number): void =>
    setIntake((cur) => (cur ? { ...cur, personality: { ...cur.personality, [key]: value } } : cur))

  const toggleChannel = (channel: string): void =>
    setIntake((cur) => {
      if (!cur) return cur
      const on = cur.channels.includes(channel)
      return { ...cur, channels: on ? cur.channels.filter((c) => c !== channel) : [...cur.channels, channel] }
    })

  const visualBrief = useMemo(
    () => sections.find((s) => s.name === 'Visual Direction Brief')?.content ?? '',
    [sections]
  )

  function withCompetitors(base: BrandIntakeInput): BrandIntakeInput {
    const competitors = competitorsText
      .split(',')
      .map((c) => c.trim())
      .filter(Boolean)
    return { ...base, competitors }
  }

  function validate(candidate: BrandIntakeInput): string | null {
    if (!candidate.brand_archetype) return 'Pick a brand type first.'
    if (!candidate.brand_name.trim()) return 'Brand name is required.'
    if (!candidate.one_liner.trim()) return 'A one-liner is required.'
    if (!candidate.primary_audience.trim()) return 'Primary audience is required.'
    if (!candidate.top_12mo_goal.trim()) return 'Top 12-month goal is required.'
    if (candidate.competitors.length && (candidate.competitors.length < 3 || candidate.competitors.length > 5))
      return 'Provide 3-5 competitors, or leave the field empty.'
    return null
  }

  async function handleGenerate(): Promise<void> {
    if (!intake || !meta) return
    if (!requireHf()) return
    const candidate = withCompetitors(intake)
    const problem = validate(candidate)
    if (problem) {
      setError(problem)
      return
    }
    setLoading(true)
    setError('')
    setResult(null)
    setImages(null)
    setImagesError('')
    setSections([])

    const order = meta.sectionNames
    const collected: BrandSection[] = []
    try {
      for (let i = 0; i < order.length; i++) {
        const name = order[i]
        setProgress({ done: i, total: order.length, current: name })
        try {
          collected.push(await generateBrandSection(candidate, name))
        } catch (err) {
          // The first section failing is systemic (Space not set / asleep / down) — abort.
          if (i === 0) throw err
          const phase = meta.phases.find((p) => p.sections.includes(name))?.phase ?? ''
          collected.push({
            name,
            phase,
            content: `⚠️ **Generation failed:** ${err instanceof Error ? err.message : String(err)}`
          })
        }
        setSections([...collected])
      }
      setProgress({ done: order.length, total: order.length, current: 'Assembling document…' })
      const res = await assembleBrandDocument(candidate, Object.fromEntries(collected.map((s) => [s.name, s.content])))
      setResult(res)
      void refreshLibrary()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
      setProgress(null)
    }
  }

  async function handleRegen(name: string): Promise<void> {
    if (!intake) return
    setRegen((r) => ({ ...r, [name]: true }))
    setError('')
    try {
      const sec = await generateBrandSection(withCompetitors(intake), name)
      setSections((prev) => prev.map((s) => (s.name === name ? sec : s)))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRegen((r) => ({ ...r, [name]: false }))
    }
  }

  async function handleImages(): Promise<void> {
    if (!intake) return
    if (!visualBrief.trim()) {
      setImagesError('Generate the Visual Direction Brief section first — the images are grounded in it.')
      return
    }
    setImagesLoading(true)
    setImagesError('')
    try {
      const res = await generateBrandImages(withCompetitors(intake), visualBrief)
      setImages(res.images)
    } catch (err) {
      setImagesError(err instanceof Error ? err.message : String(err))
    } finally {
      setImagesLoading(false)
    }
  }

  function loadDemo(): void {
    if (!meta) return
    setIntake(meta.demo)
    setCompetitorsText(meta.demo.competitors.join(', '))
    setError('')
  }

  async function copyVoiceCard(): Promise<void> {
    if (!result) return
    await navigator.clipboard.writeText(result.voiceCard)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  if (metaError) {
    return <div style={{ ...card, ...errText }}>Couldn&apos;t load Brand Studio: {metaError}</div>
  }
  if (!meta || !intake) {
    return <div style={{ font: "600 14px 'Quicksand'", color: 'var(--ink-muted)' }}>Loading Brand Studio…</div>
  }

  const groupLabel = { ...sectionEyebrow, marginTop: 4 }
  const showCard = sections.length > 0 || loading || result

  return (
    <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
      {/* ---- intake form ---------------------------------------------------- */}
      <div style={{ width: 420, flexShrink: 0, ...card, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={sectionEyebrow}>Brand intake</div>
          <div style={secondaryButtonSmall} onClick={loadDemo}>
            Load demo
          </div>
        </div>

        {/* Brand type */}
        <div>
          <label style={label}>Brand type (archetype)</label>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
              maxHeight: 210,
              overflowY: 'auto',
              border: '2px solid var(--border)',
              borderRadius: 12,
              padding: 6
            }}
          >
            {meta.archetypes.map((a) => {
              const on = intake.brand_archetype === a.id
              return (
                <div
                  key={a.id}
                  onClick={() => set('brand_archetype', a.id)}
                  style={{
                    padding: '8px 11px',
                    borderRadius: 9,
                    cursor: 'pointer',
                    border: '2px solid',
                    borderColor: on ? 'var(--border)' : 'transparent',
                    background: on ? 'var(--accent)' : 'transparent'
                  }}
                >
                  <div style={{ font: "700 13px 'Quicksand'", color: on ? 'var(--accent-ink)' : 'var(--ink)' }}>
                    {a.name}
                  </div>
                  <div
                    style={{
                      font: "600 11.5px/1.4 'Quicksand'",
                      color: on ? 'var(--accent-ink)' : 'var(--ink-muted)',
                      marginTop: 2
                    }}
                  >
                    {a.description}
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div>
          <label style={label}>Secondary type (optional)</label>
          <select
            value={intake.secondary_archetype ?? ''}
            onChange={(e) => set('secondary_archetype', e.target.value || null)}
            style={select}
          >
            <option value="">(none)</option>
            {meta.archetypes.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>

        {/* Basics */}
        <div style={groupLabel}>Basics</div>
        <div>
          <label style={label}>Brand category</label>
          <select value={intake.brand_category} onChange={(e) => set('brand_category', e.target.value)} style={select}>
            {meta.categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label style={label}>Brand name</label>
          <input value={intake.brand_name} onChange={(e) => set('brand_name', e.target.value)} placeholder="e.g. Copper Kettle" style={textInput} />
        </div>
        <div>
          <label style={label}>One-liner</label>
          <textarea
            value={intake.one_liner}
            onChange={(e) => set('one_liner', e.target.value)}
            placeholder="One sentence: what you make and for whom."
            rows={2}
            style={textarea}
          />
        </div>
        <div>
          <label style={label}>Founding story</label>
          <textarea
            value={intake.founding_story}
            onChange={(e) => set('founding_story', e.target.value)}
            placeholder="Why the brand exists (optional)."
            rows={3}
            style={textarea}
          />
        </div>
        <div>
          <label style={label}>Primary audience</label>
          <textarea value={intake.primary_audience} onChange={(e) => set('primary_audience', e.target.value)} rows={2} style={textarea} />
        </div>
        <div>
          <label style={label}>Secondary audience</label>
          <input value={intake.secondary_audience} onChange={(e) => set('secondary_audience', e.target.value)} placeholder="Optional" style={textInput} />
        </div>
        <div style={{ display: 'flex', gap: 12 }}>
          <div style={{ flex: 1 }}>
            <label style={label}>Geography</label>
            <input value={intake.geography} onChange={(e) => set('geography', e.target.value)} placeholder="Optional" style={textInput} />
          </div>
          <div style={{ flex: 1 }}>
            <label style={label}>Model</label>
            <div style={segGroup}>
              {meta.businessModels.map((m) => (
                <div key={m} style={segItem(intake.business_model === m)} onClick={() => set('business_model', m)}>
                  {m}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Positioning */}
        <div style={groupLabel}>Positioning</div>
        <div>
          <label style={label}>Competitors (3-5, comma-separated)</label>
          <input
            value={competitorsText}
            onChange={(e) => setCompetitorsText(e.target.value)}
            placeholder="e.g. Blue Tokai, Sleepy Owl, Araku"
            style={textInput}
          />
        </div>
        <div>
          <label style={label}>Differentiation hypothesis</label>
          <textarea value={intake.differentiation_hypothesis} onChange={(e) => set('differentiation_hypothesis', e.target.value)} rows={2} style={textarea} />
        </div>
        <div>
          <label style={label}>Admired brands + why</label>
          <textarea value={intake.admired_brands} onChange={(e) => set('admired_brands', e.target.value)} rows={2} style={textarea} />
        </div>
        <div>
          <label style={label}>&quot;Never sound like&quot;</label>
          <textarea value={intake.never_sound_like} onChange={(e) => set('never_sound_like', e.target.value)} rows={2} style={textarea} />
        </div>
        <div>
          <label style={label}>Existing assets (logo/colors/tagline)</label>
          <textarea value={intake.existing_assets} onChange={(e) => set('existing_assets', e.target.value)} rows={2} style={textarea} />
        </div>
        <div>
          <label style={label}>Top 12-month goal</label>
          <input value={intake.top_12mo_goal} onChange={(e) => set('top_12mo_goal', e.target.value)} style={textInput} />
        </div>

        {/* Personality */}
        <div style={groupLabel}>Personality</div>
        {meta.personalitySliders.map((s) => (
          <div key={s.key}>
            <div style={{ display: 'flex', justifyContent: 'space-between', font: "700 11.5px 'Quicksand'", color: 'var(--ink-muted)' }}>
              <span>{s.left}</span>
              <span>{s.right}</span>
            </div>
            <input
              type="range"
              min={1}
              max={7}
              step={1}
              value={intake.personality[s.key] ?? 4}
              onChange={(e) => setSlider(s.key, Number(e.target.value))}
              style={{ width: '100%', accentColor: 'var(--accent)' }}
            />
          </div>
        ))}

        {/* Channels */}
        <div style={groupLabel}>Channels</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {meta.channels.map((c) => (
            <div key={c} style={chip(intake.channels.includes(c))} onClick={() => toggleChannel(c)}>
              {c}
            </div>
          ))}
        </div>

        <div style={{ ...primaryButton, opacity: loading ? 0.6 : 1 }} onClick={loading ? undefined : handleGenerate}>
          {loading ? 'Generating…' : 'Generate Brand Document'}
        </div>
        <div style={{ font: "600 11.5px/1.5 'Quicksand'", color: 'var(--ink-faint)' }}>
          Runs on your free CPU Space, one section at a time — the full document takes a while. Sections appear as they finish.
        </div>
        {error && <div style={errText}>{error}</div>}
      </div>

      {/* ---- output --------------------------------------------------------- */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {!showCard && (
          <div style={{ ...card, textAlign: 'center', padding: '52px 30px' }}>
            <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)', marginBottom: 6 }}>No brand document yet</div>
            <div style={{ font: "600 14px/1.7 'Quicksand'", color: 'var(--ink-muted)' }}>
              Fill in the intake and hit generate. Twelve sections — strategy, voice, messaging, visual direction and a
              90-day plan — written by your fine-tuned BrandForge model.
            </div>
          </div>
        )}

        {showCard && (
          <div style={{ ...card, padding: '30px 34px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={sectionEyebrow}>Brand document</div>
                <h1 style={{ font: "700 28px/1.2 'Kalam'", color: 'var(--ink)', margin: '6px 0 0' }}>
                  {intake.brand_name || 'Your brand'}
                </h1>
              </div>
              {result && (
                <div style={{ display: 'flex', gap: 8 }}>
                  <div style={secondaryButtonSmall} onClick={() => result.docxPath && window.api.openFile(result.docxPath)}>
                    Download .docx
                  </div>
                  <div style={primaryButtonSmall} onClick={copyVoiceCard}>
                    {copied ? 'Copied ✓' : 'Copy voice card'}
                  </div>
                  <SaveButton
                    libraryId={result.libraryId}
                    tool="Brand"
                    title={`${intake?.brand_name || 'Your brand'} — Brand Document`}
                    subtitle="Brand document"
                    content={result.markdown}
                  />
                </div>
              )}
            </div>

            {loading && progress && (
              <div style={{ marginTop: 16 }}>
                <div style={{ font: "700 13px 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 6 }}>
                  {progress.current === 'Assembling document…'
                    ? 'Assembling document…'
                    : `Writing ${progress.current} — ${progress.done + 1} of ${progress.total}`}
                </div>
                <div style={{ height: 8, borderRadius: 999, background: 'var(--surface)', border: '2px solid var(--border)', overflow: 'hidden' }}>
                  <div
                    style={{
                      height: '100%',
                      width: `${Math.round((progress.done / progress.total) * 100)}%`,
                      background: 'var(--accent)',
                      transition: 'width .3s'
                    }}
                  />
                </div>
              </div>
            )}

            {result && result.palette.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 16 }}>
                {result.palette.map((s) => (
                  <div key={s.hex} style={{ textAlign: 'center' }}>
                    <div style={{ width: 62, height: 44, borderRadius: 8, background: s.hex, border: '2px solid var(--border)' }} />
                    <div style={{ font: "700 10px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 3 }}>{s.name || s.hex}</div>
                  </div>
                ))}
              </div>
            )}

            {meta.phases.map((phase) => {
              const present = phase.sections.filter((name) => sections.some((s) => s.name === name))
              if (!present.length) return null
              return (
                <div key={phase.phase} style={{ marginTop: 22 }}>
                  <div
                    style={{
                      font: "700 12px 'Quicksand'",
                      letterSpacing: '.14em',
                      textTransform: 'uppercase',
                      color: 'var(--accent-deep)',
                      borderBottom: '2px dashed var(--border-soft)',
                      paddingBottom: 6
                    }}
                  >
                    {phase.phase}
                  </div>
                  {present.map((name) => {
                    const sec = sections.find((s) => s.name === name)!
                    return (
                      <details key={name} style={{ marginTop: 12 }} open>
                        <summary
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            cursor: 'pointer',
                            font: "700 16px 'Kalam'",
                            color: 'var(--ink)'
                          }}
                        >
                          {name}
                          <span
                            style={{ ...secondaryButtonSmall, opacity: regen[name] || loading ? 0.6 : 1 }}
                            onClick={(e) => {
                              e.preventDefault()
                              if (!regen[name] && !loading) void handleRegen(name)
                            }}
                          >
                            {regen[name] ? 'Regenerating…' : 'Regenerate'}
                          </span>
                        </summary>
                        <div style={{ marginTop: 4 }}>
                          <MarkdownPanel markdown={sec.content} />
                        </div>
                      </details>
                    )
                  })}
                </div>
              )
            })}

            {/* Brand images */}
            {result && (
              <div style={{ marginTop: 26, paddingTop: 18, borderTop: '2px dashed var(--border-soft)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <div>
                    <div style={sectionEyebrow}>Brand images</div>
                    <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginTop: 3 }}>
                      Logo mark, mood board and social header from the Visual Direction Brief. Uses your Modal image GPU after setup, otherwise HF text-to-image.
                    </div>
                  </div>
                  <div
                    style={{ ...primaryButtonSmall, opacity: imagesLoading || !visualBrief.trim() ? 0.6 : 1 }}
                    onClick={imagesLoading || !visualBrief.trim() ? undefined : handleImages}
                  >
                    {imagesLoading ? 'Generating images…' : images ? 'Regenerate images' : 'Generate brand images'}
                  </div>
                </div>
                {imagesError && <div style={{ ...errText, marginTop: 8 }}>{imagesError}</div>}
                {images && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginTop: 14 }}>
                    {images.map((img) => (
                      <div key={img.assetType}>
                        <div style={{ font: "700 11.5px 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 5 }}>{img.assetType}</div>
                        {img.url ? (
                          <BackendImage
                            url={img.url}
                            alt={img.assetType}
                            style={{ width: '100%', borderRadius: 12, border: '2px solid var(--border)', display: 'block' }}
                          />
                        ) : (
                          <div style={{ ...errText, font: "600 11.5px 'Quicksand'" }}>{img.error ?? 'Failed'}</div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
