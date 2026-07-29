"""
Keyword research with a four-tier fallback chain:

1. Google Ads API (KeywordPlanIdeaService.GenerateKeywordIdeas) — official
   search volume/competition, via credentials configured by the tool operator
   (see .env / GOOGLE_ADS_* below). Read-only; no campaigns are touched.
2. Keyword Surfer scrape (headless Chromium + the unpacked extension, reading
   the ks-main-keyword-widget it injects into Google search results).
3. Google Autocomplete + pytrends (relative interest, not absolute volume).
4. LLM-estimated volumes/CPC (clearly labeled as estimates, billed to the
   user's own HF token like every other LLM call in this app).

Every KeywordData result is tagged with which tier actually produced it, and
the report always states which source was used.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import httpx

from modules import llm

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

_EXTENSION_DIR = Path(__file__).resolve().parent.parent / "extension"
_AUTOCOMPLETE_URL = "https://suggestqueries.google.com/complete/search"
_CAPTCHA_MARKERS = ("unusual traffic", "recaptcha", "sorry/index")

# ISO country code -> Google Ads geo target constant. Extend as needed;
# unmapped/blank geo falls back to no geo targeting (worldwide).
_GEO_TARGET_CONSTANTS = {
    "US": "geoTargetConstants/2840",
    "GB": "geoTargetConstants/2826",
    "CA": "geoTargetConstants/2124",
    "AU": "geoTargetConstants/2036",
    "IN": "geoTargetConstants/2356",
    "DE": "geoTargetConstants/2276",
    "FR": "geoTargetConstants/2250",
}


@dataclass
class KeywordData:
    keyword: str
    volume: str | None
    cpc: str | None
    related: list[str] = field(default_factory=list)
    source: str = "unknown"  # "google_ads_api" | "keyword_surfer" | "autocomplete_trends" | "llm_estimate"


# The Google Ads API tier runs on one shared operator account (Basic access:
# 15,000 operations/day, per the developer token — not per end user of this
# public Space). Without a per-user cap, one heavy user could exhaust the
# whole account's daily quota for everyone else. 25/user/day is a generous
# multiple of normal single-user usage (each plan generation is one batched
# GenerateKeywordIdeas call = one operation) while bounding worst-case abuse
# to a small fraction of the shared 15,000/day ceiling. Identity is the user's
# own HF token (hashed, never stored raw) since this app has no other notion
# of "user" — resets naturally at midnight UTC; also resets on a Space
# restart, which is an acceptable (conservative, not exploitable) limitation
# given HF Spaces' ephemeral storage.
_PER_USER_DAILY_LIMIT = 25
_usage_lock = threading.Lock()
_usage: dict[str, tuple[str, int]] = {}  # hashed user key -> (date_str, count)


def _user_key(hf_token: str) -> str:
    return hashlib.sha256((hf_token or "").strip().encode("utf-8")).hexdigest()[:16]


def _check_and_consume_quota(hf_token: str) -> bool:
    """True (and consumes one unit) if this user is still under today's
    Google Ads API quota; False if they've hit the limit, in which case the
    caller should fall back to the next tier."""
    key = _user_key(hf_token)
    today = date.today().isoformat()
    with _usage_lock:
        stored_date, count = _usage.get(key, (today, 0))
        if stored_date != today:
            count = 0
        if count >= _PER_USER_DAILY_LIMIT:
            _usage[key] = (today, count)
            return False
        _usage[key] = (today, count + 1)
        return True


def research_keywords(
    seed_keywords: list[str],
    hf_token: str,
    model: str,
    geo: str = "",
) -> list[KeywordData]:
    if _check_and_consume_quota(hf_token):
        try:
            results = _google_ads_keyword_ideas(seed_keywords, geo)
            if results:
                return results
        except Exception as exc:
            print(f"[keywords] Google Ads API path unavailable: {exc}")
    else:
        print(f"[keywords] Google Ads API per-user daily quota ({_PER_USER_DAILY_LIMIT}) reached — falling back")

    try:
        results = _scrape_with_surfer(seed_keywords, geo)
        if results:
            return results
    except Exception as exc:
        print(f"[keywords] Keyword Surfer path unavailable: {exc}")

    try:
        results = _autocomplete_and_trends(seed_keywords, geo)
        if results:
            return results
    except Exception as exc:
        print(f"[keywords] Autocomplete/pytrends path failed: {exc}")

    return _llm_estimate(seed_keywords, hf_token, model)


# --- Tier 1: Google Ads API --------------------------------------------------

def _google_ads_config() -> dict | None:
    values = {
        "developer_token": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "client_id": os.environ.get("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": os.environ.get("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": os.environ.get("GOOGLE_ADS_REFRESH_TOKEN"),
        "login_customer_id": os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
    }
    if not all(values.values()):
        return None
    values["login_customer_id"] = values["login_customer_id"].replace("-", "")
    values["use_proto_plus"] = True
    return values


def _google_ads_keyword_ideas(seed_keywords: list[str], geo: str) -> list[KeywordData]:
    config = _google_ads_config()
    if config is None:
        raise RuntimeError("Google Ads API credentials not configured (see .env)")

    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException

    client = GoogleAdsClient.load_from_dict(config)
    service = client.get_service("KeywordPlanIdeaService")

    request = client.get_type("GenerateKeywordIdeasRequest")
    request.customer_id = config["login_customer_id"]
    request.language = "languageConstants/1000"  # English
    geo_constant = _GEO_TARGET_CONSTANTS.get(geo.upper())
    if geo_constant:
        request.geo_target_constants.append(geo_constant)
    request.keyword_seed.keywords.extend(seed_keywords)
    request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH

    try:
        response = service.generate_keyword_ideas(request=request)
    except GoogleAdsException as exc:
        messages = "; ".join(err.message for err in exc.failure.errors)
        raise RuntimeError(f"Google Ads API error: {messages}") from exc

    results = []
    for idea in response:
        metrics = idea.keyword_idea_metrics
        volume = metrics.avg_monthly_searches if metrics.avg_monthly_searches else None
        low = metrics.low_top_of_page_bid_micros / 1_000_000 if metrics.low_top_of_page_bid_micros else None
        high = metrics.high_top_of_page_bid_micros / 1_000_000 if metrics.high_top_of_page_bid_micros else None
        cpc = f"${low:.2f}-${high:.2f}" if low and high else None
        results.append(
            KeywordData(
                keyword=idea.text,
                volume=f"{volume:,}/mo" if volume else None,
                cpc=cpc,
                related=[],
                source="google_ads_api",
            )
        )
        if len(results) >= 50:  # real Google Ads data is high-signal; cap generously
            break
    return results


# --- Tier 2: Keyword Surfer scrape -----------------------------------------

def _scrape_with_surfer(seed_keywords: list[str], geo: str) -> list[KeywordData]:
    if not _EXTENSION_DIR.exists() or not (_EXTENSION_DIR / "manifest.json").exists():
        raise RuntimeError("extension/ not present (fetch_extension.py did not run)")

    from playwright.sync_api import sync_playwright

    results: list[KeywordData] = []
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir="",
            headless=False,  # extensions require a "headed" new-headless context
            args=[
                f"--disable-extensions-except={_EXTENSION_DIR}",
                f"--load-extension={_EXTENSION_DIR}",
                "--headless=new",
            ],
        )
        try:
            page = context.new_page()
            for kw in seed_keywords:
                data = _scrape_one_keyword(page, kw, geo)
                if data is not None:
                    results.append(data)
        finally:
            context.close()
    return results


def _scrape_one_keyword(page, keyword: str, geo: str):
    params = {"q": keyword}
    if geo:
        params["gl"] = geo
    url = f"https://www.google.com/search?{urlencode(params)}"
    page.goto(url, timeout=20000)
    content_lower = page.content().lower()
    if any(marker in content_lower for marker in _CAPTCHA_MARKERS):
        raise RuntimeError("blocked by Google (captcha/unusual traffic)")

    try:
        page.wait_for_selector(".ks-main-keyword-widget", timeout=6000)
    except Exception:
        return None  # widget didn't render for this query — skip, not a hard failure

    widget_text = page.locator(".ks-main-keyword-widget").first.inner_text()
    volume_match = re.search(r"([\d.,]+\s*[KM]?)\s*/?\s*mo", widget_text, re.IGNORECASE)
    cpc_match = re.search(r"\$\s*([\d.,]+)", widget_text)

    related = []
    for related_el in page.locator(".ks-cell").all()[:15]:
        text = related_el.inner_text().strip()
        if text and text.lower() != keyword.lower():
            related.append(text)

    return KeywordData(
        keyword=keyword,
        volume=volume_match.group(0) if volume_match else None,
        cpc=f"${cpc_match.group(1)}" if cpc_match else None,
        related=related,
        source="keyword_surfer",
    )


# --- Tier 3: Google Autocomplete + pytrends ---------------------------------

def _autocomplete_and_trends(seed_keywords: list[str], geo: str) -> list[KeywordData]:
    results: list[KeywordData] = []

    trend_scores: dict[str, float] = {}
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(timeout=(5, 10))
        for batch_start in range(0, len(seed_keywords), 5):
            batch = seed_keywords[batch_start : batch_start + 5]
            pytrends.build_payload(batch, timeframe="today 3-m", geo=geo or "")
            interest_df = pytrends.interest_over_time()
            for kw in batch:
                if kw in interest_df.columns and len(interest_df[kw]) > 0:
                    trend_scores[kw] = float(interest_df[kw].mean())
    except Exception as exc:
        print(f"[keywords] pytrends unavailable: {exc}")

    with httpx.Client(timeout=10) as client:
        for kw in seed_keywords:
            related = []
            try:
                resp = client.get(_AUTOCOMPLETE_URL, params={"client": "firefox", "q": kw})
                if resp.status_code == 200:
                    parsed = json.loads(resp.text)
                    related = parsed[1][:10] if len(parsed) > 1 else []
            except Exception as exc:
                print(f"[keywords] autocomplete failed for '{kw}': {exc}")

            score = trend_scores.get(kw)
            if score is None:
                volume_label = None
            elif score >= 60:
                volume_label = "High relative interest (est.)"
            elif score >= 25:
                volume_label = "Medium relative interest (est.)"
            else:
                volume_label = "Low relative interest (est.)"

            if related or volume_label:
                results.append(
                    KeywordData(
                        keyword=kw,
                        volume=volume_label,
                        cpc=None,
                        related=related,
                        source="autocomplete_trends",
                    )
                )
    return results


# --- Tier 4: LLM estimate ----------------------------------------------------

def _llm_estimate(seed_keywords: list[str], hf_token: str, model: str) -> list[KeywordData]:
    prompt = f"""For each of these seed keywords, estimate (as a rough, clearly-labeled
guess — you do not have live search data) a relative search-volume tier
(Low/Medium/High), a rough CPC range in USD, and 5 related keyword ideas.

Seed keywords: {json.dumps(seed_keywords)}

Respond ONLY with a JSON array like:
[{{"keyword": "...", "volume": "Medium (LLM estimate)", "cpc": "$1-3 (LLM estimate)",
   "related": ["...", "..."]}}]
"""
    raw = llm.chat(
        hf_token=hf_token,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        temperature=0.3,
    )
    try:
        json_str = re.search(r"\[.*\]", raw, re.DOTALL).group(0)
        parsed = json.loads(json_str)
    except Exception:
        return [
            KeywordData(keyword=kw, volume=None, cpc=None, related=[], source="llm_estimate")
            for kw in seed_keywords
        ]

    return [
        KeywordData(
            keyword=item.get("keyword", ""),
            volume=item.get("volume"),
            cpc=item.get("cpc"),
            related=item.get("related", []),
            source="llm_estimate",
        )
        for item in parsed
    ]
