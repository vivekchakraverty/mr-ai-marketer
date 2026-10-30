# Bundled ffmpeg / ffprobe

DocuMaker's audio/frame extraction and TutorialMaker's screenshot capture both shell out to
`ffmpeg`/`ffprobe` on PATH. Rather than requiring every user to install ffmpeg themselves, the
packaged app bundles static binaries here and Electron's `spawnPackagedBackend()` prepends this
directory to the backend process's `PATH` at launch.

These binaries are **not committed to git** (large, license-bearing, platform-specific) — fetch
them before running `npm run build:<platform>`:

- **Windows** (`resources/ffmpeg/win/ffmpeg.exe`, `ffprobe.exe`): any static Windows build, e.g.
  [gyan.dev's builds](https://www.gyan.dev/ffmpeg/builds/) — the smaller "essentials" build is
  plenty (~80MB) unless you need an exotic codec. The dev build on this machine used the "full"
  build via `scoop install ffmpeg`, which also works but is ~10x larger than necessary for what
  this app actually uses (audio extraction, frame grabs) — swap in "essentials" for a real release
  to keep the installer smaller.
- **macOS** (`resources/ffmpeg/mac/ffmpeg`, `ffprobe`): a native **arm64** static build —
  CI uses [Martin Riedl's builds](https://ffmpeg.martin-riedl.de/), pinned by version and by
  SHA-256. The arch matters: electron-builder builds the dmg for the host, `macos-latest` is
  arm64, and an x86_64 ffmpeg runs under Rosetta well enough to pass a smoke test while being the
  wrong binary to ship. Not [evermeet.cx](https://evermeet.cx/ffmpeg/), which this used to name:
  it is x86_64 only, and it retired the download API CI depended on (HTTP 410 from v1.1.0).
- **Linux** (`resources/ffmpeg/linux/ffmpeg`, `ffprobe`): a static build — CI uses
  [BtbN's GitHub-hosted builds](https://github.com/BtbN/FFmpeg-Builds/releases), checked against
  their published checksums. Not johnvansickle.com, which went dark and broke three releases.

Each platform's CI job in `.github/workflows/build.yml` downloads the matching pair into this
structure before invoking `npm run build:<platform>`, and the step after it fails the build if
either binary is missing or will not run. The macOS and Linux builds are both GPL static, so
switching between their sources has not changed the licence of what ships.
