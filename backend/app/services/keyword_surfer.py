"""Keyword Surfer volumes and related terms, scraped on this machine.

Keyword Surfer is a Chrome extension that injects search volume, CPC and related-term
data into Google's results page. There is no API for it — the numbers only exist once the
extension has run inside a browser on a real SERP — so reading them means driving a
browser, and this is the only part of the app that does.

It runs *here*, not on the plan Space, and that placement is the whole point. The Space
lives on a datacenter address; Google answers an automated browser from one with a captcha
page carrying no results at all. A desktop machine at least has a real consumer network
behind it, and a proxy setting for when that is not enough.

Nothing here is load-bearing. Every failure path returns nothing and the caller keeps
whatever keyword data it already had, because this is an enrichment of a table that is
already complete without it.
"""
from __future__ import annotations

import io
import re
import struct
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .. import config

# The Chrome Web Store listing for Keyword Surfer. Downloaded once and unpacked into the
# user's data directory rather than shipped in the installer: it is ~9 MB, it is not ours
# to redistribute, and a stale copy is worse than a fresh one.
EXTENSION_ID = "bafijghppfhdpldihckdcadbcobikaca"
_CRX_URL = "https://clients2.google.com/service/update2/crx"

# Google's "unusual traffic" interstitial. Checked on the page body because the response
# is a normal 200 — the block is the page content, not the status code.
_CAPTCHA_MARKERS = ("unusual traffic", "recaptcha", "sorry/index")

# One Google page load per keyword, so this is a real wait. Bounded well below the number
# of keywords a plan produces: the point is to enrich the terms that matter, not to spend
# five minutes confirming what the rest of the pipeline already said.
MAX_KEYWORDS = 12
_PAGE_TIMEOUT_MS = 25_000
_WIDGET_TIMEOUT_MS = 8_000

_download_lock = threading.Lock()


class SurferUnavailable(RuntimeError):
    """Surfer could not be read this run. Never fatal — the caller carries on without it."""


def extension_dir() -> Path:
    return config.DATA_DIR / "keyword-surfer" / "extension"


def _crx_to_zip(crx: bytes) -> bytes:
    """Strip the CRX2/CRX3 header to get at the ZIP payload underneath."""
    if crx[:4] != b"Cr24":
        raise SurferUnavailable("the download was not a Chrome extension")
    version = struct.unpack("<I", crx[4:8])[0]
    if version == 3:
        header_size = struct.unpack("<I", crx[8:12])[0]
        return crx[12 + header_size :]
    if version == 2:
        pubkey_len, sig_len = struct.unpack("<II", crx[8:16])
        return crx[16 + pubkey_len + sig_len :]
    raise SurferUnavailable(f"unsupported CRX version {version}")


def ensure_extension() -> Path:
    """The unpacked extension, downloading it once if this machine has never had it.

    Locked because two plans generated at once would otherwise unpack into the same
    directory simultaneously and leave a half-written extension that Chromium refuses.
    """
    target = extension_dir()
    if (target / "manifest.json").exists():
        return target

    with _download_lock:
        if (target / "manifest.json").exists():  # won the race while waiting
            return target
        url = f"{_CRX_URL}?" + urlencode(
            {
                "response": "redirect",
                "prodversion": "120.0.0.0",
                "acceptformat": "crx2,crx3",
                "x": f"id={EXTENSION_ID}&uc",
            }
        )
        try:
            response = httpx.get(url, follow_redirects=True, timeout=60)
            response.raise_for_status()
        except httpx.HTTPError as err:
            raise SurferUnavailable(f"could not download the extension: {err}") from err

        # Unpacked beside the target and moved into place, so an interrupted download
        # never leaves a directory that looks installed but isn't.
        staging = target.with_name(target.name + ".partial")
        if staging.exists():
            import shutil

            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(_crx_to_zip(response.content))) as archive:
                archive.extractall(staging)
        except (zipfile.BadZipFile, OSError) as err:
            raise SurferUnavailable(f"could not unpack the extension: {err}") from err

        target.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(target)
        return target


def _playwright_proxy(proxy: dict | None) -> dict | None:
    """Playwright's proxy shape, or None when nothing is configured.

    None rather than an empty dict: Playwright reads a dict with a blank server as a
    misconfiguration rather than as "no proxy".
    """
    server = str((proxy or {}).get("proxyServer") or "").strip()
    if not server:
        return None
    out: dict = {"server": server}
    username = str((proxy or {}).get("proxyUsername") or "").strip()
    if username:
        out["username"] = username
        out["password"] = str((proxy or {}).get("proxyPassword") or "").strip()
    return out


def _parse_widget(text: str) -> tuple[str | None, str | None]:
    volume = re.search(r"([\d.,]+\s*[KM]?)\s*/?\s*mo", text, re.IGNORECASE)
    cpc = re.search(r"\$\s*([\d.,]+)", text)
    return (volume.group(0) if volume else None, f"${cpc.group(1)}" if cpc else None)


def scrape(keywords: list[str], geo: str = "", proxy: dict | None = None) -> list[dict]:
    """Surfer's figures for `keywords`, as rows shaped like the rest of the pipeline's.

    Raises SurferUnavailable for anything that stops the whole run — no browser, no
    extension, a dead proxy, a captcha. A keyword whose widget simply doesn't render is
    skipped rather than failing the batch: Surfer has no data for plenty of long-tail
    terms and that is not an error.
    """
    terms = [k for k in dict.fromkeys((k or "").strip() for k in keywords) if k][:MAX_KEYWORDS]
    if not terms:
        return []

    ext = ensure_extension()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as err:  # pragma: no cover - playwright is a hard dependency
        raise SurferUnavailable(f"playwright is not available: {err}") from err

    rows: list[dict] = []
    launch_proxy = _playwright_proxy(proxy)
    try:
        with sync_playwright() as p:
            context = _launch(p, ext, launch_proxy)
            try:
                page = context.new_page()
                for term in terms:
                    row = _scrape_one(page, term, geo, via_proxy=bool(launch_proxy))
                    if row:
                        rows.append(row)
            finally:
                context.close()
    except SurferUnavailable:
        raise
    except Exception as err:  # noqa: BLE001 — a browser that won't start is not fatal here
        raise SurferUnavailable(f"{type(err).__name__}: {err}") from err

    return rows


def _launch(playwright, ext: Path, launch_proxy: dict | None):
    return playwright.chromium.launch_persistent_context(
        user_data_dir="",
        # Extensions need a headed context; --headless=new is the mode that both loads
        # them and still draws nothing on the user's screen.
        headless=False,
        proxy=launch_proxy,
        args=[
            f"--disable-extensions-except={ext}",
            f"--load-extension={ext}",
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )


# The address Google will see. Fetched through the same browser as the search itself, so
# it reflects the proxy actually in force rather than what the settings claim.
_IP_ECHO_URL = "https://api.ipify.org?format=json"


def probe(proxy: dict | None = None, keyword: str = "leather laptop bag", geo: str = "US") -> dict:
    """Try one real lookup and describe what happened, without raising.

    Exists because the alternative way to find out whether a proxy works is to run a whole
    plan and read the log five minutes later. Returns the exit address alongside the
    verdict: "blocked" and "blocked from an address that is not my proxy" look identical
    otherwise, and only one of them means the proxy is misconfigured.
    """
    result: dict = {"ok": False, "detail": "", "exitIp": "", "usingProxy": False, "sample": None}

    launch_proxy = _playwright_proxy(proxy)
    result["usingProxy"] = bool(launch_proxy)

    try:
        ext = ensure_extension()
    except SurferUnavailable as err:
        result["detail"] = str(err)
        return result

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as err:  # pragma: no cover
        result["detail"] = f"playwright is not available: {err}"
        return result

    try:
        with sync_playwright() as p:
            context = _launch(p, ext, launch_proxy)
            try:
                page = context.new_page()

                # Done first and tolerantly: a dead proxy fails here, which is the single
                # most useful thing to report, and an echo service being down must not be
                # mistaken for the proxy being broken.
                try:
                    page.goto(_IP_ECHO_URL, timeout=_PAGE_TIMEOUT_MS)
                    import json as _json

                    result["exitIp"] = str(_json.loads(page.locator("pre, body").first.inner_text()).get("ip", ""))
                except Exception as err:  # noqa: BLE001
                    message = f"{type(err).__name__}: {err}"
                    if "PROXY" in message.upper():
                        result["detail"] = f"the proxy refused the connection ({message.splitlines()[0]})"
                        return result
                    result["exitIp"] = "(could not determine)"

                row = _scrape_one(page, keyword, geo, via_proxy=bool(launch_proxy))
            finally:
                context.close()
    except SurferUnavailable as err:
        result["detail"] = str(err)
        return result
    except Exception as err:  # noqa: BLE001
        result["detail"] = f"{type(err).__name__}: {str(err).splitlines()[0]}"
        return result

    if row is None:
        # Past the captcha, so the hard part works; Surfer simply had nothing for this
        # term. Reported as success because the next real keyword may well have data.
        result["ok"] = True
        result["detail"] = (
            f"Google returned results, but Keyword Surfer had no data for {keyword!r}. "
            "The connection works; other keywords may still return figures."
        )
        return result

    result["ok"] = True
    result["sample"] = row
    volume = row.get("volume") or "no volume"
    result["detail"] = f"Working — {keyword!r} came back with {volume}."
    return result


def _scrape_one(page, keyword: str, geo: str, via_proxy: bool) -> dict | None:
    params = {"q": keyword}
    if geo:
        params["gl"] = geo
    page.goto(f"https://www.google.com/search?{urlencode(params)}", timeout=_PAGE_TIMEOUT_MS)

    body = page.content().lower()
    if any(marker in body for marker in _CAPTCHA_MARKERS):
        # Which side of the proxy question we are on. "Blocked" covers two problems with
        # different fixes: no proxy at all, versus a pool Google has also burned.
        via = "through the configured proxy" if via_proxy else "and no proxy is configured"
        raise SurferUnavailable(
            f"Google served a captcha instead of results {via}"
        )

    try:
        page.wait_for_selector(".ks-main-keyword-widget", timeout=_WIDGET_TIMEOUT_MS)
    except Exception:
        return None  # Surfer has nothing for this term; not a failure

    widget = page.locator(".ks-main-keyword-widget").first.inner_text()
    volume, cpc = _parse_widget(widget)

    related = []
    for cell in page.locator(".ks-cell").all()[:15]:
        text = cell.inner_text().strip()
        if text and text.lower() != keyword.lower():
            related.append(text)

    return {
        "keyword": keyword,
        "volume": volume or "",
        "cpc": cpc or "",
        "related": related,
        "source": "keyword_surfer",
        "sourceLabel": "live Keyword Surfer scrape",
    }


def merge_into(rows: list[dict], surfer_rows: list[dict]) -> list[dict]:
    """Fold Surfer's figures into an existing keyword table.

    `rows` wins on conflicts. Google Ads' numbers are measured and Surfer's are its own
    estimate, so Surfer fills blanks and adds related terms but never overwrites a figure
    that is already there. Keywords Surfer found that nothing else did are appended.

    A row carrying figures from both records both, so the sheet's data-source column stays
    honest per row instead of implying the whole table came from one place.
    """
    if not surfer_rows:
        return rows

    by_key = {}
    merged = [dict(r) for r in rows]
    for row in merged:
        by_key[str(row.get("keyword", "")).strip().lower()] = row

    for extra in surfer_rows:
        key = str(extra.get("keyword", "")).strip().lower()
        if not key:
            continue
        target = by_key.get(key)
        if target is None:
            merged.append(dict(extra))
            by_key[key] = merged[-1]
            continue

        contributed = False
        for field in ("volume", "cpc"):
            if not str(target.get(field) or "").strip() and str(extra.get(field) or "").strip():
                target[field] = extra[field]
                contributed = True

        existing_related = list(target.get("related") or [])
        seen = {r.strip().lower() for r in existing_related}
        for related in extra.get("related") or []:
            if related.strip().lower() not in seen:
                existing_related.append(related)
                seen.add(related.strip().lower())
                contributed = True
        target["related"] = existing_related

        if contributed:
            sources = [s for s in str(target.get("source") or "").split("+") if s]
            if "keyword_surfer" not in sources:
                sources.append("keyword_surfer")
                target["source"] = "+".join(sources)
                labels = [
                    lbl
                    for lbl in [str(target.get("sourceLabel") or "").strip(), "live Keyword Surfer scrape"]
                    if lbl
                ]
                target["sourceLabel"] = " + ".join(dict.fromkeys(labels))

    return merged
