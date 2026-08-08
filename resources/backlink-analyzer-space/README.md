---
title: Common Crawl Backlink Explorer
emoji: "🔗"
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 8501
pinned: false
---

# Common Crawl Backlink Explorer

Explore backlinks extracted from Common Crawl WAT files. The app is deliberately
read-only: ingestion happens offline, writes raw WAT archives and compact
Parquet backlink partitions to a Hugging Face Storage Bucket, and the Space
loads only the partitions for the requested destination domain.

## Configure the Space

Create this as a **Docker Space** (Hugging Face now hosts Streamlit through the
Docker SDK) and set these variables in **Settings → Variables and secrets**:

| Variable | Required | Purpose |
| --- | --- | --- |
| `BACKLINK_BUCKET` | Yes | Bucket ID, for example `<your-username>/common-crawl-wat-backlinks` |
| `HF_TOKEN` | Only for a private bucket | A read token scoped to that bucket |

Use a public bucket when the data is intended to be publicly browseable. A
public Space exposes its source code, so never put a write token in it.

## Ingest data

From this repository, run the ingestion script for each Common Crawl WAT shard
and destination domain you want to make available:

```powershell
python scripts/backlinks/ingest_commoncrawl.py `
  --bucket <your-username>/common-crawl-wat-backlinks `
  --target-domain example.com `
  --crawl CC-MAIN-2026-30 `
  --wat-url https://data.commoncrawl.org/crawl-data/CC-MAIN-2026-30/segments/.../wat/CC-MAIN-....wat.gz
```

The command uploads:

```text
raw/<crawl>/<wat-file>.wat.gz
derived/target_domain=<domain>/backlinks-<id>.parquet
manifest/<domain>.json
```

The raw archive remains preserved in the bucket. The app uses the much smaller
Parquet partition and manifest, which makes it feasible on a CPU Space.

## Data scope

Common Crawl reports links observed on crawled pages; it is not a complete,
real-time, or authority-scored backlink index. A WAT shard must be processed
for every target domain and crawl slice you want to expose. For broad coverage,
run ingestion as a scheduled/offline batch job rather than from the Space.
