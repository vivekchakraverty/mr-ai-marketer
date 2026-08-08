"""Streamlit dashboard for backlink partitions extracted from Common Crawl WAT."""

from __future__ import annotations

from collections import Counter
from io import BytesIO
import json
import os
from pathlib import PurePosixPath
from typing import Any

import pandas as pd
import plotly.express as px
import pyarrow.parquet as pq
import streamlit as st
from huggingface_hub import HfFileSystem

from backlink_data import canonical_target_domain


st.set_page_config(page_title="Common Crawl Backlinks", page_icon="🔗", layout="wide")

BUCKET = (os.getenv("BACKLINK_BUCKET") or "").strip()
TOKEN = (os.getenv("HF_TOKEN") or "").strip() or None
REQUIRED_COLUMNS = [
    "source_url", "source_host", "source_domain", "target_url", "target_host",
    "target_domain", "anchor_text", "rel", "link_type", "is_nofollow",
    "capture_date", "crawl", "wat_source",
]


def bucket_uri(path: str) -> str:
    return f"hf://buckets/{BUCKET}/{path.lstrip('/')}"


@st.cache_resource(show_spinner=False)
def bucket_fs() -> HfFileSystem:
    return HfFileSystem(token=TOKEN)


def read_bucket_json(path: str) -> dict[str, Any] | None:
    try:
        with bucket_fs().open(bucket_uri(path), "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


@st.cache_data(show_spinner=False, ttl=300)
def load_domain_rows(bucket: str, target_domain: str) -> tuple[pd.DataFrame, dict[str, Any] | None, str | None]:
    # Arguments make cache entries domain/bucket-specific; global settings are
    # still used by the authenticated filesystem.
    del bucket
    manifest = read_bucket_json(f"manifest/{target_domain}.json")
    if not manifest:
        return pd.DataFrame(columns=REQUIRED_COLUMNS), None, None

    frames: list[pd.DataFrame] = []
    failures: list[str] = []
    for partition in manifest.get("partitions", []):
        path = partition.get("path") if isinstance(partition, dict) else None
        if not isinstance(path, str) or not path.endswith(".parquet"):
            continue
        try:
            with bucket_fs().open(bucket_uri(path), "rb") as handle:
                frames.append(pq.read_table(BytesIO(handle.read())).to_pandas())
        except (FileNotFoundError, OSError, ValueError, pq.ArrowInvalid) as error:
            failures.append(f"{PurePosixPath(path).name}: {error}")

    if not frames:
        return pd.DataFrame(columns=REQUIRED_COLUMNS), manifest, "; ".join(failures) or None
    data = pd.concat(frames, ignore_index=True)
    for column in REQUIRED_COLUMNS:
        if column not in data:
            data[column] = ""
    data = data[REQUIRED_COLUMNS].drop_duplicates(
        subset=["source_url", "target_url", "anchor_text", "link_type", "crawl"]
    )
    return data, manifest, "; ".join(failures) or None


def available_link_types(data: pd.DataFrame) -> list[str]:
    return sorted(value for value in data["link_type"].dropna().unique() if value)


def render_empty_state() -> None:
    st.info(
        "No extracted backlink partition is available for this domain yet. "
        "Run the offline Common Crawl WAT ingestion script, then refresh this page."
    )


st.title("Common Crawl Backlink Explorer")
st.caption("Observed outgoing links in processed Common Crawl WAT shards — not a complete or real-time backlink index.")

if not BUCKET:
    st.error("Set the `BACKLINK_BUCKET` Space variable to a Hugging Face Storage Bucket ID before using the dashboard.")
    st.stop()

with st.sidebar:
    st.header("Website")
    requested_site = st.text_input("Domain or website URL", placeholder="example.com")
    st.caption(f"Bucket: `{BUCKET}`")

if not requested_site:
    st.info("Enter a website domain to load its processed backlink data.")
    st.stop()

target_domain = canonical_target_domain(requested_site)
if not target_domain:
    st.error("Enter a valid domain or HTTP(S) website URL.")
    st.stop()

with st.spinner(f"Loading backlinks for {target_domain}…"):
    rows, manifest, load_error = load_domain_rows(BUCKET, target_domain)

if load_error:
    st.warning(f"Some partitions could not be loaded: {load_error}")
if rows.empty:
    render_empty_state()
    st.stop()

rows["is_nofollow"] = rows["is_nofollow"].astype(str).str.lower().eq("true")
rows["capture_datetime"] = pd.to_datetime(rows["capture_date"], errors="coerce", utc=True)
rows["target_path"] = rows["target_url"].str.replace(r"https?://[^/]+", "", regex=True).fillna("/")

with st.sidebar:
    st.header("Filters")
    crawls = st.multiselect("Crawl", sorted(rows["crawl"].dropna().unique()), default=sorted(rows["crawl"].dropna().unique()))
    link_types = st.multiselect("Link element", available_link_types(rows), default=available_link_types(rows))
    link_attribute = st.radio("Link attribute", ["All", "Follow", "Nofollow"], horizontal=True)
    target_path_query = st.text_input("Target URL contains", placeholder="/pricing")

filtered = rows.copy()
if crawls:
    filtered = filtered[filtered["crawl"].isin(crawls)]
else:
    filtered = filtered.iloc[0:0]
if link_types:
    filtered = filtered[filtered["link_type"].isin(link_types)]
elif available_link_types(rows):
    filtered = filtered.iloc[0:0]
if link_attribute == "Follow":
    filtered = filtered[~filtered["is_nofollow"]]
elif link_attribute == "Nofollow":
    filtered = filtered[filtered["is_nofollow"]]
if target_path_query:
    filtered = filtered[filtered["target_url"].str.contains(target_path_query, case=False, na=False, regex=False)]

referring_pages = filtered["source_url"].nunique()
referring_domains = filtered["source_domain"].nunique()
follow_count = int((~filtered["is_nofollow"]).sum())
nofollow_count = int(filtered["is_nofollow"].sum())

stats = st.columns(5)
stats[0].metric("Observed links", f"{len(filtered):,}")
stats[1].metric("Referring pages", f"{referring_pages:,}")
stats[2].metric("Referring domains", f"{referring_domains:,}")
stats[3].metric("Target URLs", f"{filtered['target_url'].nunique():,}")
stats[4].metric("Follow share", f"{(follow_count / len(filtered) * 100) if len(filtered) else 0:.1f}%")

if filtered.empty:
    st.warning("No backlinks match the selected filters.")
    st.stop()

left, right = st.columns((3, 2))
with left:
    domain_summary = (
        filtered.groupby("source_domain", as_index=False)
        .agg(backlinks=("source_url", "size"), pages=("source_url", "nunique"), target_urls=("target_url", "nunique"))
        .sort_values(["backlinks", "pages"], ascending=False)
        .head(20)
    )
    st.subheader("Leading referring domains")
    st.plotly_chart(
        px.bar(
            domain_summary.sort_values("backlinks"), x="backlinks", y="source_domain", orientation="h",
            hover_data={"pages": True, "target_urls": True}, labels={"source_domain": "Referring domain", "backlinks": "Observed links"},
        ),
        use_container_width=True,
    )

with right:
    st.subheader("Link attributes")
    attributes = pd.DataFrame({"attribute": ["Follow", "Nofollow"], "links": [follow_count, nofollow_count]})
    st.plotly_chart(
        px.pie(attributes, names="attribute", values="links", hole=0.52),
        use_container_width=True,
    )

timeline = filtered.dropna(subset=["capture_datetime"]).copy()
if not timeline.empty:
    timeline["crawl_month"] = timeline["capture_datetime"].dt.to_period("M").astype(str)
    timeline_summary = timeline.groupby("crawl_month", as_index=False).agg(
        links=("source_url", "size"), referring_domains=("source_domain", "nunique")
    )
    st.subheader("Coverage over crawl time")
    st.plotly_chart(
        px.line(
            timeline_summary, x="crawl_month", y=["links", "referring_domains"], markers=True,
            labels={"value": "Count", "crawl_month": "Crawl month", "variable": "Metric"},
        ),
        use_container_width=True,
    )

anchors = Counter(anchor for anchor in filtered["anchor_text"] if anchor)
if anchors:
    st.subheader("Most common anchor text")
    anchor_table = pd.DataFrame(anchors.most_common(25), columns=["Anchor text", "Links"])
    st.dataframe(anchor_table, use_container_width=True, hide_index=True)

st.subheader("Backlink detail")
detail = filtered[
    ["source_domain", "source_url", "target_url", "anchor_text", "rel", "link_type", "crawl", "capture_date"]
].rename(columns={
    "source_domain": "Referring domain", "source_url": "Referring page", "target_url": "Target URL",
    "anchor_text": "Anchor text", "rel": "Rel", "link_type": "Element", "crawl": "Crawl", "capture_date": "Captured",
})
st.dataframe(
    detail,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Referring page": st.column_config.LinkColumn("Referring page"),
        "Target URL": st.column_config.LinkColumn("Target URL"),
    },
)
st.download_button(
    "Download filtered backlinks as CSV",
    data=detail.to_csv(index=False).encode("utf-8"),
    file_name=f"{target_domain}-common-crawl-backlinks.csv",
    mime="text/csv",
)

if manifest:
    st.caption(
        f"{len(manifest.get('partitions', []))} WAT-derived partition(s) listed in the manifest; "
        f"last updated {manifest.get('updated_at', 'unknown')}.")
