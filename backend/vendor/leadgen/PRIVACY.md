# Lead Gen Agent — Privacy & Data Handling

The Lead Gen Agent runs entirely on your own machine. There is no cloud database, no
third-party lead vendor, and no analytics service. This document describes exactly what it
collects, where it lives, and how to delete it.

## What is collected

Only **publicly available business information**, from two sources you choose per campaign:

- **OpenStreetMap / Overpass** — publicly-mapped business listings (name, category, location,
  and the website / contact tags mappers have added).
- **Self-hosted web search (SearXNG) + the business's own public website** — a company's
  public homepage/about/contact pages, read once to extract a short description and a public
  contact address.

The agent **respects `robots.txt`** when reading a company's site and only fetches a small,
fixed set of public pages (homepage, about, contact, team). It does not scrape social
networks, does not log into anything on the target's behalf, and does not buy or use any
licensed personal-data database.

## What is stored, and where

Everything is stored locally in a single SQLite database under your per-user app data
directory (`DATA_DIR/leadgen/leadgen.sqlite3`), plus a small on-disk embedding cache and the
per-campaign machine-learning model. Nothing leaves your machine except:

- the **reasoning** LLM calls you configured (billed to your own Hugging Face token), which
  send a lead's public profile text for qualification;
- the **email drafting** calls to your own Hugging Face Space (the Email Writer);
- the **outreach emails themselves**, sent from **your own mailbox** over SMTP.

### Provenance

Every lead records where it came from:

- `leads.source` — `overpass` or `searxng`.
- `leads.source_url` — the public URL it was found at.
- `leads.discovered_by_query_id` — the discovery query that surfaced it.
- `leads.email_source` — whether an email was **`scraped`** from the public site or
  **`guessed`** from a common name+domain pattern and then verified by Reacher.

## Consent, suppression, and unsubscribe

- Every outreach email includes an unsubscribe line and a `List-Unsubscribe` header.
- Any reply asking to stop (or a manual add) is written to the `suppression_list` table, and
  **every send checks the suppression list first**. A suppressed address is never contacted
  again.
- The suppression list is **never purged** by deletion routines below — removing a campaign
  must not resurrect someone who opted out.

## Deleting data

- **Delete a campaign** (in the UI, or `db.delete_campaign(id)`): removes that campaign and
  all of its leads, deals, conversation messages, drafts, and discovery queries. The global
  suppression list is intentionally left intact.
- **Delete everything**: quit the app and delete `DATA_DIR/leadgen/`. That removes the
  database, the embedding cache, and the trained models. (The self-hosted Reacher/SearXNG
  containers hold no lead data — they are stateless services.)
