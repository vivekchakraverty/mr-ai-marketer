# Mr. AI Marketer

[![CI](https://github.com/vivekchakraverty/mr-ai-marketer/actions/workflows/ci.yml/badge.svg)](https://github.com/vivekchakraverty/mr-ai-marketer/actions/workflows/ci.yml)
[![Build desktop app](https://github.com/vivekchakraverty/mr-ai-marketer/actions/workflows/build.yml/badge.svg)](https://github.com/vivekchakraverty/mr-ai-marketer/actions/workflows/build.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6.svg)](#getting-set-up)

A desktop app that does the marketing work a small team would otherwise do by hand: research
what to say, write it, publish it, and track what happened. It runs on **your own computer**,
using **your own accounts**. There is no company server in the middle, no subscription, and
nothing of yours is uploaded anywhere you didn't point it at.

It looks like a friendly beach-themed app. Underneath it is about twenty separate tools
wired into one place.

---

## Table of contents

- [What you can actually do with it](#what-you-can-actually-do-with-it)
- [What it costs to run](#what-it-costs-to-run)
- [Getting set up](#getting-set-up)
- [The tools, one by one](#the-tools-one-by-one)
- [How it's built (in plain terms)](#how-its-built-in-plain-terms)
- [The technology, explained](#the-technology-explained)
- [Configuration reference](#configuration-reference)
- [Keeping an eye on it](#keeping-an-eye-on-it)
- [Checking it for security problems](#checking-it-for-security-problems)
- [Privacy and your data](#privacy-and-your-data)
- [Troubleshooting](#troubleshooting)
- [What this is built on, and their licences](#what-this-is-built-on-and-their-licences)

---

## What you can actually do with it

Ten screens, grouped by what you're trying to get done:

| Screen | What it's for |
| --- | --- |
| **Home** | The front door — recent work and shortcuts. |
| **Research / Strategy** | Decide what to do: build a marketing plan, define your brand, find topics worth writing about, find sales leads, browse an influencer database. |
| **Create** | Write it: blog posts, guest posts, tutorials, documentation, social posts, Mastodon posts, marketing emails. |
| **Engage** | Your own Bluesky, Mastodon and Tumblr feeds — reply, post, keep up with your community. |
| **Analytics** | What happened: your sales pipeline, email opens and clicks, how your posts performed against comparable accounts. |
| **Manage** | A live planning workspace where you track campaigns and budgets. |
| **Community** | Run a Telegram community: an open group anyone can be added to, and a paid channel people subscribe to. |
| **Distribute** | Connect the places you publish to, and push finished work out to them. |
| **Library** | Everything the app has ever made for you, in one list. |
| **Settings** | Your accounts and keys. All stored encrypted on your machine. |

---

## What it costs to run

The app itself is free. The AI models it uses are not free to run, but they're cheap, and two
accounts cover essentially everything.

### Hugging Face — budget around **$10/month**

[Hugging Face](https://huggingface.co) is where the AI models live. Think of it as an app
store for AI, except you rent the computer that runs them by the second.

Sign up, then add around **$10 of monthly credits**. In practice that covers a lot: most
generation in this app is short bursts of text, and the amount you'd spend writing a few
blog posts, a stack of social posts and a marketing plan lands well inside that. You can set
a hard spending cap so it can never surprise you.

You'll need a **token** (Hugging Face's word for a password that apps use on your behalf).
Create one at Settings → Access Tokens, then paste it into this app's Settings screen. Give
it read access; if you plan to deploy your own Spaces, give it write access too.

### Modal — **free, and they give you about $30 of GPU credit**

[Modal](https://modal.com) rents graphics cards by the second. Graphics cards are what make
image generation and the larger text models fast.

Signing up is free and new accounts get roughly **$30 of free GPU credit** — for this app's
usage that goes a long way, and for many people it never runs out. You only need Modal if you
want Brand Studio to generate brand visuals and run its text model on a fast GPU. Skip it and
everything else still works.

Modal bills per second of GPU time, so set a budget cap under **Usage & Billing → Budgets**
before you start. That takes ten seconds and removes the only real risk.

### Everything else is free

Bluesky, Mastodon, Reddit, Discord, Telegram, LinkedIn, Facebook and the rest are free
accounts. The search tools use free public sources. The database is a file on your disk.

---

## Getting set up

### Just want to use it

Download the latest `mr-ai-marketer-Setup-<version>-x64.exe` from
[Releases](../../releases) and run it. Windows 10/11, 64-bit. Nothing else to install —
Python and the whole engine are inside the installer, which is why it's around 600MB.

**Windows will warn you before it runs.** You'll get a blue *"Windows protected your PC"*
box from SmartScreen. That's expected and it isn't a virus warning: it means the installer
isn't signed with a code-signing certificate, which costs a few hundred dollars a year and
this project doesn't have one. Click **More info** → **Run anyway**. If you'd rather not
take that on trust, build it yourself from source below — the result is the same app.

It installs for your user only, so it won't ask for administrator rights, and you can put
it wherever you like.

Your data — the database and everything the app generates — lives in
`%APPDATA%\mr-ai-marketer`, separate from the program itself. **Uninstalling deliberately
leaves it alone**, so reinstalling or upgrading picks up exactly where you left off. If you
want it gone, delete that folder by hand.

#### Updates

The app checks for new versions when it starts, and every six hours while it's open. When
there's one, a bar appears across the top saying so.

**Nothing downloads until you say so.** The update is roughly the size of the installer, and
spending that much of your connection without asking would be rude — so you get a *Download
update* button, then a progress bar, then a *Restart and install* button. If you'd rather not
restart right then, click nothing: it installs the next time you close the app.

Updates after the first are usually much smaller than a full re-download. The app works out
which parts actually changed and fetches only those.

Your settings, accounts and everything in your Library survive an update untouched — the
update replaces the program, not your data folder. You can check for updates by hand any time
in **Settings → App updates**, which is also where the current version is shown.

On macOS this doesn't work yet and the app will tell you so: Apple requires updates to be
signed, and this project doesn't have a certificate. Mac users download new versions manually.

### Building it yourself

#### What you need first

- **Windows 10/11** (the app is built for Windows today).
- **Node.js 20+** — [nodejs.org](https://nodejs.org).
- **Python 3.11 or 3.12** — [python.org](https://python.org).
- **WSL2 with Docker** — only needed for publishing and lead generation. The app walks you
  through installing it the first time you open Distribute.

#### Install

```bash
git clone <your fork of this repo>
cd "mr ai marketer"
```

Backend:

```bash
cd backend && python -m venv .venv && .venv\Scripts\pip install -r requirements.lock.txt
```

`requirements.lock.txt` is the exact set of versions this app is tested with.
`requirements.txt` is the shorter, commented list of what it directly needs — install from
that one instead if you're deliberately upgrading something.

App:

```bash
cd electron && npm install && npm run dev
```

#### Building the installer

Three steps, in order. The backend has to be frozen before Electron can bundle it, because
electron-builder copies `backend/dist/` in as a resource.

First, fetch `ffmpeg.exe` and `ffprobe.exe` into `resources/ffmpeg/win/` — they aren't in the
repo. [resources/ffmpeg/README.md](resources/ffmpeg/README.md) says where to get them. Use the
**essentials** build, not full: this app only extracts audio and grabs frames, and full adds
about 300MB to what every user downloads for codecs nothing here calls.

Then freeze the backend (slow — around 45 minutes, and it produces roughly 1.3GB):

```bash
cd backend && .venv\Scripts\python -m PyInstaller backend.spec --noconfirm
```

Then build the installer:

```bash
cd electron && npm run build:win
```

The result is `electron/release/mr-ai-marketer-Setup-<version>-x64.exe`, around 600MB. It's
unsigned unless you supply a certificate, so it will trip SmartScreen exactly as described
above.

Tagging a commit `v*` runs all of this on GitHub Actions for Windows, macOS and Linux and
opens a draft release — see [.github/workflows/build.yml](.github/workflows/build.yml).

### First run

Open **Settings** and paste in your Hugging Face token. That alone unlocks most of the app.
Everything else — Bluesky, Mastodon, your mail account, Modal — is optional and can be added
whenever you need the tool that uses it.

Three tools (Blog Writer, Email Writer, Brand Studio) run **fine-tuned models you deploy
yourself** as Hugging Face Spaces. A Space is a small hosted app; the free CPU tier is enough,
it just takes a minute to wake up. See [Configuration reference](#configuration-reference)
for which variable points at which Space. Until you deploy them, those three tools say so
plainly instead of failing in a confusing way.

---

## The tools, one by one

### Research / Strategy

**Marketing Plan Generator.** Describe your business, budget and team. Get back a ten-section
strategy — SEO, social, paid ads, a budget split and a 90-day timeline. It looks up real
keyword data and real advertising benchmarks rather than inventing numbers, and it reads from
a library of marketing books to ground its advice.

**Brand Studio.** A twelve-section brand document: who you are, how you sound, what you
promise, what you never say, plus visual direction. Written by a model fine-tuned on branding
literature. Can also generate brand imagery if you connect Modal.

**Topic Scout.** Finds stories gaining momentum in your niche before they're obvious. Pulls
from news, forums, code repositories, research papers, search trends, video and social, then
groups them into topics, measures each against the previous period, and reads the mood around
them. Every topic comes with its sources so you can check the claim.

**Lead Gen Agent.** Describe what you sell and who buys it. An agent finds matching
businesses, learns your taste as you approve and reject them, finds and verifies email
addresses, and drafts personalised outreach for you to approve. Nothing sends without you.

**Influencer Database.** A bundled catalogue of Instagram creators. Filter by niche, follower
count and post count, narrow to verified or contactable, export a shortlist as a spreadsheet.

### Create

**Blog Writer.** SEO-shaped articles from a topic and a keyword, exported as a Word document.

**Guest Post Suggester.** Finds sites that publish outside contributors, ranked by authority,
and pulls out their submission details.

**Tutorial Maker.** Give it a topic; it finds the best tutorial video, reads the comments to
judge whether viewers actually rate it, transcribes it, and writes a step-by-step guide with
real screenshots pulled from the video at the right moments.

**DocuMaker.** Upload a screen recording. It transcribes what you did, writes the
documentation, and matches screenshots to each step.

**Social Post Generator.** Learns from posts that genuinely performed well in your corner of
the internet, then writes in that shape — without copying anyone. Gets better the longer you
use it.

**Mastodon Post Creator.** The same idea for Mastodon, with one addition: Mastodon has no
central rulebook, and servers differ sharply on automation and promotion. The tool reads your
server's actual rules and makes you read them before it will write anything.

**Email Writer.** Marketing emails from a one-line brief, written by a model fine-tuned on
real campaigns. It also predicts a click-through rate so you can compare drafts — a
statistical estimate, always labelled as one.

**Hashtag Suggester.** Built into both post composers. Ranks tags on fit to your draft, what's
trending, and real usage numbers. When it can't get real data it says so rather than guessing.

### Engage, Analytics, Manage, Community, Distribute

**Engage** puts your Bluesky timeline and notifications, your Mastodon community, and your
Tumblr dashboard and activity, inside the app so you can reply without leaving. Each tab
speaks its own network's language rather than a lowest common denominator: the Tumblr side
reblogs (with or without a comment) instead of replying, because that is what Tumblr's API
can do and how conversation there actually works, and it treats tags as a real field because
on Tumblr they are the distribution.

**Analytics** has three views: your **sales pipeline** as a board, **email** opens/clicks/
bounces for everything the app has sent, and **Bluesky** performance compared against a
cohort of similar accounts.

**Manage** is a live planning workspace for campaigns and budgets — you type in the inputs,
the totals recalculate.

**Community** runs a Telegram community from inside the app. It is two chats, because
Telegram shows every message in a chat to everyone in it and gives you no way to hide one post
from some members: an **open group** your admins add people to, and a **paid channel** people
subscribe to. Telegram itself takes the subscription payment (in Telegram Stars), renews it
monthly, and removes anyone who stops paying — no card details ever touch this app.

Both chats are embedded in the app as the real Telegram client, so you read and reply without
leaving. There are two ways in:

* a **bot** you make with Telegram's @BotFather, which runs the paid side, and
* your **own Telegram account**, signed in on the Account tab, which is what lets you *create*
  groups and add members — a bot can do neither, by Telegram's design.

The account login needs an `api_id`/`api_hash` you get free from
[my.telegram.org/apps](https://my.telegram.org/apps); the app ships without one on purpose, so
every install uses its own. Your login is stored encrypted on your machine and shows up in
Telegram under Settings → Devices, where you can revoke it any time.

One honest limit: Telegram lets people refuse being added to groups by anyone who isn't a
contact, and slows down accounts that add lots of strangers. The app reports each person's
result individually and offers an invite link for the ones it can't add, rather than pretending
it worked.

**Distribute** connects your publishing channels and pushes finished work out. Ten are built
in (Bluesky, Mastodon, X, LinkedIn, Facebook, Instagram, Discord, Reddit, email, and Discord
replies) and you can **add hundreds more yourself** — the engine ships with connectors for
around 750 services, and the app can browse that catalogue and wire a new one up for you
without writing any code.

Reddit posts and Discord replies always pause for your approval before they go out. That's
deliberate: it's the line between automation that builds an audience and automation that
reads as spam.

---

## Where the data lives

No dataset or model file ships inside the app. The site catalogues, the influencer database
and the click-through-rate model all live in Hugging Face repos and are downloaded once, on
first use, into your own data folder. Three reasons: the installer stays small, a corrected
catalogue is a push rather than a release, and a restricted dataset stays restrictable —
a file baked into a downloaded app is a file that can never be taken back.

The one thing that is never distributed at all is the CTR model's **training data**. You get
the finished model; the campaign data it was fitted on stays in a private repo that only the
retraining script reads. A model can be shared without the corpus behind it.

The marketing-plan corpus works the same way, one step further. Rather than sending you
passages to paste into a prompt, the hosted version takes the prompt, adds the passages on
its side, runs the model there and sends back only the written result. Your Hugging Face
token goes with the request so the generation is billed to you, not to whoever hosts it —
which is worth knowing before you point the app at a service you don't run. Use a
fine-grained, inference-only token if you do. The default is still to keep everything on
your own machine.

---

## Checking it for security problems

```bash
python scripts/security/scan.py
```

That runs gitleaks, Trivy and Semgrep over the repo and prints one summary. It exists because
each of those tools reports a clean pass on this project when you invoke it the obvious way,
and each is wrong when it does — gitleaks skips merge commits, Trivy can't read an unpinned
requirements file and won't look inside a virtualenv, and Semgrep ignores anything under a
directory called `vendor`. The script's own docstring explains each trap. Install the tools
first; it tells you how for whichever one is missing.

---

## How it's built (in plain terms)

Three pieces that talk to each other on your own machine:

```
┌─────────────────────────────┐
│  The window you see         │   Electron + React
│  (buttons, forms, results)  │
└──────────────┬──────────────┘
               │  local messages, never leaves your PC
┌──────────────▼──────────────┐
│  The engine                 │   Python + FastAPI
│  (does the actual work)     │
└──────────────┬──────────────┘
               │
      ┌────────┼─────────┬──────────────┐
      ▼        ▼         ▼              ▼
   Your      AI models  Publishing   The internet
   files     you rent   engine       (public data)
   (SQLite)  (HF/Modal) (Docker)
```

- **The window** is a normal desktop app.
- **The engine** is a small web server that only listens to your own computer.
- **Your data** is a single database file plus generated documents, in your user folder.
- **AI models** are rented per use from Hugging Face, or run on a GPU via Modal.
- **The publishing engine** runs in a container on your machine and holds the connections to
  your social accounts.

---

## The technology, explained

Plain-language notes on each piece and why it's there.

### The app itself

| Thing | What it is |
| --- | --- |
| **Electron** | Lets a web-style interface run as a proper desktop app. |
| **React + TypeScript** | Builds the interface. TypeScript catches mistakes before you see them. |
| **Vite** | Rebuilds the app instantly while developing. |
| **Zustand** | Remembers what you typed as you move between screens. |

### The engine

| Thing | What it is |
| --- | --- |
| **Python + FastAPI** | The web server doing the work. Fast to write, easy to read. |
| **SQLite** | Your database — one file, no server, nothing to administer. |
| **PyInstaller** | Packs the engine into a single executable so users don't install Python. |

### The AI

| Thing | What it is |
| --- | --- |
| **Hugging Face Inference** | Rent a model by the request. Used for most writing. |
| **Hugging Face Spaces** | Small hosted apps. The fine-tuned models for blogs, emails and branding live here. |
| **Modal** | Rent a GPU by the second, for image generation and faster text. |
| **LoRA fine-tuning** | Cheap specialisation: instead of retraining a giant model, you train a small adapter on your examples. |
| **Sentence Transformers** | Turns text into numbers so the app can find genuinely similar things. |
| **ChromaDB** | Stores those numbers so search over your reference material is fast. |
| **faster-whisper** | Turns speech into text, on your machine, for video tools. |
| **FLUX.2 klein** | The image model behind brand visuals. |

### Publishing and outreach

| Thing | What it is |
| --- | --- |
| **Activepieces** | The publishing engine. Holds your social connections and does the posting. Runs in a container on your PC. |
| **Docker + WSL2** | Lets those Linux containers run on Windows. |
| **SearXNG** | A private search engine used to find leads, so searches aren't tied to you. |
| **Reacher** | Checks an email address is real before you write to it. |
| **SMTP / IMAP** | Standard mail protocols — the app sends through *your* mailbox, not a service. |

### Reading the internet

| Thing | What it is |
| --- | --- |
| **yt-dlp** | Finds and fetches video. Runs from your own connection. |
| **Piped** | A privacy-preserving front door to video search, used as a backup. |
| **ffmpeg** | The universal video/audio tool — used to grab clean screenshots. |
| **Overpass / OpenStreetMap** | Free map data, used to find local businesses. |
| **Google Trends, Hacker News, Wikipedia, RSS** | Free public signals for topic research. |

---

## Configuration reference

Everything below is optional and starts empty. **The app ships with no defaults pointing at
anyone else's account** — you deploy your own and point these at them.

Set these in your environment, or in a `.env` file in `backend/`:

| Variable | Needed for | What it is |
| --- | --- | --- |
| `HF_TOKEN` | Almost everything | Your Hugging Face token. Can also be set in the Settings screen. |
| `BLOG_WRITER_SPACE` | Blog Writer | The Space you deployed, e.g. `you/blog-writer`. |
| `EMAIL_WRITER_SPACE` | Email Writer | Your email-model Space. |
| `BRANDFORGE_SPACE` | Brand Studio | Your brand-model Space. Also settable in Settings. |
| `MARKETING_PLAN_RAG_DATASET` | Marketing Plan | A private Hugging Face Dataset holding the reference index — see [The reference index](#the-reference-index). Without it, plans are still written, just without retrieval. |
| `MAIL_TRACKER_URL` | Email open/click tracking | Your tracking Space. Without it, tracking is simply off. |
| `BRANDFORGE_MODEL` | Brand Studio on Modal | Your merged model repo. |
| `BRANDFORGE_IMAGE_BUCKET` | Brand visuals | Your Hugging Face Bucket holding the image model. |

Everything else — Bluesky, Mastodon, Tumblr, mail, Modal tokens, YouTube — is entered in the
**Settings** screen and stored encrypted on your machine.

Tumblr is the one that takes four values rather than one, because its API is OAuth 1.0a:
register an application at [tumblr.com/oauth/apps](https://www.tumblr.com/oauth/apps) for the
consumer key and secret, then open [api.tumblr.com/console](https://api.tumblr.com/console),
pick that application, and copy the token and token secret it hands you. All four are secrets.
The blog name is optional — leave it blank and the app acts as your primary blog.

### The reference index

The Marketing Plan tool grounds its advice in a library of marketing books, stored as a
searchable index of about 104,000 passages. That index is roughly **1.1 GB** — too big to put
in an installer, so it lives in a **private Hugging Face Dataset** and the app downloads it
once, on your first plan, using your own token.

To set it up:

```bash
python scripts/rag/compact_index.py
```

```bash
python scripts/rag/publish_index.py --repo your-username/dm-rag-index
```

Then set `MARKETING_PLAN_RAG_DATASET` to that repo id.

The first script is worth running even if you never publish: the index ships from Chroma with
a full-text keyword index the app never queries — a second copy of every passage plus an
inverted index. Dropping it takes the folder from **2.4 GB to 1.1 GB** with *identical*
retrieval, verified by comparing the exact passages and similarity scores returned before and
after. The script backs up first and restores itself if anything differs.

The dataset is created **private** by default, and stays that way unless you pass `--public`.
Whether the index may be shared depends on the licence of the books it was built from — that's
a decision to make deliberately, not by leaving a flag at its default.

Skip all of this and the tool still works; plans are just written without the retrieval step.

---

## Keeping an eye on it

The repo includes a small standalone monitor, `healthmon/`, that checks the app's
infrastructure, its Spaces and each of its modules, and can schedule itself:

```bash
python -m healthmon health
```

```bash
python -m healthmon install
```

That registers two Windows scheduled tasks: a full check twice a day, and a deeper
end-to-end test weekly. It writes a self-contained HTML report you can open in any browser.
See [`healthmon/README.md`](healthmon/README.md).

---

## Privacy and your data

- **Your work stays on your machine.** Documents, drafts, leads and history live in a
  database file and a folder in your user directory.
- **Your keys are encrypted** using the operating system's own credential protection, not a
  plain text file.
- **Nothing is sent anywhere you didn't connect.** Text goes to the AI provider you
  configured to have it written; posts go to the platforms you connected. There is no
  analytics service watching you and no account with the app's authors.
- **The engine only listens locally.** It is not reachable from your network.

---

## Troubleshooting

**"Something needs its own Space."** Three tools run models you deploy yourself. Deploy the
Space and set the matching variable from the table above.

**Publishing says the engine isn't running.** Open **Distribute → Start the engine**. First
start takes a minute or two — it's booting a container. Start it from the app rather than by
hand: the app also keeps the Linux virtual machine awake, and without that it will idle out
and take the engine down with it.

**A tool is slow the first time.** Free Hugging Face Spaces go to sleep when unused and take
a minute to wake. That's normal, not a fault.

**Video tools fail to download.** Check `ffmpeg` is present in `resources/ffmpeg/`. It isn't
committed to the repo because of its size and licence.

---

## What this is built on, and their licences

This app is mostly an assembly job. Seven separate projects live under `backend/vendor/`, and
a handful of third-party programs are either shipped in the installer or run as services on
your machine. None of them carry licence files inside this repo, so this section is the
record. If you only ever run this for yourself, none of it constrains you — it matters when
you redistribute.

### The seven vendored projects

| Project | Where it's used | Origin | Licence |
| --- | --- | --- | --- |
| `socialpost` | Social Post, Mastodon Post, Hashtag Suggester, the shared LLM wrapper | Written for this app | MIT, as part of this repo |
| `dmstrategy` | Marketing Plan | Written for this app | MIT, as part of this repo |
| `guestpostsuggester` | Guest Post Suggester | Written for this app | MIT, as part of this repo |
| `tutorialmaker` | TutorialMaker | Written for this app | MIT, as part of this repo |
| `docmaker` | DocuMaker | Own Hugging Face Space, pulled in as a git subtree | **MIT** (declared in the Space's own README) |
| `topicscout` | Topic Scout | Merge of two projects — see below | **MIT** (both parts) |
| `leadgen` | Lead Gen Agent, Outreach CRM | Reimplementation of a GPLv3 project's workflow — see below | Needs your attention |

**`topicscout` is a merge of two upstreams.** The ranking engine, evidence model and signal
tracking come from TrendScout, a Streamlit app written for this project. The social and
consumer discovery feeds — Reddit RSS, Google Trends RSS, YouTube search, TikTok Creative
Center, Amazon Best Sellers — plus the bilingual sentiment lexicon are ported from
[TrendScope](https://github.com/mamboyepez17/trendscope), which is **MIT licensed**. MIT is
compatible with this repo's licence; it asks only that the copyright notice travel with the
code, which is what this entry is for.

**`leadgen` follows [OpenOutreach](https://github.com/eracle/OpenOutreach), which is
GPLv3.** The pipeline shape (discover → qualify → find and verify email → draft → send →
agentic follow-up), the Campaign/Lead/Deal/Task data model, the deal-state machine and the
explore/exploit qualifier are all deliberately faithful to it — the source comments say so
throughout. What's here is a reimplementation that replaces OpenOutreach's paid lead database
and email lookup with free backends, not a fork of its code. That distinction is what keeps
this repo MIT: workflow and architecture aren't copyrightable, source code is. **If any
OpenOutreach source was actually copied rather than reimplemented, this directory is a
derivative work and must be GPLv3.** Worth confirming before you redistribute.

### Shipped in the installer

| Thing | Licence | What that means here |
| --- | --- | --- |
| [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) (gyan.dev static build) | **GPL-3.0** | The one real redistribution obligation. The app never links it — DocuMaker and TutorialMaker shell out to it as a separate executable on PATH — so this is mere aggregation and the MIT grant on the app code is unaffected. But GPLv3 obliges anyone distributing the binary to offer its corresponding source. If you publish an installer, link to the exact build you bundled. |

### Services you run yourself

These are never redistributed — you pull the image or run the binary — so their terms are
between you and them, not conditions on this repo.

| Thing | Licence | Worth knowing |
| --- | --- | --- |
| [Activepieces](https://github.com/activepieces/activepieces) `0.86.2` | MIT core; `packages/ee` under a separate commercial licence | The publishing engine. Everything this app touches is the MIT core. |
| [SearXNG](https://github.com/searxng/searxng) | AGPL-3.0 | Private metasearch for lead discovery. Self-hosted, unmodified. |
| [Reacher](https://github.com/reacherhq/check-if-email-exists) | AGPL-3.0 **or** commercial | Email verification. Reacher's own terms say commercial use needs either an AGPL-compatible codebase or a paid licence — if you use the Lead Gen Agent commercially, that's yours to sort out with them. |

### Notable libraries

Ordinary dependencies, all permissive: **faster-whisper** (MIT), **yt-dlp** (Unlicense),
**ChromaDB** (Apache-2.0), **Sentence Transformers** (Apache-2.0), **FastAPI**, **Electron**
and **React** (all MIT).

The AI models are a separate question — most are open-weight (Apache-2.0 or MIT), but a few
carry restrictions, and one image model is non-commercial. See
[The AI](#the-ai) above and the model ids in the source for specifics.

---

## Contributing and licence

Issues and pull requests welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers how to get a
working checkout, the three checks to run before opening a PR, and how the tools are wired
together if you want to add one. [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) is the short
version of being decent to people.

Found a security problem? Please don't open a public issue —
[SECURITY.md](SECURITY.md) explains how to report it privately and what's in scope.

If you deploy your own Spaces, please don't hardcode them in shared code — use the
environment variables above, so nobody's clone quietly sends traffic to your account.

Released under the **MIT Licence** — see [LICENSE](LICENSE). In short: use it, change it,
ship it commercially, just keep the copyright notice.

That grant covers the application code and the projects under `backend/vendor/` that were
written for it. It does **not** override the terms of the third-party work this app builds
on — most notably the GPL-3.0 ffmpeg binaries in the installer, and the GPLv3 project whose
workflow the Lead Gen Agent follows.
[What this is built on](#what-this-is-built-on-and-their-licences) has the full picture and
the two items that need a decision before you redistribute. Read it before you fork this for
anything beyond personal use.
