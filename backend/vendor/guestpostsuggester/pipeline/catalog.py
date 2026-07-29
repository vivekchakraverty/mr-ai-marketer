"""Load the guest-post xlsx, coalesce fragmented rows into one record per domain, and
build the TF-IDF search text. Runs once at app startup; result is held in memory.

Both merge passes use pandas' native groupby().first() ("first non-null entry per group")
instead of a per-group Python loop — the naive per-column .dropna().iloc[0] approach was
profiled at ~150s for this catalog (255K individual pandas column-access calls); the
vectorized equivalent does the same coalesce in a fraction of a second.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

import pandas as pd

from . import config
from .domains import registrable_domain

_JUNK_COLUMNS = ["Unnamed: 9", 1]
_MERGE_COLUMNS = [
    "Title", "Language", "Guest Posts URL", "Contact URL",
    "Theme", "Monetization", "Website Name", "Niche",
]


@dataclass
class Site:
    domain: str              # registrable domain, e.g. "example.com" — OPR/availability key
    raw_domain: str          # original xlsx Domain string, e.g. "http://10hunting.com/"
    title: str = ""
    language: str = ""
    guest_posts_url: str = ""
    contact_url: str = ""
    theme: str = ""
    monetization: str = ""
    website_name: str = ""
    niche: str = ""
    search_text: str = ""    # HTML-unescaped Title + domain + Niche, fed to TF-IDF
    page_rank: float = 0.0   # filled in after OPR scores are loaded


def _normalize_domain_for_grouping(raw: str) -> str:
    """Loosely normalize (lowercase, strip scheme + trailing slash) to group xlsx rows
    that refer to the same site. Deliberately looser than registrable_domain() — group
    first, then derive one canonical registrable domain per group afterward.
    """
    d = str(raw).strip().lower()
    d = re.sub(r"^https?://", "", d)
    return d.rstrip("/")


def _clean_text(x) -> str:
    if pd.isna(x):
        return ""
    # Some source rows are double HTML-encoded (e.g. literal "&amp;amp;"); unescape to a
    # fixed point so both single- and double-encoded entities fully resolve.
    s = str(x).strip()
    for _ in range(3):
        unescaped = html.unescape(s)
        if unescaped == s:
            break
        s = unescaped
    return s


def _coalesce_first_nonnull(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """Vectorized 'first non-null value per column per group', keeping `group_col` itself
    as a real column (not the index) so it can be grouped on again in a later pass."""
    cols = [c for c in df.columns if c != group_col]
    merged = df.groupby(group_col, sort=False)[cols].first().reset_index()
    return merged


def load_catalog() -> list[Site]:
    # read_only speeds up openpyxl's xlsx parsing substantially on a file this size
    # (profiled: ~83s default vs a few seconds read_only) — no styling/formula info needed.
    df = pd.read_excel(
        config.CATALOG_XLSX_PATH, sheet_name=config.CATALOG_SHEET_NAME,
        engine_kwargs={"read_only": True},
    )
    df = df.drop(columns=[c for c in _JUNK_COLUMNS if c in df.columns], errors="ignore")
    df["_group_key"] = df["Domain"].apply(_normalize_domain_for_grouping)

    # Pass 1: coalesce fragmented rows that share the same loosely-normalized Domain string.
    pass1 = _coalesce_first_nonnull(df[["Domain", "_group_key", *_MERGE_COLUMNS]], "_group_key")
    pass1 = pass1.drop(columns=["_group_key"])

    pass1["domain"] = pass1["Domain"].apply(registrable_domain)
    pass1 = pass1[pass1["domain"] != ""]  # unparseable domain strings; skip rather than index garbage

    # Pass 2: a handful of pass-1 rows still share the same registrable domain because their
    # raw Domain strings differed (www vs bare, http vs https) even though they refer to the
    # same site (verified against real data: ~48 such cases). Coalesce those too, so every
    # downstream consumer (search index, OPR/availability keys, UI) can assume exactly one
    # Site per registrable domain.
    pass2 = _coalesce_first_nonnull(pass1[["domain", "Domain", *_MERGE_COLUMNS]], "domain")

    for col in ["Domain", *_MERGE_COLUMNS]:
        pass2[col] = pass2[col].map(_clean_text)

    # Rename to safe identifiers before itertuples() — several source columns contain
    # spaces (e.g. "Guest Posts URL"), which itertuples() would otherwise mangle into
    # fragile positional names like row._3.
    pass2 = pass2.rename(columns={
        "Domain": "raw_domain", "Title": "title", "Language": "language",
        "Guest Posts URL": "guest_posts_url", "Contact URL": "contact_url",
        "Theme": "theme", "Monetization": "monetization",
        "Website Name": "website_name", "Niche": "niche",
    })

    sites: list[Site] = []
    for row in pass2.itertuples(index=False):
        search_text = " ".join(filter(None, [row.title, row.domain, row.niche]))
        sites.append(Site(
            domain=row.domain,
            raw_domain=row.raw_domain,
            title=row.title,
            language=row.language,
            guest_posts_url=row.guest_posts_url,
            contact_url=row.contact_url,
            theme=row.theme,
            monetization=row.monetization,
            website_name=row.website_name,
            niche=row.niche,
            search_text=search_text,
        ))
    return sites
