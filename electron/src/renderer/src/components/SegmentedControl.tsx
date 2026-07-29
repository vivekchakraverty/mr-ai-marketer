import { segGroup, segItem } from '../styles/styleKit'

interface Props {
  options: readonly string[]
  value: string
  onChange: (value: string) => void
}

export default function SegmentedControl({ options, value, onChange }: Props): React.JSX.Element {
  return (
    <div style={segGroup}>
      {options.map((opt) => (
        <div key={opt} style={segItem(value === opt)} onClick={() => onChange(opt)}>
          {opt}
        </div>
      ))}
    </div>
  )
}
