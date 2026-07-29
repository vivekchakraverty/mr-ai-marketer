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
- **macOS** (`resources/ffmpeg/mac/ffmpeg`, `ffprobe`): a static build such as
  [evermeet.cx](https://evermeet.cx/ffmpeg/) (both x86_64 and arm64 needed for a universal build,
  or ship per-arch).
- **Linux** (`resources/ffmpeg/linux/ffmpeg`, `ffprobe`): a static build such as
  [johnvansickle.com's builds](https://johnvansickle.com/ffmpeg/).

Each platform's CI job in `.github/workflows/build.yml` should download the matching pair into
this structure before invoking `npm run build:<platform>`.
