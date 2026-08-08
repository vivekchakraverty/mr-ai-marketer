<!--
Please read CONTRIBUTING.md if you haven't. The short version: one change per PR, and say
how you checked it.
-->

## What this changes

<!-- And why. If it fixes an open issue, write "Fixes #123" so it closes on merge. -->

## How you tested it

<!--
Be specific and be honest. "Ran the app, generated three posts, all came back" is genuinely
useful. So is "I couldn't test the Mastodon path, I don't have an account" — say which parts
you didn't verify rather than leaving it unsaid.
-->

## Checklist

- [ ] `cd backend && .venv\Scripts\python -m pytest app/tests -q` passes
- [ ] `cd electron && npm run typecheck` passes
- [ ] `python scripts/security/scan.py` is clean
- [ ] No credential is hardcoded anywhere in this diff — new config reads from the
      environment and is documented in `backend/.env.example`
- [ ] If a dependency was added: both `requirements.txt` and `requirements.lock.txt`
      (or `package.json` and `package-lock.json`) are updated
- [ ] Anything non-obvious I worked out is written down as a comment explaining *why*

## Anything else worth knowing

<!-- Tradeoffs you made, things you were unsure about, follow-up work this leaves behind. -->
