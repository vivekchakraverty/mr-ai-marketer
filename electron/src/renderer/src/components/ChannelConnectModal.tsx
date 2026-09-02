import ChannelConnectForm, { type ChannelConnectFooterContext } from './ChannelConnectForm'
import { PLATFORM_SETUP_GUIDES, type PlatformSetupGuide } from '../state/platformSetupGuides'
import { primaryButton, secondaryButtonSmall } from '../styles/styleKit'

interface Props {
  channel: string
  connected: boolean
  /** False while the distribution engine is unreachable — the form still opens (so the setup
   * steps are readable and credentials can be pasted in advance), but saving needs the engine. */
  engineReady?: boolean
  /** Supplied for user-added channels, whose form is generated from the piece's own auth
   * schema instead of one of the hand-written guides. */
  guide?: PlatformSetupGuide
  /** Only passed for user-added channels — built-in ones cannot be removed. */
  onRemove?: () => void
  onClose: () => void
  onChanged: () => void
}

/**
 * The connect dialog: a backdrop, a card, and the shared credential form inside it.
 *
 * The form itself lives in ChannelConnectForm because the first-run walkthrough shows the
 * same fields without this chrome. All this adds is the overlay and the footer — here,
 * saving also dismisses the dialog; in the walkthrough it advances a step instead.
 */
export default function ChannelConnectModal({
  channel,
  connected,
  engineReady = true,
  guide: guideOverride,
  onRemove,
  onClose,
  onChanged
}: Props): React.JSX.Element {
  const guide = guideOverride ?? PLATFORM_SETUP_GUIDES[channel]

  function done(): void {
    onChanged()
    onClose()
  }

  function footer({ busy, authKind, connect, disconnect }: ChannelConnectFooterContext): React.ReactNode {
    return (
      <div style={{ display: 'flex', gap: 10, marginTop: 8, flexWrap: 'wrap' }}>
        {authKind !== 'OAUTH2' && (
          <div style={{ ...primaryButton, flex: 1, opacity: busy ? 0.6 : 1 }} onClick={busy ? undefined : connect}>
            {busy ? 'Connecting…' : connected ? 'Reconnect' : 'Connect'}
          </div>
        )}
        {connected && (
          <div style={secondaryButtonSmall} onClick={busy ? undefined : disconnect}>
            Disconnect
          </div>
        )}
        {onRemove && (
          <div style={{ ...secondaryButtonSmall, color: 'var(--danger-ink)' }} onClick={busy ? undefined : onRemove}>
            Remove channel
          </div>
        )}
        <div style={secondaryButtonSmall} onClick={onClose}>
          {authKind === 'OAUTH2' ? 'Close' : 'Cancel'}
        </div>
      </div>
    )
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
          width: 460,
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
        <ChannelConnectForm
          channel={channel}
          connected={connected}
          engineReady={engineReady}
          guide={guide}
          onConnected={done}
          onDisconnected={done}
          footer={footer}
        />
      </div>
    </div>
  )
}
