import { fetchLibrary } from '../api/client'
import { useAppStore } from './store'

export async function refreshLibrary(): Promise<void> {
  const { setLibrary, setLibraryLoading } = useAppStore.getState()
  setLibraryLoading(true)
  try {
    const res = await fetchLibrary()
    setLibrary(res.items)
  } catch {
    // Backend unreachable (e.g. still booting) — keep whatever's already loaded. Every
    // caller fires this as `void refreshLibrary()`, so a throw here would surface as an
    // unhandled rejection rather than anything the user could act on.
  } finally {
    setLibraryLoading(false)
  }
}
