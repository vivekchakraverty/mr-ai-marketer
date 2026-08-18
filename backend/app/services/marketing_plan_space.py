"""Client for the Digital Marketer Space, which generates the marketing plan.

The plan used to be assembled here: this backend ran the vendored keyword/SEO/social/ads
modules and searched a multi-GB retrieval index the user had to download first. The Space
now runs all of that, so setting MARKETING_PLAN_SPACE moves generation there and the local
index stops being needed at all. Leave it unset and the local pipeline runs exactly as it
did — nothing was removed, and a checkout without the Space still generates plans.

Who pays is unchanged. The caller's own Hugging Face token goes with the request and the
inference is billed to it, the same as when the models were called from this machine. What
is new is that the token leaves the machine to get there, which is a real disclosure and
the reason a fine-grained, inference-scoped token is worth using.

The user's Google Ads credentials from Settings travel with the request too, so the
official keyword tier runs against *their* account. Without them the Space falls through
its own free tiers — a headless-Chromium Keyword Surfer scrape, then Google Autocomplete
and Trends, then clearly-labelled LLM estimates — so keyword research still happens, just
with weaker numbers and a source note that says so.

Keyword Surfer is the one source that is deliberately *not* asked of the Space. It has to
be scraped from a browser on a real consumer network, which a Space does not have — see
services/keyword_surfer.py, which runs it here and merges the result in afterwards.

What the Space *will* take is the finished figures. A collected run can be attached to a
plan and travels as `supplied_keywords`, where it is used only if the Google Ads tier comes
back empty — so the SEO plan is built on real numbers whenever there are any.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

# What the Space calls its "let each stage pick its own model" option. The app's own
# dropdown says "Auto"; this is the value that has to go over the wire.
AUTO_MODEL_LABEL = "Auto (recommended model per task)"

# Long by design: a sleeping Space has to wake, and a plan is five model calls plus live
# keyword research. Bounded anyway, because a request that will never answer should fail
# rather than hold a queue slot forever.
TIMEOUT_SECONDS = 1800

_client = None
_client_src = ""


class PlanSpaceError(RuntimeError):
    """The Space could not produce a plan. Surfaced to the user as-is."""


def is_configured() -> bool:
    return bool(config.MARKETING_PLAN_SPACE)


def _get_client():
    """A cached gradio_client for the configured Space.

    gradio_client rather than hand-rolled HTTP: Gradio's endpoint layout is a
    version-dependent detail and the client reads the right route from the Space's own
    config, the same way Blog Writer and Brand Studio already talk to theirs.
    """
    global _client, _client_src
    from gradio_client import Client

    src = config.require_space(
        config.MARKETING_PLAN_SPACE, "MARKETING_PLAN_SPACE", "Marketing Plan"
    )
    if _client is None or _client_src != src:
        try:
            _client = Client(src, verbose=False, httpx_kwargs={"timeout": TIMEOUT_SECONDS})
        except Exception as err:  # noqa: BLE001 — unreachable, refused, not a Gradio app
            raise PlanSpaceError(f"could not connect to {src}: {err}") from err
        _client_src = src
    return _client


def available_models() -> list[str]:
    """The models the Space actually offers, read from its own API description.

    Asked rather than assumed: the Space's runtime model policy is enforced there, and a
    list hardcoded here would drift into offering options the Space rejects. An empty
    result means only "Auto" is shown, which the Space always accepts.
    """
    try:
        api = _get_client().view_api(return_format="dict", print_info=False)
        params = api["named_endpoints"]["/generate_plan"]["parameters"]
        for param in params:
            if param.get("parameter_name") == "model":
                enum = (param.get("type") or {}).get("enum") or []
                return [m for m in enum if m != AUTO_MODEL_LABEL]
    except Exception as err:  # noqa: BLE001 — a dropdown is not worth failing a screen over
        print(f"[marketing-plan] could not read the Space's model list: {err}")
    return []


@dataclass
class PlanResult:
    status: str
    full_markdown: str
    seo_markdown: str
    social_markdown: str
    ads_markdown: str
    keywords_markdown: str
    keyword_rows: list[dict] = field(default_factory=list)
    keyword_source_note: str = ""


def _as_path(value) -> Path | None:
    """gradio_client hands file outputs back as a path string or a FileData dict."""
    if not value:
        return None
    raw = value if isinstance(value, str) else value.get("path")
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() else None


def generate(
    *,
    product_description: str,
    budget_usd_per_month: float,
    manpower_summary: str,
    industry_key: str,
    geo: str,
    model: str,
    hf_token: str,
    google_ads: dict | None = None,
    supplied_keywords: list[dict] | None = None,
    copy_files_to: Path | None = None,
) -> PlanResult:
    ga = google_ads or {}
    client = _get_client()
    try:
        result = client.predict(
            product_description=product_description,
            budget_usd_per_month=float(budget_usd_per_month or 0),
            manpower_summary=manpower_summary,
            industry_key=industry_key,
            geo=geo,
            model=model or AUTO_MODEL_LABEL,
            hf_token_in=hf_token,
            google_ads_developer_token=ga.get("developerToken", ""),
            google_ads_client_id=ga.get("clientId", ""),
            google_ads_client_secret=ga.get("clientSecret", ""),
            google_ads_refresh_token=ga.get("refreshToken", ""),
            google_ads_login_customer_id=ga.get("loginCustomerId", ""),
            # Sent as JSON text because Gradio's typed inputs are scalars; the Space
            # parses it and ignores anything malformed rather than failing the plan.
            keyword_data_json=json.dumps(supplied_keywords) if supplied_keywords else "",
            api_name="/generate_plan",
        )
    except Exception as err:  # noqa: BLE001 — any transport/protocol failure
        raise PlanSpaceError(f"the plan Space could not be reached: {err}") from err

    if not isinstance(result, (list, tuple)) or len(result) < 9:
        raise PlanSpaceError(
            f"unexpected response from the plan Space ({len(result) if hasattr(result, '__len__') else '?'} values)"
        )

    status, seo_md, social_md, ads_md, full_md, plan_file, keywords_md, keywords_file, keywords_data = result[:9]
    status = str(status or "")

    # The Space reports refusals and failures through its status line rather than by
    # raising, so an empty plan with a message in `status` is the normal shape of an
    # error and has to be checked for explicitly.
    if not str(full_md or "").strip():
        raise PlanSpaceError(status.strip() or "the plan Space returned an empty plan")

    payload = keywords_data if isinstance(keywords_data, dict) else {}
    rows = [r for r in (payload.get("rows") or []) if isinstance(r, dict)]

    # gradio_client downloads file outputs into its own temp directory, which is not
    # guaranteed to outlive this process — copy anything worth keeping somewhere we own.
    if copy_files_to is not None:
        copy_files_to.mkdir(parents=True, exist_ok=True)
        for src, name in ((plan_file, "space-plan.md"), (keywords_file, "space-keywords.csv")):
            path = _as_path(src)
            if path is not None:
                shutil.copy(path, copy_files_to / name)

    return PlanResult(
        status=status,
        full_markdown=str(full_md or ""),
        seo_markdown=str(seo_md or ""),
        social_markdown=str(social_md or ""),
        ads_markdown=str(ads_md or ""),
        keywords_markdown=str(keywords_md or ""),
        keyword_rows=rows,
        keyword_source_note=str(payload.get("sourceNote") or ""),
    )
