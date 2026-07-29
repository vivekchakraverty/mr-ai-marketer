import { chip } from '../styles/styleKit'

interface Props {
  options: readonly string[]
  values: string[]
  onToggle: (value: string) => void
}

export default function ChipGroup({ options, values, onToggle }: Props): React.JSX.Element {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {options.map((opt) => (
        <div key={opt} style={chip(values.includes(opt))} onClick={() => onToggle(opt)}>
          {opt}
        </div>
      ))}
    </div>
  )
}
