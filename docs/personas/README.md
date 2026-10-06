# Audience Personas

Open **Research / Strategy → Audience Personas**. Start a named run or the fictional offline demo. The five steps are Interview, Research plan, Collect & analyze, Review, and Personas. Answers and the current step save in the app's local SQLite database, so a named run resumes after restart.

## Collecting data

The research plan lists seed phrases, editable queries, estimated requests and source switches. In Full mode you can set Quick, Standard or Deep research depth, mark a regulated industry, and exclude sources or topics using lines such as `source: Bluesky` or `topic: health claims`. Public connectors start disabled. Turn on only sources you are allowed to use, confirm their terms, and click **Approve & run**. The app sends at most one request per second to each domain, identifies itself with a User-Agent, caches responses for one hour and makes one bounded retry on rate limit or server errors. Page collection checks robots.txt and refuses social, review, login and paywalled pages. It does not log in, evade blocking or crawl at scale.

Available sources:

| Source | Use | Setup |
| --- | --- | --- |
| Your CSV | Import customer comments, tickets, surveys, reviews or query exports with a text column mapping | Choose a local CSV before approval on step 2, or after collection on step 3 and re-run. Import only data you have permission to analyze. |
| Own Bluesky history | Text and engagement relative to the account's preceding 30-day median | Connect account and enable analytics elsewhere in the app. |
| Public websites | Title, headings and page paragraphs from explicitly approved HTTPS URLs | Add URLs on step 2; robots.txt must permit access. |
| [Hacker News Algolia API](https://hn.algolia.com/api) | Public stories and comments | Enable on step 2. No API key is issued for this endpoint. |
| [Stack Exchange API](https://api.stackexchange.com/docs/advanced-search) | Public questions | Enable on step 2. Unauthenticated use is bounded. |
| [Bluesky public AppView](https://docs.bsky.app/docs/api/app-bsky-feed-get-author-feed) | Public post search | Enable on step 2. Public GETs do not require a token. |
| [Mastodon search API](https://docs.joinmastodon.org/methods/search/) | Public searchable statuses | Connect Mastodon in Settings; server indexing may be incomplete. |
| [YouTube Data API commentThreads](https://developers.google.com/youtube/v3/docs/commentThreads/list) | Comments on specific videos | Save a YouTube API key in Settings and put a video URL in the approved URLs list. |
| [Wikimedia pageviews API](https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html) | Aggregate topic-attention trend | Enable on step 2. It is not counted as buyer evidence. |

Reddit and Tumblr are documented stubs. Bring your own permitted CSV exports for these sources. Open public API endpoints without key support use explicit source approval and terms confirmation; authenticated connectors use the existing encrypted Settings store. The keys pass to the backend in memory for a run, and are never saved with persona data. No LLM is called by this feature; there is no model fee. It uses bundled scikit-learn locally when the sample supports clustering.

## How the result is built

The app removes likely names, handles, emails, phone numbers and URLs before saving evidence, then drops short and near-duplicate text. It tags stated pains, goals, triggers and objections. For usable multisource samples it tests two to four TF-IDF clusters with a fixed seed, comparing silhouette, bootstrap agreement, minimum 10% segment share and distinctiveness. When evidence is sparse, it starts from your named hypotheses and marks the resulting personas **Low — hypothesis**. Fewer than five usable units produces no personas. Counts describe the collected sample, not the size of a market.

Each claim can open its anonymized supporting snippets. A claim with no evidence is marked **user-assumed**. Review labels, values and priority; split a segment using a phrase that separates actual evidence, or merge it while preserving evidence links. Export JSON or Markdown, copy summaries, and refresh when the evidence grows. The report records method, sample, sources, cluster choice and limitations. Edits to a persona label or summary are marked **Edited by you** and matched back to a refreshed persona by evidence overlap.

## Privacy and limits

This is a research aid, not a legal-compliance tool. Check your basis for processing personal data under applicable law, including GDPR and India's DPDP Act. Avoid uploading identifiable customer records; use only data you may analyze. PII scrubbing is heuristic and should be reviewed before export. A **Delete this run's data** action removes the saved answers, imports, evidence and personas for that run. The app sends source queries and approved URLs to the selected providers when you approve collection; demo mode makes no network calls.

Public conversations overrepresent people willing to post; competitor pages represent a business's own claims. A few posts cannot validate buying roles, channel reach or demographic assumptions. Interview buyers and run the suggested message tests before relying on a persona for spending decisions.

## Adding a collector

Add a bounded function in `backend/app/personas/collectors.py` that uses a documented official API or a robots-allowed public page and returns `core.evidence_unit()` records. Register it in the source plan in `core.py`, dispatch it in `routers/personas.py`, keep it off by default, and add mocked HTTP tests. Do not persist credentials or raw profiles. Update this document with API terms, quota and limitations.
