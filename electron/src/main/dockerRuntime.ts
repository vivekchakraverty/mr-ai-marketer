import { spawn, type ChildProcess } from 'child_process'

// Windows-only: manages a bare Docker Engine running inside WSL2 (not Docker Desktop).
// See the Distribution Layer plan for the full rationale — Activepieces needs a real
// Linux-container layer (its custom-code step uses a kernel-level sandbox), and this
// avoids requiring the heavier Docker Desktop GUI app.
//
// All Docker interaction happens via `wsl.exe -d <distro> -- ...`, never a `docker` CLI
// on the Windows PATH — the daemon stays inside the WSL2 VM, no exposed TCP port.
const WSL_DISTRO = 'Ubuntu'

export interface DockerRuntimeStatus {
  wslInstalled: boolean
  distroPresent: boolean
  distroIsV2: boolean
  dockerInstalled: boolean
  dockerRunning: boolean
}

export class RebootRequiredError extends Error {
  constructor() {
    super('Restart your computer to finish enabling WSL2, then reopen Distribute to continue setup.')
    this.name = 'RebootRequiredError'
  }
}

interface RunResult {
  stdout: string
  stderr: string
  code: number
}

function runRaw(exe: string, args: string[]): Promise<{ buffer: Buffer; code: number }> {
  return new Promise((resolve) => {
    const proc = spawn(exe, args, { windowsHide: true })
    const chunks: Buffer[] = []
    proc.stdout?.on('data', (c: Buffer) => chunks.push(c))
    proc.stderr?.on('data', (c: Buffer) => chunks.push(c))
    proc.on('close', (code) => resolve({ buffer: Buffer.concat(chunks), code: code ?? -1 }))
    proc.on('error', () => resolve({ buffer: Buffer.alloc(0), code: -1 }))
  })
}

// wsl.exe's own launcher-level commands (`wsl -l -v`, `wsl --status`, not `-d <distro> --`)
// emit UTF-16LE on Windows; commands run *inside* a distro produce plain UTF-8 from the
// Linux program itself. Confirmed against a real WSL install, not assumed from docs.
async function runWslLauncher(args: string[]): Promise<RunResult> {
  const { buffer, code } = await runRaw('wsl.exe', args)
  const looksUtf16 = buffer.length > 3 && buffer[1] === 0 && buffer[3] === 0
  const text = buffer.toString(looksUtf16 ? 'utf16le' : 'utf8')
  return { stdout: text, stderr: '', code }
}

async function runInDistro(args: string[], asRoot = true): Promise<RunResult> {
  const wslArgs = ['-d', WSL_DISTRO, ...(asRoot ? ['-u', 'root'] : []), '--', ...args]
  const { buffer, code } = await runRaw('wsl.exe', wslArgs)
  const text = buffer.toString('utf8')
  return { stdout: text, stderr: code !== 0 ? text : '', code }
}

export async function detectStatus(): Promise<DockerRuntimeStatus> {
  const listResult = await runWslLauncher(['-l', '-v'])
  const wslInstalled = listResult.code === 0
  const distroPresent = wslInstalled && listResult.stdout.includes(WSL_DISTRO)
  const distroIsV2 = distroPresent && new RegExp(`${WSL_DISTRO}\\s+\\S+\\s+2`).test(listResult.stdout)

  let dockerInstalled = false
  let dockerRunning = false
  if (distroPresent) {
    const which = await runInDistro(['sh', '-c', 'command -v docker'])
    dockerInstalled = which.code === 0 && which.stdout.trim().length > 0
    if (dockerInstalled) {
      const version = await runInDistro(['docker', 'version'])
      dockerRunning = version.code === 0 && version.stdout.includes('Server:')
    }
  }

  return { wslInstalled, distroPresent, distroIsV2, dockerInstalled, dockerRunning }
}

/** Converts a Windows path (e.g. `V:\mr ai marketer\resources`) to its WSL mount path
 * (`/mnt/v/mr ai marketer/resources`) so Docker running inside WSL can read our bundled
 * files directly — no need to copy anything into the Linux filesystem. */
export function toWslPath(windowsPath: string): string {
  const drive = windowsPath[0].toLowerCase()
  const rest = windowsPath.slice(2).replace(/\\/g, '/')
  return `/mnt/${drive}${rest}`
}

export type BootstrapProgress = (step: string) => void

export async function bootstrap(onProgress: BootstrapProgress): Promise<void> {
  let status = await detectStatus()

  if (!status.wslInstalled || !status.distroPresent) {
    onProgress('Installing WSL2 + Ubuntu — this can take a few minutes…')
    await runRaw('wsl.exe', ['--install', '-d', WSL_DISTRO, '--no-launch'])
    status = await detectStatus()
    if (!status.distroPresent) {
      // Underlying virtualization features (Virtual Machine Platform) needed enabling and
      // that only takes effect after a restart — this is the one interruption we can't avoid.
      throw new RebootRequiredError()
    }
  }

  if (!status.dockerInstalled) {
    onProgress('Installing Docker Engine inside WSL…')
    const install = await runInDistro(['sh', '-c', 'curl -fsSL https://get.docker.com | sh'])
    if (install.code !== 0) {
      throw new Error(`Docker Engine install failed: ${install.stderr.slice(0, 500)}`)
    }
  }

  onProgress('Starting the Distribution engine…')
  await ensureDaemonRunning()
}

export async function ensureDaemonRunning(timeoutMs = 30000): Promise<void> {
  const already = await runInDistro(['docker', 'version'])
  if (already.code === 0) return

  await runInDistro(['sh', '-c', 'dockerd > /var/log/dockerd.log 2>&1 & disown'])

  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const poll = await runInDistro(['docker', 'version'])
    if (poll.code === 0) return
    await new Promise((r) => setTimeout(r, 1000))
  }
  throw new Error('Docker daemon did not become ready in time.')
}


/**
 * The address the Windows host answers on from inside the WSL VM, or '' if unavailable.
 *
 * This is the VM's default gateway, which is the Windows side of the `vEthernet (WSL)`
 * adapter. It is assigned per boot, so it cannot be hardcoded — and it is emphatically NOT
 * `host-gateway`, the answer Docker Desktop would give: on a bare Docker Engine inside WSL2
 * that resolves to the VM's own bridge (172.17.0.1), which answers nothing.
 *
 * Measured on a real install: gateway 172.30.240.1, matching the Windows adapter exactly,
 * and a Windows port bound there was reachable from inside the VM with no firewall rule.
 */
export async function wslHostAddress(): Promise<string> {
  const result = await runInDistro(['sh', '-c', 'ip route show default'])
  if (result.code !== 0) return ''
  const match = /default\s+via\s+(\d+\.\d+\.\d+\.\d+)/.exec(result.stdout)
  return match ? match[1] : ''
}

export function dockerCommand(args: string[]): Promise<RunResult> {
  return runInDistro(['docker', ...args])
}

// WSL2's lightweight VM suspends itself shortly after the last foreground `wsl.exe` session
// ends, regardless of background services (dockerd, containers) still logically running inside
// it — confirmed empirically: a container that had fully booted and reported "listening" went
// unreachable a couple of minutes later with no crash in its own logs, then came back the next
// time a `wsl.exe` command touched the distro. Since our port-forwarded HTTP requests to
// Activepieces don't reliably wake a suspended VM themselves, we hold one long-lived `wsl.exe`
// process open for as long as Activepieces needs to stay reachable — the standard community
// workaround for this behavior, and consistent with avoiding a `.wslconfig` edit (a global,
// user-visible machine setting) for something our app can just handle on its own.
let keepAliveProcess: ChildProcess | null = null

export function startWslKeepAlive(): void {
  if (keepAliveProcess && keepAliveProcess.exitCode === null) return
  keepAliveProcess = spawn('wsl.exe', ['-d', WSL_DISTRO, '--', 'sleep', 'infinity'], {
    windowsHide: true,
    stdio: 'ignore'
  })
  keepAliveProcess.on('exit', () => {
    keepAliveProcess = null
  })
}

export function stopWslKeepAlive(): void {
  keepAliveProcess?.kill()
  keepAliveProcess = null
}
