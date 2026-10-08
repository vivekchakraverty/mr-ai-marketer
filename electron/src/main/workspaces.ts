import { app } from 'electron'
import { randomUUID } from 'crypto'
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'fs'
import { join } from 'path'

export interface Workspace {
  id: string
  name: string
}

export interface WorkspaceSnapshot {
  activeId: string
  workspaces: Workspace[]
}

interface WorkspaceRegistry extends WorkspaceSnapshot {
  lastStartedId?: string
}

const DEFAULT_ID = 'default'
// Capture this before setPath changes Electron's userData path. The existing install stays
// exactly where it was; only new workspaces use child directories under this root.
const rootDataDir = app.getPath('userData')
const registryPath = join(rootDataDir, 'workspaces.json')
const workspaceDir = (id: string): string =>
  id === DEFAULT_ID ? rootDataDir : join(rootDataDir, 'workspaces', id)

function validate(value: unknown): WorkspaceRegistry {
  if (!value || typeof value !== 'object') throw new Error('Workspace list is damaged.')
  const registry = value as WorkspaceRegistry
  if (!Array.isArray(registry.workspaces) || typeof registry.activeId !== 'string') {
    throw new Error('Workspace list is damaged.')
  }
  const ids = new Set<string>()
  for (const workspace of registry.workspaces) {
    if (!workspace || typeof workspace.name !== 'string' || !workspace.name.trim() ||
        typeof workspace.id !== 'string' ||
        (workspace.id !== DEFAULT_ID && !/^[0-9a-f-]{36}$/.test(workspace.id)) ||
        ids.has(workspace.id)) {
      throw new Error('Workspace list is damaged.')
    }
    ids.add(workspace.id)
  }
  if (!ids.has(DEFAULT_ID) || !ids.has(registry.activeId)) {
    throw new Error('Workspace list is damaged.')
  }
  return registry
}

function readRegistry(): WorkspaceRegistry {
  if (!existsSync(registryPath)) {
    return { activeId: DEFAULT_ID, workspaces: [{ id: DEFAULT_ID, name: 'My workspace' }] }
  }
  return validate(JSON.parse(readFileSync(registryPath, 'utf8')))
}

function writeRegistry(registry: WorkspaceRegistry): void {
  const tempPath = `${registryPath}.${randomUUID()}.tmp`
  writeFileSync(tempPath, JSON.stringify(registry, null, 2), 'utf8')
  renameSync(tempPath, registryPath)
}

export function initializeWorkspaces(): void {
  mkdirSync(rootDataDir, { recursive: true })
  const registry = readRegistry()
  if (!existsSync(registryPath)) writeRegistry(registry)
  if (registry.activeId !== DEFAULT_ID) {
    const path = workspaceDir(registry.activeId)
    // Return to the original workspace if a removable/missing profile folder cannot be
    // opened. Keep the profile in the list so restoring its folder recovers the data.
    if (!existsSync(path)) {
      console.error(`[workspaces] data folder is missing for ${registry.activeId}; opening the original workspace`)
      registry.activeId = DEFAULT_ID
      writeRegistry(registry)
      return
    }
    app.setPath('userData', path)
    app.setPath('sessionData', path)
  }
}

export function getWorkspaces(): WorkspaceSnapshot {
  const { activeId, workspaces } = readRegistry()
  return { activeId, workspaces }
}

/** A prior profile may have left fixed-port Docker services alive after a crash. */
export function previousWorkspaceNeedsCleanup(): boolean {
  const registry = readRegistry()
  return Boolean(registry.lastStartedId && registry.lastStartedId !== registry.activeId)
}

export function markWorkspaceStarted(): void {
  const registry = readRegistry()
  registry.lastStartedId = registry.activeId
  writeRegistry(registry)
}

export function createWorkspace(rawName: string): WorkspaceSnapshot {
  const name = rawName.trim()
  if (!name || name.length > 60) throw new Error('Enter a workspace name of 1–60 characters.')
  const registry = readRegistry()
  if (registry.workspaces.some((workspace) => workspace.name.toLocaleLowerCase() === name.toLocaleLowerCase())) {
    throw new Error('A workspace with that name already exists.')
  }
  const id = randomUUID()
  mkdirSync(workspaceDir(id), { recursive: true })
  registry.workspaces.push({ id, name })
  writeRegistry(registry)
  return getWorkspaces()
}

export function renameWorkspace(id: string, rawName: string): WorkspaceSnapshot {
  const name = rawName.trim()
  if (!name || name.length > 60) throw new Error('Enter a workspace name of 1–60 characters.')
  const registry = readRegistry()
  const workspace = registry.workspaces.find((item) => item.id === id)
  if (!workspace) throw new Error('Workspace was not found.')
  if (registry.workspaces.some((item) => item.id !== id && item.name.toLocaleLowerCase() === name.toLocaleLowerCase())) {
    throw new Error('A workspace with that name already exists.')
  }
  workspace.name = name
  writeRegistry(registry)
  return getWorkspaces()
}

export function selectWorkspace(id: string): WorkspaceSnapshot {
  const registry = assertWorkspaceAvailable(id)
  registry.activeId = id
  writeRegistry(registry)
  return getWorkspaces()
}

export function assertWorkspaceAvailable(id: string): WorkspaceSnapshot {
  const registry = readRegistry()
  if (!registry.workspaces.some((workspace) => workspace.id === id)) {
    throw new Error('Workspace was not found.')
  }
  if (!existsSync(workspaceDir(id))) {
    throw new Error('Workspace data is missing. Nothing was switched.')
  }
  return registry
}
