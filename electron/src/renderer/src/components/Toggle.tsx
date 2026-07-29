import { toggleKnob, toggleTrack } from '../styles/styleKit'

interface Props {
  on: boolean
  onToggle: () => void
}

export default function Toggle({ on, onToggle }: Props): React.JSX.Element {
  return (
    <div style={toggleTrack(on)} onClick={onToggle}>
      <div style={toggleKnob(on)} />
    </div>
  )
}
