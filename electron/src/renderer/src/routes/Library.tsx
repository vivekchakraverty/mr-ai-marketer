import { useAppStore, type LibraryFilter } from '../state/store'
import LibraryCard from '../components/LibraryCard'

const FILTERS: { label: LibraryFilter; tool: string | null }[] = [
  { label: 'All', tool: null },
  { label: 'Plans', tool: 'Plan' },
  { label: 'Brand', tool: 'Brand' },
  { label: 'Blogs', tool: 'Blog' },
  { label: 'Tutorials', tool: 'Tutorial' },
  { label: 'Guest', tool: 'Guest' },
  { label: 'Docs', tool: 'Docs' }
]

export default function Library(): React.JSX.Element {
  const library = useAppStore((s) => s.library)
  const filter = useAppStore((s) => s.filter)
  const setFilter = useAppStore((s) => s.setFilter)
  const openReader = useAppStore((s) => s.openReader)

  const active = FILTERS.find((f) => f.label === filter) ?? FILTERS[0]
  const items = active.tool ? library.filter((i) => i.tool === active.tool) : library

  return (
    <div style={{ position: 'relative', maxWidth: 1200, margin: '0 auto', padding: '30px 34px 60px' }}>
      <div
        style={{
          position: 'absolute',
          top: 22,
          right: 32,
          width: 34,
          height: 34,
          borderRadius: '50%',
          border: '2.5px solid var(--border)',
          background: 'conic-gradient(var(--accent) 0 25%, var(--surface) 0 50%, var(--tool-tutorial) 0 75%, var(--tool-guest) 0 100%)',
          boxShadow: 'var(--shadow-sm)',
          animation: 'bob 2.8s ease-in-out infinite'
        }}
      />
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 20, flexWrap: 'wrap', gap: 10 }}>
        <div>
          <div
            style={{
              font: "700 13.5px 'Quicksand'",
              letterSpacing: '.05em',
              textTransform: 'uppercase',
              color: 'var(--ink-faint)',
              textDecoration: 'underline wavy',
              textDecorationColor: 'var(--ink-faint)',
              textUnderlineOffset: 4
            }}
          >
            Library
          </div>
          <div style={{ font: "700 31px 'Kalam'", color: 'var(--ink)', marginTop: 6 }}>Your entire empire</div>
        </div>
        <div style={{ font: "600 13px 'Quicksand'", color: 'var(--ink-faint)' }}>{library.length} of 70 saved</div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 22, flexWrap: 'wrap' }}>
        {FILTERS.map((f) => (
          <div
            key={f.label}
            style={{
              padding: '8px 16px',
              borderRadius: 999,
              border: '2px solid var(--border)',
              font: "700 12.5px 'Quicksand'",
              cursor: 'pointer',
              ...(filter === f.label
                ? { background: 'var(--accent)', color: 'var(--accent-ink)' }
                : { background: 'var(--surface)', color: 'var(--ink-muted)' })
            }}
            onClick={() => setFilter(f.label)}
          >
            {f.label}
          </div>
        ))}
      </div>

      {items.length === 0 ? (
        <div
          style={{
            border: '2px dashed var(--border)',
            borderRadius: 20,
            padding: 60,
            textAlign: 'center',
            font: "700 17px 'Kalam'",
            color: 'var(--ink-fainter-2)'
          }}
        >
          Nothing here yet in this category.
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 18 }}>
          {items.map((item) => (
            <LibraryCard key={item.id} item={item} onOpen={() => openReader(item)} />
          ))}
        </div>
      )}
    </div>
  )
}
