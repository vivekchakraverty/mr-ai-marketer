import { useEffect, useMemo, useState } from 'react'
import {
  createCustomChannel,
  fetchCataloguePiece,
  fetchDistributionCatalogue,
  type CataloguePiece,
  type CataloguePieceDetail,
  type InputChoice,
  type PieceProp
} from '../api/client'
import { label, primaryButton, secondaryButtonSmall, select, textInput } from '../styles/styleKit'

interface Props {
  onClose: () => void
  onAdded: () => void
}

/** A stable channel key from the piece's display name — the flow, the connection and the
 * job rows are all keyed by this string, so it has to be slug-safe and match the backend's
 * `^[a-z0-9][a-z0-9-]{1,38}$`. */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 39)
}

/** Props worth putting in front of the user: anything required, plus anything we can
 * pre-bind to the post payload. The rest are optional piece settings that would turn a
 * two-decision form into a forty-field one. */
function relevantProps(props: PieceProp[]): PieceProp[] {
  return props.filter((p) => p.required || p.suggestedBinding)
}

export default function AddChannelModal({ onClose, onAdded }: Props): React.JSX.Element {
  const [query, setQuery] = useState('')
  const [pieces, setPieces] = useState<CataloguePiece[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [picked, setPicked] = useState<CataloguePieceDetail | null>(null)
  const [actionName, setActionName] = useState('')
  const [channelKey, setChannelKey] = useState('')
  const [inputMap, setInputMap] = useState<Record<string, InputChoice>>({})
  const [busy, setBusy] = useState(false)

  // Debounced so typing in the search box doesn't fire a request per keystroke against a
  // ~750-entry catalogue.
  useEffect(() => {
    let cancelled = false
    const timer = setTimeout(() => {
      setLoading(true)
      fetchDistributionCatalogue(query)
        .then((res) => {
          if (cancelled) return
          setPieces(res.pieces)
          setTotal(res.total)
          setError('')
        })
        .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)))
        .finally(() => !cancelled && setLoading(false))
    }, 220)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query])

  const action = useMemo(
    () => picked?.actions.find((a) => a.name === actionName) ?? null,
    [picked, actionName]
  )
  const shownProps = useMemo(() => (action ? relevantProps(action.props) : []), [action])

  async function handlePick(piece: CataloguePiece): Promise<void> {
    setBusy(true)
    setError('')
    try {
      const detail = await fetchCataloguePiece(piece.name)
      setPicked(detail)
      setChannelKey(slugify(detail.displayName))
      const first = detail.actions[0]
      setActionName(first?.name ?? '')
      seedInputMap(first?.props ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  /** Pre-fills each shown prop with the binding the backend suggested, so the common case
   * (one text field) is already correct and the user only touches what it couldn't know. */
  function seedInputMap(props: PieceProp[]): void {
    const seeded: Record<string, InputChoice> = {}
    for (const p of relevantProps(props)) {
      seeded[p.key] = p.suggestedBinding ? { field: p.suggestedBinding } : { value: '' }
    }
    setInputMap(seeded)
  }

  function chooseAction(name: string): void {
    setActionName(name)
    seedInputMap(picked?.actions.find((a) => a.name === name)?.props ?? [])
  }

  function setChoice(key: string, choice: InputChoice): void {
    setInputMap((cur) => ({ ...cur, [key]: choice }))
  }

  async function handleAdd(): Promise<void> {
    if (!picked || !action) return
    const missing = shownProps.find(
      (p) => p.required && !('field' in (inputMap[p.key] ?? {})) && !(inputMap[p.key] as { value?: string })?.value?.trim()
    )
    if (missing) {
      setError(`${missing.label} needs either a post field or a fixed value.`)
      return
    }
    if (!/^[a-z0-9][a-z0-9-]{1,38}$/.test(channelKey)) {
      setError('The channel key must be lowercase letters, numbers and dashes.')
      return
    }
    setBusy(true)
    setError('')
    try {
      await createCustomChannel({
        channel: channelKey,
        label: picked.displayName,
        pieceName: picked.name,
        pieceVersion: picked.version,
        actionName: action.name,
        authType: picked.auth.type ?? 'OAUTH2',
        inputMap
      })
      onAdded()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(43, 36, 32, 0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 60
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: 560,
          maxHeight: '85vh',
          overflowY: 'auto',
          background: 'var(--surface)',
          border: '2.5px solid var(--border)',
          borderRadius: 22,
          padding: 30,
          boxShadow: '9px 10px 0 rgba(43,36,32,.22)'
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)', marginBottom: 4 }}>
          {picked ? `Set up ${picked.displayName}` : 'Add a channel'}
        </div>
        <p style={{ font: "600 13px/1.6 'Quicksand'", color: 'var(--ink-muted)', margin: '0 0 16px' }}>
          {picked
            ? 'Choose what this channel does with a post, then connect it from its card.'
            : `Anything your distribution engine can post to — ${total} platforms, read live from the engine itself.`}
        </p>

        {!picked && (
          <>
            <input
              autoFocus
              value={query}
              placeholder="Search platforms — telegram, slack, pinterest…"
              onChange={(e) => setQuery(e.target.value)}
              style={{ ...textInput, marginBottom: 14 }}
            />
            {loading && <div style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-faint)' }}>Reading the catalogue…</div>}
            {!loading && pieces.length === 0 && (
              <div style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-faint)' }}>Nothing matching that.</div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              {pieces.map((p) => (
                <div
                  key={p.name}
                  onClick={busy ? undefined : () => handlePick(p)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 11,
                    background: 'var(--surface-tint)',
                    border: '2px solid var(--border)',
                    borderRadius: 14,
                    padding: '10px 13px',
                    cursor: 'pointer'
                  }}
                >
                  {p.logoUrl && <img src={p.logoUrl} alt="" style={{ width: 22, height: 22, flexShrink: 0 }} />}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ font: "700 14px 'Quicksand'", color: 'var(--ink)' }}>
                      {p.displayName}
                      {p.builtIn && (
                        <span style={{ font: "700 10.5px 'Quicksand'", color: 'var(--ink-faint)', marginLeft: 8 }}>
                          ALREADY BUILT IN
                        </span>
                      )}
                      {p.alreadyAdded && (
                        <span style={{ font: "700 10.5px 'Quicksand'", color: 'var(--accent-deep)', marginLeft: 8 }}>ADDED</span>
                      )}
                    </div>
                    <div
                      style={{
                        font: "600 11.5px 'Quicksand'",
                        color: 'var(--ink-faint)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis'
                      }}
                    >
                      {p.actionCount} action{p.actionCount === 1 ? '' : 's'}
                      {p.authType ? ` · ${p.authType.replace('_', ' ').toLowerCase()}` : ''}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {picked && (
          <>
            <div style={{ marginBottom: 14 }}>
              <label style={label}>What should it do?</label>
              <select style={select} value={actionName} onChange={(e) => chooseAction(e.target.value)}>
                {picked.actions.map((a) => (
                  <option key={a.name} value={a.name}>
                    {a.label}
                  </option>
                ))}
              </select>
              {action?.description && (
                <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginTop: 5 }}>
                  {action.description}
                </div>
              )}
            </div>

            {shownProps.map((p) => {
              const choice = inputMap[p.key] ?? { value: '' }
              const bound = 'field' in choice
              return (
                <div key={p.key} style={{ marginBottom: 14 }}>
                  <label style={label}>
                    {p.label}
                    {!p.required && ' (optional)'}
                  </label>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <select
                      style={{ ...select, flex: '0 0 150px' }}
                      value={bound ? choice.field : '__fixed'}
                      onChange={(e) =>
                        setChoice(p.key, e.target.value === '__fixed' ? { value: '' } : { field: e.target.value })
                      }
                    >
                      <option value="__fixed">A fixed value</option>
                      {picked.payloadFields.map((f) => (
                        <option key={f} value={f}>
                          From post: {f}
                        </option>
                      ))}
                    </select>
                    {bound ? (
                      <div
                        style={{
                          ...textInput,
                          flex: 1,
                          color: 'var(--ink-faint)',
                          display: 'flex',
                          alignItems: 'center'
                        }}
                      >
                        filled from each post
                      </div>
                    ) : p.options.length > 0 ? (
                      <select
                        style={{ ...select, flex: 1 }}
                        value={(choice as { value: string }).value}
                        onChange={(e) => setChoice(p.key, { value: e.target.value })}
                      >
                        <option value="">Choose…</option>
                        {p.options.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        style={{ ...textInput, flex: 1 }}
                        value={(choice as { value: string }).value}
                        placeholder={p.description.slice(0, 60) || 'Same for every post'}
                        onChange={(e) => setChoice(p.key, { value: e.target.value })}
                      />
                    )}
                  </div>
                </div>
              )
            })}

            <div style={{ marginBottom: 14 }}>
              <label style={label}>Channel key</label>
              <input style={textInput} value={channelKey} onChange={(e) => setChannelKey(e.target.value)} />
              <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 5 }}>
                How this channel is referred to internally. Leave it as it is unless it clashes with another.
              </div>
            </div>
          </>
        )}

        {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a', margin: '10px 0' }}>{error}</div>}

        <div style={{ display: 'flex', gap: 10, marginTop: 14, flexWrap: 'wrap' }}>
          {picked && (
            <div style={{ ...primaryButton, flex: 1, opacity: busy ? 0.6 : 1 }} onClick={busy ? undefined : handleAdd}>
              {busy ? 'Adding…' : 'Add channel'}
            </div>
          )}
          {picked && (
            <div style={secondaryButtonSmall} onClick={() => setPicked(null)}>
              Back
            </div>
          )}
          <div style={secondaryButtonSmall} onClick={onClose}>
            Cancel
          </div>
        </div>
      </div>
    </div>
  )
}
