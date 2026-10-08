import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, sep } from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const root = mkdtempSync(join(tmpdir(), 'mraim-workspaces-'))
const compiledPath = join(root, 'workspaces.cjs')
const electronMockPath = join(root, 'electron-mock.cjs')

function boot() {
  delete require.cache[require.resolve(compiledPath)]
  delete require.cache[require.resolve(electronMockPath)]
  const api = require(compiledPath)
  const { paths } = require(electronMockPath)
  api.initializeWorkspaces()
  return { api, paths }
}

try {
  const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'main', 'workspaces.ts'), 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText.replace('require("electron")', 'require("./electron-mock.cjs")')
  writeFileSync(compiledPath, compiled)
  writeFileSync(electronMockPath,
    `const root=${JSON.stringify(root)}; const paths={userData:root,sessionData:root}; ` +
    'exports.paths=paths; exports.app={getPath:(name)=>paths[name],setPath:(name,value)=>{paths[name]=value}};'
  )

  const original = boot()
  assert.equal(original.paths.userData, root)
  writeFileSync(join(root, 'mr-ai-marketer.sqlite3'), 'original database')
  writeFileSync(join(root, 'config.enc'), 'original credentials')

  const created = original.api.createWorkspace('Second account')
  const second = created.workspaces.find((workspace) => workspace.id !== 'default')
  assert.ok(second)
  assert.ok(existsSync(join(root, 'workspaces', second.id)))
  assert.equal(original.api.previousWorkspaceNeedsCleanup(), false)
  original.api.markWorkspaceStarted()
  original.api.selectWorkspace(second.id)

  const next = boot()
  const secondDir = join(root, 'workspaces', second.id)
  assert.equal(next.paths.userData, secondDir)
  assert.equal(next.paths.sessionData, secondDir)
  assert.equal(existsSync(join(secondDir, 'mr-ai-marketer.sqlite3')), false)
  assert.equal(existsSync(join(secondDir, 'config.enc')), false)
  assert.equal(next.api.previousWorkspaceNeedsCleanup(), true)
  next.api.markWorkspaceStarted()
  assert.equal(next.api.previousWorkspaceNeedsCleanup(), false)

  next.api.renameWorkspace(second.id, 'Second renamed')
  assert.throws(() => next.api.createWorkspace('second renamed'), /already exists/)
  assert.throws(() => next.api.selectWorkspace(randomUUID()), /not found/)
  next.api.selectWorkspace('default')

  const restored = boot()
  assert.equal(restored.paths.userData, root)
  assert.equal(readFileSync(join(root, 'mr-ai-marketer.sqlite3'), 'utf8'), 'original database')
  assert.equal(readFileSync(join(root, 'config.enc'), 'utf8'), 'original credentials')
  assert.equal(restored.api.getWorkspaces().workspaces[1].name, 'Second renamed')
  console.log('Workspace migration, isolation, switch-back, and recovery checks passed.')
} finally {
  if (!root.startsWith(tmpdir() + sep)) throw new Error('Unsafe test cleanup path')
  rmSync(root, { recursive: true, force: true })
}
