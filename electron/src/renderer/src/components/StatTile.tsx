interface Props {
  value: string | number
  label: string
}

export default function StatTile({ value, label }: Props): React.JSX.Element {
  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '2.5px solid var(--border)',
        borderRadius: 18,
        padding: '13px 24px',
        boxShadow: 'var(--shadow-sm)',
        textAlign: 'center'
      }}
    >
      <div style={{ font: "700 26px 'Kalam'", color: 'var(--accent-deep)' }}>{value}</div>
      <div
        style={{
          font: "700 11px 'Quicksand'",
          letterSpacing: '.05em',
          textTransform: 'uppercase',
          color: 'var(--ink-faint)'
        }}
      >
        {label}
      </div>
    </div>
  )
}
