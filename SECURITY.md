# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for a security problem. Use GitHub's private reporting
instead: go to the **Security** tab → **Report a vulnerability**. That opens a private thread
visible only to the maintainers.

If that isn't available to you, email **vivekchakraverty@gmail.com** with `SECURITY` in the
subject line.

This is a personal project maintained by one person, so please be realistic about response
times: expect an acknowledgement within about a week. There is no bug bounty.

When you report, the useful things to include are what you did, what happened, and why you
think it's a problem. A proof of concept helps enormously; so does telling us which version
or commit you were on.

## What counts

This app runs entirely on the user's own machine, which shapes what a vulnerability actually
looks like here. The things worth reporting:

- **Anything that lets a remote party reach the local backend.** The API binds to localhost
  and is meant to be reachable only by the app itself. A CORS gap, a DNS-rebinding path, or a
  missing origin check that lets a web page in the user's browser talk to it is a real bug.
- **Credential exposure.** Tokens and passwords are stored encrypted on disk. Anything that
  writes one in plaintext, logs one, or sends one somewhere the user didn't point the app at
  is a real bug — including in crash reports and debug output.
- **Command or code injection** through content the app fetches: a scraped page, a Mastodon
  post, an RSS item, an LLM response. Any of these are attacker-influenced data.
- **Path traversal** in the file-writing paths — document generation, the library, backups.
- **Anything that makes the app publish, send, or post without the user asking.**
- **Dependency vulnerabilities** that are actually reachable from this code. Please check
  reachability first; a CVE in a transitive package that this app never calls is noise.

## What doesn't

- Findings that require an attacker who already has code execution or filesystem access as the
  user. At that point the machine is theirs and the app can't defend against it.
- The mail-tracking Space's shared sync secret. It's documented as a deterrent against casual
  scraping and nothing more — the real privacy boundary is client-side, in `sync_from_space()`,
  which discards any event whose token isn't a message that installation actually sent. See the
  module docstring in `backend/app/services/mail_tracking.py`.
- Self-XSS, missing headers on endpoints that only ever serve localhost, or scanner output
  pasted without a working exploit.
- Anything in `backend/vendor/`, which is vendored upstream code. Report those upstream.

## Running the scanners yourself

```bash
python scripts/security/scan.py
```

That runs gitleaks, Trivy and Semgrep and prints one summary. Read the script's docstring
before trusting a clean result from any of them individually — each of those tools reports a
false pass on this project when invoked the obvious way, and the script exists to work around
exactly that.

## Supported versions

The latest release on the `main` branch. This project doesn't backport fixes to older tags.
