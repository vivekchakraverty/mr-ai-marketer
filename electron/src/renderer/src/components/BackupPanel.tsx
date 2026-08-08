import { useEffect, useState } from 'react'
import { createBackup, deleteBackup, listBackups, restoreBackup, type BackupEntry } from '../api/client'
import { label, secondaryButtonSmall, textInput } from '../styles/styleKit'

/**
 * Backups of everything the app has made.
 *
 * There is no server holding a second copy of any of this — that is the point of the app, and
 * also the risk. A backup is the only way back from a corrupted file, a mistaken "Reset data"
 * on the Manage screen, or a disk that starts failing.
 *
 * Restoring is guarded rather than confirmed-with-a-dialog: it takes a snapshot of the
 * current state first, automatically, and tells you what that snapshot is called. Someone who
 * restores the wrong backup can therefore always get back, which a confirmation dialog does
 * not give you.
 */
function pretty(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export default function BackupPanel(): React.JSX.Element {
  const [backups, setBackups] = useState<BackupEntry[]>([])
  const [directory, setDirectory] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState('')
  const [confirming, setConfirming] = useState<string | null>(null)

  async function refresh(): Promise<void> {
    const res = await listBackups().catch(() => null)
    if (!res) return
    setBackups(res.backups)
    setDirectory(res.directory)
  }

  useEffect(() => {
    void refresh()
  }, [])

  async function run(key: string, fn: () => Promise<void>): Promise<void> {
    setBusy(key)
    try {
      await fn()
      await refresh()
    } catch {
      // The shared error popup shows it; nothing useful to add here.
    } finally {
      setBusy('')
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 12 }}>
        <div style={{ flex: '1 1 200px' }}>
          <label style={label}>Label (optional)</label>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="before the big campaign"
            style={textInput}
          />
        </div>
        <div
          style={{ ...secondaryButtonSmall, opacity: busy === 'create' ? 0.6 : 1 }}
          onClick={
            busy === 'create'
              ? undefined
              : () =>
                  void run('create', async () => {
                    await createBackup(note.trim())
                    setNote('')
                  })
          }
        >
          {busy === 'create' ? 'Backing up…' : 'Back up now'}
        </div>
      </div>

      {backups.length === 0 && (
        <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
          No backups yet. One copy of everything lives on this machine and nowhere else.
        </div>
      )}

      {backups.map((b) => (
        <div
          key={b.id}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '10px 12px',
            marginBottom: 8,
            background: 'var(--surface-tint)',
            border: '2px solid var(--border)',
            borderRadius: 14,
            flexWrap: 'wrap'
          }}
        >
          <div style={{ flex: 1, minWidth: 180 }}>
            <div style={{ font: "700 12.5px 'Quicksand'", color: 'var(--ink)' }}>{b.id}</div>
            <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
              {new Date(b.createdAt).toLocaleString()} · {b.databases.length} database
              {b.databases.length === 1 ? '' : 's'} · {pretty(b.bytes)}
            </div>
          </div>

          {confirming === b.id ? (
            <>
              <span style={{ font: "700 11.5px 'Quicksand'", color: 'var(--danger-ink)' }}>
                Replace current data?
              </span>
              <div
                style={{ ...secondaryButtonSmall, borderColor: 'var(--danger-ink)', color: 'var(--danger-ink)' }}
                onClick={() =>
                  void run('restore', async () => {
                    const res = await restoreBackup(b.id)
                    setConfirming(null)
                    window.alert(
                      `Restored ${res.restored.join(', ')}.\n\n` +
                        `Your previous data was saved as "${res.safetyBackup}".\n\n${res.detail}`
                    )
                  })
                }
              >
                {busy === 'restore' ? 'Restoring…' : 'Yes, restore'}
              </div>
              <div style={secondaryButtonSmall} onClick={() => setConfirming(null)}>
                Cancel
              </div>
            </>
          ) : (
            <>
              <div style={secondaryButtonSmall} onClick={() => void window.api.openFile(b.path)}>
                Show files
              </div>
              <div style={secondaryButtonSmall} onClick={() => setConfirming(b.id)}>
                Restore
              </div>
              <div
                style={secondaryButtonSmall}
                onClick={() => void run('delete', async () => void (await deleteBackup(b.id)))}
              >
                Delete
              </div>
            </>
          )}
        </div>
      ))}

      {directory && (
        <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 10 }}>
          Stored in <code>{directory}</code> — copy that folder somewhere else and you have an
          off-machine backup.
        </div>
      )}
    </div>
  )
}
