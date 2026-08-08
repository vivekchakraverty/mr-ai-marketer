# Contributing

Thanks for looking. This is a personal project that got large, so the most useful thing to
know up front is how it's put together — [README.md](README.md) covers that, and the sections
on *How it's built* and *The technology, explained* are worth reading before you change
anything.

## Getting a working checkout

The full instructions are in [Getting set up](README.md#getting-set-up). The short version:

```bash
cd backend && python -m venv .venv && .venv\Scripts\pip install -r requirements.lock.txt
```

```bash
cd electron && npm install && npm run dev
```

Install from `requirements.lock.txt`, not `requirements.txt`. The lock is the exact set of
versions this app is tested against; `requirements.txt` is the shorter commented list of direct
dependencies, and resolving it fresh currently picks up a protobuf major version this app has
not been tested with. Use `requirements.txt` only when you are deliberately upgrading
something — and then regenerate the lock (below) in the same commit.

You'll want a `backend/.env` too. Copy `backend/.env.example` and fill in what you need; every
variable in it is optional and the app degrades honestly when one is missing.

## Before you open a pull request

Run these three. All of them pass on `main`, so a failure is something you introduced:

```bash
cd backend && .venv\Scripts\python -m pytest app/tests -q
```

```bash
cd electron && npm run typecheck
```

```bash
python scripts/security/scan.py
```

The security scan matters more than it looks. Don't substitute a bare `gitleaks detect` or
`trivy fs .` for it — each of those returns a false clean on this repo, and the script's
docstring explains why for each tool.

## House style

The code in this repo is commented unusually heavily, and specifically it comments **why**
rather than what. Several of those comments exist because the obvious approach was tried and
broke something — for example the `# Read from the environment, never hardcoded` note in
`backend/app/services/mail_tracking.py`, or the by-reference cloudpickle trap in the
BrandForge Modal backend. Please match that: if you work out something non-obvious, leave the
reasoning behind for whoever hits it next. If you remove a comment like that, say in the PR
why it no longer applies.

Otherwise, match the surrounding file. There is no formatter or linter enforced in CI, so
consistency is by hand: 4-space indent in Python, 2-space in TypeScript, and existing naming
patterns over your preferred ones.

**Never hardcode a credential**, not even a low-value shared one, and not even temporarily.
Put it in `config.py` reading from the environment, and add it to `backend/.env.example` with
a comment saying what it's for and what happens when it's empty. A secret with a default is a
secret every person who installs the app has.

## Adding a tool

Most tools here follow the same shape: a router in `backend/app/routers/`, a service in
`backend/app/services/` (or a vendored project under `backend/vendor/`), a route component in
`electron/src/renderer/src/routes/`, and registration in the nav and the store. Copy the
closest existing tool rather than starting from scratch — the wiring has more steps than it
looks, and missing one usually shows up as a screen that renders but never loads.

## Dependencies

Adding a Python dependency means editing **both** files:

1. Add it with a comment to `requirements.txt` saying what needs it.
2. Regenerate the lock so CI installs the same thing you tested:

```bash
cd backend && .venv\Scripts\pip freeze > requirements.lock.txt
```

For npm, `npm install <pkg>` updates `package.json` and `package-lock.json` together — commit
both. Please be conservative here: this app ships as a desktop installer, so every dependency
is weight the user downloads, and every native module is a thing that can fail to build on one
of the three platforms.

## Releasing a new version

Installed copies of the app check GitHub Releases for updates — at launch and every six hours
after — and can download and install one in place. That machinery only works if a release is
cut in the exact shape it expects, so the steps aren't optional.

1. **Bump `version` in `electron/package.json`.** This is the number the running app compares
   against, so it's the one that decides whether anyone is offered an update.
2. Commit that, then tag it **`v` + the same version** and push the tag:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

The tag and `electron/package.json` must agree. Nothing enforces it, and if they disagree the
release is named one thing while the app announces another.

3. The tag triggers [.github/workflows/build.yml](.github/workflows/build.yml), which builds
   all three platforms and opens a **draft** release with the installers attached.
4. Write the release notes. They're shown to users in the app, so write them for the person
   who has to decide whether to restart their work, not for a changelog.
5. **Publish the release.** This is the step that ships it: electron-updater reads the
   *latest published* release, so a draft is invisible to every installed copy. Nothing
   reaches anyone until you click Publish.

### Don't detach these files from the release

Each release must keep `latest.yml` and the `.blockmap` next to the installer. They aren't
build noise:

- **`latest.yml`** is the update feed. Without it the updater has nothing to read, and every
  installed app quietly concludes it's up to date — forever, with no error anywhere.
- **`<installer>.exe.blockmap`** is what makes updates small. With it, the updater downloads
  only the blocks that changed rather than the full ~600MB. Delete it and every user
  re-downloads the whole installer for a one-line fix.

The workflow uploads both. If you ever build and attach a release by hand, attach them too.

### Don't put spaces in the installer filename

`artifactName` in `electron/package.json` uses `${name}` (`mr-ai-marketer`), not
`${productName}` (`Mr. AI Marketer`), and that is load-bearing rather than cosmetic. GitHub
rewrites spaces in release asset filenames, electron-builder writes its own guess at the
rewritten name into `latest.yml`, and when those two guesses disagree the updater downloads a
URL that 404s. The failure shows up only in a real end-to-end update against a published
release — never in a local build — so it's an expensive one to reintroduce. A filename with no
spaces in it cannot be rewritten, so there is nothing to disagree about.

### Two things that are genuinely broken and shouldn't surprise you

- **macOS auto-update does not work**, and can't until the app is signed and notarised —
  Squirrel.Mac refuses unsigned updates. Mac users have to download the new `.dmg` themselves.
  Windows is unaffected: NSIS updates work fine unsigned.
- **Windows updates are unsigned**, so `publisherName` is deliberately absent from the build
  config. Setting it would make electron-updater verify a signature that isn't there and fail
  every update. If a code-signing certificate is ever bought, set `publisherName` at the same
  time — until then the update channel is authenticated only by HTTPS to GitHub.

## Vendored code

`backend/vendor/` holds separate projects folded into this one. `docmaker` is a git subtree —
update it with `git subtree pull`, not by editing files in place, or the next pull will
conflict with your changes. Treat the rest as upstream too: fix bugs where the code came from
where you can.

## Pull requests

Keep them focused — one change per PR. Say what you changed and why, and how you checked it
works; if you tested it by using the app rather than by a test, say that, it's still useful
information. If you couldn't test part of it, say which part. An honest "I didn't verify the
Mastodon path, I don't have an account" is far more useful than silence.

## Reporting bugs and security issues

Bugs: open an issue with what you did, what happened, and what you expected. The app version
is in Settings.

Security problems go through [SECURITY.md](SECURITY.md) instead — please don't open a public
issue for those.
